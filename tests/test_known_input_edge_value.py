"""is_known_input_edge must be VALUE-aware: a recipe-input item at or below its
vanilla quantity is a known edge (necessity confirmation), but an ELEVATED count
threshold on that same input (e.g. craftFence oak_planks>=9 when vanilla fence
needs 4) is a QUANTITY DRIFT and must be written back as a discovered gate, not
swallowed as 'confirmed_known'. Regression guard for the R2 frontier outcome."""
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

from Adam.tcpg.ccg import CCG                                  # noqa: E402
from Adam.tcpg.proposer import Candidate                      # noqa: E402


def _fence(n):
    return Candidate(action="craftFence", dimension="resource",
                     target="inventory_count", property="oak_planks",
                     comparator=">=", value=n)


def test_known_input_edge_is_value_aware():
    g = CCG.init_default()                       # vanilla fence: oak_planks = 4
    assert g.is_known_input_edge(_fence(4)) is True    # == vanilla -> known
    assert g.is_known_input_edge(_fence(2)) is True    # below vanilla -> known
    assert g.is_known_input_edge(_fence(8)) is False   # elevated -> DRIFT
    assert g.is_known_input_edge(_fence(9)) is False   # operational drift -> DRIFT


def test_quantity_drift_on_recipe_input_writes_back_as_accepted():
    g = CCG.init_default()
    c = _fence(9)                                # the discovered operational gate
    g.write_back(c)
    assert g.conditions[c.cid]["status"] == "accepted"   # NOT confirmed_known
    assert c.cid in g.e_ca["craftFence"]                 # added as a real E_ca gate


def test_vanilla_quantity_confirmation_still_known():
    g = CCG.init_default()
    c = _fence(4)                                # necessity confirmation, not a drift
    g.write_back(c)
    assert g.conditions[c.cid]["status"] == "confirmed_known"
    assert "craftFence" not in g.e_ca or c.cid not in g.e_ca.get("craftFence", [])


def test_non_recipe_input_unaffected():
    # R1/R4 style: an item that is NOT a recipe input is never 'known' (any value)
    g = CCG.init_default()
    button = Candidate(action="craftIronPickaxe", dimension="resource",
                       target="inventory_count", property="oak_button",
                       comparator=">=", value=1)
    assert g.is_known_input_edge(button) is False
