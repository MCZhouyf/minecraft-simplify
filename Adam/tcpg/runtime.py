"""Stage 6: TCPG verification closed loop (Algorithm 1; paper Sec. 4.1/4.4).

One runtime object lives inside ADAM (lazy-constructed by ADAM._tcpg_runtime).
ADAM calls exactly one entry point per executed action:

    runtime.on_action(action, success, inventory, observation)

which (i) generates candidates (success -> necessity, failure -> recovery),
(ii) routes the natural observation into the dual pools, and (iii) spends the
remaining verification budget on in-episode interventions:
    snapshot Ct -> scarce-side plan -> retry action -> undo -> verify Ct
    -> pool update (void on drift) -> threshold decision -> write-back.

verification_mode semantics (frozen contract):
  off            everything below is bypassed
  adam_original  ADAM's own causal_learning path; runtime bypassed
  llm_writeback  failure proposals are written back UNVERIFIED (ablation floor)
  freedo_oracle  same candidates/decisions, interventions via env.reset
                 (zero-cost simulator do); intervention steps counted as 0
  tcpg           full method (in-episode interventions via the executor)

Dependency injection: `execute_action(action)->bool` and `llm(prompt)->str`
come from ADAM; offline tests pass mocks (no Minecraft, no API key).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from Adam.tcpg import compiler as C
from Adam.tcpg import predicates as P
from Adam.tcpg.ccg import CCG
from Adam.tcpg.eventlog import log_event
from Adam.tcpg.posterior import Acquisition, DualPool, scarce_side
from Adam.tcpg.proposer import (Candidate, candidates_from_success,
                                propose_from_failure)

CONFIG_DEFAULTS = {"delta": 0.3, "tau_acc": 0.9, "tau_rej": 0.1, "n_min": 4,
                   "M": 100_000, "rr_every": 3, "step_budget": 120,
                   "max_interventions_per_event": 3, "seed": 0,
                   "cost_alpha": 0.5, "cost_c0": 1.0, "trigger_budget": 0.0,
                   "sim_verify_cost": 2.0, "sim_cost_mode": "floor",
                   "posterior_mode": "dual",
                   "min_verifications_per_cand": 0,
                   # Component-ablation toggles (paper Table 6). Both default ON;
                   # the runner flips them off for the "- necessity test" and
                   # "- neighbor expansion" ablation rows. Statistical contract
                   # (4.5) is unaffected -- these only change which candidates
                   # enter the pool, not the accept/reject rule.
                   "necessity_test": True, "neighbor_expand": True,
                   # Closed-loop tail (reproposal.py). OFF by default so existing
                   # runs are byte-identical; enable for the none-of-the-above
                   # reproposal + signature-fallback experiment.
                   "nota_reproposal": False, "max_reproposal_rounds": 2,
                   "reproposal_signature_fallback": True,
                   "proposal_failure_signature_fallback": True,
                   "nota_reproposal_early_low_signal": True,
                   "nota_low_signal_min_candidates": 4,
                   "nota_low_signal_min_samples": 8}
CTX_KEYS = ("held.name", "agent.y")          # snapshot keys verified after undo
_ORDERED_DOMAINS = P.schema().get("ordered_domains", {})


class TcpgRuntime:
    def __init__(self, env, ccg: Optional[CCG] = None, mode: str = "tcpg",
                 execute_action: Optional[Callable[[str], bool]] = None,
                 llm: Optional[Callable[[str], str]] = None,
                 config: Optional[Dict[str, Any]] = None,
                 trial_id: str = "-"):
        self.env = env
        self.ccg = ccg or CCG.init_default()
        self.mode = mode
        self.execute_action = execute_action
        self.llm = llm
        self.cfg = {**CONFIG_DEFAULTS, **(config or {})}
        self.trial_id = trial_id
        self.step = 0
        self.steps_used = 0                   # verification env-steps spent
        self.cands: Dict[str, Candidate] = {}
        self.pools: Dict[str, DualPool] = {}
        self.k5: Dict[str, Dict[str, Any]] = {}
        self._plan_fail_counts: Dict[str, int] = {}
        self._reproposal_rounds: Dict[str, int] = {}   # NOTA rounds per action
        self._reproposal_cids: set[str] = set()
        self.acq = Acquisition(rr_every=self.cfg["rr_every"],
                               delta=self.cfg["delta"], seed=self.cfg["seed"],
                               cost_alpha=self.cfg["cost_alpha"],
                               cost_c0=self.cfg["cost_c0"])

    # ================================================================ entry
    def on_action(self, action: str, success: bool,
                  inventory: Dict[str, int],
                  observation: Optional[Dict[str, Any]] = None) -> None:
        self.step += 1
        if self.mode in ("off", "adam_original"):
            return
        log_event("action", {"action": action, "y": int(success),
                             "inventory_digest": _digest(inventory)},
                  self.trial_id, self.step)

        new = self._generate_candidates(action, success, inventory, observation)
        if self.mode == "llm_writeback":
            for c in new:
                if c.source == "tcpg":        # unverified write-back (ablation)
                    self.ccg.write_back(c, self.trial_id, self.step)
            return

        full = self._snapshot(full=True)
        self._register(new, inventory, held=full.get("held.name"),
                       agent_y=full.get("agent.y"))
        self._route_natural(action, success)
        self._verification_loop(action, inventory, anchor=full)
        if not success:
            self._maybe_repropose(action, inventory, observation, full)

    def candidate_records(self) -> List[Dict[str, Any]]:
        """Candidate dicts for summary.json, with each candidate's two-sided
        pool counts (n_pos/k_pos/n_neg/k_neg) merged in from its DualPool.

        The pool -- not the Candidate -- is the live home of those counts, so a
        bare to_dict() always reported zeros; cost_layering_report's mean_neff
        (paper 4.5 budget-sample evidence: cheap contrasts buy more effective
        two-sided observations) silently read 0 for every bias without this
        sync. Idempotent; safe to call once at run end."""
        recs = []
        for cid, c in self.cands.items():
            pool = self.pools.get(cid)
            if pool is not None:
                c.n_pos, c.k_pos = pool.n_pos, pool.k_pos
                c.n_neg, c.k_neg = pool.n_neg, pool.k_neg
            recs.append(c.to_dict())
        return recs

    # ============================================================ candidates
    def _generate_candidates(self, action, success, inventory, observation
                             ) -> List[Candidate]:
        if success:
            if not self.cfg.get("necessity_test", True):
                return []                     # ablation: drop the success branch
            assumed = self.ccg.assumed_preconds(action)
            if self.mode == "freedo_oracle" and self.cfg.get("oracle_preconds"):
                assumed = self.cfg["oracle_preconds"]
            return candidates_from_success(
                action, assumed,
                skip_cids=self.ccg.decided_cids() | set(self.cands),
                trial_id=self.trial_id, step=self.step)
        obs = dict(observation or {"inventory": inventory})
        try:
            snap = self._snapshot(full=True)
            obs.setdefault("inventory", dict(inventory))
            obs.setdefault("held", snap.get("held.name"))
            if all(k in snap for k in ("agent.x", "agent.y", "agent.z")):
                obs.setdefault("position", {
                    "x": snap.get("agent.x"),
                    "y": snap.get("agent.y"),
                    "z": snap.get("agent.z"),
                })
            if "world.time_of_day" in snap:
                obs.setdefault("time_of_day", snap.get("world.time_of_day"))
        except Exception:  # noqa: BLE001
            obs.setdefault("inventory", dict(inventory))
        event = {"action": action,
                 "expected_effect": self.ccg.e_out.get(action, "expected output"),
                 "observation": obs}
        try:
            proposed = propose_from_failure(event, llm=self.llm,
                                            expand=self.cfg.get("neighbor_expand", True),
                                            trial_id=self.trial_id, step=self.step)
        except Exception as exc:  # noqa: BLE001 — proposer junk never kills the loop
            log_event("proposal", {"trigger": "failure", "error": str(exc)},
                      self.trial_id, self.step)
            proposed = []
        if proposed:
            return proposed
        return self._proposal_failure_fallback(action, obs)

    def _proposal_failure_fallback(self, action: str,
                                   observation: Optional[Dict[str, Any]]
                                   ) -> List[Candidate]:
        """Last-resort candidate coverage when the proposal service returns no
        legal candidates at all (for example repeated 5xx/proxy failures).

        This is narrower than NOTA reproposal: it does not run after rejected
        candidates, and it only uses the bounded signature enumeration already
        used by the closed-loop tail. The goal is operational robustness: a
        transient LLM outage should not turn a run into "18 proposals, 0
        interventions" when the typed signature can still provide verifiable
        fallback candidates.
        """
        if not self.cfg.get("proposal_failure_signature_fallback", True):
            return []
        try:
            from Adam.tcpg import reproposal as RP
            exclude = set(self.cands) | self.ccg.decided_cids()
            cands = RP.enumerate_signature_fallback(action, exclude, observation)
        except Exception as exc:  # noqa: BLE001
            log_event("proposal_fallback",
                      {"stage": "signature_fallback", "error": str(exc)},
                      self.trial_id, self.step)
            return []
        log_event("proposal_fallback",
                  {"stage": "signature_fallback", "n_new": len(cands),
                   "cids": [c.cid for c in cands]},
                  self.trial_id, self.step)
        return cands

    def _register(self, new: List[Candidate], inventory: Dict[str, int],
                  held: str = None, agent_y: float = None) -> None:
        state = {"inventory": dict(inventory)}
        if held is not None:
            state["held"] = held
        if agent_y is not None:
            state["agent_y"] = agent_y
        for c in new:
            if c.cid in self.cands or c.cid in self.ccg.decided_cids():
                continue
            k5 = C.compile(c, state)
            log_event("compile", {"cid": c.cid, "feasible": k5["feasible"],
                                  "est_steps": k5["est_steps"],
                                  "sim_verifiable": bool(k5.get("sim_verifiable")),
                                  "infeasible_reason": k5["infeasible_reason"]},
                      self.trial_id, self.step)
            if not k5["feasible"]:
                c.status = "observe_only"     # C3 runtime semantics
            self.cands[c.cid] = c
            self.pools[c.cid] = DualPool()
            self.k5[c.cid] = k5

    # ====================================================== natural routing
    def _route_natural(self, action: str, success: bool) -> None:
        active = [c for c in self.cands.values()
                  if c.action == action and c.status in ("undecided", "observe_only")]
        if not active:
            return
        try:
            from Adam.tcpg.predicates import eval_predicates
            core = [c.predicate() for c in active if c.origin == "core"]
            results = eval_predicates(self.env, core) if core else {}
        except Exception as exc:  # noqa: BLE001
            log_event("nat_obs", {"error": str(exc)}, self.trial_id, self.step)
            return
        for c in active:
            r = results.get(c.cid)
            if not r or not r["known"]:
                continue                       # unknown NEVER enters a pool
            side = "pos" if r["value"] == 1 else "neg"
            self.pools[c.cid].update(side, int(success))
            log_event("nat_obs", {"cid": c.cid, "side": side, "y": int(success)},
                      self.trial_id, self.step)

    # ===================================================== verification loop
    def _eligible(self, action: str) -> List[str]:
        accepted_floor = self._accepted_numeric_floor(action)
        provisional_floor = self._provisional_success_floor(action)
        out = []
        for cid, c in self.cands.items():
            if c.action != action or c.status != "undecided" or not self.k5[cid]["feasible"]:
                continue
            floor = accepted_floor.get(self._threshold_group_key(c))
            if floor is not None and self._is_stronger_numeric_threshold(c, floor):
                continue
            floor = provisional_floor.get(self._threshold_group_key(c))
            if floor is not None and self._is_stronger_numeric_threshold(c, floor):
                continue
            out.append(cid)
        return out

    def _schedulable(self, cid: Optional[str]) -> bool:
        if cid is None:
            return False
        max_fail = int(self.cfg.get("max_plan_failures_per_cand", 1))
        return self._plan_fail_counts.get(cid, 0) < max_fail

    def _select_special_candidate(self, action: str, elig: List[str],
                                  selector) -> Optional[str]:
        cid = selector(action, elig)
        if self._schedulable(cid):
            return cid
        return None

    def _state_set_candidate(self, action: str, elig: List[str],
                             costs: Dict[str, float]) -> Optional[str]:
        """Prefer command-set in-world numeric/state candidates before costly
        construction-style situational plans. These still retry the real action
        in Minecraft; only the contrast state is reached by command."""
        best: Optional[str] = None
        best_key = None
        for cid in elig:
            c = self.cands[cid]
            if c.target not in ("time_of_day", "y_level"):
                continue
            k5 = self.k5[cid]
            primitives = {p.get("primitive") for p in k5.get("plan_plus", [])}
            primitives |= {p.get("primitive") for p in k5.get("plan_minus", [])}
            if not primitives.intersection({"set_time", "set_y"}):
                continue
            pool = self.pools[cid]
            # Natural failures under the predicate are informative for these
            # situational candidates; choose the cheapest under-sampled state
            # boundary first.
            natural_fail = pool.n_neg - pool.k_neg
            rank = (pool.n_eff, -natural_fail, costs.get(cid, 0.0), cid)
            if best is None or rank < best_key:
                best, best_key = cid, rank
        return best

    def _active_inventory_frontier(self, action: str, elig: List[str]) -> Optional[str]:
        """Keep resource-count threshold search moving once it has started.

        In R2/craftFence the true cause is inventory_count(oak_planks). The LLM can
        also propose many y_level/time decoys; those are valid but slow. Once an
        exact set_count frontier for an inventory threshold has active evidence,
        finish that monotone family before exploring unrelated situational states.
        """
        best: Optional[Candidate] = None
        best_key = None
        for cid in elig:
            c = self.cands[cid]
            if c.target != "inventory_count" or c.comparator != ">=":
                continue
            k5 = self.k5.get(cid, {})
            primitives = {p.get("primitive") for p in k5.get("plan_plus", [])}
            primitives |= {p.get("primitive") for p in k5.get("plan_minus", [])}
            if "set_count" not in primitives:
                continue
            pool = self.pools.get(cid)
            if pool is None:
                continue
            rank_value = self._threshold_rank(c)
            if rank_value is None:
                continue
            # Natural failures under the predicate or prior active samples mean
            # this frontier is already relevant enough to finish locally.
            active = pool.n_eff > 0 or (pool.n_neg - pool.k_neg) > 0
            if not active:
                continue
            rank = (0 if pool.k_pos > 0 and pool.k_neg == 0 else 1,
                    0 if c.source == "frontier" else 1,
                    pool.n_eff, rank_value, cid)
            if best is None or rank < best_key:
                best, best_key = c, rank
        return best.cid if best is not None else None

    def _numeric_group_key(self, c: Candidate) -> Optional[Tuple[str, str, str, str]]:
        if c.target not in ("inventory_count", "y_level"):
            return None
        if c.comparator not in (">=", "<="):
            return None
        if not isinstance(c.value, (int, float)):
            return None
        return (c.action, c.target, str(c.property), c.comparator)

    def _ordered_group_key(self, c: Candidate) -> Optional[Tuple[str, str, str, str]]:
        if c.comparator not in (">=", "<="):
            return None
        prim = P.schema()["primitives"].get(c.target) or {}
        value_type = prim.get("value_type")
        if value_type not in P.schema().get("ordered_domains", {}):
            return None
        return (c.action, c.target, str(c.property), c.comparator)

    def _ordered_rank(self, c: Candidate) -> Optional[int]:
        prim = P.schema()["primitives"].get(c.target) or {}
        domain = P.schema().get("ordered_domains", {}).get(prim.get("value_type"))
        if not domain:
            return None
        try:
            return list(domain).index(str(c.value))
        except ValueError:
            return None

    def _threshold_group_key(self, c: Candidate) -> Optional[Tuple[str, str, str, str]]:
        return self._numeric_group_key(c) or self._ordered_group_key(c)

    def _threshold_rank(self, c: Candidate) -> Optional[float]:
        key = self._numeric_group_key(c)
        if key is not None:
            return float(c.value)
        rank = self._ordered_rank(c)
        return float(rank) if rank is not None else None

    def _is_stronger_numeric_threshold(self, c: Candidate, floor: float) -> bool:
        if c.comparator == ">=":
            rank = self._threshold_rank(c)
            return rank is not None and rank > float(floor)
        if c.comparator == "<=":
            rank = self._threshold_rank(c)
            return rank is not None and rank < float(floor)
        return False

    def _has_accepted_weaker_threshold(self, c: Candidate) -> bool:
        """True when a monotone family already has an accepted weaker/equal gate.

        For upward thresholds, accepting held_tool>=stone makes >=iron and
        >=diamond redundant: they may be sufficient but are not the discovered
        minimal gate. For downward thresholds, accepting y<=-10 makes y<=-11
        redundant for the same reason.
        """
        key = self._threshold_group_key(c)
        rank = self._threshold_rank(c)
        if key is None or rank is None:
            return False
        for other in self.cands.values():
            if other.cid == c.cid or other.action != c.action:
                continue
            if other.status != "accepted":
                continue
            if self._threshold_group_key(other) != key:
                continue
            other_rank = self._threshold_rank(other)
            if other_rank is None:
                continue
            if c.comparator == ">=" and other_rank <= rank:
                return True
            if c.comparator == "<=" and other_rank >= rank:
                return True
        return False

    def _accepted_numeric_floor(self, action: str) -> Dict[Tuple[str, str, str, str], float]:
        floors: Dict[Tuple[str, str, str, str], float] = {}
        for c in self.cands.values():
            if c.action != action or c.status != "accepted":
                continue
            key = self._threshold_group_key(c)
            if key is None:
                continue
            val = self._threshold_rank(c)
            if val is None:
                continue
            if key not in floors:
                floors[key] = val
            elif c.comparator == ">=":
                floors[key] = min(floors[key], val)
            else:
                floors[key] = max(floors[key], val)
        return floors

    def _provisional_success_floor(self, action: str) -> Dict[Tuple[str, str, str, str], float]:
        """Smallest threshold in each monotone family that has already shown a
        positive-side success under intervention, even if it has not yet reached
        n_min. Once a lower threshold has positive evidence, stronger siblings
        add no new boundary information until the lower bound is resolved."""
        floors: Dict[Tuple[str, str, str, str], float] = {}
        for cid, c in self.cands.items():
            if c.action != action:
                continue
            key = self._threshold_group_key(c)
            pool = self.pools.get(cid)
            if key is None or pool is None or pool.k_pos <= 0:
                continue
            val = self._threshold_rank(c)
            if val is None:
                continue
            if key not in floors:
                floors[key] = val
            elif c.comparator == ">=":
                floors[key] = min(floors[key], val)
            else:
                floors[key] = max(floors[key], val)
        return floors

    def _boundary_bridge(self, action: str, elig: List[str]) -> Optional[str]:
        """Prioritize the undecided threshold that lies strictly between a
        rejected and an accepted sibling in the same monotone family. This
        covers numeric thresholds and ordered enums such as held_tool tiers."""
        all_groups: Dict[Tuple[str, str, str, str], List[Candidate]] = {}
        by_group: Dict[Tuple[str, str, str, str], List[Candidate]] = {}
        for c in self.cands.values():
            if c.action != action:
                continue
            key = self._threshold_group_key(c)
            if key is not None:
                all_groups.setdefault(key, []).append(c)
        for cid in elig:
            c = self.cands[cid]
            key = self._threshold_group_key(c)
            if key is not None:
                by_group.setdefault(key, []).append(c)
        bridge: Optional[Candidate] = None
        for key, group in by_group.items():
            full_group = all_groups.get(key, group)
            accepted = sorted(r for c in full_group
                              for r in [self._threshold_rank(c)]
                              if r is not None and (
                                  c.status == "accepted"
                                  or self.pools.get(c.cid, DualPool()).k_pos > 0))
            rejected = sorted(r for c in full_group
                              for r in [self._threshold_rank(c)]
                              if r is not None and c.status == "rejected")
            undecided = sorted((r, c) for c in group if c.status == "undecided"
                               for r in [self._threshold_rank(c)] if r is not None)
            if not accepted or not rejected or not undecided:
                continue
            lo_rej = max(rejected)
            hi_acc = min(accepted)
            if hi_acc <= lo_rej:
                continue
            mids = [c for v, c in undecided if lo_rej < v < hi_acc]
            if not mids:
                continue
            cand = min(mids, key=lambda c: (
                abs(float(self._threshold_rank(c)) - (lo_rej + hi_acc) / 2.0),
                float(self._threshold_rank(c)), c.cid))
            if bridge is None or float(self._threshold_rank(cand)) < float(self._threshold_rank(bridge)):
                bridge = cand
        return bridge.cid if bridge is not None else None

    def _promising_numeric_boundary(self, action: str, elig: List[str]) -> Optional[str]:
        """Keep sampling a monotone boundary candidate once it has shown a
        positive-side success. A single positive contrast is not enough to pass
        the posterior gate, but it is enough to make stronger siblings
        redundant and to justify filling this candidate to n_min before testing
        unrelated numeric groups."""
        best: Optional[Candidate] = None
        best_key = None
        for cid in elig:
            c = self.cands[cid]
            key = self._threshold_group_key(c)
            pool = self.pools.get(cid)
            if key is None or pool is None or pool.k_pos <= 0:
                continue
            if self._has_accepted_weaker_threshold(c):
                continue
            rank_value = self._threshold_rank(c)
            if rank_value is None:
                continue
            # Prefer the most boundary-like surviving threshold in its monotone
            # direction, then the one with the least two-sided evidence.
            rank = (0 if pool.k_neg == 0 else 1,
                    rank_value if c.comparator == ">=" else -rank_value,
                    pool.n_eff < int(self.cfg["n_min"]),
                    pool.n_eff, cid)
            if best is None or rank < best_key:
                best, best_key = c, rank
        return best.cid if best is not None else None

    def _active_ordered_boundary(self, action: str, elig: List[str]) -> Optional[str]:
        """Actively contrast ordered-domain candidates after natural failures.

        Neighbour expansion can recover a missed tier threshold (for example
        held_tool>=stone from an LLM proposal of held_tool>=wooden). Natural
        failures under the recovered candidate are useful negative evidence, but
        the candidate still needs an active positive contrast. Prefer the lowest
        recovered tier with such evidence before open numeric frontiers consume
        the per-trigger budget.
        """
        best: Optional[Candidate] = None
        best_key = None
        for cid in elig:
            c = self.cands[cid]
            if self._ordered_group_key(c) is None:
                continue
            if self._has_accepted_weaker_threshold(c):
                continue
            pool = self.pools.get(cid)
            if pool is None:
                continue
            natural_fail = pool.n_neg - pool.k_neg
            if pool.n_eff > 0 or natural_fail <= 0:
                continue
            rank = self._threshold_rank(c)
            if rank is None:
                continue
            key = (pool.n_eff, -natural_fail, rank, cid)
            if best is None or key < best_key:
                best, best_key = c, key
        return best.cid if best is not None else None

    def _promising_positive_candidate(self, action: str, elig: List[str],
                                      costs: Optional[Dict[str, float]] = None
                                      ) -> Optional[str]:
        """Keep sampling a non-numeric candidate after it has a clean positive
        contrast and no negative-side successes. This lets external-context
        gates such as nearby_block(water) reach n_min instead of being starved
        by cheap but irrelevant frontier siblings."""
        best: Optional[Candidate] = None
        best_key = None
        for cid in elig:
            c = self.cands[cid]
            if self._threshold_group_key(c) is not None:
                continue
            pool = self.pools.get(cid)
            if pool is None or pool.k_pos <= 0 or pool.k_neg > 0:
                continue
            rank = (pool.n_eff, (costs or {}).get(cid, 0.0), cid)
            if best is None or rank < best_key:
                best, best_key = c, rank
        return best.cid if best is not None else None

    def _reproposal_probe_candidate(self, action: str, elig: List[str],
                                    costs: Optional[Dict[str, float]] = None
                                    ) -> Optional[str]:
        """Give NOTA-generated candidates one active contrast before older
        frontier siblings can dominate the budget."""
        best: Optional[str] = None
        best_key = None
        for cid in elig:
            if cid not in self._reproposal_cids:
                continue
            c = self.cands[cid]
            if c.action != action:
                continue
            pool = self.pools.get(cid)
            if pool is None or pool.n_eff > 0:
                continue
            rank = (0 if c.target == "nearby_block" else 1,
                    (costs or {}).get(cid, 0.0), cid)
            if best is None or rank < best_key:
                best, best_key = cid, rank
        return best

    def _numeric_overshoot_neighbor(self, action: str, elig: List[str]) -> Optional[str]:
        """If threshold n succeeds on both sides, n is stronger than needed for
        an up-monotone gate (or weaker than needed for down-monotone). Verify
        the adjacent weaker sibling next; it is the only candidate that can
        still be the boundary."""
        elig_set = set(elig)
        by_key: Dict[Tuple[str, str, str, str], Dict[float, Candidate]] = {}
        for c in self.cands.values():
            if c.action != action:
                continue
            key = self._threshold_group_key(c)
            rank = self._threshold_rank(c)
            if key is not None and rank is not None:
                by_key.setdefault(key, {})[rank] = c
        best: Optional[Candidate] = None
        best_key = None
        for cid, c in self.cands.items():
            if c.action != action:
                continue
            key = self._threshold_group_key(c)
            pool = self.pools.get(cid)
            if key is None or pool is None or pool.k_pos <= 0:
                continue
            if self._numeric_group_key(c) is not None and pool.k_neg <= 0:
                continue
            rank_value = self._threshold_rank(c)
            if rank_value is None:
                continue
            step = -1.0 if c.comparator == ">=" else 1.0
            neighbor = by_key.get(key, {}).get(rank_value + step)
            if neighbor is None or neighbor.cid not in elig_set:
                continue
            n_pool = self.pools.get(neighbor.cid)
            if self._ordered_group_key(c) is not None and n_pool is not None \
                    and (n_pool.k_pos > 0
                         or (n_pool.n_pos > 0 and n_pool.k_pos == 0)):
                continue
            n_eff = n_pool.n_eff if n_pool is not None else 0
            neighbor_rank = self._threshold_rank(neighbor)
            rank = (n_eff, neighbor_rank if neighbor_rank is not None else 0.0,
                    neighbor.cid)
            if best is None or rank < best_key:
                best, best_key = neighbor, rank
        return best.cid if best is not None else None

    def _costs(self) -> Dict[str, float]:
        """Per-candidate intervention cost for the acquisition denominator
        (paper 4.4 cost stratification).

        Resource-input candidates (sim_verifiable: inventory_count / held_tool /
        held_item) reach their contrast value by the agent's own take / craft /
        equip actions -- the embodied analogue of ADAM's controlled-config
        isolation. The paper costs this as "a small constant for the take/equip
        PLUS a recipe subplan when the contrast item must be synthesised". The
        compiler's est_steps already folds in that synthesis subplan, so
        max(sim_verify_cost, est_steps) is the constant when the item is on hand
        (est tiny) and the subplan cost when it must be crafted (est large) --
        e.g. testing held_tool>=diamond with no diamond pickaxe in inventory is
        genuinely expensive, which is exactly the case the table-6b ablation
        needs. Situational-constraint candidates (nearby_block / y_level /
        time_of_day / sky_exposed) keep full est_steps (real movement / waiting
        / placement). sim_cost_mode="flat" reproduces the round-2 flat constant
        for backward comparison."""
        sim_cost = float(self.cfg.get("sim_verify_cost", 2.0))
        sim_mode = str(self.cfg.get("sim_cost_mode", "floor"))
        out: Dict[str, float] = {}
        for cid in self.cands:
            est = float(self.k5[cid].get("est_steps", 0.0))
            if not self.k5[cid].get("sim_verifiable"):
                out[cid] = est                       # situational: real exploration
            elif sim_mode == "flat":
                out[cid] = sim_cost                  # round-2 legacy flat cost
            else:
                out[cid] = max(sim_cost, est)        # floor (paper 4.4)
        return out

    def _verification_loop(self, action: str, inventory: Dict[str, int],
                           anchor: Optional[Dict[str, Any]] = None) -> None:
        done = 0
        # per-trigger budget: cap total intervention step-cost this trigger so
        # a few cheap checks are preferred over one runaway expensive plan.
        budget = float(self.cfg.get("trigger_budget", 0.0))
        spent = 0.0
        # Cost stratification (paper 4.4) -- see _costs(). Emitted once per
        # trigger so the stage-3 audit can verify resource-input vs situational
        # cost layering directly from K7 (manual sec.3 / tables 6, 6b).
        costs = self._costs()
        log_event("cost_model", {
            "action": action,
            "alpha": self.acq.cost_alpha, "c0": self.acq.cost_c0,
            "sim_verify_cost": float(self.cfg.get("sim_verify_cost", 2.0)),
            "sim_cost_mode": str(self.cfg.get("sim_cost_mode", "floor")),
            "trigger_budget": budget,
            "costs": {cid: {
                "cost": round(costs[cid], 2),
                "est_steps": round(float(self.k5[cid].get("est_steps", 0.0)), 2),
                "sim_verifiable": bool(self.k5[cid].get("sim_verifiable"))}
                for cid in self.cands}},
            self.trial_id, self.step)
        # Per-candidate minimum-verification floor: guarantee every eligible
        # candidate (incl. an expensive true cause whose c(h) > budget, e.g.
        # held_tool>=stone with est_steps~110) at least this many active
        # verifications, regardless of cost-aware ranking, so a costly true
        # cause is not starved by cost ranking + aborts. 0 disables (default).
        min_floor = int(self.cfg.get("min_verifications_per_cand", 0))
        verified: Dict[str, int] = {}
        while (done < self.cfg["max_interventions_per_event"]
               and self.steps_used < self.cfg["step_budget"]):
            remaining = (budget - spent) if budget > 0 else None
            elig = self._eligible(action)
            sched_elig = [cid for cid in elig if self._schedulable(cid)]
            bridge = self._select_special_candidate(action, sched_elig,
                                                    self._boundary_bridge)
            promising = self._select_special_candidate(action, sched_elig,
                                                       self._promising_numeric_boundary)
            ordered_boundary = self._select_special_candidate(
                action, sched_elig, self._active_ordered_boundary)
            promising_positive = self._promising_positive_candidate(
                action, sched_elig, costs)
            if not self._schedulable(promising_positive):
                promising_positive = None
            reproposal_probe = self._reproposal_probe_candidate(
                action, sched_elig, costs)
            if not self._schedulable(reproposal_probe):
                reproposal_probe = None
            inventory_frontier = self._active_inventory_frontier(action, sched_elig)
            if not self._schedulable(inventory_frontier):
                inventory_frontier = None
            state_set = self._state_set_candidate(action, sched_elig, costs)
            if not self._schedulable(state_set):
                state_set = None
            overshoot = self._select_special_candidate(action, sched_elig,
                                                       self._numeric_overshoot_neighbor)
            # candidates still under the floor take priority and bypass the
            # budget cap (so an expensive true cause is not skipped); among them
            # pick the least-verified (deterministic round-robin).
            under = [cid for cid in sched_elig
                     if verified.get(cid, 0) < min_floor] if min_floor else []
            selected_by_acq = False
            if bridge is not None:
                cid = bridge
            elif promising is not None:
                cid = promising
            elif ordered_boundary is not None:
                cid = ordered_boundary
            elif promising_positive is not None:
                cid = promising_positive
            elif reproposal_probe is not None:
                cid = reproposal_probe
            elif inventory_frontier is not None:
                cid = inventory_frontier
            elif state_set is not None:
                cid = state_set
            elif overshoot is not None:
                cid = overshoot
            elif under:
                def floor_rank(cid_: str) -> tuple:
                    cand = self.cands[cid_]
                    pool = self.pools[cid_]
                    # Under the min-verification floor, prefer candidates that
                    # already have natural failure evidence and are cheap to
                    # actively contrast. Frontier numeric siblings are useful
                    # for threshold search, but should not starve a recalled
                    # low-cost neighbour such as held_tool>=stone in R5/R6.
                    reproposal_penalty = 0 if cid_ in self._reproposal_cids else 1
                    source_penalty = 1 if cand.source == "frontier" else 0
                    natural_fail = pool.n_neg - pool.k_neg
                    active_boundary = (
                        self._threshold_group_key(cand) is not None
                        and pool.k_pos > 0 and pool.k_neg == 0
                    )
                    return (0 if active_boundary else 1,
                            verified.get(cid_, 0),
                            reproposal_penalty,
                            -natural_fail,
                            source_penalty,
                            costs.get(cid_, 0.0),
                            cid_)
                cid = min(under, key=floor_rank)
            else:
                cid = self.acq.select(self.pools, sched_elig,
                                      costs=costs, budget=remaining)
                selected_by_acq = cid is not None
            if cid is None:
                return
            if not selected_by_acq:
                self.acq._picks += 1
                self.acq.counts[cid] = self.acq.counts.get(cid, 0) + 1
            c, pool, k5 = self.cands[cid], self.pools[cid], self.k5[cid]
            side = scarce_side(pool)
            plan = k5["plan_plus"] if side == "pos" else k5["plan_minus"]
            undo = k5["undo_plus"] if side == "pos" else k5["undo_minus"]
            undo_len = 0 if k5["irreversible"] else len(undo)
            if self.steps_used + len(plan) + 1 + undo_len > self.cfg["step_budget"]:
                return                          # cannot afford this intervention
            spent += costs.get(cid, 0.0)        # charge this intervention's cost
            verified[cid] = verified.get(cid, 0) + 1
            snap = self._snapshot()
            log_event("intervention_start",
                      {"cid": cid, "side": side, "plan_len": len(plan),
                       "cost": round(costs.get(cid, 0.0), 2),
                       "sim_verifiable": bool(k5.get("sim_verifiable")),
                       "ctx_snapshot": snap}, self.trial_id, self.step)
            if not self._do(plan, cid):
                # Forward plan partially executed before failing (e.g. placed a
                # chest, deposited a tool, then errored) -> genuine residual
                # state. Abort + reset anchor; do not verify next on pollution.
                self._plan_fail_counts[cid] = self._plan_fail_counts.get(cid, 0) + 1
                restored = self._reset_to_anchor(anchor)
                log_event("trigger_abort",
                          {"cid": cid, "reason": "plan_fail",
                           "anchor_restored": restored}, self.trial_id, self.step)
                return                          # plan failed -> abort + reset
            y2 = bool(self.execute_action(action))
            self.steps_used += 1
            pool.update(side, int(y2))
            log_event("retry", {"cid": cid, "action": action, "y": int(y2)},
                      self.trial_id, self.step)
            if y2 and anchor is not None:
                restored = self._reset_to_anchor(anchor)
                log_event("ctx_resync",
                          {"cid": cid, "reason": "post_success_retry",
                           "kept_obs": True, "restored": restored},
                          self.trial_id, self.step)
            undo_ok = self._do(undo, cid, undo=True) if not k5["irreversible"] else True
            ctx_ok = self._ctx_matches(snap, c)
            log_event("undo", {"cid": cid, "ok": undo_ok, "ctx_match": ctx_ok},
                      self.trial_id, self.step)
            if not undo_ok:
                # Undo GENUINELY failed: the environment is polluted (held=null,
                # inventory drained). Void this observation, reset to anchor and
                # abort the trigger so the next candidate is not verified on a
                # polluted context.
                pool.invalidate_last()
                restored = self._reset_to_anchor(anchor)
                log_event("trigger_abort",
                          {"cid": cid, "reason": "undo_fail",
                           "anchor_restored": restored}, self.trial_id, self.step)
                return
            if not ctx_ok:
                # Undo SUCCEEDED but the post-undo context differs from the
                # pre-intervention snapshot on a checked field (commonly
                # held.name after an equip/unequip undo). The observation y2 was
                # taken in the intended intervened state BEFORE undo, so it is
                # valid; the drift only threatens the NEXT observation. Restore
                # the comparable context via reset-to-anchor: if that succeeds we
                # KEEP the observation and CONTINUE (no wasted abort); only if
                # the context cannot be restored do we abort (true pollution).
                if self._reset_to_anchor(anchor):
                    log_event("ctx_resync",
                              {"cid": cid, "kept_obs": True, "restored": True},
                              self.trial_id, self.step)
                    # fall through: keep observation, update posterior, continue
                else:
                    pool.invalidate_last()
                    log_event("trigger_abort",
                              {"cid": cid, "reason": "ctx_unrestorable",
                               "anchor_restored": False}, self.trial_id, self.step)
                    return
            q, g = pool.stats(delta=self.cfg["delta"], M=self.cfg["M"],
                              seed=self.cfg["seed"])
            log_event("posterior_update",
                      {"cid": cid, "pools": pool.__dict__ | {}, "q_hat": round(q, 4),
                       "gamma_plus": round(g, 4)}, self.trial_id, self.step)
            decision = pool.decide(delta=self.cfg["delta"],
                                   tau_acc=self.cfg["tau_acc"],
                                   tau_rej=self.cfg["tau_rej"],
                                   n_min=self.cfg["n_min"], M=self.cfg["M"],
                                   seed=self.cfg["seed"],
                                   mode=self.cfg.get("posterior_mode", "dual"))
            if decision == "accepted":
                if self._has_accepted_weaker_threshold(c):
                    c.status, c.decided_step = "confirmed_known", self.step
                    log_event("writeback",
                              {"cid": c.cid, "decision": "confirmed_known",
                               "action": c.action, "target": c.target,
                               "value": c.value,
                               "reason": "redundant_stronger_threshold"},
                              self.trial_id, self.step)
                elif self.ccg.is_known_input_edge(c):
                    c.status, c.decided_step = "confirmed_known", self.step
                else:
                    c.status, c.decided_step = "accepted", self.step
                self.ccg.write_back(c, self.trial_id, self.step)
                return
            elif decision == "rejected":
                c.status, c.decided_step = "rejected", self.step
                self.ccg.reject(c, self.trial_id, self.step)
            done += 1
            if (self.cfg.get("nota_reproposal", False)
                    and self._early_low_signal_nota(action) is not None):
                return

    # ============================================ none-of-the-above reproposal
    def _failure_observation(self, action: str, inventory: Dict[str, int],
                             observation: Optional[Dict[str, Any]]
                             ) -> Dict[str, Any]:
        """Rebuild the failure observation fed to the (re)proposer -- mirrors the
        failure branch of _generate_candidates without mutating it."""
        obs = dict(observation or {"inventory": inventory})
        try:
            snap = self._snapshot(full=True)
            obs.setdefault("inventory", dict(inventory))
            obs.setdefault("held", snap.get("held.name"))
            if all(k in snap for k in ("agent.x", "agent.y", "agent.z")):
                obs.setdefault("position", {"x": snap.get("agent.x"),
                                            "y": snap.get("agent.y"),
                                            "z": snap.get("agent.z")})
            if "world.time_of_day" in snap:
                obs.setdefault("time_of_day", snap.get("world.time_of_day"))
            if "block_below.name" in snap:
                obs.setdefault("block_below", snap.get("block_below.name"))
        except Exception:  # noqa: BLE001
            obs.setdefault("inventory", dict(inventory))
        return obs

    def _maybe_repropose(self, action: str, inventory: Dict[str, int],
                         observation: Optional[Dict[str, Any]],
                         anchor: Optional[Dict[str, Any]]) -> None:
        """Closed-loop tail: when intervention has rejected every proposed
        candidate for `action` and none was accepted (a none-of-the-above
        misspecification signal), drive a second round -- counterexample-driven
        LLM reproposal first, signature enumeration fallback if that adds nothing
        new -- then verify the new candidates. Opt-in via cfg['nota_reproposal'];
        when off this is a no-op and the frozen loop is byte-identical."""
        if not self.cfg.get("nota_reproposal", False):
            return
        rounds = self._reproposal_rounds.get(action, 0)
        if rounds >= int(self.cfg.get("max_reproposal_rounds", 2)):
            return
        from Adam.tcpg import reproposal as RP
        signal = RP.detect_none_of_the_above(action, self.cands, self.ccg)
        if signal is None:
            signal = self._early_low_signal_nota(action)
        if signal is None:
            return
        self._reproposal_rounds[action] = rounds + 1
        log_event("nota", {"action": action, "round": rounds + 1,
                           "n_decided": signal["n_decided"],
                           "rejected_cids": signal["rejected_cids"],
                           "reason": signal["reason"]}, self.trial_id, self.step)
        obs = self._failure_observation(action, inventory, observation)
        rejected = [self.cands[c] for c in signal["rejected_cids"]
                    if c in self.cands]
        new = RP.repropose(
            action=action,
            expected_effect=self.ccg.e_out.get(action, "expected output"),
            observation=obs, rejected=rejected,
            exclude_cids=set(self.cands) | self.ccg.decided_cids(),
            llm=self.llm,
            use_signature_fallback=self.cfg.get(
                "reproposal_signature_fallback", True),
            trial_id=self.trial_id, step=self.step)
        if not new:
            return
        self._reproposal_cids.update(c.cid for c in new)
        full = anchor or self._snapshot(full=True)
        self._register(new, inventory, held=full.get("held.name"),
                       agent_y=full.get("agent.y"))
        self._route_natural(action, False)
        self._verification_loop(action, inventory, anchor=full)

    def _early_low_signal_nota(self, action: str) -> Optional[Dict[str, Any]]:
        """Detect a stuck misspecification before every candidate is exhausted.

        Strict NOTA waits until all proposed candidates are decided. In live
        runs, numeric frontiers for a wrong target can keep the set formally
        non-exhausted for many triggers even after interventions repeatedly show
        zero positive signal. Under the opt-in reproposal mode, emit a bounded
        early counterexample once enough candidates have been actively tested
        and no intervention sample has ever made the failed action succeed.
        If any candidate has produced a positive intervention-side success,
        boundary verification is making progress, so this stays silent.
        """
        if not self.cfg.get("nota_reproposal_early_low_signal", True):
            return None
        action_cands = [c for c in self.cands.values() if c.action == action]
        if not action_cands:
            return None
        if any(c.status == "accepted" for c in action_cands):
            return None
        if self._has_unexhausted_monotone_frontier(action_cands):
            return None
        rejected = [c for c in action_cands if c.status in ("rejected", "confirmed_known")]
        tested: List[Candidate] = []
        n_samples = 0
        for c in action_cands:
            pool = self.pools.get(c.cid)
            if pool is None:
                continue
            if pool.k_pos > 0 or pool.k_neg > 0:
                return None
            if pool.n_eff > 0:
                tested.append(c)
                n_samples += pool.n_pos + pool.n_neg
        min_cands = int(self.cfg.get("nota_low_signal_min_candidates", 4))
        min_samples = int(self.cfg.get("nota_low_signal_min_samples", 8))
        if not rejected and (len(tested) < min_cands or n_samples < min_samples):
            return None
        rejected_or_tested = rejected or tested
        return {
            "action": action,
            "rejected_cids": [c.cid for c in rejected_or_tested],
            "n_decided": len(rejected),
            "reason": "early_low_signal_none_accepted",
        }

    def _has_unexhausted_monotone_frontier(self, cands: List[Candidate]) -> bool:
        """Return True while a monotone numeric/order frontier can still climb.

        For threshold gates, zero positive samples below the boundary are expected;
        treating that as NOTA before the frontier has tried its stronger siblings
        skips the only path that can reach the real threshold.
        """
        tiers = ["wooden", "golden", "stone", "iron", "diamond", "netherite"]
        groups: Dict[tuple, List[tuple]] = {}
        for c in cands:
            if c.source != "frontier" or c.status != "undecided":
                continue
            if c.comparator not in (">=", "<="):
                continue
            key = (c.target, c.property, c.comparator)
            try:
                val = float(c.value)
            except (TypeError, ValueError):
                if c.target != "held_tool" or str(c.value) not in tiers:
                    continue
                val = float(tiers.index(str(c.value)))
            pool = self.pools.get(c.cid)
            groups.setdefault(key, []).append((val, c, pool))
        for (_target, _prop, comp), rows in groups.items():
            if len(rows) < 2:
                continue
            rows.sort(key=lambda x: x[0], reverse=(comp == "<="))
            for _val, c, pool in rows:
                if c.status != "undecided":
                    continue
                if pool is None or pool.n_eff == 0:
                    return True
                if pool.k_pos == 0 and pool.k_neg == 0:
                    return True
                # A positive sample means boundary verification is active; early
                # NOTA is already suppressed by the caller, so no need to inspect
                # farther siblings in this group.
                break
        return False

    # ------------------------------------------------------------- do / undo
    def _reset_to_anchor(self, anchor: Optional[Dict[str, Any]]) -> bool:
        """Restore the comparable context to the episode anchor after an
        intervention left residual state. Rebuilds reset options (inventory,
        held item, position) from the anchor snapshot. Returns False if no
        anchor is available or reset fails — caller still aborts the trigger.

        Note: this restores the *comparable context* needed for the next
        observation, not the irreversibly-spent interaction cost. Only
        recoverable dimensions (inventory contents, equipped tool, position)
        are reset; the cost already paid is not refunded."""
        if not anchor:
            return False
        try:
            self._clear_anchor_workspace(anchor)
            options: Dict[str, Any] = {"mode": "hard"}
            if isinstance(anchor.get("inventory"), dict):
                options["inventory"] = dict(anchor["inventory"])
            px, py, pz = (anchor.get("agent.x"), anchor.get("agent.y"),
                          anchor.get("agent.z"))
            if all(v is not None for v in (px, py, pz)):
                options["position"] = {"x": px, "y": py, "z": pz}
            held = anchor.get("held.name")
            if held:
                options["equipment"] = [held]
            self.env.reset(options=options)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _clear_anchor_workspace(self, anchor: Dict[str, Any]) -> None:
        """Remove helper blocks left by intervention primitives near anchor.

        The executor's fixed work positions are relative to the bot's floored
        anchor position: `placeItem(..., where="near")` uses +2, `on_last` uses
        +2/+1, and roof interventions use +0/+2. These blocks are transient
        intervention scaffolding, not observations. If they remain after a
        reset/resync, later interventions fail with "target block is chest" and
        freedo gates can stall. Keep the cleanup narrow and block-name scoped so
        staged ores at +2/+3 and authored environmental gates are not removed.
        """
        px, py, pz = (anchor.get("agent.x"), anchor.get("agent.y"),
                      anchor.get("agent.z"))
        if not all(isinstance(v, (int, float)) for v in (px, py, pz)):
            return
        x, y, z = int(float(px) // 1), int(float(py) // 1), int(float(pz) // 1)
        targets = [
            (x + 2, y, z),       # placeItem near / useChest work slot
            (x + 2, y + 1, z),   # placeItem on_last upper block
            (x, y + 2, z),       # sky cover
        ]
        helper_blocks = ("chest", "crafting_table", "furnace",
                         "dirt", "cobblestone")
        commands: List[str] = ["/gamerule doTileDrops false"]
        for tx, ty, tz in targets:
            for block in helper_blocks:
                commands.append(
                    f"/execute if block {tx} {ty} {tz} minecraft:{block} "
                    f"run setblock {tx} {ty} {tz} minecraft:air")
        commands.append("/gamerule doTileDrops true")
        code = "".join(
            f'bot.chat({json.dumps(c)});\nawait bot.waitForTicks(2);\n'
            for c in commands)
        try:
            self.env.step(code)
        except Exception:  # noqa: BLE001
            pass

    def _do(self, plan: List[Dict[str, Any]], cid: str, undo: bool = False) -> bool:
        if not plan:
            return True
        if self.mode == "freedo_oracle":
            return self._freedo(plan, cid)      # zero-cost simulator do
        from Adam.tcpg.executor import run_plan
        ok, _ = run_plan(self.env, plan, cid=cid, trial_id=self.trial_id,
                         step=self.step)
        self.steps_used += len(plan)
        return ok

    def _freedo(self, plan: List[Dict[str, Any]], cid: str) -> bool:
        """Oracle do: realize the plan's NET effect at ZERO interaction cost.
        Inventory/held/position -> reset options (no in-world execution).
        Time/sky -> game commands. Only genuinely unrealizable primitives fall
        through to real execution (should be empty for the suite's I+/I-).

        This is the key cost-down: round 1 executed moveTo/wait in-world via
        run_plan, which made freedo SLOWER than tcpg and timed out (X1 moveTo
        180s). Now position is set by reset and time by command, both instant."""
        inv_delta: Dict[str, int] = {}
        inv_set: Dict[str, int] = {}              # exact-set targets (set_count)
        equip: Optional[str] = None
        dy = 0.0                                  # net vertical move (moveTo y/dx)
        abs_y: Optional[float] = None
        commands: List[str] = []                  # /time, /setblock for sky
        passthrough: List[Dict[str, Any]] = []
        for call in plan:
            p, a = call["primitive"], call["args"]
            if p == "useChest":
                sgn = -1 if a["op"] == "deposit" else 1
                for it in a["items"]:
                    inv_delta[it["name"]] = inv_delta.get(it["name"], 0) + sgn * it["count"]
            elif p in ("mineBlock", "craftItem", "smeltItem") and "name" in a \
                    and not a.get("special"):
                inv_delta[a["name"]] = inv_delta.get(a["name"], 0) + a.get("count", 1)
            elif p == "set_count":               # boundary intervention: exact set
                inv_set[a["name"]] = max(0, int(a.get("count", 0)))
            elif p == "set_y":
                abs_y = float(a["y"])
            elif p == "set_time":
                commands.append(f"/time set {int(a['tick']) % 24000}")
            elif p == "equip":
                equip = a["name"]
            elif p == "moveTo":
                if "y" in a:
                    abs_y = float(a["y"])         # absolute target depth
                # dx-only moves (nearby_block I-) don't change the gated state
                # relevant to verification; ignore for oracle realization.
            elif p == "wait":
                # time-of-day target: pick a representative tick inside/outside
                # the window and set it directly (no waiting).
                if "until_in" in a:
                    lo, hi = a["until_in"]
                    commands.append(f"/time set {int((lo + hi) // 2)}")
                elif "until_out" in a:
                    lo, hi = a["until_out"]
                    out_tick = (hi + 2000) % 24000  # just outside the window
                    commands.append(f"/time set {int(out_tick)}")
            elif p == "placeItem" and a.get("where") == "roof":
                commands.append("__SKY_COVER__")   # realized below via setblock
            elif p == "mineBlock" and a.get("special") == "roof_column":
                commands.append("__SKY_OPEN__")
            else:
                passthrough.append(call)
        try:
            snap = self._snapshot(full=True)
            inv = dict(snap.get("inventory", {}))
            for k, v in inv_delta.items():
                inv[k] = max(0, inv.get(k, 0) + v)
            for k, v in inv_set.items():          # exact-set overrides any delta
                inv[k] = max(0, int(v))
            options = {"mode": "hard", "inventory": inv}
            # position: apply absolute y target (X1) or keep current
            px = snap.get("agent.x")
            pz = snap.get("agent.z")
            py = abs_y if abs_y is not None else snap.get("agent.y")
            if all(v is not None for v in (px, py, pz)):
                options["position"] = {"x": px, "y": py, "z": pz}
            if equip:
                options["equipment"] = [equip]
            self.env.reset(options=options)
            # realize time/sky via commands (instant, no waiting/digging)
            self._freedo_commands(commands)
            if passthrough:                        # should be empty for suite I+/I-
                from Adam.tcpg.executor import run_plan
                ok, _ = run_plan(self.env, passthrough, cid=cid,
                                 trial_id=self.trial_id, step=self.step)
                return ok
            return True
        except Exception:  # noqa: BLE001
            return False

    def _freedo_commands(self, commands: List[str]) -> None:
        """Issue /time and sky-cover commands via a single env.step (chat),
        without counting interaction cost."""
        if not commands:
            return
        chats: List[str] = []
        for c in commands:
            if c == "__SKY_COVER__":
                chats.append("/setblock ~ ~2 ~ minecraft:dirt")
            elif c == "__SKY_OPEN__":
                chats.append("/setblock ~ ~2 ~ minecraft:air")
            else:
                chats.append(c)
        code = "".join(
            f'bot.chat({json.dumps(c)});\nawait bot.waitForTicks(10);\n'
            for c in chats)
        try:
            self.env.step(code)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- context Ct
    def _snapshot(self, full: bool = False) -> Dict[str, Any]:
        try:
            from Adam.tcpg.predicates import state_snapshot
            s = state_snapshot(self.env)
        except Exception:  # noqa: BLE001
            return {}
        if full:
            return s
        out = {k: s.get(k) for k in CTX_KEYS}
        out["inventory_digest"] = _digest(s.get("inventory", {}))
        out["_full"] = s                          # for gate-field ctx checking
        return out

    def _ctx_matches(self, snap: Dict[str, Any], cand=None) -> bool:
        if not snap:
            return True                         # no snapshot -> cannot falsify
        now = self._snapshot(full=True)
        snap_full = snap.get("_full", snap)
        if snap.get("held.name") != now.get("held.name"):
            return False
        y0, y1 = snap.get("agent.y"), now.get("agent.y")
        if isinstance(y0, (int, float)) and isinstance(y1, (int, float)) \
                and abs(y0 - y1) > 3.0:
            return False
        # gate-relevant field: a candidate gating on depth/time/sky must have its
        # gated dimension RESTORED after undo, else the next natural observation
        # is taken under a drifted context (this tightens the C-suite negative
        # pool, which was polluted by residual context drift).
        if cand is not None:
            field = self._gate_field(cand)
            if field and field in snap_full and field in now:
                v0, v1 = snap_full[field], now[field]
                if isinstance(v0, (int, float)) and isinstance(v1, (int, float)):
                    if abs(v0 - v1) > self._gate_tol(cand):
                        return False
                elif v0 != v1:
                    return False
        return True

    @staticmethod
    def _gate_field(cand) -> Optional[str]:
        """Snapshot field that the candidate's gate reads, for ctx restoration."""
        return {"y_level": "agent.y", "time_of_day": "world.time_of_day",
                "sky_exposed": "sky_exposed",
                "held_tool": "held.name", "held_item": "held.name"
                }.get(cand.target)

    @staticmethod
    def _gate_tol(cand) -> float:
        return 3.0 if cand.target == "y_level" else (
            2000.0 if cand.target == "time_of_day" else 0.0)


def _digest(inv: Dict[str, int]) -> str:
    return hashlib.sha1(json.dumps(inv, sort_keys=True).encode()).hexdigest()[:10]
