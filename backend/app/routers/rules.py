"""Cohort rule endpoints with condition-level optimistic concurrency.

Save protocol:
- Client sends the version id its editor was opened from (`base_version_id`).
- If the rule's current version moved on (a teammate saved first), the server
  answers 409 with both condition-level diffs and a merge suggestion; the
  client must confirm the merge and resubmit against the new base.
- Removing/loosening an exclusion condition that already entered review
  (in_review/approved) always requires `acknowledge_restriction_removal`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role
from ..diffing import diff_conditions, is_empty, removed_restrictions, three_way_merge
from ..models import CohortRule, RuleVersion, User
from ..security import content_hash

router = APIRouter(prefix="/api/rules", tags=["rules"])
researcher = require_role("researcher")
reviewer = require_role("reviewer")


def version_view(v: RuleVersion, db: Session) -> dict:
    creator = db.get(User, v.created_by)
    return {
        "id": v.id, "rule_id": v.rule_id, "version_no": v.version_no,
        "status": v.status, "conditions": v.conditions,
        "conditions_hash": v.conditions_hash,
        "created_by": creator.display_name if creator else v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "submitted_at": v.submitted_at.isoformat() if v.submitted_at else None,
        "review_comment": v.review_comment,
    }


def rule_view(rule: CohortRule, db: Session) -> dict:
    versions = (db.query(RuleVersion).filter(RuleVersion.rule_id == rule.id)
                .order_by(RuleVersion.version_no.desc()).all())
    reviewed = next((v for v in versions if v.status in ("in_review", "approved")), None)
    owner = db.get(User, rule.owner_id)
    return {
        "id": rule.id, "name": rule.name,
        "owner": owner.display_name if owner else None,
        "current_version_id": rule.current_version_id,
        "reviewed_version": version_view(reviewed, db) if reviewed else None,
        "versions": [version_view(v, db) for v in versions],
    }


@router.get("")
def list_rules(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rules = db.query(CohortRule).order_by(CohortRule.id).all()
    out = []
    for r in rules:
        current = db.get(RuleVersion, r.current_version_id) if r.current_version_id else None
        out.append({"id": r.id, "name": r.name,
                    "current_version_no": current.version_no if current else None,
                    "current_status": current.status if current else None,
                    "current_version_id": r.current_version_id})
    return out


@router.get("/review/queue")
def review_queue(db: Session = Depends(get_db), _: User = Depends(reviewer)):
    rows = (db.query(RuleVersion).filter(RuleVersion.status == "in_review")
            .order_by(RuleVersion.submitted_at).all())
    return [version_view(v, db) for v in rows]


class CreateRuleIn(BaseModel):
    name: str
    conditions: list[dict] = []


@router.post("", status_code=201)
def create_rule(body: CreateRuleIn, db: Session = Depends(get_db),
                user: User = Depends(researcher)):
    rule = CohortRule(name=body.name, owner_id=user.id)
    db.add(rule)
    db.flush()
    v = RuleVersion(rule_id=rule.id, version_no=1, status="draft",
                    conditions=body.conditions,
                    conditions_hash=content_hash(body.conditions),
                    created_by=user.id)
    db.add(v)
    db.flush()
    rule.current_version_id = v.id
    db.commit()
    return rule_view(rule, db)


@router.get("/{rule_id}")
def get_rule(rule_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    rule = db.get(CohortRule, rule_id)
    if not rule:
        raise HTTPException(404, "unknown rule")
    return rule_view(rule, db)


@router.get("/{rule_id}/diff")
def diff_versions(rule_id: int, from_id: int, to_id: int,
                  db: Session = Depends(get_db), _: User = Depends(current_user)):
    a, b = db.get(RuleVersion, from_id), db.get(RuleVersion, to_id)
    if not a or not b or a.rule_id != rule_id or b.rule_id != rule_id:
        raise HTTPException(404, "unknown version")
    return diff_conditions(a.conditions, b.conditions)


class SaveIn(BaseModel):
    base_version_id: int
    conditions: list[dict]
    confirm_merge: bool = False
    acknowledge_restriction_removal: bool = False


def _latest_reviewed(db: Session, rule_id: int) -> RuleVersion | None:
    return (db.query(RuleVersion)
            .filter(RuleVersion.rule_id == rule_id,
                    RuleVersion.status.in_(["in_review", "approved"]))
            .order_by(RuleVersion.version_no.desc()).first())


@router.put("/{rule_id}/save")
def save_rule(rule_id: int, body: SaveIn, db: Session = Depends(get_db),
              user: User = Depends(researcher)):
    rule = db.get(CohortRule, rule_id)
    if not rule:
        raise HTTPException(404, "unknown rule")
    base = db.get(RuleVersion, body.base_version_id)
    if not base or base.rule_id != rule_id:
        raise HTTPException(400, "base_version_id does not belong to this rule")
    current = db.get(RuleVersion, rule.current_version_id)

    # 1) Concurrent-edit detection: someone else advanced the current version.
    if base.id != current.id:
        merge = three_way_merge(base.conditions, current.conditions, body.conditions)
        return JSONResponse(status_code=409, content={
            "error": "concurrent_modification",
            "message": "规则在你编辑期间已被他人保存，请确认条件级合并后重新提交",
            "current_version_id": current.id,
            "their_diff": merge["their_diff"],
            "your_diff": merge["your_diff"],
            "conflicts": merge["conflicts"],
            "merged": merge["merged"],
        })

    # 2) Never silently drop restrictions that already entered review.
    reviewed = _latest_reviewed(db, rule_id)
    if reviewed:
        removed = removed_restrictions(reviewed.conditions, body.conditions)
        if removed and not body.acknowledge_restriction_removal:
            return JSONResponse(status_code=409, content={
                "error": "restriction_removal",
                "message": "本次保存删除/修改了已进入审核流程的排除条件，需显式确认",
                "removed_restrictions": removed,
                "reviewed_version_no": reviewed.version_no,
                "reviewed_status": reviewed.status,
            })

    if is_empty(diff_conditions(base.conditions, body.conditions)):
        return {"unchanged": True, "rule": rule_view(rule, db)}

    next_no = (db.query(RuleVersion).filter(RuleVersion.rule_id == rule_id)
               .count() + 1)
    v = RuleVersion(rule_id=rule_id, version_no=next_no, status="draft",
                    conditions=body.conditions,
                    conditions_hash=content_hash(body.conditions),
                    created_by=user.id)
    db.add(v)
    db.flush()
    rule.current_version_id = v.id
    db.commit()
    return {"unchanged": False, "rule": rule_view(rule, db)}


@router.post("/{rule_id}/versions/{version_id}/submit")
def submit_version(rule_id: int, version_id: int, db: Session = Depends(get_db),
                   user: User = Depends(researcher)):
    v = db.get(RuleVersion, version_id)
    if not v or v.rule_id != rule_id:
        raise HTTPException(404, "unknown version")
    if v.status != "draft":
        raise HTTPException(409, f"version is {v.status}, only drafts can be submitted")
    v.status = "in_review"
    v.submitted_at = datetime.now(timezone.utc)
    db.commit()
    return version_view(v, db)


class ReviewIn(BaseModel):
    comment: str | None = None


@router.post("/versions/{version_id}/approve")
def approve_version(version_id: int, body: ReviewIn, db: Session = Depends(get_db),
                    user: User = Depends(reviewer)):
    v = db.get(RuleVersion, version_id)
    if not v:
        raise HTTPException(404, "unknown version")
    if v.status != "in_review":
        raise HTTPException(409, f"version is {v.status}, only in_review can be approved")
    v.status = "approved"
    v.reviewed_by = user.id
    v.reviewed_at = datetime.now(timezone.utc)
    v.review_comment = body.comment
    db.commit()
    return version_view(v, db)


@router.post("/versions/{version_id}/reject")
def reject_version(version_id: int, body: ReviewIn, db: Session = Depends(get_db),
                   user: User = Depends(reviewer)):
    v = db.get(RuleVersion, version_id)
    if not v:
        raise HTTPException(404, "unknown version")
    if v.status != "in_review":
        raise HTTPException(409, f"version is {v.status}, only in_review can be rejected")
    v.status = "rejected"
    v.reviewed_by = user.id
    v.reviewed_at = datetime.now(timezone.utc)
    v.review_comment = body.comment
    db.commit()
    return version_view(v, db)
