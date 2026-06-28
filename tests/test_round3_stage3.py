"""Round-3 stage-3 offline regression tests.

Pins the stage-3 fixes so `pytest -m "not integration"` stays green and the
batch protocol can rely on them BEFORE any live Minecraft run:

  runner routing / robustness
    - DATAPACK_BIASES derived from each bias's mechanism (not a stale id set)
    - oracle_plan_steps lookup is None-safe (un-backfilled YAML never crashes)
    - discovery combos cover all 9 biases x 3 modes and every bias has a task
    - run_one skips a stale suite bias instead of KeyError-ing

  sanity_check
    - GT_TARGET comes from biases.yaml (C1->nearby_block, C3->time_of_day, and
      R4/R5/R6/C4 present), with a full-predicate GT match

  runtime cost stratification (paper 4.4)
    - _costs(): sim_verifiable -> max(sim_verify_cost, est) (floor) / sim_verify_cost
      (flat); situational -> est_steps
    - the cost_model K7 event is emitted with per-candidate cost/est/sim_verifiable
    - intervention_start carries cost + sim_verifiable

  offline tooling on synthetic data
    - audit_k7.audit_dir flags a cost-stratification mismatch and passes clean data
    - cost_layering_report.build splits resource-input vs situational and reads alpha
"""
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

import Adam.tcpg.runtime as RT                               # noqa: E402
import Adam.tcpg.executor as EX                              # noqa: E402
import Adam.tcpg.predicates as P                             # noqa: E402
from Adam.tcpg.ccg import CCG                                # noqa: E402
from Adam.tcpg.proposer import Candidate                     # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime                    # noqa: E402

sys.path.insert(0, str(REPO / "experiments"))
import runner                                                # noqa: E402
from experiments import evaluate                             # noqa: E402
from experiments import sanity_check                         # noqa: E402
from experiments import audit_k7                             # noqa: E402
from experiments import cost_layering_report as clr          # noqa: E402


# ===================================================== runner routing / robustness
def test_datapack_biases_derived_from_mechanism():
    # datapack_tag (R5, R6) route through the datapack; mod_event biases
    # (R1, R2, R4, C1, C3, C4) route through the mod config. R2 was a
    # datapack_recipe but moved to a mod craft_result gate (the mineflayer agent
    # crafts via bundled vanilla recipes and only honors the mcdrift.json gate,
    # so a datapack recipe was inert for it).
    assert runner.DATAPACK_BIASES == {"R5", "R6"}
    for mod_bias in ("R1", "R2", "R4", "C1", "C3", "C4"):
        assert mod_bias not in runner.DATAPACK_BIASES


def test_runner_can_load_generated_bias_file():
    env = os.environ.copy()
    env["IAP_BIASES_PATH"] = "mc_drift/out/generated/generated_biases.yaml"
    code = (
        "from experiments import runner; "
        "assert {'C0','C1','C2'} <= runner.DATAPACK_BIASES; "
        "assert 'C0' in runner.KNOWN_BIASES; "
        "assert runner._bias_oracle_steps('C0') == 38; "
        "print('ok')"
    )
    res = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                         text=True, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, check=True)
    assert res.stdout.strip() == "ok"


def test_oracle_steps_none_safe():
    # backfilled values are ints; an unknown / un-backfilled id maps to the
    # neutral default rather than crashing the live `None <= 50` budget math.
    assert isinstance(runner._bias_oracle_steps("R6"), int)
    assert runner._bias_oracle_steps("R6") == 331
    assert runner._bias_oracle_steps("NOPE") == 100
    runner._ORACLE_STEPS["__none__"] = None                  # simulate empty YAML
    assert runner._bias_oracle_steps("__none__") == 100
    del runner._ORACLE_STEPS["__none__"]


def test_r2_freedo_oracle_inventory_extra_uses_gt_threshold():
    # R2 is a stockpile gate: natural episodes start with 6 planks, but
    # freedo_oracle's solvability smoke must synthesize the GT I+ state
    # (oak_planks >= 8) without leaking that answer into tcpg discovery.
    assert runner.TASKS["biases"]["R2"]["inventory"]["oak_planks"] == 6
    assert runner._freedo_oracle_inventory_extra("R2") == {"oak_planks": 8}
    assert runner._freedo_oracle_inventory_extra("C1") == {}


