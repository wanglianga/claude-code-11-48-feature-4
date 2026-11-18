"""Preset adjudication rules (AR-v1), blinded manual resolution and the
immutable adjudication list (snapshot).

AR-v1 classifies each de-duplicated cluster into the five protocol categories:

  confirmed_event         已裁定事件 — a candidate meets the severity threshold
                          (severe) with a usable occurrence date
  pending_adjudication    待裁定 — conflicting evidence, routed to the blinded
                          expert panel instead of being auto-closed
  non_event               非事件 — only mild/moderate records below threshold
  competing_termination   竞争终止 — a death record (incl. death BEFORE the SAE
                          was confirmed); never treated as "no event"
  insufficient            信息不足 — no usable severity/date to decide

Follow-up-only clusters are not SAE adjudications: lost follow-up becomes the
``censored`` risk category and transfer-out becomes ``indeterminate``.

Event-date rule: the date of the EARLIEST record that meets the severity
threshold — not the date the external receipt was first known. The awareness
basis is tracked separately (``known_date``) so a run can declare which clock
it used.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import (
    AdjudicationSnapshot,
    AdjudicationSnapshotItem,
    AdjudicationTask,
    AdjudicationVote,
    CLASS_COMPETING,
    CLASS_CONFIRMED,
    CLASS_INSUFFICIENT,
    CLASS_NON_EVENT,
    CLASS_PENDING,
    EventCandidate,
    EventCluster,
    EventClusterMember,
    RISK_CENSORED,
    RISK_INDETERMINATE,
)
from .security import content_hash

RULE_VERSION = "AR-v1"
SEVERE = "severe"
MILD = "mild"


def _dates(members: list[EventCandidate], predicate) -> list[str]:
    out = []
    for m in members:
        if predicate(m) and m.occurrence_date:
            out.append(m.occurrence_date)
    return out


def rule_classify(members: list[EventCandidate]) -> dict:
    """Pure AR-v1 decision over one cluster's candidate rows."""
    sae = [m for m in members if m.event_type == "sae"]
    deaths = [m for m in members if m.event_type == "death" or m.is_fatal is True]
    lost = [m for m in members if m.event_type == "lost_fu"]
    transfers = [m for m in members if m.event_type == "transfer_out"]

    # Follow-up-only clusters feed the risk set, not the SAE adjudication.
    if not sae and not deaths:
        if transfers:
            return {"classification": RISK_INDETERMINATE, "mode": "auto",
                    "event_date": None,
                    "known_date": max((m.known_date for m in transfers if m.known_date),
                                      default=None),
                    "reason": "转院后结局不可得 → 无法判定"}
        if lost:
            return {"classification": RISK_CENSORED, "mode": "auto",
                    "event_date": None,
                    "known_date": max((m.known_date for m in lost if m.known_date),
                                      default=None),
                    "reason": "失访 → 右删失"}
        return {"classification": CLASS_INSUFFICIENT, "mode": "auto",
                "event_date": None, "known_date": None,
                "reason": "无可用 SAE/死亡记录"}

    # Death always wins -> competing termination, even if confirmation was
    # still pending when the patient died.
    if deaths:
        dday = min((d.death_date or d.occurrence_date for d in deaths
                    if (d.death_date or d.occurrence_date)), default=None)
        kday = min((d.known_date for d in deaths if d.known_date), default=None)
        return {"classification": CLASS_COMPETING, "mode": "auto",
                "event_date": dday, "known_date": kday,
                "reason": "存在死亡记录 → 竞争终止（死亡先于确认亦计入）"}

    severe = [m for m in sae if m.severity == SEVERE and m.occurrence_date]
    if severe:
        # Conflict: a severe record contradicted by a linked mild follow-up
        # note claiming another cause. Escalation (moderate->severe admission/
        # readmission) and external receipts confirming severity do NOT count
        # as conflicts and are auto-confirmed.
        contradiction = any(
            m.severity == MILD and m.source_kind == "fu_note"
            for m in sae
        )
        event_day = min(m.occurrence_date for m in severe)
        known_day = min((m.known_date for m in severe if m.known_date),
                        default=None)
        if contradiction:
            return {"classification": CLASS_PENDING, "mode": "manual",
                    "event_date": event_day, "known_date": known_day,
                    "reason": "重度记录与轻度随访备注相互矛盾 → 盲法专家裁定"}
        return {"classification": CLASS_CONFIRMED, "mode": "auto",
                "event_date": event_day, "known_date": known_day,
                "reason": "存在满足重度阈值且资料完整的记录，事件日取最早阈值记录日"}

    # No severe record: either too thin to judge or below threshold.
    thin = [m for m in sae if not m.severity or not m.occurrence_date]
    if thin and not any(m.severity and m.occurrence_date for m in sae):
        return {"classification": CLASS_INSUFFICIENT, "mode": "auto",
                "event_date": None,
                "known_date": max((m.known_date for m in sae if m.known_date),
                                  default=None),
                "reason": "缺严重程度或发生日期，证据不足以裁定"}
    return {"classification": CLASS_NON_EVENT, "mode": "auto",
            "event_date": None,
            "known_date": max((m.known_date for m in sae if m.known_date),
                              default=None),
            "reason": "仅轻/中度记录，未达 SAE 严重程度阈值 → 非事件"}


