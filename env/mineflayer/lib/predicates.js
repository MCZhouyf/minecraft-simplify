/**
 * MC-Drift Stage 3: predicate evaluation layer (contract K3).
 *
 * Pure, synchronous world-state queries — one small function per primitive,
 * registered in EVALUATORS. Every result is {id, value: 0|1|null, raw, known, error}.
 * known=false (value=null) means "state not obtainable" (unloaded chunk, empty
 * hand for type queries, missing station, ...) — callers MUST distinguish
 * unknown from 0.
 */

const TIERS = ["wooden", "golden", "stone", "iron", "diamond", "netherite"];
const TOOL_CATEGORIES = ["pickaxe", "axe", "shovel", "sword", "hoe"];
const STONE_FAMILY = new Set([
    "stone", "cobblestone", "smooth_stone", "deepslate", "stone_bricks"]);
// chunks within this radius of the bot are guaranteed loaded -> absence is real
const RELIABLE_SCAN_RADIUS = 16;
const MAX_BUILD_HEIGHT = 320;

function tierRank(itemName) {
    if (!itemName) return -1;
    const m = itemName.match(/^(wooden|golden|stone|iron|diamond|netherite)_/);
    return m ? TIERS.indexOf(m[1]) : -1;
}

function compareNum(cur, cmp, val) {
    switch (cmp) {
        case ">=": return cur >= val;
        case "<=": return cur <= val;
        case "=":  return cur === val;
        default: throw new Error(`bad comparator ${cmp} for numeric predicate`);
    }
}

function ok(id, bool, raw) { return {id, value: bool ? 1 : 0, raw, known: true, error: null}; }
function unknown(id, raw, why) { return {id, value: null, raw, known: false, error: why || null}; }

// ----------------------------------------------------------------- primitives
function invCount(bot, name) {
    return bot.inventory.items()
        .filter((it) => it.name === name)
        .reduce((s, it) => s + it.count, 0);
}