def test_r5_freedo_oracle_gt_setup_equips_stone_pickaxe():
    assert "stone_pickaxe" not in runner.TASKS["biases"]["R5"]["inventory"]
    setup = runner._freedo_oracle_gt_setup("R5")
    assert setup["inventory_extra"]["stone_pickaxe"] == 1
    assert setup["equipment"] == ["stone_pickaxe"]
    assert setup["commands"] == []


def test_r5_freedo_oracle_uses_drift_gt_not_vanilla_tool_precond():
    pre = runner._freedo_oracle_gt_preconds("R5")
    assert pre == [{
        "dimension": "capability",
        "target": "held_tool",
        "property": "tier",
        "comparator": ">=",
        "value": "stone",
    }]
    pre6 = runner._freedo_oracle_gt_preconds("R6")
    assert pre6[0]["target"] == "held_tool"
    assert pre6[0]["property"] == "tier"
    assert pre6[0]["value"] == "diamond"


def test_freedo_oracle_gt_candidate_validates_pickaxe_alias():
    c = runner._freedo_oracle_gt_candidate("R5", "gatherCoalOre")
    assert c is not None
    assert c.target == "held_tool"
    assert c.property == "tier"
    assert c.value == "stone"
    assert c.source == "freedo_oracle"


def test_freedo_oracle_gt_setup_materializes_context_predicates():
    c1 = runner._freedo_oracle_gt_setup("C1")
    assert c1["inventory_extra"] == {}
    assert c1["equipment"] == []
    assert c1["commands"] == ["/setblock +3 ~ ~ minecraft:furnace"]

    c3 = runner._freedo_oracle_gt_setup("C3")
    assert c3["inventory_extra"] == {}
    assert c3["equipment"] == []
    assert c3["commands"] == [
        "/time set 18000",
        "/setblock +3 ~ ~ minecraft:furnace",
    ]

    c4 = runner._freedo_oracle_gt_setup("C4")
    assert c4["inventory_extra"] == {}
    assert c4["equipment"] == []
    assert c4["commands"] == []
    assert c4["spot_override"] == {"y": -10}


def test_c3_uses_stable_preplaced_furnace_for_time_gate():
    c3 = runner.TASKS["biases"]["C3"]
    assert "furnace" not in c3["inventory"]
    assert ["+3 ~ ~", "furnace"] in c3["setblocks"]
    assert "/time set 6000" in c3["commands"]


def test_c4_setblocks_follow_agent_for_y_level_interventions():
    c4 = runner.TASKS["biases"]["C4"]
    assert c4["setblocks_follow_agent"] is True
    assert ["+1 ~ ~", "diamond_ore"] in c4["setblocks"]


def test_setup_episode_maps_equipment_to_mainhand(monkeypatch):
    class Env:
        def __init__(self):
            self.reset_options = None
            self.steps = []

        def reset(self, options=None):
            self.reset_options = options
            return []

        def step(self, code):
            self.steps.append(code)
            return []

    calls = []
    monkeypatch.setattr(runner, "chat", lambda *a, **k: calls.append(a[1:]))
    env = Env()
    runner.setup_episode(env, {"inventory": {"stone_pickaxe": 1},
                               "setblocks": []}, [0, 70, 0],
                         equipment=["stone_pickaxe"])
    assert env.reset_options["equipment"] == [
        None, None, None, None, "stone_pickaxe", None
    ]
    arena = "\n".join(calls[0])
    assert "/fill -1 69 -1 4 69 1 minecraft:stone" in arena
    assert "/tp @s 0 70 0" in arena