def sync_cluster_task(db: Session, cluster: EventCluster,
                      members: list[EventCandidate]) -> AdjudicationTask:
    """Apply AR-v1 to a cluster and create/refresh its queue task."""
    decision = rule_classify(members)
    cluster.rule_classification = decision["classification"]
    cluster.rule_event_date = decision["event_date"]
    cluster.rule_known_date = decision["known_date"]

    task = (db.query(AdjudicationTask)
            .filter(AdjudicationTask.cluster_id == cluster.id).first())
    if task is None:
        task = AdjudicationTask(cluster_id=cluster.id,
                                rule_classification=decision["classification"])
        db.add(task)

    # Manual re-decision of a task is never overwritten by re-running the rule.
    if task.status == "resolved":
        return task

    task.mode = decision["mode"]
    task.rule_classification = decision["classification"]
    if decision["mode"] == "auto" and decision["classification"] not in (
            CLASS_PENDING,):
        task.status = "resolved"
        task.resolution = decision["classification"]
        task.resolved_event_date = decision["event_date"]
        task.resolved_known_date = decision["known_date"]
        task.resolution_comment = f"AR-v1 自动裁定：{decision['reason']}"
        task.resolved_at = datetime.now(timezone.utc)
        cluster.final_classification = decision["classification"]
        cluster.final_event_date = decision["event_date"]
        cluster.final_known_date = decision["known_date"]
    else:
        task.status = "pending"
        task.required_votes = 2
        cluster.final_classification = CLASS_PENDING
        cluster.final_event_date = decision["event_date"]
        cluster.final_known_date = decision["known_date"]
    return task


def apply_vote(db: Session, task: AdjudicationTask, reviewer_id: int,
               vote: str, proposed_event_date: str | None,
               comment: str | None) -> AdjudicationTask:
    """Record one blinded vote and auto-close the panel on consensus."""
    if task.mode != "manual":
        raise ValueError("only manual-adjudication tasks accept panel votes")
    if task.status == "resolved":
        raise ValueError("task already resolved")
    existing = next((v for v in task.votes if v.reviewer_id == reviewer_id), None)
    if existing:
        existing.vote = vote
        existing.proposed_event_date = proposed_event_date
        existing.comment = comment
    else:
        db.add(AdjudicationVote(task_id=task.id, reviewer_id=reviewer_id,
                                vote=vote, proposed_event_date=proposed_event_date,
                                comment=comment))
    db.flush()

    # Query the votes explicitly: the relationship collection can lag the
    # just-flushed row within the same session.
    all_votes = (db.query(AdjudicationVote)
                 .filter(AdjudicationVote.task_id == task.id).all())
    distinct = {v.vote for v in all_votes}
    if len(all_votes) >= task.required_votes and len(distinct) == 1:
        _resolve_manual(db, task, vote, proposed_event_date,
                        f"盲法专家组一致裁定（{len(all_votes)} 票）", reviewer_id)
    elif len(all_votes) >= task.required_votes:
        task.status = "in_review"  # disagreement: awaits the chief's tie-break
    return task


def resolve_disagreement(db: Session, task: AdjudicationTask, reviewer_id: int,
                         final_vote: str, event_date: str | None,
                         known_date: str | None,
                         comment: str) -> AdjudicationTask:
    """Chief-adjudicator override when the blinded panel split its votes."""
    if task.mode != "manual":
        raise ValueError("only manual tasks can be overridden")
    distinct = {v.vote for v in task.votes}
    if len(distinct) < 2:
        raise ValueError("tie-break requires a recorded panel disagreement")
    _resolve_manual(db, task, final_vote, event_date,
                    f"裁定分歧后由首席专家终裁：{comment}", reviewer_id,
                    known_date=known_date)
    return task


