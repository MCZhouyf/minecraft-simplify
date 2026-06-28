"""Minimal K7 event logger (stage 4). Full runner integration arrives in stage 7.

Events are appended as JSONL to the path in $IAP_K7_LOG; when unset this is a
no-op, so library code can log unconditionally."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_FIELDS = ("trial_id", "step")


def log_event(type_: str, payload: dict, trial_id: str = "-", step: int = -1) -> None:
    path = os.environ.get("IAP_K7_LOG")
    if not path:
        return
    rec = {"ts": time.time(), "trial_id": trial_id, "step": step,
           "type": type_, "payload": payload}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
