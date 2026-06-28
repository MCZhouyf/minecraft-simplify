# IaP — Interventions-as-Planning closed loop (paper Figure 2) + downstream success

A self-contained, **runnable and tested** reference implementation of the
paper's Figure 2 flow:

> 受领任务 → 查询 CCG → LLM 规划 → 执行高层动作 → **动作失败时触发因果校准闭环**
> (提案 typed structure → 双侧对照验证 + 边界干预 → 代价感知 → 写回 CCG →
> 全否决则 NOTA 签名枚举扩展候选) → **用更新后的 CCG 重新规划** → 完成任务。

Two entry points, the two halves of Figure 2:

* **`run_iap`** — the **full closed loop**. The agent starts a task with an empty
  inventory and a *stale* graph, fails, **discovers the hidden gate online**
  (propose → verify → NOTA → boundary → write-back), replans and completes.
* **`run_downstream`** — the **frozen-graph attribution** experiment (Stage B):
  hand the planner `before` / `after` / `oracle` graphs and measure downstream
  task-completion success. Fills Table 2 / Table 3 (writeback-success) / Table 4
  (success).

Standard-library only; **26 tests** (`python3 tests/run_all.py`).

## Quick start

    cd iap_downstream
    python3 tests/run_all.py                      # 26 tests, all green
    python3 -m iap_downstream.run_iap --seeds 5   # full Figure-2 loop
    python3 -m iap_downstream.run_downstream --out table_downstream.csv --seeds 5
    python3 -m iap_downstream.run_iap --no-dual-pool   # point-estimate ablation
    python3 -m iap_downstream.run_iap --cost-alpha 0   # cost-blind acquisition

`run_iap` (mock) result: craftBoat/mineDiamond drift → 100% completion by online
discovery (`water_radius<=3`, `y_level<=-8` operational); origin → 100%, 0
interventions.

## How it maps to Figure 2

| Figure-2 stage | module |
|---|---|
| query CCG → plan → execute | `planner.py`, `executor.py` |
| detect failure → trigger calibration | `agent.py` |
| **propose** typed candidate gate (LLM) | `proposer.py` (`Proposer`/`MockProposer`, `Candidate`) |
| **verify** two-sided contrast + boundary intervention | `calibration.py` |
| dual-pool Beta **posterior** | `posterior.py` (`DualPool`, dual/point) |
| **NOTA** signature enumeration | `nota.py` |
| cost-aware **acquisition** | `acquisition.py` |
| **write-back** → replan | `causal_graph.CausalGraph.add_gate`, `agent.py` |
| downstream completion / attribution | `harness.py`, `metrics.py` |
| environment (true world + interventions) | `env_adapter.py`, `mock_env.py` |

Reproduces the craftBoat walkthrough exactly: resource candidates rejected →
NOTA finds the water gate → boundary pins `water_radius<=3` → write-back +
replan completes the boat. mineDiamond discovers the **operational** depth
`y_level<=-8` (Route B), not nominal `-10`.

## File map

| file | role |
|------|------|
| `causal_graph.py` | predicates, actions, grounding, `State`, `CausalGraph` (+ `add_gate`) |
| `planner.py` | STRIPS goal-regression + validator + BFS fallback |
| `executor.py` | closed-loop execution with bounded replan (Stage B) |
| `posterior.py` | dual-pool Beta posterior; `mode="point"` = `-dual_pool` ablation |
| `proposer.py` | typed candidate proposer (LLM prior); adapter seam + mock |
| `nota.py` | NOTA signature enumeration |
| `acquisition.py` | cost-aware ordering (`cost_alpha=0` ablation) |
| `calibration.py` | propose → verify → NOTA → boundary → write-back |
| `agent.py` | integrated Figure-2 loop (`run_iap_episode`) |
| `env_adapter.py` | **seams you implement**: `Env` (+ `probe`, `signatures`) |
| `mock_env.py` | reference MC-Drift mock + belief graphs |
| `harness.py` | Stage-B `downstream_success` / `run_sweep` / `sanity_checks` |
| `metrics.py` | Wilson CI, CSV, Table 2/3/4 aggregation |
| `run_iap.py` / `run_downstream.py` | CLIs |
| `tests/` | 26 tests + `run_all.py` |

## Wiring to the real repo (see CODEX_PROMPT.md)

You implement adapters; you do not touch loop logic.

1. **`Env` adapter** — the five Stage-B methods + discovery hooks:
   * `probe(assignments, action_name) -> bool`: apply an intervention, test
     whether the action's *true* world-preconditions hold, restore. (Two-sided
     contrast / boundary evidence on the real simulator.)
   * `signatures() -> [..]`: enumerable gate templates for NOTA (depth,
     proximity, tier, count …) with `true_set`/`false_set`/`probe_values`.
2. **`Proposer`** backed by your real LLM (returns `Candidate`s).
3. **CCG ↔ `CausalGraph`** loader; write-back via `CausalGraph.add_gate`.

The mock proves the control flow and the accept/reject logic; the only new
surface on the real env is the adapter.

## Invariants the tests pin down

* dual pool **rejects** a weak-contrast candidate (pos *and* neg succeed) that
  the point estimate accepts — the precision guarantee under confounders.
* NOTA recovers the gate after all resource candidates are rejected.
* drift completes only via discovery; origin completes with zero interventions;
  write-back never fires without enough evidence.
* Stage-B: drift `before≈0`, `after`/`oracle` high, origin `after≈before`.

## Budget separation

`run_iap` reports Stage-A interventions and downstream steps separately; the
discovery cost is not part of the completion-rate denominator.
