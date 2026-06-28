"""Stage 5: intervention-plan executor — renders K5 primitive calls to JS and
runs them through env.step (the agent's standard execution channel; never
resets, never uses game commands).

run_plan(env, plan) executes ONE primitive per env.step so each step gets its
own K7 intervention_step event and a clean abort path: any failed step makes
run_plan return False — the caller must then DISCARD the pending observation
(candidate stays undecided; pools are never polluted)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from pathlib import Path

from Adam.tcpg.eventlog import log_event

_PRIM_DIR = Path(__file__).resolve().parent.parent / "control_primitives"
_PRIMS_CACHE: str = ""


def _prims() -> str:
    """Primitive JS sources, loaded directly from files (no `javascript`
    bridge dependency, so this module imports cleanly offline)."""
    global _PRIMS_CACHE
    if not _PRIMS_CACHE:
        _PRIMS_CACHE = "\n".join(
            f.read_text(encoding="utf-8")
            for f in sorted(_PRIM_DIR.glob("*.js"))) + "\n"
    return _PRIMS_CACHE


def _js(call: Dict[str, Any]) -> str:
    p, a = call["primitive"], call["args"]
    if p == "mineBlock":
        if a.get("special") == "roof_column":
            n = int(a.get("count", 6))
            return f"""
const base = bot.entity.position.floored();
for (let dy = 2; dy <= {n + 1}; dy++) {{
    const b = bot.blockAt(base.offset(0, dy, 0));
    if (!b || b.boundingBox === 'empty' || b.transparent) continue;
    await bot.dig(b);
}}
bot.chat("[iap] roof opened");
"""
        opts = "{maxCollectAttempts: 2, totalTimeoutMs: 20000"
        if a.get("drop"):
            opts += f", drop: {json.dumps(a['drop'])}"
        opts += "}"
        return (f"await mineBlock(bot, {json.dumps(a['name'])}, "
                f"{int(a.get('count', 1))}, "
                f"{opts});")
    if p == "craftItem":
        return f"await craftItem(bot, {json.dumps(a['name'])}, {int(a.get('count', 1))});"
    if p == "smeltItem":
        return (f"await smeltItem(bot, {json.dumps(a['name'])}, "
                f"{json.dumps(a.get('fuel', 'coal'))}, {int(a.get('count', 1))});")
    if p == "placeItem":
        where = a.get("where", "near")
        if where == "roof":
            return f"""
const mcData = require('minecraft-data')(bot.version);
const head = bot.entity.position.floored().offset(0, 2, 0);
const below = bot.blockAt(head.offset(0, -1, 0));
await bot.equip(mcData.itemsByName[{json.dumps(a['name'])}].id, 'hand');
try {{ await bot.placeBlock(bot.blockAt(head.offset(1, 0, 0)) || below, new (require('vec3').Vec3)(0, 1, 0)); }}
catch (e) {{ await placeItem(bot, {json.dumps(a['name'])}, head); }}
bot.chat("[iap] roof closed");
"""
        offset = "(2, 1, 0)" if where == "on_last" else "(2, 0, 0)"
        return (f"await placeItem(bot, {json.dumps(a['name'])}, "
                f"bot.entity.position.floored().offset{offset});")
    if p == "useChest":
        items = json.dumps({i["name"]: i["count"] for i in a["items"]})
        fn = "depositItemIntoChest" if a["op"] == "deposit" else "getItemFromChest"
        ensure = """
const mcData = require('minecraft-data')(bot.version);
const chestPos = bot.entity.position.floored().offset(2, 0, 0);
let chest = bot.blockAt(chestPos);
if (!chest || chest.name !== 'chest') {
    await placeItem(bot, 'chest', chestPos);
    await bot.waitForTicks(10);
    chest = bot.blockAt(chestPos);
}
if (!chest || chest.name !== 'chest') {
    chest = bot.findBlock({matching: mcData.blocksByName['chest'].id, maxDistance: 12});
}
if (!chest || chest.name !== 'chest') throw new Error('[iap] no chest available for stash');
"""
        return ensure + f"await {fn}(bot, chest.position, {items});"
    if p == "equip":
        return f"""
const mcData = require('minecraft-data')(bot.version);
const want = {json.dumps(a['name'])};
const it = mcData.itemsByName[want];
if (!it) throw new Error('[iap] unknown item to equip: ' + want);
let equipped = false;
for (let attempt = 0; attempt < 3; attempt++) {{
    try {{ await bot.equip(it.id, 'hand'); }} catch (e) {{ /* retry below */ }}
    await bot.waitForTicks(6);
    const held = bot.heldItem;
    if (held && held.name === want) {{ equipped = true; break; }}
    await bot.waitForTicks(6);
}}
if (!equipped) {{
    const held = bot.heldItem;
    throw new Error('[iap] equip unconfirmed: wanted ' + want +
                    ', holding ' + (held ? held.name : 'nothing'));
}}
"""
    if p == "moveTo":
        if "y" in a:
            return f"""
const {{ Movements, goals }} = require('mineflayer-pathfinder');
const {{ Vec3 }} = require('vec3');
const m = new Movements(bot);
m.canDig = true; m.allowFreeMotion = true;
bot.pathfinder.setMovements(m);
await bot.pathfinder.goto(new goals.GoalY({int(a['y'])}));
"""
        dx = int(a.get("dx", 0))
        return f"""
