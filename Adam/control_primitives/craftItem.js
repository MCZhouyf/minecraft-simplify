async function craftItem(bot, name, count = 1) {
    // return if name is not string
    if (typeof name !== "string") {
        throw new Error("name for craftItem must be a string");
    }
    // return if count is not number
    if (typeof count !== "number") {
        throw new Error("count for craftItem must be a number");
    }
    const itemByName = mcData.itemsByName[name];
    if (!itemByName) {
        throw new Error(`No item named ${name}`);
    }
    const before = bot.inventory.count(itemByName.id);
    const craftingTable = bot.findBlock({
        matching: mcData.blocksByName.crafting_table.id,
        maxDistance: 32,
    });
    if (!craftingTable) {
        bot.chat("Craft without a crafting table");
    } else {
        await bot.pathfinder.goto(
            new GoalLookAtBlock(craftingTable.position, bot.world)
        );
    }
    const recipe = bot.recipesFor(itemByName.id, null, 1, craftingTable)[0];
    if (recipe) {
        bot.chat(`I can make ${name}`);
        if (!mcdriftCraftGateAllows(bot, itemByName.name)) {
            bot.chat(`MC-Drift gate blocked crafting ${name}`);
            return;
        }
        try {
            await bot.craft(recipe, count, craftingTable);
            await bot.waitForTicks(6);
            const after = bot.inventory.count(itemByName.id);
            if (after <= before) {
                throw new Error(`crafted ${name} was not found in inventory`);
            }
            bot.chat(`I did the recipe for ${name} ${count} times`);
        } catch (err) {
            bot.chat(`I cannot do the recipe for ${name} ${count} times`);
            throw err;
        }
    } else {
        failedCraftFeedback(bot, name, itemByName, craftingTable);
        _craftItemFailCount++;
        if (_craftItemFailCount > 10) {
            throw new Error(
                "craftItem failed too many times, check chat log to see what happened"
            );
        }
    }
}

function mcdriftCraftGateAllows(bot, resultName) {
    const iapPath = "/root/.minecraft/config/iap-drift/tasks.json";
    if (typeof fs !== "undefined" && fs.existsSync(iapPath)) {
        try {
            const config = JSON.parse(fs.readFileSync(iapPath, "utf8"));
            const resultId = `minecraft:${resultName}`;
            const tasks = config.tasks || {};
            for (const id of Object.keys(tasks)) {
                const task = tasks[id];
                if (!task || task.enabled === false || task.event !== "crafting_output") continue;
                const targets = task.target_items || [];
                if (!targets.includes(resultId)) continue;
                if (!mcdriftPredicateAllows(bot, task.ground_truth)) return false;
            }
        } catch (err) {
            return true;
        }
    }

    const path = "/root/.minecraft/config/mcdrift.json";
    if (typeof fs === "undefined" || !fs.existsSync(path)) return true;

    let config;
    try {
        config = JSON.parse(fs.readFileSync(path, "utf8"));
    } catch (err) {
        return true;
    }

    const enabled = Array.isArray(config.enabled) ? config.enabled : [];
    const gates = config.gates || {};
    const resultId = `minecraft:${resultName}`;

    for (const id of enabled) {
        const gate = gates[id];
        if (!gate || gate.gate !== "craft_result" || !gate.params) continue;
        const params = gate.params;
        if (params.result_match && !(new RegExp(params.result_match).test(resultId))) {
            continue;
        }

        let pass = true;
        if (params.require === "inventory_min") {
            const itemName = String(params.item || "").replace(/^minecraft:/, "");
            const item = mcData.itemsByName[itemName];
            pass = !!item && bot.inventory.count(item.id) >= Number(params.count || 1);
        } else if (params.require === "nearby_block") {
            const blockName = String(params.block || "").replace(/^minecraft:/, "");
            const block = mcData.blocksByName[blockName];
            pass = !!block && !!bot.findBlock({
                matching: block.id,
                maxDistance: Number(params.radius || 3),
            });
        } else if (params.require === "sky_visible") {
            const base = bot.entity.position.floored().offset(0, 1, 0);
            pass = true;
            for (let dy = 0; dy <= 32; dy++) {
                const block = bot.blockAt(base.offset(0, dy, 0));
                if (block && block.name !== "air" && block.name !== "cave_air" && block.name !== "void_air") {
                    pass = false;
                    break;
                }
            }
        }

        if (!pass) return false;
    }
    return true;
}

function mcdriftPredicateAllows(bot, predicate) {
    const normalized = String(predicate || "").trim().replace(/\s+/g, " ");

    let match = normalized.match(/^nearby_block\(([a-z0-9_]+)\)\s*<=k\s*(\d+)$/);
    if (match) {
        const blockName = match[1].replace(/^minecraft:/, "");
        const radius = Number(match[2]);
        const block = mcData.blocksByName[blockName];
        return !!block && !!bot.findBlock({ matching: block.id, maxDistance: radius });
    }

    match = normalized.match(/^nearby_entity\(([a-z0-9_]+)\)\s*<=k\s*(\d+)$/);
    if (match) {
        const entityName = match[1].replace(/^minecraft:/, "");
        const radius = Number(match[2]);
        const entity = bot.nearestEntity(e => {
            const name = e.name || e.type || "";
            return name === entityName && e.position.distanceTo(bot.entity.position) <= radius;
        });
        return !!entity;
    }

    match = normalized.match(/^time_of_day\(time\)\s+in\s+\[(\d+),\s*(\d+)\]$/);
    if (match) {
        const time = ((bot.time.timeOfDay % 24000) + 24000) % 24000;
        const start = Number(match[1]);
        const end = Number(match[2]);
        return start <= end ? time >= start && time <= end : time >= start || time <= end;
    }

    return true;
}
