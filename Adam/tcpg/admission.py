"""Stage 4: controlled signature extension — primitive admission (paper App. B.6).

When proposals for an out-of-whitelist (target, property) keep getting rejected
(>= k_trigger times, tracked by record_rejection), the LLM is asked to write an
evaluator for that property in the restricted query language Q. The expression
goes through a STATIC check (grammar + field whitelist + typing) and a DYNAMIC
check (>= n recorded snapshots: boolean, deterministic, non-constant), is
admitted as observe-only (contract K9 in schema_runtime.json), and is upgraded
to intervenable iff its main field maps to an existing macro family.

Guarantees preserved by construction: Q is total/deterministic/boolean (C4),
admission is rate-limited (|H| stays finite for Theorem 2), and admission only
extends the hypothesis language — write-back still requires do-evidence.

Q grammar (tokens are whitespace-separated where ambiguous):
  expr  := bexpr
  bexpr := comp | bexpr 'and' bexpr | bexpr 'or' bexpr | 'not' bexpr | '(' bexpr ')'
  comp  := nexpr op nexpr | field '==' literal | field 'in' '{' lit (',' lit)* '}'
  nexpr := field | NUMBER | nexpr '+' nexpr | nexpr '-' nexpr
           | 'abs' '(' nexpr ')' | 'count' '(' field ')'
  field := dotted name from the observation interface, or inventory[item_name]
  op    := '>=' | '<=' | '>' | '<'
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from Adam.tcpg.eventlog import log_event

TCPG_DIR = Path(__file__).resolve().parent
REPO_ROOT = TCPG_DIR.parent.parent
STATE_FIELDS_PATH = TCPG_DIR / "state_fields.json"
RUNTIME_SCHEMA_PATH = TCPG_DIR / "schema_runtime.json"
REJECTS_PATH = TCPG_DIR / "admission_rejects.json"
ADMISSION_PROMPT_PATH = REPO_ROOT / "prompts" / "admission_prompt.txt"

DEFAULTS = {"k_trigger": 3, "per_episode_cap": 1, "global_cap": 8,
            "min_snapshots": 50, "min_known_ratio": 0.8}

# fields whose Q expressions can be compiled to existing I± macro families
# (stage-5 compiler owns the authoritative matching; this map mirrors it)
FIELD_FAMILY = {
    "agent.y": "y_level",
    "world.time_of_day": "time_of_day",
    "held.name": "held_item",
    "held.tier": "held_tool",
    "inventory": "inventory_count",
    "block_below.name": "block_below",
    "sky_exposed": "sky_exposed",
}


# =============================================================== tokenizer/parser
_TOKEN_RE = re.compile(r"""
    (?P<NUMBER>-?\d+(\.\d+)?)
  | (?P<OP>>=|<=|==|>|<)
  | (?P<LPAR>\() | (?P<RPAR>\))
  | (?P<LBRACE>\{) | (?P<RBRACE>\})
  | (?P<LBRACK>\[) | (?P<RBRACK>\])
  | (?P<COMMA>,)
  | (?P<PLUS>\+) | (?P<MINUS>-)
  | (?P<NAME>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<WS>\s+)