const {{ Movements, goals }} = require('mineflayer-pathfinder');
const m = new Movements(bot);
bot.pathfinder.setMovements(m);
const p0 = bot.entity.position;
await bot.pathfinder.goto(new goals.GoalXZ(Math.floor(p0.x) + {dx}, Math.floor(p0.z)));
"""
    if p == "moveToBlock":
        name = a["name"]
        radius = max(1, int(a.get("radius", 3)))
        max_distance = max(radius, int(a.get("maxDistance", 32)))
        return f"""
const mcData = require('minecraft-data')(bot.version);
const {{ Movements, goals }} = require('mineflayer-pathfinder');
const rawBlockName = {json.dumps(name)};
const blockName = rawBlockName.startsWith('minecraft:') ? rawBlockName.slice(10) : rawBlockName;
const aliases = blockName === 'water' ? ['water', 'flowing_water']
    : (blockName === 'lava' ? ['lava', 'flowing_lava'] : [blockName]);
const blockIds = aliases.map(n => mcData.blocksByName[n]).filter(Boolean).map(b => b.id);
if (!blockIds.length) throw new Error('[iap] unknown block for moveToBlock: ' + rawBlockName);
const target = bot.findBlock({{
    matching: b => blockIds.includes(b.type),
    maxDistance: {max_distance}
}});
if (!target) throw new Error('[iap] moveToBlock target not found: ' + rawBlockName);
const m = new Movements(bot);
m.canDig = true; m.allowFreeMotion = true;
bot.pathfinder.setMovements(m);
let goal = new goals.GoalNear(target.position.x, target.position.y, target.position.z, {radius});
if (blockName === 'water' || blockName === 'lava') {{
    const offsets = [[1,0],[-1,0],[0,1],[0,-1],[2,0],[-2,0],[0,2],[0,-2]];
    for (const [dx, dz] of offsets) {{
        const x = target.position.x + dx;
        const y = target.position.y;
        const z = target.position.z + dz;
        const feet = bot.blockAt(new Vec3(x, y, z));
        const head = bot.blockAt(new Vec3(x, y + 1, z));
        const floor = bot.blockAt(new Vec3(x, y - 1, z));
        if (feet && head && floor && feet.boundingBox === 'empty'
                && head.boundingBox === 'empty' && floor.boundingBox === 'block') {{
            goal = new goals.GoalBlock(x, y, z);
            break;
        }}
    }}
}}
await bot.pathfinder.goto(goal);
"""
    if p == "set_y":
        y = int(round(float(a["y"])))
        return f"""
const p = bot.entity.position;
bot.chat(`/tp @s ${{p.x.toFixed(3)}} {y} ${{p.z.toFixed(3)}}`);
await bot.waitForTicks(10);
"""
    if p == "set_time":
        tick = int(a["tick"]) % 24000
        return f"""
bot.chat("/time set {tick}");
await bot.waitForTicks(10);
"""
    if p == "wait":
        cond = ("t >= {0} && t <= {1}" if "until_in" in a else "t < {0} || t > {1}").format(
            *(a.get("until_in") or a.get("until_out")))
        max_checks = int(a.get("max_checks", 3))
        wait_ticks = int(a.get("wait_ticks", 40))
        return f"""
let __iap_wait_ok = false;
for (let i = 0; i < {max_checks}; i++) {{
    const t = bot.time.timeOfDay;
    if ({cond}) {{ __iap_wait_ok = true; break; }}
    await bot.waitForTicks({wait_ticks});
}}
if (!__iap_wait_ok) {{
    const t = bot.time.timeOfDay;
    throw new Error('[iap] wait timeout: time_of_day=' + t);
}}
"""
    if p == "set_count":
        # Boundary intervention exact-set: clear the item then give exactly n
        # (preserves all other inventory). Sim-verifiable reset; charged the
        # flat sim_verify_cost by the runtime, not these in-world commands.
        name = a["name"]
        n = max(0, int(a.get("count", 0)))
        item = name if ":" in name else "minecraft:" + name
        lines = [f'bot.chat("/clear @s {item}");', "await bot.waitForTicks(6);"]
        if n > 0:
            lines += [f'bot.chat("/give @s {item} {n}");', "await bot.waitForTicks(6);"]
        return "\n".join(lines) + "\n"
    raise ValueError(f"unknown primitive {p}")


def render(call: Dict[str, Any]) -> str:
    """Primitive sources + one rendered call (one env.step payload)."""
    return _prims() + "\n" + _js(call) + "\n"


def run_plan(env, plan: List[Dict[str, Any]],
             cid: str = "-", trial_id: str = "-", step: int = -1,
             retries: int = 1) -> Tuple[bool, List[Dict[str, Any]]]:
    """Execute a K5 plan one primitive per /step. Returns (success, events).
    Each primitive gets up to `retries` extra attempts on transient errors
    (e.g. "Failed to step Minecraft server"); a primitive that still fails
    aborts the plan (clean-discard contract: caller voids the observation)."""
    events: List[Dict[str, Any]] = []
    for k, c in enumerate(plan):
        last_exc = None
        for attempt in range(retries + 1):
            try:
                env.step(render(c))
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = str(exc)
                import time as _t
                _t.sleep(0.5)
        if last_exc is not None:
            events.append({"k": k, "call": c, "ok": False, "error": last_exc})
            log_event("intervention_step", {"cid": cid, "primitive": c["primitive"],
                                            "args": c["args"], "ok": False,
                                            "error": last_exc, "attempts": retries + 1},
                      trial_id, step)
            return False, events
        events.append({"k": k, "call": c, "ok": True})
        log_event("intervention_step", {"cid": cid, "primitive": c["primitive"],
                                        "args": c["args"], "ok": True},
                  trial_id, step)
    return True, events