def test_make_execute_replaces_setblocks_before_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "chat", lambda env, *cmds, **k: calls.extend(cmds))

    class Env:
        def step(self, code):
            return []

    snapshots = [
        {"agent.x": 0, "agent.y": 70, "agent.z": 0,
         "inventory": {"coal": 0}},
        {"inventory": {"coal": 0}},
    ]
    monkeypatch.setattr(P, "state_snapshot", lambda env: snapshots.pop(0))
    import Adam.skill_loader as SL
    monkeypatch.setattr(SL, "skill_loader", lambda action: "await noop(bot);")

    task = {"action": "gatherCoalOre", "goal": "coal",
            "setblocks": [["+2 ~ ~", "coal_ore"]]}
    execute = runner.make_execute(Env(), task, [0, 70, 0])
    assert execute("gatherCoalOre") is False
    assert "/setblock 2 70 0 minecraft:coal_ore" in calls


def test_r5_r6_use_stable_ore_target_path():
    assert runner.TASKS["biases"]["R5"]["setblocks"] == []
    assert runner.TASKS["biases"]["R5"]["ore"] == ["coal_ore", "+2 ~ ~"]
    assert runner.TASKS["biases"]["R6"]["setblocks"] == []
    assert runner.TASKS["biases"]["R6"]["ore"] == ["gold_ore", "+2 ~ ~"]


def test_solvability_smoke_writes_single_episode(monkeypatch, tmp_path):
    class Env:
        pass

    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(runner, "enable_bias", lambda *a, **k: None)
    monkeypatch.setattr(runner, "disable_all", lambda *a, **k: None)
    seen = {}

    def fake_setup(env, task, spot, extra_inv=None, extra_cmds=(),
                   remove_inv=(), equipment=None):
        seen["extra_inv"] = dict(extra_inv or {})
        seen["equipment"] = list(equipment or [])
        seen["extra_cmds"] = list(extra_cmds or [])

    monkeypatch.setattr(runner, "setup_episode", fake_setup)
    monkeypatch.setattr(runner, "make_execute", lambda env, task, spot: (lambda action: True))
    monkeypatch.setattr(P, "state_snapshot", lambda env: {"inventory": {"coal": 1}})

    runner.run_solvability_smoke(Env(), "discovery", "R5", 0)

    out = tmp_path / "discovery" / "R5_solvability_smoke_s0"
    assert out.joinpath("summary.json").exists()
    assert json.loads(out.joinpath("episodes.jsonl").read_text())["natural_success"] is True
    assert seen["extra_inv"]["stone_pickaxe"] == 1
    assert seen["equipment"] == ["stone_pickaxe"]
    assert seen["extra_cmds"] == []


def test_solvability_smoke_passes_gt_context_commands(monkeypatch, tmp_path):
    class Env:
        pass

    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(runner, "enable_bias", lambda *a, **k: None)
    monkeypatch.setattr(runner, "disable_all", lambda *a, **k: None)
    seen = {}

    def fake_setup(env, task, spot, extra_inv=None, extra_cmds=(),
                   remove_inv=(), equipment=None):
        seen["extra_inv"] = dict(extra_inv or {})
        seen["extra_cmds"] = list(extra_cmds or [])

    monkeypatch.setattr(runner, "setup_episode", fake_setup)
    monkeypatch.setattr(runner, "make_execute", lambda env, task, spot: (lambda action: True))
    monkeypatch.setattr(P, "state_snapshot", lambda env: {"inventory": {"iron_pickaxe": 1}})

    runner.run_solvability_smoke(Env(), "discovery", "C1", 0)

    out = tmp_path / "discovery" / "C1_solvability_smoke_s0"
    summary = json.loads(out.joinpath("summary.json").read_text())
    assert summary["natural_success"] is True
    assert summary["gt_setup"]["commands"] == ["/setblock +3 ~ ~ minecraft:furnace"]
    assert seen["extra_inv"] == {}
    assert seen["extra_cmds"] == ["/setblock +3 ~ ~ minecraft:furnace"]


def test_solvability_smoke_applies_gt_y_spot_override(monkeypatch, tmp_path):
    class Env:
        pass

    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(runner, "enable_bias", lambda *a, **k: None)
    monkeypatch.setattr(runner, "disable_all", lambda *a, **k: None)
    seen = {}

    def fake_setup(env, task, spot, extra_inv=None, extra_cmds=(),
                   remove_inv=(), equipment=None):
        seen["spot"] = list(spot)

    monkeypatch.setattr(runner, "setup_episode", fake_setup)
    monkeypatch.setattr(runner, "make_execute", lambda env, task, spot: (lambda action: True))
    monkeypatch.setattr(P, "state_snapshot", lambda env: {"inventory": {"diamond": 1}})

    runner.run_solvability_smoke(Env(), "discovery", "C4", 0)

    assert seen["spot"] == [0, -10, 0]


