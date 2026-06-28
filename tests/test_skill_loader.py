import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

import Adam.skill_loader as SL  # noqa: E402


def test_skill_loader_falls_back_when_babel_parse_fails(monkeypatch):
    monkeypatch.setattr(SL, "process_message", lambda _source: "parse failed")

    code = SL.skill_loader("craftFurnace")

    assert "async function craftFurnace(bot)" in code
    assert "await craftFurnace(bot);" in code

