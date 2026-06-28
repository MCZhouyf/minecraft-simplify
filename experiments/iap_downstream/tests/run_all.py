"""Run the whole test suite without pytest:  python tests/run_all.py

(``python -m pytest`` also works if pytest is installed.)
"""
import importlib
import sys
import traceback
from pathlib import Path

# make the package importable when run from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODULES = ["test_planner", "test_executor", "test_harness", "test_metrics", "test_agent"]


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    passed = failed = 0
    failures = []
    for modname in MODULES:
        mod = importlib.import_module(modname)
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"PASS  {modname}.{name}")
            except Exception:  # noqa: BLE001
                failed += 1
                failures.append(f"{modname}.{name}")
                print(f"FAIL  {modname}.{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    if failures:
        print("failed:", ", ".join(failures))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