def test_natural_smoke_writes_unmodified_episode(monkeypatch, tmp_path):
    class Env:
        pass

    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(runner, "enable_bias", lambda *a, **k: None)
    monkeypatch.setattr(runner, "disable_all", lambda *a, **k: None)
    seen = {}

    def fake_setup(env, task, spot, extra_inv=None, extra_cmds=(),
                   remove_inv=(), equipment=None):
        seen["extra_inv"] = dict(extra_inv or {})
        seen["extra_cmds"] = list(extra_cmds or [])
        seen["equipment"] = equipment

    monkeypatch.setattr(runner, "setup_episode", fake_setup)
    monkeypatch.setattr(runner, "make_execute", lambda env, task, spot: (lambda action: False))
    monkeypatch.setattr(P, "state_snapshot", lambda env: {"inventory": {}})

    runner.run_natural_smoke(Env(), "discovery", "C1", 0)

    out = tmp_path / "discovery" / "C1_natural_smoke_s0"
    summary = json.loads(out.joinpath("summary.json").read_text())
    assert summary["mode"] == "natural_smoke"
    assert summary["natural_success"] is False
    assert json.loads(out.joinpath("k7.jsonl").read_text())["type"] == "natural_smoke"
    assert seen == {"extra_inv": {}, "extra_cmds": [], "equipment": None}


def test_discovery_combos_cover_all_biases():
    kws = list(runner.combos("discovery", scripted=True))
    assert len(kws) == 27                                    # 9 biases x 3 modes x 1 seed
    biases = {k["bias_id"] for k in kws}
    modes = {k["mode"] for k in kws}
    assert biases == {"R1", "R2", "R4", "R5", "R6", "C1", "C2", "C3", "C4"}
    assert modes == {"tcpg", "freedo_oracle", "llm_writeback"}
    # every discovery bias must have a tasks.yaml entry (no stale routing)
    for b in biases:
        assert b in runner.TASKS["biases"]


def test_run_one_skips_stale_bias():
    # a suite case referencing a bias with no tasks.yaml entry must skip cleanly
    # (the round-2 code KeyError-ed). env=None proves it returns before any env use.
    assert "Z9" not in runner.TASKS["biases"]
    assert runner.run_one(None, "discovery", "Z9", "tcpg", 0) is None


# ============================================================= sanity_check GT map
def test_sanity_gt_target_from_yaml():
    gt = sanity_check.GT_TARGET
    assert gt["C1"] == "nearby_block"          # round-2 wrongly had held_tool
    assert gt["C3"] == "time_of_day"           # round-2 wrongly had held_tool
    assert gt["C4"] == "y_level"
    assert gt["R5"] == "held_tool" and gt["R6"] == "held_tool"
    assert gt["R1"] == "inventory_count"
    assert gt["C2"] == "nearby_block"
    assert set(gt) == {"R1", "R2", "R4", "R5", "R6", "C1", "C2", "C3", "C4"}


def test_sanity_gt_predicate_match_distinguishes_value():
    # R6 true cause is held_tool>=diamond; the vanilla held_tool>=iron proposal
    # must NOT count as the GT (this is the neighbor-expansion case in paper 6.6).
    assert sanity_check._gt_hit(
        "R6", {"target": "held_tool", "property": "pickaxe",
               "comparator": ">=", "value": "diamond"})
    assert not sanity_check._gt_hit(
        "R6", {"target": "held_tool", "property": "pickaxe",
               "comparator": ">=", "value": "iron"})


def test_held_tool_tier_property_matches_pickaxe_gt():
    cand = Candidate("gatherCoalOre", "capability", "held_tool",
                     "tier", ">=", "stone")
    cand.status = "accepted"
    assert runner._gt_accepted("R5", type("RT", (), {"cands": {
        "c": cand
    }})())
    assert evaluate.is_gt(
        "R5", {"target": "held_tool", "property": "tier",
               "comparator": ">=", "value": "stone"})


