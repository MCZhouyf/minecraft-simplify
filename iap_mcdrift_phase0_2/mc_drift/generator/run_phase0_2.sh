#!/usr/bin/env bash
set -euo pipefail

python -m mc_drift.generator.validate_tasks mc_drift/tasks/u_tasks_final.yaml \
  --labels mc_drift/tasks/u_tasks_labels.csv

python -m mc_drift.generator.build_datapack \
  --tasks mc_drift/tasks/u_tasks_final.yaml \
  --labels mc_drift/tasks/u_tasks_labels.csv \
  --out mc_drift/out/datapacks \
  --pack-name iap_phase0_2 \
  --pack-format 10

python -m pytest mc_drift/tests/test_phase0_2_static.py -q
