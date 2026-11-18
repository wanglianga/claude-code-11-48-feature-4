"""ORM models for the controlled multi-center analysis platform."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32))  # data_admin | researcher | reviewer
    display_name: Mapped[str] = mapped_column(String(128))


class Center(Base):
    __tablename__ = "centers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)  # C1..C6
    name: Mapped[str] = mapped_column(String(128))
    agent_url: Mapped[str] = mapped_column(String(256))


class Dictionary(Base):
    __tablename__ = "dictionaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    variables: Mapped[list["Variable"]] = relationship(back_populates="dictionary")


class Variable(Base):
    __tablename__ = "variables"
    __table_args__ = (UniqueConstraint("dict_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dict_id: Mapped[int] = mapped_column(ForeignKey("dictionaries.id"))
    name: Mapped[str] = mapped_column(String(64))  # e.g. followup.outcome
    entity: Mapped[str] = mapped_column(String(32))  # patient|diagnosis|treatment|followup
    label: Mapped[str] = mapped_column(String(128))
    value_type: Mapped[str] = mapped_column(String(16))  # categorical|numeric|date
    allowed_values: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dictionary: Mapped[Dictionary] = relationship(back_populates="variables")


class CenterMapping(Base):
    """Maps a canonical dictionary variable to a center-local CSV field."""

    __tablename__ = "center_mappings"
    __table_args__ = (UniqueConstraint("center_id", "dict_id", "variable_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    dict_id: Mapped[int] = mapped_column(ForeignKey("dictionaries.id"))
    variable_name: Mapped[str] = mapped_column(String(64))
    local_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_map: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="mapped")  # mapped|missing


class DataBatch(Base):
    """A data batch registered by a center; locked batches are immutable run inputs."""

    __tablename__ = "data_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    label: Mapped[str] = mapped_column(String(64))  # e.g. 2026Q3
    dict_id: Mapped[int] = mapped_column(ForeignKey("dictionaries.id"))
    status: Mapped[str] = mapped_column(String(16), default="registered")
    # registered -> locked -> superseded (when a newer batch is locked)
    record_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    available_scope: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    center: Mapped[Center] = relationship()


class CohortRule(Base):
    __tablename__ = "cohort_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("cohort_rules.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    # draft -> in_review -> approved | rejected ; a newer draft supersedes for editing
    status: Mapped[str] = mapped_column(String(16), default="draft")
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    conditions_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule: Mapped[CohortRule] = relationship()


class Run(Base):
    """One orchestrated multi-center execution of an approved rule version."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.id"))
    request_spec: Mapped[dict] = mapped_column(JSON)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending -> running -> completed | completed_with_gaps | failed
    k_threshold: Mapped[int] = mapped_column(Integer, default=5)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # --- Event adjudication / competing-risk freeze (Feature 4) ---------------
    # Snapshot of the immutable adjudication list used by THIS run; adjudications
    # finished while the run is in progress only enter the NEXT run.
    adjudication_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("adjudication_snapshots.id"), nullable=True)
    time_basis: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # occurrence (事件发生时间) | awareness (获知时间)
    pending_candidates: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_version: Mapped[RuleVersion] = relationship()
    center_results: Mapped[list["RunCenterResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    batches: Mapped[list["RunBatch"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    risk_results: Mapped[list["RunRiskResult"]] = relationship(
        cascade="all, delete-orphan")


class RunBatch(Base):
    """Immutable snapshot: which locked batch each center contributed to a run."""

    __tablename__ = "run_batches"
    __table_args__ = (UniqueConstraint("run_id", "center_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("data_batches.id"))
    batch_label: Mapped[str] = mapped_column(String(64))
    run: Mapped[Run] = relationship(back_populates="batches")
    batch: Mapped[DataBatch] = relationship()


class RunCenterResult(Base):
    __tablename__ = "run_center_results"
    __table_args__ = (UniqueConstraint("run_id", "center_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending | success | failed | missing_mapping
    aggregates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run: Mapped[Run] = relationship(back_populates="center_results")
    center: Mapped[Center] = relationship()


class OutputTable(Base):
    """An aggregated result table a researcher wants to export; needs review."""

    __tablename__ = "output_tables"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    title: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(24), default="pending_review")
    # pending_review | approved | rejected
    table_data: Mapped[dict] = mapped_column(JSON)  # groups + totals + coverage
    flags: Mapped[list] = mapped_column(JSON, default=list)  # small-cell findings
    k_threshold: Mapped[int] = mapped_column(Integer, default=5)
    run: Mapped[Run] = relationship()


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("output_tables.id"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(16))  # approve | suppress | merge | reject
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    output: Mapped[OutputTable] = relationship()


class PublishedReport(Base):
    """Approved output frozen with full provenance for reproducibility."""

    __tablename__ = "published_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("output_tables.id"), unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.id"))
    decision_id: Mapped[int] = mapped_column(ForeignKey("review_decisions.id"))
    published_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report: Mapped[dict] = mapped_column(JSON)  # frozen table + provenance + hashes
    report_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[OutputTable] = relationship()


# ===========================================================================
# Feature 4: SAE adjudication workflow & competing-risk data service
#
# Five separately-owned, auditable state layers:
#   1. candidate intake ......... EventIntakeBatch / EventCandidate (center-local
#                                 originals pulled in with a content hash)
#   2. entity de-duplication .... EventCluster / EventClusterMember (one clinical
#                                 event across hospitals is counted once)
#   3. adjudication queue ....... AdjudicationTask / AdjudicationVote (rule-based
#                                 auto decisions vs blinded manual panel)
#   4. immutable decision list .. AdjudicationSnapshot / ...Item (frozen for runs)
#   5. statistical risk set ..... RunRiskResult (per-center competing-risk
#                                 aggregates) feeding the existing review layer
# ===========================================================================

# Candidate lifecycle statuses and the five protocol classifications.
CANDIDATE_ACTIVE = "active"
CLASS_CONFIRMED = "confirmed_event"          # 已裁定事件
CLASS_PENDING = "pending_adjudication"       # 待裁定
CLASS_NON_EVENT = "non_event"                # 非事件
CLASS_COMPETING = "competing_termination"    # 竞争终止（死亡）
CLASS_INSUFFICIENT = "insufficient"          # 信息不足
RISK_CENSORED = "censored"                   # 删失（失访/分析截点）
RISK_INDETERMINATE = "indeterminate"         # 无法判定（转院等）

ADJUDICATION_CLASSES = (
    CLASS_CONFIRMED, CLASS_PENDING, CLASS_NON_EVENT,
    CLASS_COMPETING, CLASS_INSUFFICIENT,
)


class EventIntakeBatch(Base):
    """One audited pull of a center's locally-stored event candidates."""

    __tablename__ = "event_intake_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    label: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="imported")
    # imported -> superseded by a newer intake of the same center
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    center: Mapped[Center] = relationship()


class EventCandidate(Base):
    """A raw event candidate record (mild symptom, admission, external-hospital
    receipt, death record or follow-up note). The center keeps the original
    events.csv; this row is the hash-stamped audited copy."""

    __tablename__ = "event_candidates"
    __table_args__ = (UniqueConstraint("intake_batch_id", "candidate_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    intake_batch_id: Mapped[int] = mapped_column(ForeignKey("event_intake_batches.id"))
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    patient_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(24))      # sae|death|lost_fu|transfer_out
    source_kind: Mapped[str] = mapped_column(String(32))
    occurrence_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    known_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_fatal: Mapped[bool] = mapped_column(Boolean, default=False)
    external_hospital: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_receipt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    death_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    death_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_center: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=CANDIDATE_ACTIVE)
    intake_batch: Mapped[EventIntakeBatch] = relationship()
    center: Mapped[Center] = relationship()


class EventCluster(Base):
    """A de-duplicated clinical event: one or more candidates (possibly from
    two centers) fused because they describe the same occurrence."""

    __tablename__ = "event_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    patient_ids: Mapped[list] = mapped_column(JSON, default=list)
    center_codes: Mapped[list] = mapped_column(JSON, default=list)
    # Output of the preset shared rule (AR-v1); manual adjudication may override.
    rule_classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rule_event_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rule_known_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    dedup_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Live resolution state, mirrored from the adjudication task.
    final_classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    final_event_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    final_known_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    members: Mapped[list["EventClusterMember"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan")


class EventClusterMember(Base):
    __tablename__ = "event_cluster_members"
    __table_args__ = (UniqueConstraint("candidate_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("event_clusters.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("event_candidates.id"))
    cluster: Mapped[EventCluster] = relationship(back_populates="members")
    candidate: Mapped[EventCandidate] = relationship()


class AdjudicationTask(Base):
    """Queue entry for one cluster. Auto-resolvable clusters close immediately;
    conflict clusters go to a blinded manual panel."""

    __tablename__ = "adjudication_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("event_clusters.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending | in_review | resolved ; disagreement tracked via votes
    mode: Mapped[str] = mapped_column(String(16), default="auto")  # auto | manual
    rule_classification: Mapped[str] = mapped_column(String(32))
    required_votes: Mapped[int] = mapped_column(Integer, default=2)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_event_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolved_known_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolution_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cluster: Mapped[EventCluster] = relationship()
    votes: Mapped[list["AdjudicationVote"]] = relationship(
        back_populates="task", cascade="all, delete-orphan")


class AdjudicationVote(Base):
    """One blinded expert vote. Reviewers cannot see each other's votes until
    they submit; split votes put the task into the disagreement state."""

    __tablename__ = "adjudication_votes"
    __table_args__ = (UniqueConstraint("task_id", "reviewer_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("adjudication_tasks.id"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    vote: Mapped[str] = mapped_column(String(32))
    proposed_event_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    task: Mapped[AdjudicationTask] = relationship(back_populates="votes")


class AdjudicationSnapshot(Base):
    """An immutable, hash-stamped adjudication list. Runs freeze a reference to
    one snapshot; later adjudications only affect later snapshots/runs."""

    __tablename__ = "adjudication_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    rule_version: Mapped[str] = mapped_column(String(16))  # AR-v1
    status: Mapped[str] = mapped_column(String(16), default="frozen")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    # per-center confirmation coverage + classification counters
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items: Mapped[list["AdjudicationSnapshotItem"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan")


class AdjudicationSnapshotItem(Base):
    __tablename__ = "adjudication_snapshot_items"
    __table_args__ = (UniqueConstraint("snapshot_id", "cluster_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("adjudication_snapshots.id"), index=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("event_clusters.id"))
    patient_id: Mapped[str] = mapped_column(String(64), index=True)
    center_code: Mapped[str] = mapped_column(String(16))
    classification: Mapped[str] = mapped_column(String(32))
    event_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    known_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_contact_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_candidate_ids: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(16))  # auto | manual
    snapshot: Mapped[AdjudicationSnapshot] = relationship(back_populates="items")


class RunRiskResult(Base):
    """Per-center competing-risk aggregate for a run's frozen snapshot."""

    __tablename__ = "run_risk_results"
    __table_args__ = (UniqueConstraint("run_id", "center_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending | success | failed | missing_mapping
    aggregates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    center: Mapped[Center] = relationship()