# ============================================================ runtime _costs() unit
def _bare_rt(cfg=None):
    return TcpgRuntime(None, ccg=CCG.init_default(), mode="tcpg",
                       execute_action=lambda a: False, llm=lambda p: "[]",
                       config=cfg or {})


def test_costs_floor_logic():
    sim = Candidate("craftFence", "resource", "inventory_count",
                    "oak_planks", ">=", 8)
    real = Candidate("mineDiamondOre", "context", "y_level", "y", "<=", -10)
    rt = _bare_rt({"sim_verify_cost": 2.0, "sim_cost_mode": "floor"})
    rt.cands = {sim.cid: sim, real.cid: real}
    rt.k5 = {sim.cid: {"sim_verifiable": True, "est_steps": 50.0, "feasible": True},
             real.cid: {"sim_verifiable": False, "est_steps": 80.0, "feasible": True}}

    costs = rt._costs()
    assert costs[sim.cid] == 50.0              # floor: max(2, 50) -- must-craft sim
    assert costs[real.cid] == 80.0             # situational keeps full est_steps

    rt.k5[sim.cid]["est_steps"] = 1.0          # item on hand -> tiny est
    assert rt._costs()[sim.cid] == 2.0         # floor: max(2, 1) = sim_verify_cost

    rt.cfg["sim_cost_mode"] = "flat"
    rt.k5[sim.cid]["est_steps"] = 50.0
    assert rt._costs()[sim.cid] == 2.0         # flat ignores est entirely
    assert rt._costs()[real.cid] == 80.0       # situational unaffected by mode


# ===================================================== runtime cost_model emission
class _World:
    def __init__(self):
        self.inventory = {"iron_pickaxe": 1, "chest": 1}
        self.held = "iron_pickaxe"
        self.y = -6.0

    def snapshot(self):
        return {"agent.x": 0.0, "agent.y": self.y, "agent.z": 0.0,
                "world.time_of_day": 6000, "sky_exposed": True,
                "held.name": self.held, "held.tier": 3,
                "block_below.name": "stone", "inventory": dict(self.inventory)}


class _Env:
    def __init__(self, world):
        self.world = world

    def reset(self, options=None):
        if options and "equipment" in options:
            self.world.held = options["equipment"][0]
        if options and "inventory" in options:
            self.world.inventory = dict(options["inventory"])
        return []

    def step(self, code):
        return [[0, {"inventory": self.world.inventory}]]


def _llm_stone(prompt):
    return json.dumps([{"dimension": "capability", "target": "held_tool",
                        "property": "tier", "comparator": ">=", "value": "stone"}])


def _run_with_capture(monkeypatch, cfg=None):
    world = _World()
    env = _Env(world)
    monkeypatch.setattr(P, "state_snapshot",
                        lambda e, timeout=60: world.snapshot())
    monkeypatch.setattr(P, "eval_predicates",
                        lambda e, preds, timeout=60: {
                            p["id"]: {"id": p["id"], "known": True, "raw": 0,
                                      "value": 0, "error": None} for p in preds})
    monkeypatch.setattr(EX, "run_plan", lambda e, plan, **k: (True, []))
    monkeypatch.setattr(RT, "run_plan", lambda e, plan, **k: (True, []),
                        raising=False)
    captured = []
    orig = RT.log_event

    def cap(ev, payload, trial="-", step=-1):
        captured.append((ev, payload))
        return orig(ev, payload, trial, step)
    monkeypatch.setattr(RT, "log_event", cap)
    monkeypatch.setattr(TcpgRuntime, "_ctx_matches",
                        lambda self, snap, cand=None: True)
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=lambda a: False, llm=_llm_stone,
                     config=cfg or {"M": 20_000, "step_budget": 400,
                                    "max_interventions_per_event": 4,
                                    "min_verifications_per_cand": 1})
    rt._captured = captured
    rt.on_action("mineGoldOre", False, dict(world.inventory),
                 {"inventory": dict(world.inventory)})
    return rt, captured


