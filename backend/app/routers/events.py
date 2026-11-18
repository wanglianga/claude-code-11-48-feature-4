"""Event adjudication endpoints: audited intake, de-duplicated clusters, the
blinded adjudication queue and immutable adjudication lists (snapshots).

Roles:
  data_admin  — pull center-local candidates into audited intake batches
  adjudicator — blinded manual votes; the chief adjudicator breaks ties
  researcher  — freeze/read snapshots and launch competing-risk runs
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import adjudication as adj
from .. import competing
from ..db import get_db
from ..deps import current_user, require_role
from ..models import (
    ADJUDICATION_CLASSES,
    AdjudicationSnapshot,
    AdjudicationTask,
    AdjudicationVote,
    Center,
    EventCandidate,
    EventCluster,
    EventIntakeBatch,
    User,
)

router = APIRouter(prefix="/api/events", tags=["events"])
admin = require_role("data_admin")
adjudicator = require_role("adjudicator")
researcher = require_role("researcher")

ADJ_CLASS_LABELS = {
    "confirmed_event": "已裁定事件",
    "pending_adjudication": "待裁定",
    "non_event": "非事件",
    "competing_termination": "竞争终止（死亡）",
    "insufficient": "信息不足",
    "censored": "删失（失访）",
    "indeterminate": "无法判定（转院）",
}


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------

def candidate_view(c: EventCandidate) -> dict:
    return {
        "id": c.id, "candidate_id": c.candidate_id, "center_code": c.report_center,
        "patient_id": c.patient_id, "event_type": c.event_type,
        "source_kind": c.source_kind, "occurrence_date": c.occurrence_date,
        "known_date": c.known_date, "severity": c.severity,
        "is_fatal": c.is_fatal, "external_hospital": c.external_hospital,
        "external_receipt_id": c.external_receipt_id,
        "linked_candidate_id": c.linked_candidate_id,
        "death_date": c.death_date, "death_cause": c.death_cause,
        "evidence": c.evidence, "notes": c.notes, "row_hash": c.row_hash,
        "status": c.status,
    }


def cluster_view(db: Session, cl: EventCluster, full: bool = False) -> dict:
    task = (db.query(AdjudicationTask)
            .filter(AdjudicationTask.cluster_id == cl.id).first())
    out = {
        "id": cl.id, "cluster_key": cl.cluster_key,
        "patient_ids": cl.patient_ids, "center_codes": cl.center_codes,
        "dedup_reason": cl.dedup_reason,
        "rule_classification": cl.rule_classification,
        "rule_classification_label": ADJ_CLASS_LABELS.get(cl.rule_classification),
        "rule_event_date": cl.rule_event_date, "rule_known_date": cl.rule_known_date,
        "final_classification": cl.final_classification,
        "final_classification_label": ADJ_CLASS_LABELS.get(cl.final_classification),
        "final_event_date": cl.final_event_date,
        "final_known_date": cl.final_known_date,
        "task_id": task.id if task else None,
        "task_status": task.status if task else None,
        "mode": task.mode if task else None,
        "member_count": len(cl.members),
    }
    if full:
        out["members"] = [candidate_view(m.candidate) for m in cl.members]
        if task:
            out["resolution_comment"] = task.resolution_comment
            out["votes"] = [
                {"reviewer_id": v.reviewer_id, "vote": v.vote,
                 "vote_label": ADJ_CLASS_LABELS.get(v.vote, v.vote),
                 "proposed_event_date": v.proposed_event_date,
                 "comment": v.comment, "created_at": v.created_at.isoformat()}
                for v in task.votes]
    return out


def task_view(db: Session, task: AdjudicationTask, user: User,
              full: bool = False) -> dict:
    cl = task.cluster
    out = {
        "id": task.id, "cluster_id": cl.id, "cluster_key": cl.cluster_key,
        "patient_ids": cl.patient_ids, "center_codes": cl.center_codes,
        "status": task.status, "mode": task.mode,
        "rule_classification": task.rule_classification,
        "rule_classification_label": ADJ_CLASS_LABELS.get(task.rule_classification),
        "required_votes": task.required_votes,
        "vote_count": len(task.votes),
        "my_vote": next((v.vote for v in task.votes
                         if v.reviewer_id == user.id), None),
        "disagreement": len({v.vote for v in task.votes}) > 1,
        "resolution": task.resolution,
        "resolution_label": ADJ_CLASS_LABELS.get(task.resolution) if task.resolution else None,
        "resolved_event_date": task.resolved_event_date,
        "resolved_known_date": task.resolved_known_date,
        "resolution_comment": task.resolution_comment,
    }
    if full:
        out["evidence"] = [candidate_view(m.candidate) for m in cl.members]
        # Blinding: while the panel is open, a reviewer cannot see who voted what.
        if task.status == "resolved":
            out["votes"] = [
                {"reviewer_id": v.reviewer_id,
                 "reviewer": db.get(User, v.reviewer_id).display_name,
                 "vote": v.vote, "vote_label": ADJ_CLASS_LABELS.get(v.vote, v.vote),
                 "proposed_event_date": v.proposed_event_date,
                 "comment": v.comment, "created_at": v.created_at.isoformat()}
                for v in task.votes]
        else:
            out["votes"] = [{"reviewer_id": v.reviewer_id, "hidden": True}
                            for v in task.votes]
    return out


# --------------------------------------------------------------------------
# Layer 1: audited intake (data admin)
# --------------------------------------------------------------------------

class IntakeIn(BaseModel):
    label: str = "2026Q3-events"


@router.post("/intake/{center_code}", status_code=201)
def intake(center_code: str, body: IntakeIn, db: Session = Depends(get_db),
           user: User = Depends(admin)):
    center = db.query(Center).filter(Center.code == center_code).first()
    if not center:
        raise HTTPException(404, "unknown center")
    try:
        batch = competing.intake_center(db, center, user.id, body.label)
    except Exception as e:  # agent unreachable / no events.csv
        raise HTTPException(502, f"中心事件接入失败：{e}")
    return {"id": batch.id, "center_code": center_code, "label": batch.label,
            "candidate_count": batch.candidate_count,
            "source_snapshot_hash": batch.source_snapshot_hash,
            "status": batch.status}


@router.get("/intakes")
def list_intakes(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = (db.query(EventIntakeBatch).join(Center)
            .order_by(EventIntakeBatch.id.desc()).all())
    return [{"id": b.id, "center_code": b.center.code, "label": b.label,
             "status": b.status, "candidate_count": b.candidate_count,
             "source_snapshot_hash": b.source_snapshot_hash,
             "created_at": b.created_at.isoformat()} for b in rows]


@router.get("/candidates")
def list_candidates(center_code: str | None = None,
                    patient_id: str | None = None,
                    db: Session = Depends(get_db),
                    _: User = Depends(current_user)):
    q = db.query(EventCandidate).filter(EventCandidate.status == "active")
    if center_code:
        center = db.query(Center).filter(Center.code == center_code).first()
        if not center:
            raise HTTPException(404, "unknown center")
        q = q.filter(EventCandidate.center_id == center.id)
    if patient_id:
        q = q.filter(EventCandidate.patient_id == patient_id)
    return [candidate_view(c) for c in q.order_by(EventCandidate.center_id,
                                                  EventCandidate.known_date)]


# --------------------------------------------------------------------------
# Layer 2: de-duplicated clusters
# --------------------------------------------------------------------------

@router.get("/clusters")
def list_clusters(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.query(EventCluster).order_by(EventCluster.id).all()
    return [cluster_view(db, cl) for cl in rows]


@router.get("/clusters/{cluster_id}")
def get_cluster(cluster_id: int, db: Session = Depends(get_db),
                _: User = Depends(current_user)):
    cl = db.get(EventCluster, cluster_id)
    if not cl:
        raise HTTPException(404, "unknown cluster")
    return cluster_view(db, cl, full=True)


# --------------------------------------------------------------------------
# Layer 3: blinded adjudication queue
# --------------------------------------------------------------------------

@router.get("/adjudication/queue")
def queue(db: Session = Depends(get_db), user: User = Depends(adjudicator)):
    tasks = (db.query(AdjudicationTask)
             .filter(AdjudicationTask.mode == "manual")
             .order_by(AdjudicationTask.status, AdjudicationTask.id).all())
    return [task_view(db, t, user) for t in tasks]


@router.get("/adjudication/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db),
             user: User = Depends(adjudicator)):
    task = db.get(AdjudicationTask, task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    return task_view(db, task, user, full=True)


class VoteIn(BaseModel):
    vote: str  # confirmed_event | non_event | competing_termination | insufficient
    proposed_event_date: str | None = None
    comment: str | None = None


@router.post("/adjudication/tasks/{task_id}/vote")
def vote(task_id: int, body: VoteIn, db: Session = Depends(get_db),
         user: User = Depends(adjudicator)):
    task = db.get(AdjudicationTask, task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    if body.vote not in ADJUDICATION_CLASSES or body.vote == "pending_adjudication":
        raise HTTPException(400, "invalid vote classification")
    try:
        adj.apply_vote(db, task, user.id, body.vote,
                       body.proposed_event_date, body.comment)
        db.commit()
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.refresh(task)
    return task_view(db, task, user, full=True)


class TieBreakIn(BaseModel):
    final_vote: str
    event_date: str | None = None
    known_date: str | None = None
    comment: str = ""


@router.post("/adjudication/tasks/{task_id}/tie-break")
def tie_break(task_id: int, body: TieBreakIn, db: Session = Depends(get_db),
              user: User = Depends(adjudicator)):
    if user.username != "chief":
        raise HTTPException(403, "仅首席裁定专家可对分歧做终裁")
    task = db.get(AdjudicationTask, task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    try:
        adj.resolve_disagreement(db, task, user.id, body.final_vote,
                                 body.event_date, body.known_date, body.comment)
        db.commit()
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.refresh(task)
    return task_view(db, task, user, full=True)


# --------------------------------------------------------------------------
# Layer 4: immutable adjudication lists
# --------------------------------------------------------------------------

@router.post("/snapshots", status_code=201)
def create_snapshot(db: Session = Depends(get_db),
                    user: User = Depends(require_role("researcher", "data_admin"))):
    if db.query(EventCluster).count() == 0:
        raise HTTPException(409, "尚无事件候选/去重簇，无法冻结裁定清单")
    snap = adj.freeze_snapshot(db, user.id)
    return snapshot_view(snap)


@router.get("/snapshots")
def list_snapshots(db: Session = Depends(get_db),
                   _: User = Depends(current_user)):
    rows = db.query(AdjudicationSnapshot).order_by(AdjudicationSnapshot.id.desc()).all()
    return [{"id": s.id, "version": s.version, "rule_version": s.rule_version,
             "status": s.status, "item_count": s.item_count,
             "pending_count": s.pending_count, "snapshot_hash": s.snapshot_hash,
             "created_at": s.created_at.isoformat()} for s in rows]


def snapshot_view(s: AdjudicationSnapshot) -> dict:
    return {
        "id": s.id, "version": s.version, "rule_version": s.rule_version,
        "status": s.status, "item_count": s.item_count,
        "pending_count": s.pending_count, "manifest": s.manifest,
        "snapshot_hash": s.snapshot_hash,
        "created_at": s.created_at.isoformat(),
        "items": [
            {"cluster_id": i.cluster_id, "patient_id": i.patient_id,
             "center_code": i.center_code, "classification": i.classification,
             "classification_label": ADJ_CLASS_LABELS.get(i.classification),
             "event_date": i.event_date, "known_date": i.known_date,
             "last_contact_date": i.last_contact_date, "mode": i.mode,
             "source_candidate_ids": i.source_candidate_ids}
            for i in s.items],
    }


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: int, db: Session = Depends(get_db),
                 _: User = Depends(current_user)):
    s = db.get(AdjudicationSnapshot, snapshot_id)
    if not s:
        raise HTTPException(404, "unknown snapshot")
    return snapshot_view(s)


@router.get("/snapshots/{snapshot_id}/verify")
def verify_snapshot(snapshot_id: int, db: Session = Depends(get_db),
                    _: User = Depends(current_user)):
    from ..security import content_hash

    s = db.get(AdjudicationSnapshot, snapshot_id)
    if not s:
        raise HTTPException(404, "unknown snapshot")
    items_payload = [
        {"cluster_id": i.cluster_id, "patient_id": i.patient_id,
         "center_code": i.center_code, "classification": i.classification,
         "event_date": i.event_date, "known_date": i.known_date,
         "last_contact_date": i.last_contact_date,
         "source_candidate_ids": i.source_candidate_ids, "mode": i.mode}
        for i in s.items]
    recomputed = content_hash(
        {"version": s.version, "manifest": s.manifest, "items": items_payload})
    return {"snapshot_id": s.id, "stored_hash": s.snapshot_hash,
            "recomputed_hash": recomputed,
            "intact": recomputed == s.snapshot_hash}
