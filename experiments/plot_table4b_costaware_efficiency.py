"""Plot Table 4b cost-aware ordering efficiency."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="experiments/results/table4b_costaware_efficiency_plot_data.csv")
    ap.add_argument("--raw", default="experiments/results/table4b_costaware_efficiency_raw.csv")
    ap.add_argument("--out-prefix", default="experiments/results/table4b_costaware_efficiency")
    args = ap.parse_args(argv)
    plot_rows = read_rows(Path(args.input))
    raw_rows = read_rows(Path(args.raw))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    settings = ["core", "dense"]
    variants = ["full_costaware", "minus_costaware"]
    colors = {"full_costaware": "#2f6f9f", "minus_costaware": "#c56b32"}

    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.4))
    for ax, setting in zip(axes[:2], settings):
        vals = []
        errs = []
        for variant in variants:
            row = next((r for r in plot_rows if r["setting"] == setting and r["variant"] == variant), None)
            vals.append(float(row["mean"]) if row else 0.0)
            errs.append(float(row["std"]) if row else 0.0)
        ax.bar([0, 1], vals, yerr=errs, capsize=4, color=[colors[v] for v in variants])
        ax.set_title(f"{setting.capitalize()} setting")
        ax.set_xticks([0, 1], ["Full", "-Cost"])
        ax.set_ylabel("Embodied cost before GT")
        ax.grid(axis="y", alpha=0.25)

    dense = [r for r in raw_rows if r["setting"] == "dense"]
    acc = []
    for variant in variants:
        rs = [r for r in dense if r["variant"] == variant]
        acc.append(sum(int(r["gt_accepted"]) for r in rs) / len(rs) if rs else 0.0)
    axes[2].bar([0, 1], acc, color=[colors[v] for v in variants])
    axes[2].set_title("Dense GT accepted")
    axes[2].set_xticks([0, 1], ["Full", "-Cost"])
    axes[2].set_ylim(0, 1.05)
    axes[2].set_ylabel("Rate")
    axes[2].grid(axis="y", alpha=0.25)

    fig.suptitle("Cost-aware ordering efficiency under core and dense candidate settings")
    fig.tight_layout()
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    png = out_prefix.with_suffix(".png")
    pdf = out_prefix.with_suffix(".pdf")
    fig.savefig(png, dpi=200)
    fig.savefig(pdf)
    print(f"wrote {png}")
    print(f"wrote {pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
