"""Transaction-safety test: when an intervention's undo (or forward plan)
fails, the trigger ABORTS and resets to the episode anchor — it must NOT keep
verifying the next candidate on a polluted context. This prevents the X1-style
failure where a failed chest/equip undo drains inventory/held and pollutes the
subsequent y_level verification."""
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

import Adam.tcpg.runtime as RT                            # noqa: E402
from Adam.tcpg.ccg import CCG                             # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime                 # noqa: E402


class World:
    def __init__(self):
        self.inventory = {"iron_pickaxe": 1, "chest": 1}
        self.held = "iron_pickaxe"
        self.x, self.y, self.z = 0.0, -6.0, 0.0

    def execute_action(self, a):
        return False                              # always fails -> triggers proposer

    def snapshot(self):
        return {"agent.x": self.x, "agent.y": self.y, "agent.z": self.z,
                "world.time_of_day": 6000, "sky_exposed": True,
                "held.name": self.held, "held.tier": 3,
                "block_below.name": "stone",
                "inventory": dict(self.inventory)}


class Env:
    def __init__(self, world):
        self.world = world
        self.reset_calls = 0
        self.reset_anchors = []
        self.step_codes = []

    def reset(self, options=None):
        self.reset_calls += 1
        if options:
            self.reset_anchors.append(options)
            if "inventory" in options:
                self.world.inventory = dict(options["inventory"])
            if "position" in options:
                self.world.y = options["position"]["y"]
            if "equipment" in options:
                self.world.held = options["equipment"][0]
        return []

    def step(self, code):
        self.step_codes.append(code)
        return []


def x1_llm(prompt):
    # propose y_level (true cause) + an inventory_count candidate whose undo
    # we will sabotage to pollute state
    return json.dumps([
        {"dimension": "context", "target": "y_level", "property": "y",
         "comparator": "<=", "value": -10},
        {"dimension": "resource", "target": "inventory_count",
         "property": "coal", "comparator": ">=", "value": 1},
    ])


@pytest.fixture()
def rig(monkeypatch):
    world = World()
    env = Env(world)

    def sabotaged_run_plan(env_, plan, cid="-", trial_id="-", step=-1):
        # forward plans mutate state; the UNDO plan (deposit back / equip) FAILS
        # midway, leaving residue — simulate by draining held on undo.
        is_undo = any(c["primitive"] == "useChest" and c["args"].get("op") == "withdraw"
                      for c in plan) or any(c["primitive"] == "equip" for c in plan)
        for call in plan:
            p, a = call["primitive"], call["args"]
            if p == "useChest":
                sgn = -1 if a["args"].get("op") == "deposit" else 1 \
                    if isinstance(a.get("args"), dict) else (
                        -1 if a.get("op") == "deposit" else 1)
                for it in a.get("items", []):
                    world.inventory[it["name"]] = max(
                        0, world.inventory.get(it["name"], 0) + sgn * it["count"])
            elif p == "placeItem":
                world.inventory[a["name"]] = max(0, world.inventory.get(a["name"], 0) - 1)
            elif p == "moveTo" and "y" in a:
                world.y = float(a["y"])
        if is_undo:
            world.held = None                    # undo failed -> pollution
            return False, []
        return True, []

    import Adam.tcpg.executor as EX
    monkeypatch.setattr(EX, "run_plan", sabotaged_run_plan)
    monkeypatch.setattr(RT, "run_plan", sabotaged_run_plan, raising=False)
    import Adam.tcpg.predicates as P
    monkeypatch.setattr(P, "state_snapshot",
                        lambda env_, timeout=60: world.snapshot())

    def fake_eval(env_, preds, timeout=60):
        s = world.snapshot()
        out = {}
        for p in preds:
            if p["target"] == "y_level":
                out[p["id"]] = {"id": p["id"], "known": True, "raw": s["agent.y"],
                                "value": int(s["agent.y"] <= float(p["value"])),
                                "error": None}
            elif p["target"] == "inventory_count":
                cur = s["inventory"].get(p["property"], 0)
                out[p["id"]] = {"id": p["id"], "known": True, "raw": cur,
                                "value": int(cur >= int(p["value"])), "error": None}
            else:
                out[p["id"]] = {"id": p["id"], "known": False, "raw": None,
                                "value": None, "error": "mock"}
        return out

    monkeypatch.setattr(P, "eval_predicates", fake_eval)
    return world, env


def test_undo_failure_aborts_and_resets_to_anchor(rig):
    world, env = rig
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=world.execute_action, llm=x1_llm,
                     config={"M": 20_000, "step_budget": 300,
                             "max_interventions_per_event": 8,
                             "neighbor_expand": False})
    # one failed action triggers the verification loop
    rt.on_action("mineDiamondOre", False, dict(world.inventory),
                 {"inventory": dict(world.inventory)})
    # after an undo failure the loop must have reset to anchor (held restored,
    # not left at None), and emitted a trigger_abort
    assert env.reset_calls >= 1, "should reset to anchor on undo failure"
    last = env.reset_anchors[-1]
    assert last.get("equipment") == ["iron_pickaxe"], "anchor held restored"
    assert last.get("inventory", {}).get("iron_pickaxe") == 1, "anchor inv restored"
    # held must not be left polluted (None) after the trigger ends
    assert world.held == "iron_pickaxe", "context restored, not polluted"


def test_reset_to_anchor_helper_restores_fields():
    world = World()
    env = Env(world)
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=world.execute_action, llm=x1_llm)
    world.held = None
    world.inventory = {}
    world.y = -50.0
    anchor = {"agent.x": 0.0, "agent.y": -6.0, "agent.z": 0.0,
              "held.name": "iron_pickaxe", "inventory": {"iron_pickaxe": 1, "chest": 1}}
    ok = rt._reset_to_anchor(anchor)
    assert ok
    assert world.held == "iron_pickaxe" and world.y == -6.0
    assert world.inventory.get("iron_pickaxe") == 1


def test_reset_to_anchor_clears_only_fixed_helper_workspace():
    world = World()
    env = Env(world)
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=world.execute_action, llm=x1_llm)
    anchor = {"agent.x": 2.5, "agent.y": 69.9, "agent.z": 0.5,
              "held.name": "iron_pickaxe", "inventory": {"iron_pickaxe": 1}}
    assert rt._reset_to_anchor(anchor)
    code = "\n".join(env.step_codes)
    assert "setblock 4 69 0 minecraft:air" in code
    assert "minecraft:chest" in code and "minecraft:crafting_table" in code
    assert "setblock 5 69 0 minecraft:air" not in code
    assert "minecraft:coal_ore" not in code


def test_reset_to_anchor_none_is_safe():
    world = World()
    env = Env(world)
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=world.execute_action, llm=x1_llm)
    assert rt._reset_to_anchor(None) is False    # no anchor -> False, no crash
