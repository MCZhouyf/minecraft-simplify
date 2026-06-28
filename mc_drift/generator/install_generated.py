"""Use a generated K1 bias file with the existing datapack/mod-config generator.

This wrapper avoids changing mc_drift/datapack_gen.py. It passes the generated
bias path into datapack_gen.generate() / export_mod_config() directly.

Example:
  python -m mc_drift.generator.install_generated \
    --bias-file mc_drift/out/generated/generated_biases.yaml \
    --generate \
    --export-mod-config

Optional install into the configured Minecraft world:
  python -m mc_drift.generator.install_generated \
    --bias-file mc_drift/out/generated/generated_biases.yaml \
    --generate --install --export-mod-config
"""

from __future__ import annotations

import argparse
from pathlib import Path
import logging

from mc_drift import datapack_gen as D


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bias-file", type=Path, required=True)
    ap.add_argument("--biases", default="all", help="comma-separated generated bias IDs or all")
    ap.add_argument("--out-dir", type=Path, default=D.DEFAULT_OUT_DIR)
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--export-mod-config", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ids = [s.strip() for s in args.biases.split(",") if s.strip()]

    cfg = D.load_config()
    if args.generate or args.install:
        packs = D.generate(ids, out_dir=args.out_dir, config=cfg, biases_path=args.bias_file)
        print("generated:", *[p.name for p in packs])
        if args.install:
            dests = D.install(D.world_save_path(cfg), out_dir=args.out_dir)
            print("installed:", *[d.name for d in dests])

    if args.export_mod_config:
        out = Path(cfg["minecraft_dir"]) / "config" / "mcdrift.json"
        exported = D.export_mod_config(ids, out, biases_path=args.bias_file, feedback_level=cfg.get("feedback_level", "minimal"))
        print("exported:", exported)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
