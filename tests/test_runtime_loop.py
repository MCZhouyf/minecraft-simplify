"""Stage 6 offline tests: the verification closed loop end-to-end with a
MOCKED environment (C2-style world: gold mining succeeds iff diamond pick).
No Minecraft, no LLM key."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import Adam.tcpg.runtime as RT                          # noqa: E402
from Adam.tcpg.ccg import CCG                           # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime               # noqa: E402


class MockWorld:
    """Inventory/held simulator with a hidden C2 mechanism."""
    def __init__(self):
        self.inventory = {"iron_pickaxe": 1, "crafting_table": 1,
                          "stick": 2, "coal": 4}
        self.held = "iron_pickaxe"
        self.y = -20.0

    def execute_action(self, action):
        if action == "mineGoldOre":
            ok = self.held == "diamond_pickaxe"          # hidden gate (C2)
            if ok:
                self.inventory["raw_gold"] = self.inventory.get("raw_gold", 0) + 1
            return ok
        return True

    def snapshot(self):
        return {"agent.y": self.y, "held.name": self.held,
                "held.tier": 4 if self.held == "diamond_pickaxe" else 3,
                "world.time_of_day": 6000, "world.is_raining": False,
                "block_below.name": "stone", "sky_exposed": False,
                "inventory": dict(self.inventory)}


class MockEnv:
    def __init__(self, world):
        self.world = world
        self.reset_calls = 0

    def reset(self, options=None):
        self.reset_calls += 1
        if options and "inventory" in options:
            self.world.inventory = dict(options["inventory"])
        return []


@pytest.fixture()
def rig(monkeypatch):
    world = MockWorld()
    env = MockEnv(world)

    def fake_run_plan(env_, plan, cid="-", trial_id="-", step=-1):
        for call in plan:
            p, a = call["primitive"], call["args"]
            if p == "equip":
                world.held = a["name"]
            elif p == "useChest":
                sgn = -1 if a["op"] == "deposit" else 1
                for it in a["items"]:
                    world.inventory[it["name"]] = max(
                        0, world.inventory.get(it["name"], 0) + sgn * it["count"])
            elif p in ("mineBlock", "craftItem", "smeltItem") and not a.get("special"):
                world.inventory[a["name"]] = world.inventory.get(a["name"], 0) \
                    + a.get("count", 1)
            elif p == "placeItem":
                world.inventory[a["name"]] = max(0, world.inventory.get(a["name"], 0) - 1)
            elif p == "moveTo" and "y" in a:
                world.y = float(a["y"])
        return True, []

    import Adam.tcpg.executor as EX
    monkeypatch.setattr(EX, "run_plan", fake_run_plan)
    monkeypatch.setattr(RT, "run_plan", fake_run_plan, raising=False)

    import Adam.tcpg.predicates as P

    def fake_eval(env_, preds, timeout=60):
        s = world.snapshot()
        out = {}
        for p in preds:
            if p["target"] == "held_tool":
                tiers = ["wooden", "golden", "stone", "iron", "diamond", "netherite"]
                cur = s["held.tier"]
                out[p["id"]] = {"id": p["id"], "known": True, "raw": world.held,
                                "value": int(cur >= tiers.index(p["value"])),
                                "error": None}
            elif p["target"] == "inventory_count":
                cur = s["inventory"].get(p["property"], 0)
                out[p["id"]] = {"id": p["id"], "known": True, "raw": cur,
                                "value": int(cur >= int(p["value"])), "error": None}
            elif p["target"] == "y_level":
                out[p["id"]] = {"id": p["id"], "known": True, "raw": s["agent.y"],
                                "value": int(s["agent.y"] <= float(p["value"])),
                                "error": None}
            else:
                out[p["id"]] = {"id": p["id"], "known": False, "raw": None,
                                "value": None, "error": "mock"}
        return out

    monkeypatch.setattr(P, "eval_predicates", fake_eval)
    monkeypatch.setattr(P, "state_snapshot", lambda env_, timeout=60: world.snapshot())
    return world, env


def c2_llm(prompt):
    return json.dumps([
        {"dimension": "capability", "target": "held_tool", "property": "tier",
         "comparator": ">=", "value": "diamond"},
        {"dimension": "context", "target": "y_level", "property": "y",
         "comparator": "<=", "value": -16},
    ])


def make_rt(world, env, mode="tcpg", **cfg):
    return TcpgRuntime(env, ccg=CCG.init_default(), mode=mode,
                       execute_action=world.execute_action, llm=c2_llm,
                       config={"M": 30_000, "step_budget": 300,
                               "max_interventions_per_event": 8, **cfg})


def test_off_mode_is_inert(rig):
    world, env = rig
    rt = make_rt(world, env, mode="off")
    rt.on_action("mineGoldOre", False, world.inventory, None)
    assert not rt.cands and rt.steps_used == 0


def test_full_loop_discovers_c2_and_rejects_confound(rig):
    """Failure -> two candidates -> interventions separate the true gate
    (tier>=diamond accepted) from the confound (y<=-16: do(h=1) still fails
    with iron pick -> rejected). The accepted gate lands in the CCG and
    plan_from_graph immediately consumes it."""
    world, env = rig
    rt = make_rt(world, env)
    for _ in range(12):                     # expansion adds siblings -> more to verify
        world.held = "iron_pickaxe"
        rt.on_action("mineGoldOre", False, world.inventory,
                     {"inventory": dict(world.inventory)})
        if any(c.target == "held_tool" and str(c.value) == "diamond"
               and c.status == "accepted" for c in rt.cands.values()):
            break
    by_kv = {(c.target, str(c.value)): c for c in rt.cands.values()}
    assert by_kv[("held_tool", "diamond")].status == "accepted"
    assert by_kv[("held_tool", "iron")].status in ("rejected", "undecided")
    assert by_kv[("y_level", "-16")].status in ("rejected", "undecided")  # never accepted
    assert any(c["target"] == "held_tool" and str(c["value"]) == "diamond"
               for c in rt.ccg.conditions.values())
    plan = rt.ccg.plan_from_graph("raw_gold", {"diamond_pickaxe": 1})
    assert plan == ["mineGoldOre"]          # gate satisfied by inventory tool
    plan2 = rt.ccg.plan_from_graph("raw_gold", {"iron_pickaxe": 1, "stick": 2,
                                                "crafting_table": 1, "coal": 4})
    assert plan2 and "craftDiamondPickaxe" in plan2     # gate folded into demand


def test_llm_writeback_mode_writes_unverified(rig):
    world, env = rig
    rt = make_rt(world, env, mode="llm_writeback")
    rt.on_action("mineGoldOre", False, world.inventory,
                 {"inventory": dict(world.inventory)})
    targets = {c["target"] for c in rt.ccg.conditions.values()}
    assert targets == {"held_tool", "y_level"}          # confound written too
    assert rt.steps_used == 0                           # zero verification


def test_freedo_oracle_counts_zero_steps(rig):
    world, env = rig
    rt = make_rt(world, env, mode="freedo_oracle")
    world.held = "iron_pickaxe"
    rt.on_action("mineGoldOre", False, world.inventory,
                 {"inventory": dict(world.inventory)})
    retries = sum(1 for c in rt.cands.values()
                  for _ in range(1)) and rt.steps_used
    # only retry executions are counted; plan execution cost 0
    assert env.reset_calls > 0
    assert rt.steps_used <= rt.acq._picks               # = number of retries


def test_budget_is_respected(rig):
    world, env = rig
    rt = make_rt(world, env, step_budget=5)
    rt.on_action("mineGoldOre", False, world.inventory,
                 {"inventory": dict(world.inventory)})
    assert rt.steps_used <= 5


def test_successful_retry_resets_anchor_and_keeps_observation(rig):
    world, env = rig
    rt = make_rt(world, env, max_interventions_per_event=1)
    c = __import__("Adam.tcpg.proposer", fromlist=["Candidate"]).Candidate(
        "mineGoldOre", "capability", "held_tool", "tier", ">=", "diamond")
    rt.cands[c.cid] = c
    rt.pools[c.cid] = RT.DualPool()
    rt.k5[c.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "equip", "args": {"name": "diamond_pickaxe"}}],
        "plan_minus": [],
        "undo_plus": [],
        "undo_minus": [],
        "irreversible": False,
        "sim_verifiable": False,
        "est_steps": 1,
    }
    world.inventory["diamond_pickaxe"] = 1
    anchor = world.snapshot()

    rt._verification_loop("mineGoldOre", world.inventory, anchor=anchor)

    assert rt.pools[c.cid].k_pos == 1
    assert env.reset_calls >= 1


def test_success_branch_generates_necessity_candidates(rig):
    world, env = rig
    rt = make_rt(world, env)
    world.held = "diamond_pickaxe"
    world.inventory["diamond_pickaxe"] = 1
    rt.on_action("mineGoldOre", True, world.inventory, None)
    srcs = {c.source for c in rt.cands.values()}
    assert srcs <= {"success_precondition"} and rt.cands  # e_in-derived, no LLM


def test_early_low_signal_nota_fires_on_tested_zero_success_candidates(rig):
    world, env = rig
    rt = make_rt(world, env, nota_reproposal=True)
    Cand = __import__("Adam.tcpg.proposer", fromlist=["Candidate"]).Candidate
    for i in range(4):
        c = Cand("mineGoldOre", "resource", "inventory_count",
                 f"dummy_{i}", ">=", 1)
        rt.cands[c.cid] = c
        p = RT.DualPool()
        p.n_pos = 1
        p.k_pos = 0
        p.n_neg = 1
        p.k_neg = 0
        rt.pools[c.cid] = p

    sig = rt._early_low_signal_nota("mineGoldOre")

    assert sig is not None
    assert sig["reason"] == "early_low_signal_none_accepted"
    assert len(sig["rejected_cids"]) == 4


def test_early_low_signal_nota_silent_after_positive_evidence(rig):
    world, env = rig
    rt = make_rt(world, env, nota_reproposal=True)
    Cand = __import__("Adam.tcpg.proposer", fromlist=["Candidate"]).Candidate
    for i in range(4):
        c = Cand("mineGoldOre", "resource", "inventory_count",
                 f"dummy_{i}", ">=", 1)
        rt.cands[c.cid] = c
        p = RT.DualPool()
        p.n_pos = 1
        p.k_pos = 1 if i == 0 else 0
        p.n_neg = 1
        p.k_neg = 0
        rt.pools[c.cid] = p

    assert rt._early_low_signal_nota("mineGoldOre") is None


def test_reproposal_candidate_gets_floor_priority(rig):
    world, env = rig
    rt = make_rt(world, env, max_interventions_per_event=1,
                 min_verifications_per_cand=2)
    Cand = __import__("Adam.tcpg.proposer", fromlist=["Candidate"]).Candidate
    old = Cand("mineGoldOre", "resource", "inventory_count",
               "oak_planks", ">=", 16, source="frontier")
    new = Cand("mineGoldOre", "context", "nearby_block",
               "water", "<=k", 3)
    for c in (old, new):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = RT.DualPool()
        rt.k5[c.cid] = {
            "feasible": True,
            "plan_plus": [],
            "plan_minus": [],
            "undo_plus": [],
            "undo_minus": [],
            "irreversible": False,
            "sim_verifiable": c.target == "inventory_count",
            "est_steps": 2,
        }
    rt.pools[new.cid].n_neg = 3
    rt._reproposal_cids.add(new.cid)

    rt._verification_loop("mineGoldOre", world.inventory, anchor=world.snapshot())

    assert rt.pools[new.cid].n_pos == 1
    assert rt.pools[old.cid].n_pos == 0
