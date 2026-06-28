"""Per-target intervention policy is signature-driven:
  * sim_verifiable is derived from realize=="reset" (player-state injection),
    NOT a hardcoded set -- inventory_count/held_tool/held_item only. y_level /
    time_of_day / nearby_block / sky_exposed are realize=="in_world" (must be
    actually explored, scaled cost).
  * inventory_count boundary tests (isolated==False) top up the action's OTHER
    recipe inputs to a fresh, sufficient level so the tested threshold is the
    only thing that varies (removes the co-input-depletion confound)."""
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

from Adam.tcpg import compiler as C                          # noqa: E402
from Adam.tcpg import predicates as P                        # noqa: E402
from Adam.tcpg.ccg import CCG                                # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime                    # noqa: E402
from Adam.tcpg.proposer import Candidate                     # noqa: E402


def _set_counts(plan):
    return {s["args"]["name"]: s["args"]["count"]
            for s in plan if s["primitive"] == "set_count"}


def _frontier(action, item, comp=">=", value=8):
    c = Candidate(action, "resource", "inventory_count", item, comp, value)
    c.source = "frontier"
    return c


# --------------------------------------------------- signature declares policy
def test_schema_declares_realize_and_param_kind():
    prim = P.schema()["primitives"]
    assert prim["inventory_count"]["realize"] == "reset"
    assert prim["inventory_count"]["isolated"] is False
    assert prim["inventory_count"]["param_kind"] == "numeric_threshold"
    assert prim["held_tool"]["realize"] == "reset"
    assert prim["held_tool"]["param_kind"] == "ordered_level"
    assert prim["held_item"]["realize"] == "reset"
    # situational targets must be in_world (real exploration), scaled cost
    for t in ("y_level", "time_of_day", "nearby_block", "sky_exposed",
              "station_type", "station_base_block"):
        assert prim[t]["realize"] == "in_world", t
        assert prim[t]["cost"] == "scaled", t


def test_sim_verifiable_derived_from_realize_flag():
    # the set is exactly the realize=="reset" targets -- behaviour-preserving
    assert C.SIM_VERIFIABLE_TARGETS == frozenset({"inventory_count", "held_tool",
                                                  "held_item"})
    derived = {t for t, s in P.schema()["primitives"].items()
               if s.get("realize") == "reset"}
    assert set(C.SIM_VERIFIABLE_TARGETS) == derived


def test_y_level_is_not_sim_verifiable():
    c = Candidate("mineDiamondOre", "context", "y_level", "y", "<=", -10)
    c.source = "frontier"
    k5 = C.compile(c, state={"agent_y": 0.0})
    assert k5["sim_verifiable"] is False          # explored in-world, scaled cost
    # and no set_count appears (world-state realization, not inventory reset)
    assert all(s["primitive"] != "set_count"
               for s in k5["plan_plus"] + k5["plan_minus"])
    assert k5["plan_plus"] == [C.call("set_y", y=-10.0)]


# --------------------------------------------------- co-input isolation top-up
def test_co_inputs_topped_up_for_fence():
    k5 = C.compile(_frontier("craftFence", "oak_planks", ">=", 8),
                   state={"inventory": {"oak_planks": 6, "chest": 1}})
    plus = _set_counts(k5["plan_plus"])
    assert plus["oak_planks"] == 8                # the tested variable (I+)
    assert plus["stick"] == 2                     # fence co-input, topped up
    # held FIXED across I+/I-: same co-inputs, only the tested var differs
    minus = _set_counts(k5["plan_minus"])
    assert minus["oak_planks"] == 7
    assert minus["stick"] == 2


def test_co_inputs_for_furnace_and_pickaxe_drifts():
    # R1: craftFurnace gated by sand (not a real input) -> top up cobblestone
    f = C.compile(_frontier("craftFurnace", "sand", ">=", 1),
                  state={"inventory": {"chest": 1}})
    sc = _set_counts(f["plan_plus"])
    assert sc["sand"] == 1 and sc["cobblestone"] == 8
    # R4: craftIronPickaxe gated by oak_button -> top up iron_ingot + stick
    p = C.compile(_frontier("craftIronPickaxe", "oak_button", ">=", 1),
                  state={"inventory": {"chest": 1}})
    sc = _set_counts(p["plan_plus"])
    assert sc["oak_button"] == 1 and sc["iron_ingot"] == 3 and sc["stick"] == 2


def test_tested_item_not_in_co_inputs():
    # the boundary sets the tested item; it must not also appear as a co-input
    k5 = C.compile(_frontier("craftFence", "oak_planks", ">=", 8),
                   state={"inventory": {"oak_planks": 6, "chest": 1}})
    planks_sets = [s for s in k5["plan_plus"]
                   if s["primitive"] == "set_count" and s["args"]["name"] == "oak_planks"]
    assert len(planks_sets) == 1                  # exactly one set of the tested item


def test_non_craft_action_has_no_co_inputs():
    # an inventory_count gate on a MINING action has no recipe co-inputs to top up
    c = _frontier("mineDiamondOre", "torch", ">=", 1)
    k5 = C.compile(c, state={"inventory": {"chest": 1}})
    sc = _set_counts(k5["plan_plus"])
    assert sc == {"torch": 1}                     # only the tested var, nothing else


# --------------------------------------------------- freedo realizes the top-up
class _Env:
    def __init__(self, inv):
        self.reset_opts = []
        self.snap = {"agent.x": 0.0, "agent.y": 64.0, "agent.z": 0.0,
                     "inventory": dict(inv)}

    def reset(self, options=None):
        self.reset_opts.append(options or {})
        return []

    def step(self, code):
        return [[0, {}]]


def test_freedo_realizes_co_input_topup(monkeypatch):
    env = _Env({"oak_planks": 6, "stick": 0})     # sticks depleted from prior crafts
    monkeypatch.setattr(P, "state_snapshot", lambda e, timeout=60: dict(env.snap))
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode="freedo_oracle",
                     execute_action=lambda a: True)
    k5 = C.compile(_frontier("craftFence", "oak_planks", ">=", 8),
                   state={"inventory": {"oak_planks": 6, "chest": 1}})
    rt._freedo(k5["plan_plus"], "cid")
    inv = env.reset_opts[-1]["inventory"]
    assert inv["oak_planks"] == 8                 # tested var set exactly
    assert inv["stick"] == 2                      # co-input refreshed (was 0)