def _resolve_manual(db: Session, task: AdjudicationTask, classification: str,
                    event_date: str | None, comment: str, reviewer_id: int,
                    known_date: str | None = None) -> None:
    task.status = "resolved"
    task.resolution = classification
    task.resolved_event_date = event_date
    task.resolved_known_date = known_date
    task.resolved_by = reviewer_id
    task.resolution_comment = comment
    task.resolved_at = datetime.now(timezone.utc)
    cluster = task.cluster
    cluster.final_classification = classification
    cluster.final_event_date = event_date
    cluster.final_known_date = known_date


def _cluster_outcome(cluster: EventCluster, task: AdjudicationTask) -> dict:
    """The current authoritative outcome for a cluster (manual overrides rule)."""
    if task.status == "resolved":
        return {"classification": task.resolution,
                "event_date": task.resolved_event_date,
                "known_date": task.resolved_known_date, "mode": task.mode}
    return {"classification": cluster.rule_classification or CLASS_PENDING,
            "event_date": cluster.rule_event_date,
            "known_date": cluster.rule_known_date, "mode": task.mode}


def freeze_snapshot(db: Session, user_id: int) -> AdjudicationSnapshot:
    """Build a new immutable, hash-stamped adjudication list from the CURRENT
    cluster/task state. Runs freeze a reference to one snapshot; adjudications
    completed afterwards only appear in later snapshots/runs."""
    clusters = db.query(EventCluster).order_by(EventCluster.id).all()
    next_no = db.query(AdjudicationSnapshot).count() + 1
    version = f"{RULE_VERSION}-S{next_no:03d}"

    class_counts: dict[str, int] = {}
    center_total: dict[str, int] = {}
    center_confirmed: dict[str, int] = {}
    pending = 0
    item_specs: list[dict] = []
    for cluster in clusters:
        task = (db.query(AdjudicationTask)
                .filter(AdjudicationTask.cluster_id == cluster.id).first())
        outcome = _cluster_outcome(cluster, task)
        cls = outcome["classification"]
        class_counts[cls] = class_counts.get(cls, 0) + 1
        if cls == CLASS_PENDING:
            pending += 1
        member_cand_ids = [m.candidate_id for m in cluster.members]
        primary_center = cluster.center_codes[0] if cluster.center_codes else ""
        for code in cluster.center_codes:
            center_total[code] = center_total.get(code, 0) + 1
            if cls == CLASS_CONFIRMED:
                center_confirmed[code] = center_confirmed.get(code, 0) + 1
        last_contact = max(
            (c.known_date for c in
             db.query(EventCandidate).filter(EventCandidate.id.in_(member_cand_ids))
             if c.known_date), default=None)
        item_specs.append({
            "cluster_id": cluster.id,
            "patient_id": cluster.patient_ids[0] if cluster.patient_ids else "",
            "center_code": primary_center, "classification": cls,
            "event_date": outcome["event_date"], "known_date": outcome["known_date"],
            "last_contact_date": last_contact,
            "source_candidate_ids": member_cand_ids, "mode": outcome["mode"]})

    coverage = {}
    for code in sorted(center_total):
        coverage[code] = {
            "clusters": center_total[code],
            "confirmed": center_confirmed.get(code, 0),
            "confirmation_rate": (round(center_confirmed.get(code, 0)
                                        / center_total[code], 4)
                                  if center_total[code] else None),
        }
    manifest = {
        "rule_version": RULE_VERSION,
        "classification_counts": class_counts,
        "center_confirmation_coverage": coverage,
    }
    items_payload = [{k: v for k, v in spec.items()} for spec in item_specs]
    snapshot_hash = content_hash(
        {"version": version, "manifest": manifest, "items": items_payload})

    # All values including the hash are ready before the first flush/insert.
    snap = AdjudicationSnapshot(
        version=version, rule_version=RULE_VERSION, status="frozen",
        item_count=len(clusters), pending_count=pending, manifest=manifest,
        snapshot_hash=snapshot_hash, created_by=user_id)
    db.add(snap)
    db.flush()
    for spec in item_specs:
        db.add(AdjudicationSnapshotItem(snapshot_id=snap.id, **spec))
    db.commit()
    db.refresh(snap)
    return snap