def test_cost_model_event_emitted(monkeypatch):
    _rt, captured = _run_with_capture(monkeypatch)
    cms = [p for e, p in captured if e == "cost_model"]
    assert cms, "no cost_model event emitted"
    p = cms[0]
    assert "alpha" in p and "sim_cost_mode" in p and "sim_verify_cost" in p
    assert p["costs"], "cost_model has no per-candidate costs"
    for cid, cd in p["costs"].items():
        assert set(("cost", "est_steps", "sim_verifiable")) <= set(cd)
    # the held_tool candidate is a resource-input (sim_verifiable) candidate
    assert any(cd["sim_verifiable"] for cd in p["costs"].values())


def test_intervention_start_carries_cost(monkeypatch):
    _rt, captured = _run_with_capture(monkeypatch)
    ivs = [p for e, p in captured if e == "intervention_start"]
    assert ivs, "no intervention_start event emitted"
    for p in ivs:
        assert "cost" in p and "sim_verifiable" in p
        assert isinstance(p["cost"], (int, float))


# ===================================================== audit_k7 on synthetic data
def _write_run(tmp_path, k7_records, summary):
    d = tmp_path / "discovery" / summary["run_id"]
    d.mkdir(parents=True)
    (d / "k7.jsonl").write_text(
        "\n".join(json.dumps(r) for r in k7_records) + "\n")
    (d / "summary.json").write_text(json.dumps(summary))
    return d


def _cost_model_rec(costs, alpha=0.5, mode="floor"):
    return {"ts": 0, "trial_id": "t", "step": 1, "type": "cost_model",
            "payload": {"action": "a", "alpha": alpha, "c0": 1.0,
                        "sim_verify_cost": 2.0, "sim_cost_mode": mode,
                        "trigger_budget": 10.0, "costs": costs}}


def test_audit_k7_clean_passes(tmp_path):
    costs = {"cidA": {"cost": 50.0, "est_steps": 50.0, "sim_verifiable": True},
             "cidB": {"cost": 80.0, "est_steps": 80.0, "sim_verifiable": False}}
    k7 = [_cost_model_rec(costs),
          {"ts": 0, "trial_id": "t", "step": 2, "type": "intervention_start",
           "payload": {"cid": "cidA", "side": "neg", "plan_len": 3,
                       "cost": 50.0, "sim_verifiable": True}}]
    summary = {"run_id": "R6_tcpg_minimal_s0", "bias": "R6", "mode": "tcpg",
               "suite": "discovery", "sim_cost_mode": "floor", "candidates": []}
    d = _write_run(tmp_path, k7, summary)
    rep = audit_k7.audit_dir(d)
    assert rep["cost_violations"] == []
    assert rep["mean_cost_resource_input"] == 50.0
    assert rep["mean_cost_situational"] == 80.0


def test_audit_k7_detects_cost_violation(tmp_path):
    # cidA is sim_verifiable with est=50 in floor mode -> expected max(2,50)=50,
    # but the logged cost is 2.0 (the round-2 flat-cost bug). Must be flagged.
    costs = {"cidA": {"cost": 2.0, "est_steps": 50.0, "sim_verifiable": True}}
    k7 = [_cost_model_rec(costs)]
    summary = {"run_id": "R6_tcpg_minimal_s1", "bias": "R6", "mode": "tcpg",
               "suite": "discovery", "sim_cost_mode": "floor", "candidates": []}
    d = _write_run(tmp_path, k7, summary)
    rep = audit_k7.audit_dir(d)
    assert rep["cost_violations"], "floor-cost mismatch not detected"
    assert rep["cost_violations"][0]["expected"] == 50.0


