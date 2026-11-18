"""Center-local SAE candidate store and competing-risk risk-set executor.

Raw event candidates live ONLY in the center's ``events.csv``. Two kinds of
calls are served:

* ``events_meta`` / ``load_event_candidates`` — used by the audited intake
  (the controlled workspace pulls a hash-stamped copy; the center keeps the
  original record).
* ``compute_risk_set`` — given the cohort rule and the center's OWN
  adjudicated records (sent back to the center that produced them), classify
  every cohort patient over the one-year horizon and return AGGREGATE counts
  only. Individual event rows never appear in the response.

Classification outcome per cohort patient:
  event                adjudicated confirmed SAE within the horizon
  competing_termination death within the horizon (competing risk, NOT "no event")
  indeterminate         transfer-out / insufficient info (无法判定)
  censored              lost follow-up or horizon/analysis cut (删失)
Patients whose candidates are still pending adjudication are counted
separately as ``pending`` and censored at last contact — never as events.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from .executor import MissingMapping, _coerce, apply_rule

# Final adjudicated classifications understood by the risk-set service.
CONFIRMED_EVENT = "confirmed_event"
COMPETING_TERMINATION = "competing_termination"
CENSORED = "censored"
INDETERMINATE = "indeterminate"
NON_EVENT = "non_event"

RISK_METRICS = ["sae_event_n", "competing_death_n", "censored_n",
                "indeterminate_n", "pending_n"]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def events_csv_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "events.csv"


def load_cohort_records(data_dir: str | Path, mapping: dict[str, dict]) -> list[dict]:
    """Like executor.load_records but keeps the ``patient_id`` join key."""
    csv_path = Path(data_dir) / "registry.csv"
    with csv_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    header = set(rows[0].keys()) if rows else set()
    records = []
    for row in rows:
        rec = {"patient_id": row.get("patient_id")}
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


def load_event_candidates(data_dir: str | Path) -> list[dict]:
    """Read the center-local raw candidate rows (never leaves the center
    except through the audited intake snapshot)."""
    path = events_csv_path(data_dir)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def events_meta(data_dir: str | Path) -> dict:
    path = Path(data_dir) / "events_meta.json"
    rows = load_event_candidates(data_dir)
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r.get("event_type", "")] = by_type.get(r.get("event_type", ""), 0) + 1
    import json

    extra = json.loads(path.read_text()) if path.exists() else {}
    return {
        "candidate_count": len(rows),
        "by_type": by_type,
        "schema_version": extra.get("schema_version", "event-v1"),
        "seed": extra.get("seed"),
    }


def _empty_group(key: dict, label: str) -> dict:
    return {"key": key, "label": label, "n": 0,
            "metrics": {m: 0 for m in RISK_METRICS}}


def compute_risk_set(data_dir: str | Path, job: dict) -> dict:
    """Aggregate competing-risk outcomes for the center's cohort.

    Job fields:
      conditions, mapping      — cohort rule + registry field mapping
      request_spec.group_by    — grouping variables (registry canonical names)
      request_spec.horizon_days— follow-up horizon (default 365)
      request_spec.time_basis  — "occurrence" (event date) or "awareness"
                                 (known date); decides which timestamp is used
      analysis_date            — administrative cut for uneventful patients
      adjudicated              — this center's records:
                                 [{patient_id, classification, event_date,
                                   known_date, last_contact_date}]
      pending_patient_ids      — cohort patients with un-adjudicated candidates
    """
    conditions = job.get("conditions", [])
    spec = job.get("request_spec", {})
    mapping = job.get("mapping", {})
    horizon_days = int(spec.get("horizon_days", 365))
    time_basis = spec.get("time_basis", "occurrence")
    if time_basis not in ("occurrence", "awareness"):
        return {"status": "failed", "error": f"unknown time_basis {time_basis}"}
    analysis_date = _parse_date(job.get("analysis_date")) or date(2025, 9, 30)

    missing = [v for v in {"treatment.start_date", *spec.get("group_by", [])}
               if v not in mapping or not mapping[v].get("field")]
    if missing:
        return {"status": "missing_mapping",
                "error": f"no mapping for variable(s): {', '.join(sorted(missing))}"}
    try:
        records = load_cohort_records(data_dir, mapping)
    except MissingMapping as e:
        return {"status": "missing_mapping", "error": str(e)}

    cohort = apply_rule(records, conditions)
    group_vars = spec.get("group_by", [])

    # Index adjudicated records per patient.
    adj: dict[str, list[dict]] = {}
    for rec in job.get("adjudicated", []):
        adj.setdefault(rec["patient_id"], []).append(rec)
    pending = set(job.get("pending_patient_ids", []))

    buckets: dict[tuple, dict] = {}
    patients_seen = 0
    for reg in cohort:
        pid = reg.get("patient_id")
        # load_records only keeps mapped canonical variables; patient_id is the
        # CSV key and is preserved by the executor when present in mapping.
        tx_start = _parse_date(str(reg.get("treatment.start_date") or ""))
        if not pid or not tx_start:
            continue
        patients_seen += 1
        horizon_end = tx_start + timedelta(days=horizon_days)
        key = tuple(str(reg.get(v)) for v in group_vars)
        if key not in buckets:
            buckets[key] = _empty_group(dict(zip(group_vars, key)),
                                        " / ".join(key) if key else "ALL")
        g = buckets[key]
        g["n"] += 1

        # Pending adjudication is its own, mutually exclusive category: such
        # patients are neither events nor silently censored.
        if pid in pending:
            g["metrics"]["pending_n"] += 1
            continue
        status = _classify_patient(adj.get(pid, []), tx_start, horizon_end,
                                   analysis_date, time_basis)
        g["metrics"][f"{status}_n"] += 1

    groups = [buckets[k] for k in sorted(buckets)]
    totals = {m: sum(g["metrics"][m] for g in groups) for m in RISK_METRICS}
    return {
        "status": "success",
        "time_basis": time_basis,
        "horizon_days": horizon_days,
        "cohort_n": patients_seen,
        "groups": groups,
        "totals": {"n": patients_seen, "metrics": totals},
    }


def _classify_patient(records: list[dict], tx_start: date, horizon_end: date,
                      analysis_date: date, time_basis: str) -> str:
    """Reduce one patient's adjudicated records to a horizon-aware outcome."""
    if not records:
        return CENSORED
    # Earliest qualifying outcome wins; events and competing risks are checked
    # in date order so a death before confirmation is handled correctly.
    dated = []
    for rec in records:
        cls = rec["classification"]
        evt = _parse_date(rec.get("event_date"))
        known = _parse_date(rec.get("known_date"))
        contact = _parse_date(rec.get("last_contact_date")) or known or evt
        if cls == CONFIRMED_EVENT:
            t = evt if time_basis == "occurrence" else (known or evt)
            dated.append((t, CONFIRMED_EVENT, contact))
        elif cls == COMPETING_TERMINATION:
            dated.append((evt or contact, COMPETING_TERMINATION, contact))
        elif cls == INDETERMINATE:
            dated.append((contact, INDETERMINATE, contact))
        # NON_EVENT / CENSORED carry no terminal date.
    dated = [d for d in dated if d[0] is not None]
    dated.sort(key=lambda x: x[0])
    for t, cls, _contact in dated:
        if tx_start <= t <= horizon_end:
            if cls == CONFIRMED_EVENT:
                return "sae_event"
            if cls == COMPETING_TERMINATION:
                return "competing_death"
            if cls == INDETERMINATE:
                return "indeterminate"
    # No terminal event inside the horizon -> censored (lost fu / cut / pending).
    return "censored"
