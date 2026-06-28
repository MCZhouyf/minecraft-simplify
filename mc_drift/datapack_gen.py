"""MC-Drift Stage 1: YAML -> Minecraft datapack generator / installer.

Single source of truth is mc_drift/biases/biases.yaml (contract K1).
This module:
  * load_biases()        -- load + JSON-Schema validate + cross-check action names
  * generate()           -- build the datapack tree (recipes + block tags) deterministically
  * install()/uninstall()-- copy into / remove from <minecraft_dir>/saves/<world>/datapacks/
  * export_mod_config()  -- emit config/mcdrift.json (contract K2) for the Fabric mod (Stage 2)
CLI:
  python -m mc_drift.datapack_gen --list
  python -m mc_drift.datapack_gen --biases R1,C2 --generate
  python -m mc_drift.datapack_gen --biases all --install
  python -m mc_drift.datapack_gen --uninstall
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

log = logging.getLogger("mc_drift.datapack_gen")

MC_DRIFT_DIR = Path(__file__).resolve().parent
REPO_ROOT = MC_DRIFT_DIR.parent
CONFIG_PATH = MC_DRIFT_DIR / "config.yaml"
BIASES_PATH = MC_DRIFT_DIR / "biases" / "biases.yaml"
K1_SCHEMA_PATH = MC_DRIFT_DIR / "schemas" / "k1_bias.schema.json"
DEFAULT_OUT_DIR = MC_DRIFT_DIR / "out"
PACK_NAME = "mc_drift"

PACK_FORMAT = {"1.19": 10, "1.19.2": 10, "1.19.3": 10, "1.19.4": 12}

DATAPACK_MECHANISMS = ("datapack_recipe", "datapack_tag")


# --------------------------------------------------------------------------- config
def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    ver = str(cfg.get("minecraft_version", ""))
    if ver not in PACK_FORMAT:
        raise ValueError(
            f"config.yaml minecraft_version={ver!r} unsupported; "
            f"expected one of {sorted(PACK_FORMAT)}"
        )
    return cfg


def world_save_path(cfg: Dict[str, Any]) -> Path:
    mc_dir = Path(cfg["minecraft_dir"]).expanduser()
    world = cfg["world_name"]
    if "CHANGE_ME" in (str(mc_dir), str(world)):
        raise ValueError("Fill minecraft_dir / world_name in mc_drift/config.yaml first.")
    p = mc_dir / "saves" / world
    if not p.exists():
        raise FileNotFoundError(f"world save not found: {p}")
    return p


# --------------------------------------------------------------------------- K1 loading
def _known_action_names() -> Optional[set]:
    """Best-effort action vocabulary from Adam.util_info (letters dict). None = skip check."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from Adam import util_info  # type: ignore
    except Exception as exc:  # pragma: no cover
        log.warning("cannot import Adam.util_info (%s); skipping action-name check", exc)
        return None
    names: set = set()
    for value in vars(util_info).values():
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(k, str):
                    names.add(k)
                if isinstance(v, str):
                    names.add(v)
    return names or None


