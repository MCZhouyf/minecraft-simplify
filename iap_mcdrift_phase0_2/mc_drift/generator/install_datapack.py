"""Copy a generated datapack into a Minecraft world datapacks directory.

Example:
  python -m mc_drift.generator.install_datapack \
    --pack mc_drift/out/datapacks/iap_phase0_2 \
    --world "$APPDATA/.minecraft/saves/IAPWorld"
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", type=Path, required=True, help="Generated datapack directory.")
    parser.add_argument("--world", type=Path, required=True, help="Minecraft world save directory.")
    parser.add_argument("--name", default=None, help="Destination datapack name; defaults to pack dir name.")
    args = parser.parse_args()

    if not args.pack.exists():
        raise SystemExit(f"Datapack not found: {args.pack}")
    if not (args.pack / "pack.mcmeta").exists():
        raise SystemExit(f"Not a datapack directory, missing pack.mcmeta: {args.pack}")
    if not args.world.exists():
        raise SystemExit(f"World directory does not exist: {args.world}")

    dst = args.world / "datapacks" / (args.name or args.pack.name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(args.pack, dst)
    print(f"Installed datapack to: {dst}")
    print("In Minecraft, run /reload and /datapack list to confirm it is enabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
