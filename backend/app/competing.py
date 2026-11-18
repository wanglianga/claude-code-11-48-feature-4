"""Competing-risk data service: audited intake, de-duplication orchestration
and per-center risk-set dispatch for SAE runs.

State ownership (each layer is separately auditable):

  intake      EventIntakeBatch/EventCandidate — hash-stamped copies of the
              center-local events.csv (the center keeps the original)
  dedup       EventCluster/EventClusterMember — one clinical event once
  queue       AdjudicationTask/Vote — auto vs blinded manual decisions
  risk set    RunRiskResult — per-center competing-risk aggregates frozen to a
              run's AdjudicationSnapshot
  review      the existing OutputTable/ReviewDecision/PublishedReport layer

Death, lost follow-up and transfer-out are NEVER collapsed into "no event":
they map to competing_termination / censored / indeterminate respectively.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from . import adjudication as adj
from .agents import get_agent_client
from .dedup import build_clusters
from .models import (
    CANDIDATE_ACTIVE,
    CLASS_PENDING,
    EventCandidate,
    EventCluster,
    EventClusterMember,
    EventIntakeBatch,
    Run,
    RunRiskResult,
    Center,
    RISK_INDETERMINATE,
)
from .security import content_hash

RISK_COUNT_METRICS = ["n", "sae_event_n", "competing_death_n",
                      "censored_n", "indeterminate_n", "pending_n"]
ANALYSIS_TYPE = "competing_risk"


# --------------------------------------------------------------------------
# Layer 1+2: intake and de-duplication
# --------------------------------------------------------------------------

def intake_center(db: Session, center: Center, user_id: int,
                  label: str) -> EventIntakeBatch:
    """Pull a center's local event candidates into an audited intake batch,
    then rebuild every cluster (de-dup is global because one event can span
    two centers)."""
    payload = get_agent_client().events_intake(center.code)
    raw_rows = payload["candidates"]
    # Supersede previous batches of this center.
    for old in db.query(EventIntakeBatch).filter(
            EventIntakeBatch.center_id == center.id,
            EventIntakeBatch.status == "imported"):
        old.status = "superseded"
    for old_c in db.query(EventCandidate).filter(
            EventCandidate.center_id == center.id):
        old_c.status = "superseded"

    batch = EventIntakeBatch(
        center_id=center.id, label=label, status="imported",
        source_snapshot_hash=payload["snapshot_hash"],
        candidate_count=len(raw_rows), created_by=user_id)
    db.add(batch)
    db.flush()
    for r in raw_rows:
        db.add(EventCandidate(
            intake_batch_id=batch.id, center_id=center.id,
            candidate_id=r["candidate_id"], patient_id=r["patient_id"],
            event_type=r["event_type"], source_kind=r["source_kind"],
            occurrence_date=r.get("occurrence_date") or None,
            known_date=r.get("known_date") or None,
            severity=r.get("severity") or None,
            is_fatal=(r.get("is_fatal") == "1" or r.get("is_fatal") is True),
            external_hospital=r.get("external_hospital") or None,
            external_receipt_id=r.get("external_receipt_id") or None,
            linked_candidate_id=r.get("linked_candidate_id") or None,
            death_date=r.get("death_date") or None,
            death_cause=r.get("death_cause") or None,
            evidence=r.get("evidence") or None,
            report_center=r.get("report_center") or center.code,
            notes=r.get("notes") or None,
            row_hash=content_hash(r), status=CANDIDATE_ACTIVE))
    db.commit()
    db.refresh(batch)
    rebuild_clusters(db)
    return batch


def _active_candidate_dicts(db: Session) -> list[dict]:
    rows = db.query(EventCandidate).filter(
        EventCandidate.status == CANDIDATE_ACTIVE).order_by(EventCandidate.id).all()
    return [{
        "candidate_id": c.candidate_id, "patient_id": c.patient_id,
        "report_center": c.report_center, "center_code": c.report_center,
        "linked_candidate_id": c.linked_candidate_id,
        "external_receipt_id": c.external_receipt_id,
    } for c in rows]


def rebuild_clusters(db: Session) -> int:
    """Re-run union-find de-duplication over all active candidates and refresh
    clusters, members and AR-v1 tasks. Manual resolutions are preserved."""
    candidates = (db.query(EventCandidate)
                  .filter(EventCandidate.status == CANDIDATE_ACTIVE)
                  .order_by(EventCandidate.id).all())
    specs = build_clusters(_active_candidate_dicts(db))
    cand_by_cid = {c.candidate_id: c for c in candidates}

    existing = {cl.cluster_key: cl for cl in db.query(EventCluster).all()}
    seen_keys = set()
    for spec in specs:
        seen_keys.add(spec["cluster_key"])
        cluster = existing.get(spec["cluster_key"])
        if cluster is None:
            cluster = EventCluster(cluster_key=spec["cluster_key"])
            db.add(cluster)
            db.flush()
        cluster.patient_ids = spec["patient_ids"]
        cluster.center_codes = spec["center_codes"]
        cluster.dedup_reason = "；".join(spec["reasons"])
        # Refresh membership via an explicit delete to avoid ORM cascade
        # double-delete warnings when a cluster's members change on re-intake.
        db.query(EventClusterMember).filter(
            EventClusterMember.cluster_id == cluster.id).delete(
                synchronize_session=False)
        member_objs = []
        for cid in spec["members"]:
            cand = cand_by_cid[cid]
            db.add(EventClusterMember(cluster_id=cluster.id, candidate_id=cand.id))
            member_objs.append(cand)
        db.flush()
        adj.sync_cluster_task(db, cluster, member_objs)

    # Remove clusters that no longer correspond to active candidates.
    for key, cluster in existing.items():
        if key not in seen_keys:
            db.delete(cluster)
    db.commit()
    return len(specs)


# --------------------------------------------------------------------------
# Layer 4+5: risk-set dispatch for a frozen run
# --------------------------------------------------------------------------

def _center_patient_records(db: Session, snapshot_id: int) -> dict[str, list[dict]]:
    """center_code -> per-patient adjudicated records derived from the FROZEN
    snapshot items plus cluster membership.

    A clinical event fused across two centers is counted exactly once: the
    anchor member (earliest occurrence, ties broken by candidate id) carries
    the terminal outcome at its center; the other center's transferred patient
    is marked ``indeterminate`` so it is neither a second event nor "no event".
    """
    from .models import AdjudicationSnapshotItem

    items = db.query(AdjudicationSnapshotItem).filter(
        AdjudicationSnapshotItem.snapshot_id == snapshot_id).all()
    out: dict[str, list[dict]] = {}
    for item in items:
        members = (db.query(EventCandidate)
                   .filter(EventCandidate.id.in_(item.source_candidate_ids)).all())
        centers = sorted({m.report_center for m in members})
        anchor_center = min(
            centers,
            key=lambda code: min(
                (m.occurrence_date or "9999", m.candidate_id)
                for m in members if m.report_center == code)) if centers else ""
        per_patient: dict[tuple, dict] = {}
        for c in members:
            key = (c.report_center, c.patient_id)
            cur = per_patient.get(key)
            last_contact = max(filter(None, [
                cur["last_contact_date"] if cur else None, c.known_date]))
            if len(centers) > 1 and c.report_center != anchor_center:
                # Transferred patient: the single clinical event is adjudicated
                # at the anchor center; here it is indeterminate, never a 2nd event.
                classification = RISK_INDETERMINATE
            else:
                classification = item.classification
            per_patient[key] = {
                "patient_id": c.patient_id,
                "classification": classification,
                "event_date": item.event_date,
                "known_date": item.known_date,
                "last_contact_date": last_contact,
            }
        for (code, _pid), rec in per_patient.items():
            out.setdefault(code, []).append(rec)
    return out


def _pending_patients(records_by_center: dict[str, list[dict]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for code, recs in records_by_center.items():
        pids = [r["patient_id"] for r in recs
                if r["classification"] == CLASS_PENDING]
        if pids:
            out[code] = pids
    return out


def dispatch_risk_run(db: Session, run: Run, mappings: dict[int, dict]) -> None:
    """Send each center its OWN adjudicated records; aggregates come back."""
    client = get_agent_client()
    centers = {c.id: c for c in db.query(Center).all()}
    records_by_center = _center_patient_records(db, run.adjudication_snapshot_id)
    pending = _pending_patients(records_by_center)
    spec = run.request_spec
    analysis_date = spec.get("analysis_date") or date.today().isoformat()

    for rr in run.risk_results:
        center = centers[rr.center_id]
        job = {
            "job_id": f"risk-run-{run.id}-{center.code}",
            "conditions": run.rule_version.conditions,
            "request_spec": {
                "group_by": spec.get("group_by", []),
                "horizon_days": spec.get("horizon_days", 365),
                "time_basis": run.time_basis,
            },
            "mapping": mappings.get(rr.center_id, {}),
            "analysis_date": analysis_date,
            "adjudicated": records_by_center.get(center.code, []),
            "pending_patient_ids": pending.get(center.code, []),
        }
        result = client.risk_set(center.code, job)
        status = result.get("status", "failed")
        rr.status = status if status in ("success", "missing_mapping") else "failed"
        rr.aggregates = result if status == "success" else None
        rr.error = None if status == "success" else result.get("error", "failure")
        db.commit()


def aggregate_risk(run: Run, db: Session) -> dict:
    """Merge per-center risk aggregates with explicit coverage (gaps are never
    zeroed), in the same table shape the privacy/review layer already uses."""
    centers = {c.id: c for c in db.query(Center).all()}
    included, excluded = [], []
    buckets: dict[str, dict] = {}

    for rr in run.risk_results:
        code = centers[rr.center_id].code
        if rr.status != "success" or not rr.aggregates:
            excluded.append({"center": code, "status": rr.status,
                             "reason": rr.error or "center did not return results"})
            continue
        included.append(code)
        for g in rr.aggregates["groups"]:
            lbl = g["label"]
            if lbl not in buckets:
                buckets[lbl] = {"key": g["key"], "label": lbl, "n": 0,
                                "metrics": {m: 0 for m in RISK_COUNT_METRICS
                                            if m != "n"}, "suppressed": []}
            b = buckets[lbl]
            b["n"] += g["n"]
            for m, v in g["metrics"].items():
                b["metrics"][m] = b["metrics"].get(m, 0) + (v or 0)

    groups = [buckets[k] for k in sorted(buckets)]
    total_n = sum(g["n"] for g in groups)
    total_metrics = {m: sum(g["metrics"].get(m, 0) for g in groups)
                     for m in RISK_COUNT_METRICS if m != "n"}
    return {
        "groups": groups,
        "totals": {"n": total_n, "metrics": total_metrics},
        "count_metrics": RISK_COUNT_METRICS,
        "coverage": {
            "included_centers": included,
            "excluded_centers": excluded,
            "complete": not excluded,
        },
    }
