# MC-Drift Randomized Drift Generator

This patch adds a template-conditioned randomized drift generator for IAP-Agent.

It is additive: it does not modify `mc_drift/datapack_gen.py` or the existing
hand-authored `mc_drift/biases/biases.yaml`.

## Why this design matches the current repository

The repository already uses:

- `mc_drift/biases/biases.yaml` as the K1 mechanism ground-truth source.
- `mc_drift/datapack_gen.py` to generate per-bias datapacks and mod config.
- `mc_drift/solvability.py` plus `Adam.tcpg.compiler` to check oracle
  solvability and I+/I- intervention compilability.

This generator therefore emits a standalone K1-compatible
`generated_biases.yaml` and task-pair files, rather than inventing a new dataset
format.

## Run

From the repository root:

```bash
python -m mc_drift.generator.generate_drift_tasks \
  --raw 1000 \
  --final-specs 60 \
  --seeds-per-spec 5 \
  --seed 7 \
  --out-dir mc_drift/out/generated
```

If you only want to smoke-test without importing `Adam.tcpg.compiler`:

```bash
python -m mc_drift.generator.generate_drift_tasks \
  --raw 100 \
  --final-specs 20 \
  --seeds-per-spec 2 \
  --no-runtime-check \
  --out-dir mc_drift/out/generated_smoke
```

## Outputs

- `generated_biases.yaml`: standalone K1-compatible mechanism file.
- `generated_tasks.yaml`: machine-readable task-seed instances.
- `generated_task_pairs.txt`: human-readable origin/drift task pairs.
- `generation_report.json`: raw/valid/final counts and filter reasons.

## Install generated mechanisms

```bash
python -m mc_drift.generator.install_generated \
  --bias-file mc_drift/out/generated/generated_biases.yaml \
  --generate \
  --export-mod-config
```

Add `--install` only when the configured Minecraft world is closed:

```bash
python -m mc_drift.generator.install_generated \
  --bias-file mc_drift/out/generated/generated_biases.yaml \
  --generate --install --export-mod-config
```

## Important limitation

The current `k1_bias.schema.json` accepts IDs matching `^[RCPXEJF][0-9]$`, so
this patch assigns standalone generated IDs such as `R0...F9` and supports at
most 70 generated specs without changing the schema. For more than 70 unique
specs, relax the ID regex in the schema and update the ID assignment function.
