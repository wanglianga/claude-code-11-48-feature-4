"""Center-local job executor.

Reads ONLY the center's own registry.csv, applies the cohort rule and the
statistical request, and returns aggregated counts/means. Individual records
never leave this module's caller (the center agent server).
"""
from __future__ import annotations

import csv
from pathlib import Path

OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
}


class MissingMapping(Exception):
    def __init__(self, variable: str):
        self.variable = variable
        super().__init__(f"variable '{variable}' has no local field mapping")


def _coerce(v: str):
    if v == "":
        return None
    for cast in (int, float):
        try:
            return cast(v)
        except (ValueError, TypeError):
            continue
    return v


def load_records(data_dir: str | Path, mapping: dict[str, dict]) -> list[dict]:
    """Load registry.csv and rename local fields to canonical variables.

    mapping: {"patient.sex": {"field": "pat_sex", "value_map": {...}|None}, ...}
    """
    csv_path = Path(data_dir) / "registry.csv"
    with csv_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    header = set(rows[0].keys()) if rows else set()
    records = []
    for row in rows:
        rec = {}
        for var, spec in mapping.items():
            field = spec.get("field")
            if not field or field not in header:
                raise MissingMapping(var)
            val = row.get(field, "")
            vmap = spec.get("value_map")
            if vmap and val in vmap:
                val = vmap[val]
            rec[var] = _coerce(val)
        records.append(rec)
    return records


def _match(rec: dict, cond: dict) -> bool:
    val = rec.get(cond["variable"])
    op = OPS[cond["op"]]
    target = cond["value"]
    if val is None:
        return False
    try:
        return op(val, target)
    except TypeError:
        # e.g. comparing str with number — treat as non-match
        return False


def apply_rule(records: list[dict], conditions: list[dict]) -> list[dict]:
    includes = [c for c in conditions if c.get("kind") == "include"]
    excludes = [c for c in conditions if c.get("kind") == "exclude"]
    out = []
    for rec in records:
        if includes and not all(_match(rec, c) for c in includes):
            continue
        if any(_match(rec, c) for c in excludes):
            continue
        out.append(rec)
    return out


def _metric_name(spec: dict) -> str:
    if spec["type"] == "outcome_count":
        return f"outcome_{spec['value']}_n"
    if spec["type"] == "mean":
        return f"{spec['variable']}_mean"
    return spec["type"]


def required_variables(conditions: list[dict], request_spec: dict) -> set[str]:
    vs = {c["variable"] for c in conditions}
    vs.update(request_spec.get("group_by", []))
    for m in request_spec.get("metrics", []):
        if m["type"] in ("outcome_count", "mean"):
            vs.add(m["variable"])
    return vs


def execute(data_dir: str | Path, job: dict) -> dict:
    """Run one job. Returns {"status": "success", ...} or an error payload."""
    conditions = job.get("conditions", [])
    spec = job["request_spec"]
    mapping = job.get("mapping", {})

    missing = [v for v in sorted(required_variables(conditions, spec))
               if v not in mapping or not mapping[v].get("field")]
    if missing:
        return {"status": "missing_mapping",
                "error": f"no mapping for variable(s): {', '.join(missing)}"}
    try:
        records = load_records(data_dir, mapping)
    except MissingMapping as e:
        return {"status": "missing_mapping", "error": str(e)}

    cohort = apply_rule(records, conditions)
    group_vars = spec.get("group_by", [])
    metric_specs = [m for m in spec.get("metrics", []) if m["type"] != "count"]

    buckets: dict[tuple, list[dict]] = {}
    for rec in cohort:
        key = tuple(str(rec.get(v)) for v in group_vars)
        buckets.setdefault(key, []).append(rec)

    groups = []
    for key, members in sorted(buckets.items()):
        g = {
            "key": dict(zip(group_vars, key)),
            "label": " / ".join(key) if key else "ALL",
            "n": len(members),
            "metrics": {},
        }
        for ms in metric_specs:
            name = _metric_name(ms)
            if ms["type"] == "outcome_count":
                cnt = sum(1 for r in members if r.get(ms["variable"]) == ms["value"])
                g["metrics"][f"{name}"] = cnt
                g["metrics"][f"outcome_{ms['value']}_rate"] = (
                    round(cnt / len(members), 4) if members else None
                )
            elif ms["type"] == "mean":
                vals = [r.get(ms["variable"]) for r in members
                        if isinstance(r.get(ms["variable"]), (int, float))]
                s = round(sum(vals), 4)
                g["metrics"][name] = round(s / len(vals), 4) if vals else None
                g["metrics"][f"_{name}_sum"] = s
                g["metrics"][f"_{name}_n"] = len(vals)
        groups.append(g)

    return {
        "status": "success",
        "total_n": len(cohort),
        "groups": groups,
        "records_scanned": len(records),
    }
