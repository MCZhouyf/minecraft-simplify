"""Stage-6 integration: SINGLE-BIAS end-to-end closed loop on C2.

The headline acceptance of the whole project: with the C2 datapack enabled
(gold needs a diamond pickaxe), a failure event triggers proposal ->
compilation -> in-episode do(h=1)/do(h=0) -> dual-pool decision ->
write-back -> the verified gate immediately changes graph planning.

PRECONDITIONS (on top of earlier stages):
  * mc_drift_C2 pack installed (stage 1 flow); test enables ONLY C2
  * arena: ORE_SPOT loaded; the test re-places gold ore before every retry
    (harness privilege — the agent itself never issues commands)
The LLM is scripted (no API key): one true gate with REAL interventions
(equip swaps) and REAL predicates. Confound separation is covered by the
offline runtime-loop test; this in-world test keeps the live path short.
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg.ccg import CCG                                 # noqa: E402
from Adam.tcpg.executor import run_plan                       # noqa: E402
from Adam.tcpg import compiler as C                           # noqa: E402
from Adam.tcpg.predicates import state_snapshot               # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime                     # noqa: E402
from tests.conftest import reset_with, run_chat               # noqa: E402

pytestmark = pytest.mark.integration

ORE_SPOT = (0, -40, 0)        # ADJUST: loaded chunk; gold ore placed at +3x

START_INV = {"iron_pickaxe": 1, "diamond_pickaxe": 1, "stick": 4,
             "chest": 1, "crafting_table": 1}


def prepare_arena(env):
    run_chat(env,
             f"/fill {ORE_SPOT[0]-1} {ORE_SPOT[1]} {ORE_SPOT[2]-1} {ORE_SPOT[0]+4} {ORE_SPOT[1]+2} {ORE_SPOT[2]+1} minecraft:air",
             f"/fill {ORE_SPOT[0]-1} {ORE_SPOT[1]-1} {ORE_SPOT[2]-1} {ORE_SPOT[0]+4} {ORE_SPOT[1]-1} {ORE_SPOT[2]+1} minecraft:stone",
             f"/setblock {ORE_SPOT[0]+3} {ORE_SPOT[1]} {ORE_SPOT[2]} minecraft:gold_ore",
             f"/tp @s {ORE_SPOT[0]} {ORE_SPOT[1]} {ORE_SPOT[2]}")


def scripted_llm(prompt):
    return json.dumps([
        {"dimension": "capability", "target": "held_tool", "property": "tier",
         "comparator": ">=", "value": "diamond"},
    ])


def make_execute(env):
    """The action channel: re-place the ore (harness), run the skill, judge by
    raw_gold delta. This is the SAME channel for natural tries and retries."""
    def execute(action):
        from Adam.skill_loader import skill_loader   # lazy: needs js bridge
        assert action == "mineGoldOre"
        prepare_arena(env)
        before = state_snapshot(env)["inventory"].get("raw_gold", 0)
        try:
            env.step(skill_loader(action))
        except Exception:
            return False
        after = state_snapshot(env)["inventory"].get("raw_gold", 0)
        return after > before
    return execute


@pytest.fixture()
def c2_world(env):
    env.datapacks_enable_only(["C2"])
    reset_with(env, dict(START_INV), ORE_SPOT)
    prepare_arena(env)
    ok, _ = run_plan(env, [C.call("equip", name="iron_pickaxe")])
    assert ok
    try:
        yield env
    finally:
        env.datapacks_enable_only([])


@pytest.mark.parametrize("mode", ["tcpg", "freedo_oracle"])
def test_c2_closed_loop_discovers_and_writes_back(c2_world, mode, tmp_path,
                                                  monkeypatch):
    env = c2_world
    monkeypatch.setenv("IAP_K7_LOG", str(tmp_path / "k7.jsonl"))
    execute = make_execute(env)
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode=mode,
                     execute_action=execute, llm=scripted_llm,
                     config={"M": 30_000, "step_budget": 200,
                             "max_interventions_per_event": 8},
                     trial_id=f"e2e_c2_{mode}")

    for episode in range(4):
        run_plan(env, [C.call("equip", name="iron_pickaxe")])
        y = execute("mineGoldOre")
        inv = state_snapshot(env)["inventory"]
        rt.on_action("mineGoldOre", y, inv, {"inventory": dict(inv)})
        if any(c.status == "accepted" for c in rt.cands.values()):
            break

    by_target = {c.target: c for c in rt.cands.values()}
    assert by_target["held_tool"].status == "accepted", \
        f"pools: { {k: v.__dict__ for k, v in rt.pools.items()} }"
    assert any(c["target"] == "held_tool" for c in rt.ccg.conditions.values())

    # write-back immediately changes planning: zero-LLM closed plan
    plan = rt.ccg.plan_from_graph("raw_gold", {"diamond_pickaxe": 1})
    assert plan == ["mineGoldOre"]
    plan2 = rt.ccg.plan_from_graph(
        "raw_gold", {"iron_pickaxe": 1, "stick": 2, "crafting_table": 1, "coal": 1})
    assert plan2 and "craftDiamondPickaxe" in plan2

    k7 = [json.loads(l) for l in (tmp_path / "k7.jsonl").read_text().splitlines()]
    types = [e["type"] for e in k7]
    assert "writeback" in types and "intervention_start" in types
    if mode == "freedo_oracle":
        retries = sum(1 for e in k7 if e["type"] == "retry")
        assert rt.steps_used == retries        # plan execution cost ZERO