def test_audit_k7_gt_q_hat_trajectory(tmp_path):
    gt = {"target": "held_tool", "property": "pickaxe", "comparator": ">=",
          "value": "diamond", "status": "accepted", "cid": "gt9",
          "n_pos": 4, "n_neg": 3}
    costs = {"gt9": {"cost": 2.0, "est_steps": 2.0, "sim_verifiable": True}}
    k7 = [_cost_model_rec(costs),
          {"ts": 0, "trial_id": "t", "step": 2, "type": "posterior_update",
           "payload": {"cid": "gt9", "q_hat": 0.56, "gamma_plus": 0.3, "pools": {}}},
          {"ts": 0, "trial_id": "t", "step": 4, "type": "posterior_update",
           "payload": {"cid": "gt9", "q_hat": 0.91, "gamma_plus": 0.5, "pools": {}}}]
    summary = {"run_id": "R6_tcpg_minimal_s2", "bias": "R6", "mode": "tcpg",
               "suite": "discovery", "sim_cost_mode": "floor", "candidates": [gt]}
    d = _write_run(tmp_path, k7, summary)
    rep = audit_k7.audit_dir(d)
    assert rep["gt_cid"] == "gt9"
    assert rep["gt_q_hat_trajectory"] == [0.56, 0.91]
    assert rep["gt_q_hat_final"] == 0.91


# ============================================== cost_layering_report on synthetic
def _disc_run(tmp_path, run_id, bias, gt_cand, alpha=0.5, accept_step=3):
    k7 = [_cost_model_rec({gt_cand["cid"]: {
              "cost": 2.0, "est_steps": 2.0,
              "sim_verifiable": clr.bias_class(bias) == "resource_input"}},
              alpha=alpha),
          {"ts": 0, "trial_id": "t", "step": 2, "type": "intervention_start",
           "payload": {"cid": gt_cand["cid"], "side": "neg", "plan_len": 2,
                       "cost": 2.0, "sim_verifiable": True}},
          {"ts": 0, "trial_id": "t", "step": accept_step, "type": "writeback",
           "payload": {"cid": gt_cand["cid"], "decision": "accepted",
                       "action": bias, "target": gt_cand["target"]}}]
    summary = {"run_id": run_id, "bias": bias, "mode": "tcpg",
               "suite": "discovery", "cost_alpha": alpha,
               "steps_used": 40, "candidates": [gt_cand]}
    return _write_run(tmp_path, k7, summary)


def test_cost_layering_report_aggregates(tmp_path):
    r6_gt = {"target": "held_tool", "property": "pickaxe", "comparator": ">=",
             "value": "diamond", "status": "accepted", "cid": "r6gt",
             "n_pos": 4, "n_neg": 3}
    c4_gt = {"target": "y_level", "property": "y", "comparator": "<=",
             "value": -10, "status": "accepted", "cid": "c4gt",
             "n_pos": 3, "n_neg": 2}
    _disc_run(tmp_path, "R6_tcpg_minimal_s0", "R6", r6_gt, alpha=0.5)
    _disc_run(tmp_path, "C4_tcpg_minimal_s0", "C4", c4_gt, alpha=0.5)

    res = clr.build(tmp_path)
    classes = {row["class"]: row for row in res["by_class"]}
    assert "resource_input" in classes and "situational_constraint" in classes
    # both GTs accepted -> recall 1.0, precision 1.0 (only the GT was accepted)
    assert classes["resource_input"]["recall"] == 1.0
    assert classes["resource_input"]["precision"] == 1.0
    assert classes["resource_input"]["mean_neff"] == 3.0      # min(4,3)
    assert classes["situational_constraint"]["mean_neff"] == 2.0  # min(3,2)
    # ablation rows carry the alpha read from the cost_model event
    alphas = {row["alpha"] for row in res["ablation"]}
    assert alphas == {0.5}
    for row in res["ablation"]:
        assert row["gt_accepted"] == 1.0


def test_cost_layering_alpha_from_cost_model(tmp_path):
    # alpha must come from the cost_model event even when summary disagrees
    gt = {"target": "y_level", "property": "y", "comparator": "<=",
          "value": -10, "status": "accepted", "cid": "yy", "n_pos": 2, "n_neg": 2}
    d = _disc_run(tmp_path, "C4_tcpg_minimal_s9", "C4", gt, alpha=0.0)
    res = clr.build(tmp_path)
    assert {row["alpha"] for row in res["ablation"]} == {0.0}


