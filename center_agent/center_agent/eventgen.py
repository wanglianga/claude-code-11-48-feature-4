"""Deterministic fictional Serious Adverse Event (SAE) candidate generator.

Every center keeps its event candidates LOCALLY: this module writes
``events.csv`` next to ``registry.csv``. Each row is one *candidate record* —
a mild symptom report, a hospitalization, an external-hospital receipt
(外院回执), a death record or a lost-follow-up note — not yet an adjudicated
event. The platform later ingests, de-duplicates and adjudicates them.

The dataset deliberately contains the five acceptance scenarios:

  S1 外院晚回执        external receipt arrives after the run already saw a
                       mild-only candidate; the same clinical event must not
                       be counted twice and its event date is the earliest
                       threshold date, not the receipt date.
  S2 双中心同事件       one clinical event reported by two centers (transfer);
                       cross-center de-duplication yields exactly one event.
  S3 死亡先于确认       patient dies before severity is confirmed -> competing
                       termination (death), never "no event".
  S4 严重程度升级       mild -> severe escalation with repeated admissions;
                       rule mode may misjudge, manual mode adjudicates.
  S5 裁定意见分歧       blinded reviewers disagree -> resolution workflow.
Plus lost-to-follow-up (删失), transfer-out (无法判定) and insufficient-info
(信息不足) rows so every adjudication category is exercised.

Usage:
    python -m center_agent.eventgen --root data/centers            # all centers
    python -m center_agent.eventgen --root data/centers --center C1
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

# Canonical candidate schema (column names are identical across centers on
# purpose: event candidates follow the protocol's common SAE form, unlike the
# registry which uses center-local field names).
FIELDS = [
    "candidate_id", "patient_id", "event_type", "source_kind",
    "occurrence_date", "known_date", "severity", "is_fatal",
    "external_hospital", "external_receipt_id", "linked_candidate_id",
    "death_date", "death_cause", "evidence", "report_center", "notes",
]

EVENT_TYPES = ["sae", "death", "lost_fu", "transfer_out"]
SOURCE_KINDS = ["mild_symptom", "admission", "readmission", "external_receipt",
                "death_record", "fu_note"]
SEVERITIES = ["mild", "moderate", "severe"]

# Per-center deterministic seed.
DEFAULT_SEEDS = {"C1": 5201, "C2": 5202, "C3": 5203,
                 "C4": 5204, "C5": 5205, "C6": 5206}
CENTER_SIZES = {"C1": 180, "C2": 150, "C3": 220, "C4": 160, "C5": 140, "C6": 12}


def _row(**kw) -> dict:
    out = {f: "" for f in FIELDS}
    out.update(kw)
    return out


# Scenario patient numbers, kept within each center's registry size so the
# event/risk-set service can join them to registry.csv (ids are {C}-P{nnnn}).
# Background rows avoid these ids (see SCENARIO_PATIENTS).
SCENARIO_PATIENTS = {
    "C1": [171, 172, 173], "C2": [141, 142, 143], "C3": [211, 212],
    "C4": [151, 152], "C5": [131, 132], "C6": [10, 11],
}


def _scenario_rows(center: str) -> list[dict]:
    """Deterministic, hand-crafted acceptance-scenario candidates.

    Patient ids use the same ``{center}-P{nnnn}`` scheme as registry.csv so a
    run can join the cohort to the event/risk-set service.
    """
    p = lambda n: f"{center}-P{n:04d}"  # noqa: E731
    rows: list[dict] = []

    if center == "C1":
        # ---- S1: external-hospital LATE receipt, one clinical event ---------
        # Mild symptom first; the external receipt confirming severity arrives
        # much later. Event date must be the earliest threshold (severe) date.
        rows += [
            _row(candidate_id="C1-EVT-S1-01", patient_id=p(171), event_type="sae",
                 source_kind="mild_symptom", occurrence_date="2025-02-03",
                 known_date="2025-02-05", severity="mild", is_fatal="0",
                 evidence="门诊主诉轻微皮疹", report_center="C1",
                 notes="S1 首发轻微症状"),
            _row(candidate_id="C1-EVT-S1-02", patient_id=p(171), event_type="sae",
                 source_kind="external_receipt", occurrence_date="2025-02-10",
                 known_date="2025-04-18", severity="severe", is_fatal="0",
                 external_hospital="市立医院", external_receipt_id="EXT-7781",
                 linked_candidate_id="C1-EVT-S1-01",
                 evidence="外院回执：Stevens-Johnson 综合征（重度）",
                 report_center="C1", notes="S1 外院晚回执（获知晚于发生）"),
        ]
        # ---- S4: severity escalation with a repeated admission --------------
        rows += [
            _row(candidate_id="C1-EVT-S4-01", patient_id=p(172), event_type="sae",
                 source_kind="admission", occurrence_date="2025-03-01",
                 known_date="2025-03-02", severity="moderate", is_fatal="0",
                 evidence="首次住院：肝功能异常 2 级", report_center="C1",
                 notes="S4 首次住院（中度）"),
            _row(candidate_id="C1-EVT-S4-02", patient_id=p(172), event_type="sae",
                 source_kind="readmission", occurrence_date="2025-03-20",
                 known_date="2025-03-21", severity="severe", is_fatal="0",
                 linked_candidate_id="C1-EVT-S4-01",
                 evidence="重复住院：急性肝衰竭 4 级", report_center="C1",
                 notes="S4 严重程度升级（重复住院）"),
        ]
        # ---- plain lost-to-follow-up: right-censoring, not "no event" -------
        rows.append(_row(candidate_id="C1-EVT-LFU-01", patient_id=p(173),
                         event_type="lost_fu", source_kind="fu_note",
                         occurrence_date="2025-05-10", known_date="2025-06-01",
                         severity="", is_fatal="0",
                         evidence="连续两次随访失联，末次随访 2025-05-10",
                         report_center="C1", notes="失访 → 删失"))

    if center == "C2":
        # ---- S2a: same clinical event reported at C2 first -------------------
        rows += [
            _row(candidate_id="C2-EVT-S2-01", patient_id=p(141), event_type="sae",
                 source_kind="admission", occurrence_date="2025-01-12",
                 known_date="2025-01-13", severity="severe", is_fatal="0",
                 external_receipt_id="XFER-2210",
                 evidence="C2 住院：重度骨髓抑制，后转 C4", report_center="C2",
                 notes="S2 同一临床事件（C2 侧，含转院单号 XFER-2210）"),
        ]
        # ---- S5a: conflicting candidates that split the blinded panel --------
        rows += [
            _row(candidate_id="C2-EVT-S5-01", patient_id=p(142), event_type="sae",
                 source_kind="admission", occurrence_date="2025-02-15",
                 known_date="2025-02-16", severity="severe", is_fatal="0",
                 evidence="住院记录：急性肾损伤 3 级（与研究药物相关）",
                 report_center="C2", notes="S5 证据冲突：支持为 SAE"),
            _row(candidate_id="C2-EVT-S5-02", patient_id=p(142), event_type="sae",
                 source_kind="fu_note", occurrence_date="2025-02-20",
                 known_date="2025-02-22", severity="mild", is_fatal="0",
                 linked_candidate_id="C2-EVT-S5-01",
                 evidence="随访备注：既往脱水史，判定与基础病相关",
                 report_center="C2", notes="S5 证据冲突：支持为非事件"),
        ]
        # ---- insufficient information: 信息不足 ------------------------------
        rows.append(_row(candidate_id="C2-EVT-INS-01", patient_id=p(143),
                         event_type="sae", source_kind="fu_note",
                         occurrence_date="", known_date="2025-03-15",
                         severity="", is_fatal="0",
                         evidence="电话口述“曾住院”，无病历、无日期、无严重程度",
                         report_center="C2", notes="信息不足"))

    if center == "C3":
        # ---- S3: death BEFORE the event is confirmed -------------------------
        rows += [
            _row(candidate_id="C3-EVT-S3-01", patient_id=p(211), event_type="sae",
                 source_kind="mild_symptom", occurrence_date="2025-04-02",
                 known_date="2025-04-03", severity="mild", is_fatal="0",
                 evidence="轻微呼吸困难", report_center="C3",
                 notes="S3 事件未确认（仅轻微症状）"),
            _row(candidate_id="C3-EVT-S3-02", patient_id=p(211),
                 event_type="death", source_kind="death_record",
                 occurrence_date="2025-04-09", known_date="2025-04-10",
                 severity="severe", is_fatal="1", death_date="2025-04-09",
                 death_cause="呼吸衰竭（疑似与药物相关，未完成确认）",
                 linked_candidate_id="C3-EVT-S3-01",
                 evidence="死亡医学证明", report_center="C3",
                 notes="S3 死亡先于确认 → 竞争终止"),
        ]
        # ---- transfer out: 无法判定 (outcome unknown after transfer) --------
        rows.append(_row(candidate_id="C3-EVT-TRF-01", patient_id=p(212),
                         event_type="transfer_out", source_kind="fu_note",
                         occurrence_date="2025-05-20", known_date="2025-05-22",
                         severity="", is_fatal="0",
                         evidence="转外省治疗，之后结局不可得",
                         report_center="C3", notes="转院 → 无法判定"))

    if center == "C4":
        # ---- S2b: the OTHER half of the dual-center event --------------------
        # Same transfer receipt id XFER-2210 and a shared linkage key so the
        # de-duplicator can fuse C2-EVT-S2-01 + C4-EVT-S2-02 into one event.
        rows += [
            _row(candidate_id="C4-EVT-S2-01", patient_id=p(151), event_type="sae",
                 source_kind="admission", occurrence_date="2025-01-14",
                 known_date="2025-01-15", severity="severe", is_fatal="0",
                 external_receipt_id="XFER-2210",
                 evidence="C4 接续住院：同一转院单号 XFER-2210",
                 report_center="C4", notes="S2 同一临床事件（C4 侧）"),
            _row(candidate_id="C4-EVT-S2-02", patient_id=p(151), event_type="sae",
                 source_kind="external_receipt", occurrence_date="2025-01-12",
                 known_date="2025-02-01", severity="severe", is_fatal="0",
                 external_hospital="北方儿童医院", external_receipt_id="XFER-2210",
                 linked_candidate_id="C4-EVT-S2-01",
                 evidence="外院回执引用 XFER-2210，确认两院为同一事件",
                 report_center="C4", notes="S2 跨院回执（去重锚点）"),
        ]
        # ---- a normal, clean confirmed SAE (baseline event) -----------------
        rows.append(_row(candidate_id="C4-EVT-STD-01", patient_id=p(152),
                         event_type="sae", source_kind="admission",
                         occurrence_date="2025-06-05", known_date="2025-06-06",
                         severity="severe", is_fatal="0",
                         evidence="住院：重度感染性肺炎，资料完整",
                         report_center="C4", notes="标准已确认 SAE"))

    if center == "C5":
        # ---- another late external receipt (auto-mode misjudgement bait) -----
        rows += [
            _row(candidate_id="C5-EVT-S1-01", patient_id=p(131), event_type="sae",
                 source_kind="mild_symptom", occurrence_date="2025-03-08",
                 known_date="2025-03-09", severity="mild", is_fatal="0",
                 evidence="轻度乏力", report_center="C5",
                 notes="外院晚回执（自动模式易只按轻度处理）"),
            _row(candidate_id="C5-EVT-S1-02", patient_id=p(131), event_type="sae",
                 source_kind="external_receipt", occurrence_date="2025-03-12",
                 known_date="2025-05-30", severity="severe", is_fatal="0",
                 external_hospital="省人民医院", external_receipt_id="EXT-9032",
                 linked_candidate_id="C5-EVT-S1-01",
                 evidence="外院回执：重度心肌炎", report_center="C5",
                 notes="外院晚回执"),
        ]
        # ---- death competing risk (clean) ------------------------------------
        rows.append(_row(candidate_id="C5-EVT-DTH-01", patient_id=p(132),
                         event_type="death", source_kind="death_record",
                         occurrence_date="2025-07-01", known_date="2025-07-02",
                         severity="severe", is_fatal="1", death_date="2025-07-01",
                         death_cause="疾病进展", evidence="死亡医学证明",
                         report_center="C5", notes="竞争终止（死亡）"))

    if center == "C6":
        # Tiny center: one confirmed SAE + one lost follow-up.
        rows += [
            _row(candidate_id="C6-EVT-STD-01", patient_id=p(10), event_type="sae",
                 source_kind="admission", occurrence_date="2025-04-15",
                 known_date="2025-04-16", severity="severe", is_fatal="0",
                 evidence="住院：重度过敏反应", report_center="C6",
                 notes="标准已确认 SAE（小中心）"),
            _row(candidate_id="C6-EVT-LFU-01", patient_id=p(11),
                 event_type="lost_fu", source_kind="fu_note",
                 occurrence_date="2025-05-05", known_date="2025-05-20",
                 severity="", is_fatal="0", evidence="失访，末次随访 2025-05-05",
                 report_center="C6", notes="失访 → 删失"),
        ]
    return rows


def _random_background_rows(center: str, rng: random.Random) -> list[dict]:
    """Low-volume random background candidates so lists are not only scenarios."""
    rows = []
    reserved = set(SCENARIO_PATIENTS[center])
    n = max(2, CENTER_SIZES[center] // 40)
    for i in range(n):
        num = rng.randint(1, CENTER_SIZES[center])
        while num in reserved:
            num = rng.randint(1, CENTER_SIZES[center])
        pid = f"{center}-P{num:04d}"
        kind = rng.choice(["admission", "mild_symptom", "fu_note"])
        sev = rng.choice(SEVERITIES)
        month = rng.randint(1, 9)
        occ = f"2025-{month:02d}-{rng.randint(1, 28):02d}"
        if kind == "fu_note":
            rows.append(_row(candidate_id=f"{center}-EVT-BG-{i+1:02d}",
                             patient_id=pid, event_type="lost_fu",
                             source_kind="fu_note", occurrence_date=occ,
                             known_date=occ, severity="", is_fatal="0",
                             evidence="背景：随访备注", report_center=center,
                             notes="背景失访候选"))
        else:
            rows.append(_row(candidate_id=f"{center}-EVT-BG-{i+1:02d}",
                             patient_id=pid, event_type="sae", source_kind=kind,
                             occurrence_date=occ, known_date=occ, severity=sev,
                             is_fatal="0",
                             evidence=f"背景：{kind}，严重程度 {sev}",
                             report_center=center, notes="背景候选"))
    return rows


def generate_center_events(center: str, root: Path,
                           seed: int | None = None) -> dict:
    rng = random.Random(seed if seed is not None else DEFAULT_SEEDS[center])
    rows = _scenario_rows(center) + _random_background_rows(center, rng)
    # Stable ordering: by known date then candidate id.
    rows.sort(key=lambda r: (r["known_date"] or "9999", r["candidate_id"]))
    cdir = root / center
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / "events.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["event_type"]] = by_type.get(r["event_type"], 0) + 1
    meta = {
        "center": center, "seed": seed if seed is not None else DEFAULT_SEEDS[center],
        "candidate_count": len(rows), "by_type": by_type,
        "schema_version": "event-v1",
    }
    (cdir / "events_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/centers")
    ap.add_argument("--center", choices=list(DEFAULT_SEEDS), default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    root = Path(args.root)
    centers = [args.center] if args.center else list(DEFAULT_SEEDS)
    for c in centers:
        meta = generate_center_events(c, root, seed=args.seed)
        print(f"{c}: {meta['candidate_count']} event candidates -> "
              f"{root / c / 'events.csv'}")


if __name__ == "__main__":
    main()
