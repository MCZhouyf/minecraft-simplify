"""Stage-3 offline test: freedo realizes position/time/sky via reset+commands
(zero in-world cost), not via in-world run_plan (which caused X1 timeout)."""
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

from Adam.tcpg import compiler as C                          # noqa: E402
from Adam.tcpg.ccg import CCG                                # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime                    # noqa: E402


class RecordingEnv:
    """Records reset options + chat commands; never runs in-world plans."""
    def __init__(self):
        self.reset_opts = []
        self.chats = []
        self.snap = {"agent.x": 0.0, "agent.y": 64.0, "agent.z": 0.0,
                     "world.time_of_day": 18000, "sky_exposed": False,
                     "held.name": "iron_pickaxe", "held.tier": 3,
                     "inventory": {"iron_pickaxe": 1}}

    def reset(self, options=None):
        self.reset_opts.append(options or {})
        if options and "position" in options:
            self.snap["agent.y"] = options["position"]["y"]
        return []

    def step(self, code):
        self.chats.append(code)
        return [[0, {"inventory": self.snap["inventory"]}]]


def _rt(env, monkeypatch):
    import Adam.tcpg.predicates as P
    monkeypatch.setattr(P, "state_snapshot", lambda e, timeout=60: dict(env.snap))
    return TcpgRuntime(env, ccg=CCG.init_default(), mode="freedo_oracle",
                       execute_action=lambda a: True)


def test_freedo_ylevel_via_reset_not_inworld(monkeypatch):
    """y_level do(h) must set position via reset, NOT run an in-world moveTo."""
    env = RecordingEnv()
    rt = _rt(env, monkeypatch)
    import Adam.tcpg.executor as EX
    called = {"run_plan": 0}
    monkeypatch.setattr(EX, "run_plan",
                        lambda *a, **k: (called.__setitem__("run_plan",
                                         called["run_plan"] + 1), (True, []))[1])
    plan = [C.call("moveTo", y=-12)]            # descend to y=-12
    ok = rt._freedo(plan, "cid")
    assert ok
    # position realized via reset, no in-world run_plan
    assert env.reset_opts and env.reset_opts[-1]["position"]["y"] == -12.0
    assert called["run_plan"] == 0              # NOTHING executed in-world


def test_freedo_time_via_command(monkeypatch):
    """time_of_day do(h) must issue /time set, not an in-world wait."""
    env = RecordingEnv()
    rt = _rt(env, monkeypatch)
    plan = [C.call("wait", until_in=[0, 12000])]
    ok = rt._freedo(plan, "cid")
    assert ok
    joined = " ".join(env.chats)
    assert "/time set" in joined                # realized by command
    assert "6000" in joined                     # midpoint of [0,12000]


def test_freedo_sky_via_setblock(monkeypatch):
    env = RecordingEnv()
    rt = _rt(env, monkeypatch)
    plan = [C.call("mineBlock", name="_roof", special="roof_column", count=6)]
    ok = rt._freedo(plan, "cid")
    assert ok
    assert "/setblock" in " ".join(env.chats) and "air" in " ".join(env.chats)


def test_freedo_inventory_and_equip_via_reset(monkeypatch):
    env = RecordingEnv()
    rt = _rt(env, monkeypatch)
    plan = [C.call("equip", name="diamond_pickaxe"),
            C.call("useChest", op="withdraw",
                   items=[{"name": "diamond_pickaxe", "count": 1}])]
    ok = rt._freedo(plan, "cid")
    assert ok
    opts = env.reset_opts[-1]
    assert opts.get("equipment") == ["diamond_pickaxe"]
    assert opts["inventory"].get("diamond_pickaxe", 0) >= 1