const EVALUATORS = {
    inventory_count(bot, mcData, p) {
        const cur = invCount(bot, p.property);
        return ok(p.id, compareNum(cur, p.comparator, Number(p.value)), cur);
    },

    held_tool(bot, mcData, p) {           // property: tier
        const held = bot.heldItem;
        const curRank = held ? tierRank(held.name) : -1;
        const valRank = TIERS.indexOf(String(p.value));
        if (valRank < 0) throw new Error(`unknown tier '${p.value}'`);
        return ok(p.id, compareNum(curRank, p.comparator, valRank),
                  held ? held.name : "empty");
    },

    held_item(bot, mcData, p) {           // property: type
        const held = bot.heldItem;
        if (!held) return unknown(p.id, "empty", "empty hand: item type undefined");
        const want = String(p.value);
        const match = TOOL_CATEGORIES.includes(want)
            ? held.name.endsWith(`_${want}`)
            : held.name === want;
        return ok(p.id, match, held.name);
    },

    nearby_block(bot, mcData, p) {        // property: block name; value: radius k (<=k)
        const block = mcData.blocksByName[p.property];
        if (!block) throw new Error(`unknown block '${p.property}'`);
        const k = Number(p.value);
        const found = bot.findBlocks({matching: block.id, maxDistance: k, count: 1});
        if (found.length > 0) {
            const d = bot.entity.position.distanceTo(found[0]);
            return ok(p.id, true, Math.round(d * 10) / 10);
        }
        if (k <= RELIABLE_SCAN_RADIUS) return ok(p.id, false, null);
        return unknown(p.id, null, `absence within r=${k} not reliable (chunks may be unloaded)`);
    },

    block_below(bot, mcData, p) {         // property: type
        const pos = bot.entity.position.floored().offset(0, -1, 0);
        const b = bot.blockAt(pos);
        if (!b) return unknown(p.id, null, "block below not loaded");
        return ok(p.id, b.name === String(p.value), b.name);
    },

    y_level(bot, mcData, p) {             // property: y
        const cur = bot.entity.position.y;
        return ok(p.id, compareNum(cur, p.comparator, Number(p.value)),
                  Math.round(cur * 10) / 10);
    },

    sky_exposed(bot, mcData, p) {         // property: sky; value: true/false
        const base = bot.entity.position.floored();
        for (let y = base.y + 2; y <= MAX_BUILD_HEIGHT; y++) {
            const b = bot.blockAt(base.offset(0, y - base.y, 0));
            if (b === null) return unknown(p.id, y, "column not fully loaded");
            if (b.boundingBox !== "empty" && !b.transparent) {
                const want = String(p.value) === "true" || p.value === true;
                return ok(p.id, want === false, `blocked@y=${y}`);
            }
        }
        const want = String(p.value) === "true" || p.value === true;
        return ok(p.id, want === true, "open");
    },

    time_of_day(bot, mcData, p) {         // property: time; comparator "in"; value [a,b]
        const cur = bot.time.timeOfDay;
        if (p.comparator !== "in" || !Array.isArray(p.value) || p.value.length !== 2)
            throw new Error("time_of_day expects comparator 'in' with value [a,b]");
        const [a, b] = p.value.map(Number);
        return ok(p.id, a <= cur && cur <= b, cur);
    },

    station_type(bot, mcData, p) {        // standalone grounding: station of given type within 3
        const block = mcData.blocksByName[String(p.value)];
        if (!block) throw new Error(`unknown station '${p.value}'`);
        const found = bot.findBlocks({matching: block.id, maxDistance: 3, count: 1});
        if (found.length > 0)
            return ok(p.id, true, Math.round(bot.entity.position.distanceTo(found[0]) * 10) / 10);
        return ok(p.id, false, null);
    },

    station_base_block(bot, mcData, p) {  // block under the nearest furnace (<=3)
        const furnace = mcData.blocksByName["furnace"];
        const found = bot.findBlocks({matching: furnace.id, maxDistance: 3, count: 1});
        if (found.length === 0)
            return unknown(p.id, null, "no station within r=3: base undefined");
        const base = bot.blockAt(found[0].offset(0, -1, 0));
        if (!base) return unknown(p.id, null, "station base not loaded");
        const match = String(p.value) === "stone"
            ? STONE_FAMILY.has(base.name) : base.name === String(p.value);
        return ok(p.id, match, base.name);
    },

    ingredient_type(bot, mcData, p) {
        return unknown(p.id, null,
            "requires action context (evaluated by the compiler/executor at craft time)");
    },

    weather(bot, mcData, p) {             // property: state; clear|rain|thunder
        const cur = bot.thunderState > 0 ? "thunder" : (bot.isRaining ? "rain" : "clear");
        return ok(p.id, cur === String(p.value), cur);
    },
};

// ----------------------------------------------------------------- entry points
function evalPredicates(bot, mcData, preds) {
    return (preds || []).map((p) => {
        try {
            const fn = EVALUATORS[p.target];
            if (!fn) return unknown(p.id, null, `unknown target '${p.target}'`);
            return fn(bot, mcData, p);
        } catch (e) {
            return unknown(p && p.id, null, String((e && e.message) || e));
        }
    });
}

function stateSnapshot(bot, mcData) {
    const pos = bot.entity.position;
    const held = bot.heldItem;
    const below = bot.blockAt(pos.floored().offset(0, -1, 0));
    const inventory = {};
    bot.inventory.items().forEach((it) => {
        inventory[it.name] = (inventory[it.name] || 0) + it.count;
    });
    const sky = EVALUATORS.sky_exposed(bot, mcData,
        {id: "_snap", property: "sky", comparator: "=", value: true});
    return {
        "agent.x": Math.round(pos.x * 10) / 10,
        "agent.y": Math.round(pos.y * 10) / 10,
        "agent.z": Math.round(pos.z * 10) / 10,
        "world.time_of_day": bot.time.timeOfDay,
        "world.is_raining": !!bot.isRaining,
        "held.name": held ? held.name : null,
        "held.tier": held ? tierRank(held.name) : -1,
        "block_below.name": below ? below.name : null,
        "sky_exposed": sky.known ? !!sky.value : null,
        "inventory": inventory,
    };
}

module.exports = {evalPredicates, stateSnapshot, tierRank, TIERS};