""", re.VERBOSE)

KEYWORDS = {"and", "or", "not", "in", "abs", "count", "true", "false"}


class QSyntaxError(ValueError):
    pass


def _tokenize(src: str) -> List[Tuple[str, str]]:
    out, i = [], 0
    while i < len(src):
        m = _TOKEN_RE.match(src, i)
        if not m:
            raise QSyntaxError(f"illegal character at {i}: {src[i:i+8]!r}")
        i = m.end()
        kind = m.lastgroup
        if kind == "WS":
            continue
        out.append((kind, m.group()))
    out.append(("EOF", ""))
    return out


class QParser:
    """Recursive-descent parser -> tuple AST.

    AST node shapes:
      ("and"|"or", l, r) ("not", e)
      ("cmp", op, l, r)             op in >= <= > <
      ("eq", field_node, literal)   ("in", field_node, [literals])
      ("num", float) ("add"|"sub", l, r) ("abs", e) ("count", field_node)
      ("field", base, index|None)   e.g. ("field","agent.y",None), ("field","inventory","coal")
    """

    def __init__(self, src: str):
        if not src or not src.strip():
            raise QSyntaxError("empty expression")
        self.toks = _tokenize(src)
        self.pos = 0

    def _peek(self):
        return self.toks[self.pos]

    def _next(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def _expect(self, kind, text=None):
        k, v = self._next()
        if k != kind or (text is not None and v != text):
            raise QSyntaxError(f"expected {text or kind}, got {v!r}")
        return v

    def parse(self):
        ast = self._bexpr()
        if self._peek()[0] != "EOF":
            raise QSyntaxError(f"trailing tokens: {self._peek()[1]!r}")
        return ast

    # bexpr := and_expr ('or' and_expr)*
    def _bexpr(self):
        node = self._and_expr()
        while self._peek() == ("NAME", "or"):
            self._next()
            node = ("or", node, self._and_expr())
        return node

    def _and_expr(self):
        node = self._unary()
        while self._peek() == ("NAME", "and"):
            self._next()
            node = ("and", node, self._unary())
        return node

    def _unary(self):
        if self._peek() == ("NAME", "not"):
            self._next()
            return ("not", self._unary())
        if self._peek()[0] == "LPAR":
            save = self.pos
            self._next()
            try:                       # try boolean parenthesization first
                inner = self._bexpr()
                self._expect("RPAR")
                return inner
            except QSyntaxError:
                self.pos = save        # fall back: numeric paren inside comparison
        return self._comparison()

    def _comparison(self):
        left = self._nexpr()
        k, v = self._peek()
        if k == "OP" and v in (">=", "<=", ">", "<"):
            self._next()
            return ("cmp", v, left, self._nexpr())
        if k == "OP" and v == "==":
            self._next()
            if left[0] != "field":
                raise QSyntaxError("'==' left side must be a field")
            return ("eq", left, self._literal())
        if (k, v) == ("NAME", "in"):
            self._next()
            if left[0] != "field":
                raise QSyntaxError("'in' left side must be a field")
            self._expect("LBRACE")
            lits = [self._literal()]
            while self._peek()[0] == "COMMA":
                self._next()
                lits.append(self._literal())
            self._expect("RBRACE")
            return ("in", left, lits)
        raise QSyntaxError(f"expected comparator after expression, got {v!r}")

    def _literal(self):
        k, v = self._next()
        if k == "NUMBER":
            return float(v)
        if k == "NAME":
            if v == "true":
                return True
            if v == "false":
                return False
            return v
        raise QSyntaxError(f"expected literal, got {v!r}")

    # nexpr := term (('+'|'-') term)*
    def _nexpr(self):
        node = self._term()
        while self._peek()[0] in ("PLUS", "MINUS"):
            k, _ = self._next()
            node = ("add" if k == "PLUS" else "sub", node, self._term())
        return node

    def _term(self):
        k, v = self._peek()
        if k == "NUMBER":
            self._next()
            return ("num", float(v))
        if (k, v) == ("NAME", "abs"):
            self._next()
            self._expect("LPAR")
            inner = self._nexpr()
            self._expect("RPAR")
            return ("abs", inner)
        if (k, v) == ("NAME", "count"):
            self._next()
            self._expect("LPAR")
            f = self._fieldref()
            self._expect("RPAR")
            return ("count", f)
        if k == "LPAR":
            self._next()
            inner = self._nexpr()
            self._expect("RPAR")
            return inner
        if k == "NAME":
            return self._fieldref()
        raise QSyntaxError(f"expected numeric term, got {v!r}")

    def _fieldref(self):
        k, v = self._next()
        if k != "NAME" or v in KEYWORDS:
            raise QSyntaxError(f"expected field name, got {v!r}")
        index = None
        if self._peek()[0] == "LBRACK":
            self._next()
            ik, iv = self._next()
            if ik != "NAME":
                raise QSyntaxError("index must be an item name")
            index = iv
            self._expect("RBRACK")
        return ("field", v, index)


def parse(src: str):
    return QParser(src).parse()


def fields_used(ast) -> Set[str]:
    out: Set[str] = set()

    def walk(n):
        if not isinstance(n, tuple):
            return
        if n[0] == "field":
            out.add(n[1])
            return
        for child in n[1:]:
            if isinstance(child, tuple):
                walk(child)
            elif isinstance(child, list):
                for c in child:
                    walk(c) if isinstance(c, tuple) else None
    walk(ast)
    return out


# =============================================================== static check
def load_state_fields(path: Path = STATE_FIELDS_PATH) -> Dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


def static_check(ast, state_fields: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    sf = state_fields or load_state_fields()

    def type_of(node) -> str:                      # 'num' | 'bool' | 'str'
        kind = node[0]
        if kind == "count":
            inner = node[1]
            if inner[0] != "field" or type_of(inner) not in ("map", "num"):
                raise QSyntaxError("count(...) expects a map field")
            return "num"
        if kind in ("num", "add", "sub", "abs"):
            for child in node[1:]:
                if isinstance(child, tuple):
                    t = type_of(child)
                    if t != "num":
                        raise QSyntaxError(f"numeric op over non-numeric {child}")
            return "num"
        if kind == "field":
            base, index = node[1], node[2]
            if base not in sf:
                raise QSyntaxError(f"field '{base}' not in observation interface")
            ftype = sf[base]
            if index is not None:
                if ftype != "map":
                    raise QSyntaxError(f"'{base}' is not indexable")
                return "num"                       # map[item] -> count
            return {"number": "num", "bool": "bool", "string": "str",
                    "map": "map"}[ftype]
        if kind == "cmp":
            for side in (node[2], node[3]):
                if type_of(side) != "num":
                    raise QSyntaxError("comparison sides must be numeric")
            return "bool"
        if kind == "eq":
            ftype = type_of(node[1])
            lit = node[2]
            okmap = {"str": str, "bool": bool, "num": float}
            if ftype == "map":
                raise QSyntaxError("'==' over a map field; index it instead")
            if not isinstance(lit, okmap[ftype]) or (
                    ftype != "bool" and isinstance(lit, bool)):
                raise QSyntaxError(f"'==' literal type mismatch for {ftype} field")
            return "bool"
        if kind == "in":
            if type_of(node[1]) != "str":
                raise QSyntaxError("'in' requires a string field")
            if not all(isinstance(x, str) for x in node[2]):
                raise QSyntaxError("'in' set must contain names")
            return "bool"
        if kind in ("and", "or"):
            if type_of(node[1]) != "bool" or type_of(node[2]) != "bool":
                raise QSyntaxError(f"'{kind}' operands must be boolean")
            return "bool"
        if kind == "not":
            if type_of(node[1]) != "bool":
                raise QSyntaxError("'not' operand must be boolean")
            return "bool"
        raise QSyntaxError(f"unknown node {kind}")

    try:
        root = type_of(ast)
    except QSyntaxError as exc:
        return False, str(exc)
    if root != "bool":
        return False, f"root of expression is {root}, must be boolean"
    return True, ""


# =============================================================== evaluation
def evaluate(ast, snapshot: Dict[str, Any]) -> Tuple[Optional[bool], bool]:
    """-> (value, known). Unknown propagates, except and/or short-circuits."""
    UNKNOWN = object()

    def ev(n):
        kind = n[0]
        if kind == "num":
            return n[1]
        if kind == "field":
            base, index = n[1], n[2]
            val = snapshot.get(base)
            if index is not None:
                if not isinstance(val, dict):
                    return UNKNOWN
                return float(val.get(index, 0))    # map present -> absence is 0
            return UNKNOWN if val is None else val
        if kind in ("add", "sub"):
            a, b = ev(n[1]), ev(n[2])
            if UNKNOWN in (a, b):
                return UNKNOWN
            return a + b if kind == "add" else a - b
        if kind == "abs":
            a = ev(n[1])
            return UNKNOWN if a is UNKNOWN else abs(a)
        if kind == "count":
            v = ev(("field", n[1][1], None)) if n[1][2] is None else ev(n[1])
            if isinstance(v, dict):
                return float(len(v))
            return UNKNOWN if v is UNKNOWN else v
        if kind == "cmp":
            a, b = ev(n[2]), ev(n[3])
            if UNKNOWN in (a, b):
                return UNKNOWN
            return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[n[1]]
        if kind == "eq":
            a = ev(n[1])
            return UNKNOWN if a is UNKNOWN else a == n[2]
        if kind == "in":
            a = ev(n[1])
            return UNKNOWN if a is UNKNOWN else a in n[2]
        if kind == "and":
            a = ev(n[1])
            if a is False:
                return False
            b = ev(n[2])
            if b is False:
                return False
            if UNKNOWN in (a, b):
                return UNKNOWN
            return True
        if kind == "or":
            a = ev(n[1])
            if a is True:
                return True
            b = ev(n[2])
            if b is True:
                return True
            if UNKNOWN in (a, b):
                return UNKNOWN
            return False
        if kind == "not":
            a = ev(n[1])
            return UNKNOWN if a is UNKNOWN else (not a)
        raise ValueError(f"bad node {kind}")

    v = ev(ast)
    if v is UNKNOWN:
        return None, False
    return bool(v), True


def dynamic_check(ast, snapshots: List[Dict[str, Any]],
                  min_known_ratio: float = DEFAULTS["min_known_ratio"]
                  ) -> Tuple[bool, Dict[str, Any]]:
    n = len(snapshots)
    known_vals: List[bool] = []
    for s in snapshots:
        v1, k1 = evaluate(ast, s)
        v2, k2 = evaluate(ast, s)
        if (v1, k1) != (v2, k2):
            return False, {"reason": "non-deterministic", "n": n}
        if k1:
            known_vals.append(v1)
    stats = {"n": n, "known": len(known_vals),
             "distinct": len(set(known_vals)),
             "known_ratio": (len(known_vals) / n) if n else 0.0}
    if n == 0 or stats["known_ratio"] < min_known_ratio:
        stats["reason"] = "too many unknown evaluations"
        return False, stats
    if stats["distinct"] < 2:
        stats["reason"] = "constant over recorded snapshots"
        return False, stats
    stats["reason"] = ""
    return True, stats


# =============================================================== admission flow
def record_rejection(target: str, prop: str) -> int:
    key = f"{target}|{prop}"
    doc = {}
    if REJECTS_PATH.exists():
        try:
            doc = json.loads(REJECTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            doc = {}
    doc[key] = int(doc.get(key, 0)) + 1
    REJECTS_PATH.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc[key]


def rejection_count(target: str, prop: str) -> int:
    if not REJECTS_PATH.exists():
        return 0
    try:
        return int(json.loads(REJECTS_PATH.read_text(encoding="utf-8"))
                   .get(f"{target}|{prop}", 0))
    except Exception:
        return 0


def load_runtime() -> Dict[str, Any]:
    if RUNTIME_SCHEMA_PATH.exists():
        return json.loads(RUNTIME_SCHEMA_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "admitted": []}


def save_runtime(doc: Dict[str, Any]) -> None:
    RUNTIME_SCHEMA_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                                   encoding="utf-8")


def match_macro_family(used_fields: Set[str]) -> Optional[str]:
    fams = {FIELD_FAMILY[f] for f in used_fields if f in FIELD_FAMILY}
    return fams.pop() if len(fams) == 1 else None


def propose_primitive(target: str, prop: str, context: str,
                      llm: Callable[[str], str],
                      state_fields: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    sf = state_fields or load_state_fields()
    fields_doc = "\n".join(f"  {k}: {v}" for k, v in sorted(sf.items()))
    prompt = ADMISSION_PROMPT_PATH.read_text(encoding="utf-8").format(
        target=target, property=prop, context=context, fields=fields_doc)
    reply = re.sub(r"```[a-zA-Z]*\n?|```", "", llm(prompt) or "")
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("admission LLM reply contains no JSON object")
    obj = json.loads(reply[start:end + 1])
    for k in ("property_name", "expr", "gloss"):
        if k not in obj or not str(obj[k]).strip():
            raise ValueError(f"admission reply missing '{k}'")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", obj["property_name"]):
        raise ValueError(f"bad property_name {obj['property_name']!r}")
    return obj


def try_admit(target: str, prop: str,
              snapshots: List[Dict[str, Any]],
              llm: Callable[[str], str],
              config: Optional[Dict[str, Any]] = None,
              episode_admitted: int = 0,
              context: str = "",
              trial_id: str = "-", step: int = -1) -> Tuple[Optional[Dict], str]:
    """Full pipeline; returns (K9 record, "") on success or (None, reason)."""
    cfg = {**DEFAULTS, **(config or {})}
    rc = rejection_count(target, prop)
    if rc < cfg["k_trigger"]:
        return None, f"trigger not met ({rc}/{cfg['k_trigger']})"
    if episode_admitted >= cfg["per_episode_cap"]:
        return None, "per-episode admission cap reached"
    runtime = load_runtime()
    if len(runtime["admitted"]) >= cfg["global_cap"]:
        return None, "global admission cap reached"
    if len(snapshots) < cfg["min_snapshots"]:
        return None, f"need >={cfg['min_snapshots']} snapshots, have {len(snapshots)}"
    log_event("admission_trigger", {"target": target, "property": prop,
                                    "reject_count": rc}, trial_id, step)
    try:
        obj = propose_primitive(target, prop, context, llm)
        ast = parse(obj["expr"])
        ok_s, why_s = static_check(ast)
        if not ok_s:
            log_event("admission_check", {"property_name": obj["property_name"],
                                          "expr": obj["expr"], "static_ok": False,
                                          "reason": why_s}, trial_id, step)
            return None, f"static check failed: {why_s}"
        ok_d, stats = dynamic_check(ast, snapshots, cfg["min_known_ratio"])
        log_event("admission_check", {"property_name": obj["property_name"],
                                      "expr": obj["expr"], "static_ok": True,
                                      "dynamic_ok": ok_d, "stats": stats},
                  trial_id, step)
        if not ok_d:
            return None, f"dynamic check failed: {stats['reason']}"
    except (ValueError, QSyntaxError) as exc:
        return None, f"admission proposal invalid: {exc}"

    if any(e["property_name"] == obj["property_name"] for e in runtime["admitted"]):
        return None, f"property '{obj['property_name']}' already admitted"
    used = sorted(fields_used(ast))
    family = match_macro_family(set(used))
    record = {
        "property_name": obj["property_name"],
        "expr": obj["expr"],
        "gloss": obj["gloss"],
        "fields_used": used,
        "origin_request": {"target": target, "property": prop},
        "admitted_at": {"trial_id": trial_id, "step": step, "ts": time.time()},
        "dynamic_check": {"n_snapshots": stats["n"], "known": stats["known"],
                          "constant": False, "deterministic": True},
        "intervenable": family is not None,
        "macro_family": family,
    }
    runtime["admitted"].append(record)
    save_runtime(runtime)
    log_event("admission_result", {"property_name": record["property_name"],
                                   "admitted": True,
                                   "intervenable": record["intervenable"]},
              trial_id, step)
    return record, ""


# =============================================================== runtime eval
def eval_admitted(env, candidates, timeout: int = 60) -> Dict[str, Dict[str, Any]]:
    """Evaluate admitted-origin candidates on one live /state_snapshot.
    Mirrors the K3 result shape: {cid: {id, value, raw, known, error}}."""
    from Adam.tcpg.predicates import state_snapshot
    runtime = {e["property_name"]: e for e in load_runtime()["admitted"]}
    snap = state_snapshot(env, timeout=timeout)
    out: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        cid = c.cid if hasattr(c, "cid") else c["id"]
        target = c.target if hasattr(c, "target") else c["target"]
        entry = runtime.get(target)
        if entry is None:
            out[cid] = {"id": cid, "value": None, "raw": None, "known": False,
                        "error": f"'{target}' is not an admitted primitive"}
            continue
        val, known = evaluate(parse(entry["expr"]), snap)
        want = c.value if hasattr(c, "value") else c["value"]
        out[cid] = {"id": cid,
                    "value": (1 if (val == bool(want)) else 0) if known else None,
                    "raw": val, "known": known, "error": None}
    return out