def load_biases(path: Path = BIASES_PATH, strict_actions: bool = True) -> List[Dict[str, Any]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "biases" not in doc:
        raise ValueError("biases.yaml must be a mapping with a top-level 'biases' list")
    biases: List[Dict[str, Any]] = doc["biases"] or []

    schema = json.loads(K1_SCHEMA_PATH.read_text(encoding="utf-8"))
    seen_ids: set = set()
    actions = _known_action_names() if strict_actions else None
    for b in biases:
        if jsonschema is not None:
            try:
                jsonschema.validate(b, schema)
            except jsonschema.ValidationError as exc:
                raise ValueError(f"bias {b.get('id','?')} fails K1 schema: {exc.message}") from exc
        if b["id"] in seen_ids:
            raise ValueError(f"duplicate bias id {b['id']}")
        seen_ids.add(b["id"])
        if actions is not None and b["action"] not in actions:
            raise ValueError(
                f"bias {b['id']}: action {b['action']!r} not found in Adam.util_info vocabulary"
            )
    return biases


def select(biases: List[Dict[str, Any]], bias_ids: Iterable[str]) -> List[Dict[str, Any]]:
    ids = list(bias_ids)
    if ids == ["all"]:
        return list(biases)
    by_id = {b["id"]: b for b in biases}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise KeyError(f"unknown bias ids: {missing}")
    return [by_id[i] for i in ids]


# --------------------------------------------------------------------------- generation
def _dump_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generate(bias_ids: List[str],
             out_dir: Path = DEFAULT_OUT_DIR,
             config: Optional[Dict[str, Any]] = None,
             biases_path: Path = BIASES_PATH) -> List[Path]:
    """Build ONE DATAPACK PER BIAS: out_dir/mc_drift_<ID>/...

    Rationale (stage-1 fix): mutating the datapacks folder of an OPEN world and
    then /reload is unstable (vanilla 'zip file closed' bug). Instead we install
    every per-bias pack BEFORE the world is opened, and tests toggle individual
    packs in-session with `/datapack enable|disable "file/mc_drift_<ID>"`, which
    performs its own safe reload without touching the filesystem.

    Deterministic & idempotent. mod_event biases are skipped with an INFO log.
    Returns the list of generated pack directories.
    """
    cfg = config or load_config()
    pf = PACK_FORMAT[str(cfg["minecraft_version"])]
    chosen = select(load_biases(biases_path), bias_ids)

    out_dir.mkdir(parents=True, exist_ok=True)
    packs: List[Path] = []
    for b in chosen:
        mech = b["mechanism"]
        if mech not in DATAPACK_MECHANISMS:
            log.info("bias %s uses mechanism=%s; handled by the Fabric mod, skipping",
                     b["id"], mech)
            continue
        pack_name = f"{PACK_NAME}_{b['id']}"
        tmp = Path(tempfile.mkdtemp(prefix="mcdrift_", dir=str(out_dir)))
        try:
            pack = tmp / pack_name
            (pack / "data" / "minecraft" / "recipes").mkdir(parents=True, exist_ok=True)
            (pack / "data" / "minecraft" / "tags" / "blocks").mkdir(parents=True, exist_ok=True)
            (pack / "pack.mcmeta").write_text(_dump_json(
                {"pack": {"pack_format": pf,
                          "description": f"MC-Drift bias {b['id']} (auto-generated)"}}),
                encoding="utf-8")
            if mech == "datapack_recipe":
                (pack / "data" / "minecraft" / "recipes" / b["payload"]["recipe_file"]
                 ).write_text(_dump_json(b["payload"]["recipe_json"]), encoding="utf-8")
            elif mech == "datapack_tag":
                values = sorted(dict.fromkeys(b["payload"]["values_add"]))
                (pack / "data" / "minecraft" / "tags" / "blocks" / b["payload"]["tag_file"]
                 ).write_text(_dump_json({"replace": False, "values": values}),
                              encoding="utf-8")
            target = out_dir / pack_name
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(pack), str(target))
            packs.append(target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return packs


# --------------------------------------------------------------------------- install
def install(world_save: Path, out_dir: Path = DEFAULT_OUT_DIR) -> List[Path]:
    """Copy every generated mc_drift_* pack into <world>/datapacks/.

    IMPORTANT: run this while the world is CLOSED (or before first open).
    Packs found at world load are auto-enabled; tests then toggle them
    per-case with /datapack enable|disable (no filesystem mutation).
    """
    packs = sorted(out_dir.glob(f"{PACK_NAME}_*"))
    if not packs:
        raise FileNotFoundError(f"no generated packs under {out_dir}; run generate() first")
    dests: List[Path] = []
    for pack in packs:
        dest = world_save / "datapacks" / pack.name
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(pack, dest)
        dests.append(dest)
        log.info("installed %s", dest)
    return dests


def uninstall(world_save: Path) -> int:
    """Remove every mc_drift* pack (run while the world is CLOSED)."""
    n = 0
    for dest in (world_save / "datapacks").glob(f"{PACK_NAME}*"):
        shutil.rmtree(dest)
        log.info("removed %s", dest)
        n += 1
    return n


# --------------------------------------------------------------------------- K2 export (Stage 2 consumer)
def export_mod_config(bias_ids: List[str], out_path: Path,
                      biases_path: Path = BIASES_PATH,
                      feedback_level: str = "minimal") -> Path:
    chosen = select(load_biases(biases_path), bias_ids)
    mod = [b for b in chosen if b["mechanism"] == "mod_event"]
    cfg = {
        "version": 1,
        "feedback_level": feedback_level,
        "enabled": [b["id"] for b in mod],
        "gates": {b["id"]: {"gate": b["payload"]["gate"], "params": b["payload"]["params"]}
                  for b in mod},
        "feedback_text": {b["id"]: b.get("feedback_text", {}) for b in mod
                          if b.get("feedback_text")},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_dump_json(cfg), encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--biases", default="all",
                    help="comma-separated bias ids, or 'all' (default)")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--install", action="store_true",
                    help="generate (if needed) and copy into the LAN world's datapacks/")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--export-mod-config", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ids = [s.strip() for s in args.biases.split(",") if s.strip()]
    if args.list:
        for b in load_biases():
            print(f"{b['id']:3s} {b['level']} {b['dimension']:<11s} {b['mechanism']:<16s} {b['action']}")
        return 0
    cfg = load_config()
    if args.uninstall:
        uninstall(world_save_path(cfg))
        return 0
    if args.generate or args.install:
        packs = generate(ids, config=cfg)
        print("generated:", *[p.name for p in packs])
    if args.install:
        dests = install(world_save_path(cfg))
        print("installed:", *[d.name for d in dests])
        print("NOTE: install with the world CLOSED, then open it; packs auto-enable.")
        print("Per-test toggling: /datapack disable|enable \"file/<pack_name>\"")
    if args.export_mod_config:
        out = Path(cfg["minecraft_dir"]) / "config" / "mcdrift.json"
        print(f"exported: {export_mod_config(ids, out, feedback_level=cfg.get('feedback_level','minimal'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
