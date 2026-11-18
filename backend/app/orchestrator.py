"""Run orchestration: snapshot locked batches, dispatch per-center jobs, record
per-center outcomes. A run always references the batches locked at start time;
batch replacement during a run only affects later runs."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .agents import get_agent_client
from .competing import ANALYSIS_TYPE, aggregate_risk, dispatch_risk_run
from .models import (
    Center,
    CenterMapping,
    DataBatch,
    Run,
    RunBatch,
    RunCenterResult,
    RunRiskResult,
    RuleVersion,
)


def locked_batches(db: Session) -> dict[int, DataBatch]:
    """center_id -> currently locked batch."""
    rows = db.query(DataBatch).filter(DataBatch.status == "locked").all()
    return {b.center_id: b for b in rows}


def create_run(db: Session, rule_version: RuleVersion, request_spec: dict,
               request_hash: str, user_id: int, k_threshold: int) -> Run:
    if rule_version.status != "approved":
        raise HTTPException(409, "rule version is not approved; runs require an approved rule")
    locked = locked_batches(db)
    if not locked:
        raise HTTPException(409, "no locked data batches; ask a data admin to lock batches first")

    is_risk = request_spec.get("analysis_type") == ANALYSIS_TYPE
    snapshot = None
    if is_risk:
        # A competing-risk run is bound to an immutable adjudication list.
        # Adjudications finished while the run executes only enter the NEXT run.
        from . import adjudication as adj
        from .models import AdjudicationSnapshot, EventCluster

        if db.query(EventCluster).count() == 0:
            raise HTTPException(409, "尚无事件候选与裁定记录，无法启动竞争风险运行")
        snap_id = request_spec.get("adjudication_snapshot_id")
        if snap_id:
            snapshot = db.get(AdjudicationSnapshot, snap_id)
            if not snapshot or snapshot.status != "frozen":
                raise HTTPException(409, "指定的裁定清单不存在或未冻结")
        else:
            snapshot = adj.freeze_snapshot(db, user_id)
        time_basis = request_spec.get("time_basis", "occurrence")
        if time_basis not in ("occurrence", "awareness"):
            raise HTTPException(400, "time_basis 必须为 occurrence（发生时间）或 awareness（获知时间）")

    run = Run(rule_version_id=rule_version.id, request_spec=request_spec,
              request_hash=request_hash, status="pending",
              k_threshold=k_threshold, created_by=user_id)
    if is_risk:
        run.adjudication_snapshot_id = snapshot.id
        run.time_basis = time_basis
        run.pending_candidates = snapshot.pending_count
    db.add(run)
    db.flush()
    for center_id, batch in locked.items():
        db.add(RunBatch(run_id=run.id, center_id=center_id, batch_id=batch.id,
                        batch_label=batch.label))
        if is_risk:
            db.add(RunRiskResult(run_id=run.id, center_id=center_id, status="pending"))
        else:
            db.add(RunCenterResult(run_id=run.id, center_id=center_id, status="pending"))
    db.commit()
    db.refresh(run)
    return run


def execute_run(db: Session, run: Run) -> Run:
    """Dispatch the job to every center in the run's batch snapshot."""
    run.status = "running"
    db.commit()

    if run.adjudication_snapshot_id:
        return _execute_risk_run(db, run)

    client = get_agent_client()
    centers = {c.id: c for c in db.query(Center).all()}
    mappings = _mappings_by_center(db)
    job_base = {
        "job_id": f"run-{run.id}",
        "conditions": run.rule_version.conditions,
        "request_spec": run.request_spec,
    }

    for cr in run.center_results:
        center = centers[cr.center_id]
        job = dict(job_base)
        job["job_id"] = f"run-{run.id}-{center.code}"
        job["mapping"] = mappings.get(cr.center_id, {})
        started = time.monotonic()
        result = client.execute(center.code, job)
        elapsed = int((time.monotonic() - started) * 1000)
        cr.duration_ms = elapsed
        status = result.get("status", "failed")
        if status == "success":
            cr.status = "success"
            cr.aggregates = {"groups": result.get("groups", []),
                             "total_n": result.get("total_n", 0)}
            cr.error = None
        elif status == "missing_mapping":
            cr.status = "missing_mapping"
            cr.error = result.get("error", "missing variable mapping")
        else:
            cr.status = "failed"
            cr.error = result.get("error", "unknown failure")
        db.commit()

    run.finished_at = datetime.now(timezone.utc)
    run.status = ("completed" if all(c.status == "success" for c in run.center_results)
                  else "completed_with_gaps")
    db.commit()
    db.refresh(run)
    return run


def _execute_risk_run(db: Session, run: Run) -> Run:
    """Competing-risk execution: each center computes its own risk set locally
    from the run's frozen adjudication snapshot; only aggregates return."""
    mappings = _mappings_by_center(db)
    dispatch_risk_run(db, run, mappings)
    run.finished_at = datetime.now(timezone.utc)
    run.status = ("completed" if all(r.status == "success" for r in run.risk_results)
                  else "completed_with_gaps")
    db.commit()
    db.refresh(run)
    return run


def _mappings_by_center(db: Session) -> dict[int, dict]:
    """center_id -> {variable: {field, value_map}} for the active dictionary."""
    rows = db.query(CenterMapping).all()
    out: dict[int, dict] = {}
    for m in rows:
        if m.status != "mapped" or not m.local_field:
            continue
        out.setdefault(m.center_id, {})[m.variable_name] = {
            "field": m.local_field,
            "value_map": m.value_map,
        }
    return out
