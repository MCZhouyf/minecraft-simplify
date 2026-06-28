"""OpenAI-compatible proposer adapter for the v2 downstream IaP loop."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

from iap_downstream.causal_graph import State
from iap_downstream.proposer import Candidate, Proposer


def _resource_prior(action: str) -> List[Candidate]:
    """Deterministic resource-first fallback matching the paper's setup."""
    if action == "craftBoat":
        return [
            Candidate(action, "extra_planks", var="extra_planks", comparator=">=",
                      true_set={"extra_planks": 9}, false_set={"extra_planks": 0},
                      probe_values=(1, 3, 9), achiever="craftPlanks", cost=1.0),
            Candidate(action, "crafting_table", var="has_table", comparator=">=",
                      true_set={"has_table": 1}, false_set={"has_table": 0},
                      probe_values=(1,), achiever="craftCraftingTable", cost=1.0),
        ]
    if action == "smeltRawIron":
        return [
            Candidate(action, "more_raw_iron", var="raw_iron_count", comparator=">=",
                      true_set={"raw_iron_count": 2}, false_set={"raw_iron_count": 0},
                      probe_values=(1, 2), achiever="mineIronOre", cost=1.0),
            Candidate(action, "more_coal", var="coal_count", comparator=">=",
                      true_set={"coal_count": 2}, false_set={"coal_count": 0},
                      probe_values=(1, 2), achiever="gatherCoalOre", cost=1.0),
        ]
    if action == "mineDiamondOre":
        return [
            Candidate(action, "better_pickaxe", var="pickaxe_tier", comparator=">=",
                      true_set={"pickaxe_tier": 3}, false_set={"pickaxe_tier": 0},
                      probe_values=(0, 1, 2, 3), achiever="equip_tool", cost=1.0),
        ]
    return []


class LLMProposer(Proposer):
    def __init__(self, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, timeout: int = 60,
                 allow_fallback: bool = True):
        self.model = model or os.environ.get("IAP_LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-5.1"
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.timeout = timeout
        self.allow_fallback = allow_fallback

    def propose(self, action: str, observable: State) -> List[Candidate]:
        if not self.api_key:
            return _resource_prior(action) if self.allow_fallback else []
        try:
            payload = self._request(action, observable)
            cands = self._parse(action, payload)
            return cands or (_resource_prior(action) if self.allow_fallback else [])
        except Exception:
            if self.allow_fallback:
                return _resource_prior(action)
            raise

    def _request(self, action: str, observable: State) -> str:
        url = self.base_url
        if not url.endswith("/chat/completions"):
            url = url.rstrip("/") + "/chat/completions"
        prompt = (
            "You propose resource/capability preconditions for a failed Minecraft action. "
            "Return JSON array only. Each item keys: label,var,comparator,true,false,values,achiever,cost. "
            "Prefer concrete resource/tool hypotheses; do not explain.\n"
            f"failed_action={action}\n"
            f"observable_atoms={sorted(observable.atoms)}\n"
            f"observable_nums={observable.nums}\n"
        )
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    def _parse(self, action: str, text: str) -> List[Candidate]:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        raw = json.loads(text)
        if not isinstance(raw, list):
            return []
        out: List[Candidate] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            var = str(item.get("var") or item.get("label") or "")
            if not var:
                continue
            true_set = item.get("true_set") or item.get("true") or {}
            false_set = item.get("false_set") or item.get("false") or {}
            values = item.get("probe_values") or item.get("values") or ()
            out.append(Candidate(
                action=action,
                label=str(item.get("label") or var),
                kind=str(item.get("kind") or "num"),
                var=var,
                comparator=str(item.get("comparator") or ">="),
                true_set={str(k): float(v) for k, v in dict(true_set).items()},
                false_set={str(k): float(v) for k, v in dict(false_set).items()},
                probe_values=tuple(float(v) for v in values),
                achiever=str(item.get("achiever") or ""),
                cost=float(item.get("cost", 1.0)),
                source="llm",
            ))
        return out

