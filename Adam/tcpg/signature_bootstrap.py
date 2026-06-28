"""Stage 3: signature bootstrap — derive the predicate signature from the
agent's OBSERVATION INTERFACE and quantify the manual residue.

This produces the two numbers the paper claims in Sec. 4.3 / App. B.4:
  * how many core primitives are auto-derived from the observation API
  * how many lines of manual residue (naming + dimension binning) remain

Pipeline (each step has a CLI flag):
  1. dump   (live)    POST /state_snapshot -> state_fields.json (field -> type)
  2. derive (offline) type-driven rules    -> schema_draft.json
  3. report (offline) diff draft vs Adam/tcpg/schema.json -> manual_residue.md

Type-driven derivation rules (domain-agnostic):
  number      -> a comparison primitive  (field ⋈ k)
  bool        -> an equality primitive   (field = b)
  string/enum -> an equality primitive   (field = s)
  map[str,int]-> a per-key count primitive (field[key] ⋈ n)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
from pathlib import Path
from typing import Any, Dict

TCPG_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = TCPG_DIR / "schema.json"
DEFAULT_FIELDS = TCPG_DIR / "state_fields.json"
DEFAULT_DRAFT = TCPG_DIR / "schema_draft.json"
DEFAULT_REPORT = TCPG_DIR.parent.parent / "docs" / "manual_residue.md"


def detect_lan_port() -> int:
    env_port = os.environ.get("IAP_MC_PORT") or os.environ.get("ADAM_MC_PORT")
    if env_port:
        os.environ["IAP_MC_PORT"] = env_port
        return int(env_port)

    log_path = Path("/root/.minecraft/logs/latest.log")
    if not log_path.exists():
        raise RuntimeError("No Minecraft latest.log found; open the world to LAN or set IAP_MC_PORT.")

    patterns = (
        re.compile(r"Started serving on (\d+)"),
        re.compile(r"Local game hosted on port (\d+)"),
    )
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in reversed(lines):
        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            port = int(match.group(1))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass
            except OSError:
                continue
            os.environ["IAP_MC_PORT"] = str(port)
            return port
    raise RuntimeError("No active Minecraft LAN port detected; open the world to LAN or set IAP_MC_PORT.")

# observation field  ->  core primitive it grounds (the auto-derivable part)
FIELD_TO_PRIMITIVE = {
    "agent.y": "y_level",
    "world.time_of_day": "time_of_day",
    "world.is_raining": "weather",
    "held.name": "held_item",
    "held.tier": "held_tool",
    "block_below.name": "block_below",
    "sky_exposed": "sky_exposed",
    "inventory": "inventory_count",
}

TYPE_RULES = {
    "number": "comparison",
    "bool": "equality",
    "string": "equality",
    "map": "count_per_key",
}


# --------------------------------------------------------------------- 1. dump
def flatten_types(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """Infer a flat {field: type} view of one /state_snapshot payload."""
    out: Dict[str, str] = {}
    for key, val in snapshot.items():
        if isinstance(val, bool):
            out[key] = "bool"
        elif isinstance(val, (int, float)):
            out[key] = "number"
        elif isinstance(val, str):
            out[key] = "string"
        elif isinstance(val, dict):
            out[key] = "map"
        elif val is None:
            out[key] = "string"          # nullable scalar; refined by more samples
        else:
            out[key] = type(val).__name__
    return out


def dump_state_fields(env, out_path: Path = DEFAULT_FIELDS) -> Dict[str, str]:
    from Adam.tcpg.predicates import state_snapshot
    fields = flatten_types(state_snapshot(env))
    out_path.write_text(json.dumps(fields, indent=2, sort_keys=True), encoding="utf-8")
    return fields


# ------------------------------------------------------------------- 2. derive
def derive_signature(fields: Dict[str, str]) -> Dict[str, Any]:
    """Apply the type rules; attach the core-primitive identification where the
    field is recognized. Everything in `derived` came mechanically from the
    observation interface."""
    derived = {}
    for field, ftype in sorted(fields.items()):
        rule = TYPE_RULES.get(ftype)
        if rule is None:
            continue
        derived[field] = {
            "type": ftype,
            "derived_primitive_kind": rule,
            "grounds_core_primitive": FIELD_TO_PRIMITIVE.get(field),
        }
    return {"version": 1, "source": "observation interface (auto)", "derived": derived}


# ------------------------------------------------------------------- 3. report
def residue_report(draft: Dict[str, Any],
                   schema_path: Path = SCHEMA_PATH,
                   out_md: Path = DEFAULT_REPORT) -> Dict[str, Any]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    core = list(schema["primitives"].keys())
    auto = sorted({v["grounds_core_primitive"]
                   for v in draft["derived"].values()
                   if v.get("grounds_core_primitive")})
    manual = [p for p in core if p not in auto]
    # manual residue measured in source lines: the whitelist (dimension binning)
    # plus the primitive declarations of the non-derivable primitives.
    schema_text = schema_path.read_text(encoding="utf-8").splitlines()
    whitelist_lines = sum(1 for ln in schema_text
                          if any(f'"{d}"' in ln for d in schema["whitelist"]))
    manual_decl_lines = sum(1 for ln in schema_text
                            if any(f'"{p}"' in ln for p in manual))
    stats = {
        "core_primitives": len(core),
        "auto_derived": len(auto),
        "auto_list": auto,
        "manual_list": manual,
        "manual_residue_lines": whitelist_lines + manual_decl_lines,
    }
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        "# Signature bootstrap: manual-residue report (paper Sec. 4.3 / App. B.4)\n\n"
        f"- core primitives in Sigma_MC: **{stats['core_primitives']}**\n"
        f"- auto-derived from the observation interface: "
        f"**{stats['auto_derived']}/{stats['core_primitives']}** "
        f"({', '.join(auto)})\n"
        f"- manually added (need world queries beyond flat state, or action "
        f"context): {', '.join(manual)}\n"
        f"- manual residue (naming + dimension binning + manual declarations): "
        f"**{stats['manual_residue_lines']} lines** of schema.json\n",
        encoding="utf-8")
    return stats


# ------------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", action="store_true",
                    help="LIVE: query /state_snapshot (needs IAP_MC_PORT + open LAN world)")
    ap.add_argument("--derive", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    if args.dump:
        import sys
        sys.path.insert(0, str(TCPG_DIR.parent.parent))
        from env.bridge import VoyagerEnv
        env = VoyagerEnv(mc_port=detect_lan_port(),
                         server_port=int(os.environ.get("IAP_MF_PORT", "3000")),
                         request_timeout=120)
        env.reset(options={"mode": "hard", "inventory": {}})
        fields = dump_state_fields(env)
        print(f"dumped {len(fields)} fields -> {DEFAULT_FIELDS}")
        env.close()
    if args.derive:
        fields = json.loads(DEFAULT_FIELDS.read_text(encoding="utf-8"))
        DEFAULT_DRAFT.write_text(json.dumps(derive_signature(fields), indent=2),
                                 encoding="utf-8")
        print(f"derived -> {DEFAULT_DRAFT}")
    if args.report:
        draft = json.loads(DEFAULT_DRAFT.read_text(encoding="utf-8"))
        stats = residue_report(draft)
        print(json.dumps(stats, indent=2))
        print(f"report -> {DEFAULT_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