# ===================================================== seeds + ablation toggles
def test_seeds_override_changes_combo_count():
    # --seeds 0,1,2 mutates TASKS["defaults"]["seeds"]; combos must expand to
    # 9 biases x 3 modes x 3 seeds (manual sec.2 step 3 three-mode comparison).
    orig = runner.TASKS["defaults"]["seeds"]
    try:
        runner.TASKS["defaults"]["seeds"] = [0, 1, 2]
        kws = list(runner.combos("discovery", scripted=True))
        assert len(kws) == 81
        assert {k["seed"] for k in kws} == {0, 1, 2}
    finally:
        runner.TASKS["defaults"]["seeds"] = orig


def test_necessity_toggle_drops_success_branch(monkeypatch):
    called = []
    monkeypatch.setattr(RT, "candidates_from_success",
                        lambda *a, **k: (called.append(1), [object()])[1])
    off = _bare_rt({"necessity_test": False})
    assert off._generate_candidates("a", True, {}, None) == []
    assert not called                                   # generator NOT called
    on = _bare_rt({"necessity_test": True})
    on._generate_candidates("a", True, {}, None)
    assert called                                       # generator called


def test_freedo_oracle_success_branch_uses_oracle_preconds(monkeypatch):
    seen = {}

    def fake_success(action, preconds, **kwargs):
        seen["action"] = action
        seen["preconds"] = list(preconds)
        return []

    monkeypatch.setattr(RT, "candidates_from_success", fake_success)
    oracle = [{"dimension": "capability", "target": "held_tool",
               "property": "tier", "comparator": ">=", "value": "stone"}]
    rt = _bare_rt({"oracle_preconds": oracle})
    rt.mode = "freedo_oracle"
    rt._generate_candidates("gatherCoalOre", True, {}, None)
    assert seen == {"action": "gatherCoalOre", "preconds": oracle}

    tcpg = _bare_rt({"oracle_preconds": oracle})
    tcpg._generate_candidates("gatherCoalOre", True, {}, None)
    assert seen["preconds"] != oracle


def test_neighbor_toggle_passes_expand_flag(monkeypatch):
    seen = {}

    def fake_propose(event, llm=None, expand=True, trial_id="-", step=-1):
        seen["expand"] = expand
        return []
    monkeypatch.setattr(RT, "propose_from_failure", fake_propose)
    _bare_rt({"neighbor_expand": False})._generate_candidates("a", False, {}, None)
    assert seen["expand"] is False
    _bare_rt({"neighbor_expand": True})._generate_candidates("a", False, {}, None)
    assert seen["expand"] is True


def test_proposal_failure_signature_fallback_when_llm_returns_empty(monkeypatch):
    monkeypatch.setattr(RT, "propose_from_failure", lambda *a, **k: [])
    rt = _bare_rt({"proposal_failure_signature_fallback": True})

    cands = rt._generate_candidates(
        "mineGoldOre", False, {},
        {"position": {"x": 0, "y": -16, "z": 0}, "block_below": "stone"})

    assert cands
    assert {c.source for c in cands} == {"signature_fallback"}
    assert {"held_tool", "y_level", "time_of_day", "sky_exposed", "block_below"} & {
        c.target for c in cands
    }


def test_proposal_failure_signature_fallback_can_be_disabled(monkeypatch):
    monkeypatch.setattr(RT, "propose_from_failure", lambda *a, **k: [])
    rt = _bare_rt({"proposal_failure_signature_fallback": False})

    assert rt._generate_candidates(
        "mineGoldOre", False, {},
        {"position": {"x": 0, "y": -16, "z": 0}, "block_below": "stone"}) == []


def test_candidate_records_sync_pool_stats():
    # candidate_records() must merge DualPool n_pos/n_neg into the candidate dict
    # (the bare dataclass reports 0, which silently zeroed mean_neff).
    from Adam.tcpg.posterior import DualPool
    from Adam.tcpg.proposer import Candidate
    rt = _bare_rt({})
    c = Candidate("a", "resource", "inventory_count", "sand", ">=", 1)
    pool = DualPool()
    for _ in range(3):
        pool.update("pos", 1)
    for _ in range(2):
        pool.update("neg", 0)
    rt.cands = {c.cid: c}
    rt.pools = {c.cid: pool}
    rec = rt.candidate_records()[0]
    assert rec["n_pos"] == 3 and rec["n_neg"] == 2
    assert rec["k_pos"] == 3 and rec["k_neg"] == 0
