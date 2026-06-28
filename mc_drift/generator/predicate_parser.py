"""Predicate parser for MC-Drift Phase 0-2.

This parser is intentionally small and explicit. It validates the predicate
forms used by the current U00-U30 manifest and returns a normalized dict that
downstream datapack/Fabric builders can consume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict


class PredicateParseError(ValueError):
    pass


_PATTERNS = {
    "inventory_count": re.compile(
        r"^inventory_count\((?P<item>[a-z0-9_]+)\)\s*>=\s*(?P<count>[0-9]+)$"
    ),
    "nearby_block": re.compile(
        r"^nearby_block\((?P<block>[a-z0-9_]+)\)\s*<=k\s*(?P<radius>[0-9]+)$"
    ),
    "nearby_entity": re.compile(
        r"^nearby_entity\((?P<entity>[a-z0-9_]+)\)\s*<=k\s*(?P<radius>[0-9]+)$"
    ),
    "held_item": re.compile(
        r"^held_item\(type\)\s*=\s*(?P<item>[a-z0-9_]+)$"
    ),
    "held_tool": re.compile(
        r"^held_tool\(tier\)\s*>=\s*(?P<tier>[a-z0-9_]+)$"
    ),
    "y_level": re.compile(
        r"^y_level\(y\)\s*<=\s*(?P<y>-?[0-9]+)$"
    ),
    "time_of_day": re.compile(
        r"^time_of_day\(time\)\s+in\s+\[(?P<start>[0-9]+),\s*(?P<end>[0-9]+)\]$"
    ),
    "station_base_block": re.compile(
        r"^station_base_block\(type\)\s*=\s*(?P<block>[a-z0-9_]+)$"
    ),
}


def parse_predicate(predicate: str) -> Dict[str, Any]:
    """Parse a supported predicate string into a normalized dictionary."""
    normalized = " ".join(predicate.strip().split())
    for kind, pattern in _PATTERNS.items():
        match = pattern.match(normalized)
        if not match:
            continue
        data = match.groupdict()
        out: Dict[str, Any] = {"type": kind, "raw": predicate}
        for key, value in data.items():
            if key in {"count", "radius", "y", "start", "end"}:
                out[key] = int(value)
            else:
                out[key] = value
        return out
    raise PredicateParseError(f"Unsupported predicate syntax: {predicate!r}")


def predicate_kind(predicate: str) -> str:
    return parse_predicate(predicate)["type"]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("predicate")
    args = parser.parse_args()
    print(parse_predicate(args.predicate))
