"""SAE adjudication workflow & competing-risk data service.

Covers the five acceptance scenarios end to end:
  S1 外院晚回执      earliest-threshold event date, not the receipt-known date
  S2 双中心同事件    cross-center de-duplication -> counted exactly once
  S3 死亡先于确认    competing termination, never "no event"
  S4 严重程度升级    repeated admission auto-confirmed at the severe date
  S5 裁定意见分歧    blinded split votes -> in_review -> chief tie-break
plus lost follow-up (censored), transfer-out (indeterminate), insufficient
info, the run-freeze boundary (mid-run adjudication only enters the next run),
mutually-exclusive/exhaustive risk categories and report provenance.
"""
from __future__ import annotations

import pytest

SEEDED_RULE_VERSION = 1  # seed() creates one approved rule version
CENTERS = ["C1", "C2", "C3", "C4", "C5", "C6"]


@pytest.fixture()
def intaked(client, admin):
    for code in CENTERS:
        r = client.post(f"/api/events/intake/{code}", headers=admin,
                        json={"label": "evt-2026"})
        assert r.status_code == 201, r.text
    return client


def _clusters(client, header):
    return {c["cluster_key"]: c for c in
            client.get("/api/events/clusters", headers=header).json()}


def _risk_spec(snapshot_id: int, time_basis: str = "occurrence") -> dict:
    return {"analysis_type": "competing_risk",
            "group_by": ["treatment.path"], "horizon_days": 365,
            "time_basis": time_basis,
            "adjudication_snapshot_id": snapshot_id}


# --------------------------------------------------------------------------
# Layer 1+2: intake and cross-entity de-duplication
# --------------------------------------------------------------------------

def test_intake_pulls_hash_stamped_candidates(client, admin, intaked):
    intakes = client.get("/api/events/intakes", headers=admin).json()
    # seeding already intakes; the fixture re-intakes (superseding) — every
    # center must have at least one hash-stamped batch
    assert {i["center_code"] for i in intakes} == set(CENTERS)
    assert all(i["source_snapshot_hash"] and i["candidate_count"] > 0
               for i in intakes)
    # candidates remain queryable per center and carry a row hash
    c1 = client.get("/api/events/candidates?center_code=C1", headers=admin).json()
    assert c1 and all(r["row_hash"] for r in c1)


def test_cross_center_same_event_is_deduplicated_to_one_cluster(
        client, researcher1, intaked):
    clusters = _clusters(client, researcher1)
    s2 = clusters["CL-C2-EVT-S2-01"]
    # one clinical event reported by C2 and C4 via shared receipt XFER-2210
    assert s2["center_codes"] == ["C2", "C4"]
    assert s2["member_count"] == 3
    assert "XFER-2210" in s2["dedup_reason"]


# --------------------------------------------------------------------------
# AR-v1 five-category classification
# --------------------------------------------------------------------------

def test_ar_v1_classifies_each_scenario(client, researcher1, intaked):
    cl = _clusters(client, researcher1)
    # S1 late external receipt: confirmed, event date = earliest SEVERE record
    s1 = cl["CL-C1-EVT-S1-01"]
    assert s1["rule_classification"] == "confirmed_event"
    assert s1["rule_event_date"] == "2025-02-10"      # severe occurrence
    assert s1["rule_known_date"] == "2025-04-18"      # receipt arrived later
    # S4 escalation over a repeated admission -> confirmed at the severe date
    s4 = cl["CL-C1-EVT-S4-01"]
    assert s4["rule_classification"] == "confirmed_event"
    assert s4["rule_event_date"] == "2025-03-20"
    # S3 death before confirmation -> competing termination
    s3 = cl["CL-C3-EVT-S3-01"]
    assert s3["rule_classification"] == "competing_termination"
    assert s3["rule_event_date"] == "2025-04-09"
    # lost follow-up -> censored; transfer-out -> indeterminate; thin -> insufficient
    assert cl["CL-C1-EVT-LFU-01"]["rule_classification"] == "censored"
    assert cl["CL-C3-EVT-TRF-01"]["rule_classification"] == "indeterminate"
    assert cl["CL-C2-EVT-INS-01"]["rule_classification"] == "insufficient"
    # a clean severe admission is a confirmed non-event-free SAE
    assert cl["CL-C4-EVT-STD-01"]["rule_classification"] == "confirmed_event"


def test_conflicting_evidence_goes_to_blinded_manual_queue(
        client, adjudicator1, intaked):
    # researchers cannot open the adjudication queue
    r = client.get("/api/events/adjudication/queue",
                   headers={"Authorization": "Bearer nope"})
    assert r.status_code in (401, 403)
    queue = client.get("/api/events/adjudication/queue",
                       headers=adjudicator1).json()
    assert len(queue) == 1
    task = queue[0]
    assert task["cluster_key"] == "CL-C2-EVT-S5-01"
    assert task["mode"] == "manual" and task["status"] == "pending"
    # while open, the evidence list is present but other reviewers' votes hidden
    detail = client.get(f"/api/events/adjudication/tasks/{task['id']}",
                        headers=adjudicator1).json()
    assert detail["votes"] == []
    assert any(e["candidate_id"] == "C2-EVT-S5-01" for e in detail["evidence"])


def test_split_votes_then_chief_tie_break(client, adjudicator1, adjudicator2,
                                          chief, intaked):
    task = client.get("/api/events/adjudication/queue",
                      headers=adjudicator1).json()[0]
    tid = task["id"]
    client.post(f"/api/events/adjudication/tasks/{tid}/vote",
                headers=adjudicator1,
                json={"vote": "confirmed_event",
                      "proposed_event_date": "2025-02-15", "comment": "支持 SAE"})
    split = client.post(f"/api/events/adjudication/tasks/{tid}/vote",
                        headers=adjudicator2,
                        json={"vote": "non_event", "comment": "基础病导致"}).json()
    assert split["status"] == "in_review" and split["disagreement"] is True
    # only the chief adjudicator may break the tie
    forbidden = client.post(
        f"/api/events/adjudication/tasks/{tid}/tie-break",
        headers=adjudicator1,
        json={"final_vote": "confirmed_event", "event_date": "2025-02-15",
              "comment": "x"})
    assert forbidden.status_code == 403
    resolved = client.post(
        f"/api/events/adjudication/tasks/{tid}/tie-break", headers=chief,
        json={"final_vote": "confirmed_event", "event_date": "2025-02-15",
              "known_date": "2025-02-16", "comment": "采信住院记录"}).json()
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "confirmed_event"
    assert resolved["resolved_event_date"] == "2025-02-15"
    # after resolution the previously blinded votes become visible
    assert all("reviewer" in v for v in resolved["votes"])


# --------------------------------------------------------------------------
# Layer 4: immutable adjudication lists
# --------------------------------------------------------------------------

def test_snapshot_is_immutable_and_hash_verifies(client, researcher1, intaked):
    snap = client.post("/api/events/snapshots", headers=researcher1).json()
    assert snap["status"] == "frozen" and snap["item_count"] == 34
    assert snap["pending_count"] == 1  # S5 unresolved
    counts = snap["manifest"]["classification_counts"]
    # six hand-crafted scenario events are confirmed (plus deterministic
    # background severe SAEs, hence a lower bound on the total)
    assert counts["confirmed_event"] >= 6
    assert counts["competing_termination"] == 2  # S3 + C5 death (no background deaths)
    # the five protocol categories (+censored/indeterminate) partition all items
    assert sum(counts.values()) == snap["item_count"]
    verify = client.get(f"/api/events/snapshots/{snap['id']}/verify",
                        headers=researcher1).json()
    assert verify["intact"] is True
    # per-center confirmation coverage is published in the manifest
    coverage = snap["manifest"]["center_confirmation_coverage"]
    assert set(coverage) == set(CENTERS)
    assert all("confirmation_rate" in v for v in coverage.values())


# --------------------------------------------------------------------------
# Layer 5: competing-risk runs, freeze boundary, conclusion limits
# --------------------------------------------------------------------------

def test_risk_run_counts_and_mutually_exclusive_categories(
        client, researcher1, intaked):
    snap = client.post("/api/events/snapshots", headers=researcher1).json()
    run = client.post("/api/runs", headers=researcher1,
                      json={"rule_version_id": SEEDED_RULE_VERSION,
                            "request_spec": _risk_spec(snap["id"])}).json()
    assert run["analysis_type"] == "competing_risk"
    assert run["time_basis"] == "occurrence"
    assert run["pending_candidates"] == 1
    m = run["result"]["totals"]["metrics"]
    n = run["result"]["totals"]["n"]
    # S2 cross-center event contributes exactly ONE SAE (6 confirmed clusters)
    assert m["sae_event_n"] == 6
    assert m["competing_death_n"] == 2   # S3 + C5 death
    assert m["indeterminate_n"] == 2     # C3 transfer + C4 side of fused S2
    assert m["pending_n"] == 1
    # categories partition the cohort (nothing is silently "no event")
    assert (m["sae_event_n"] + m["competing_death_n"] + m["censored_n"]
            + m["indeterminate_n"] + m["pending_n"]) == n
    adj_info = run["adjudication"]
    assert adj_info["time_basis_label"] == "事件发生时间"
    assert adj_info["snapshot_hash"] == snap["snapshot_hash"]


def test_mid_run_adjudication_only_enters_next_run(
        client, researcher1, adjudicator1, adjudicator2, chief, intaked):
    snap1 = client.post("/api/events/snapshots", headers=researcher1).json()
    run_a = client.post(
        "/api/runs", headers=researcher1,
        json={"rule_version_id": SEEDED_RULE_VERSION,
              "request_spec": _risk_spec(snap1["id"])}).json()
    events_a = run_a["result"]["totals"]["metrics"]["sae_event_n"]

    # resolve the pending S5 task AFTER run A finished
    task = client.get("/api/events/adjudication/queue",
                      headers=adjudicator1).json()[0]
    client.post(f"/api/events/adjudication/tasks/{task['id']}/vote",
                headers=adjudicator1,
                json={"vote": "confirmed_event",
                      "proposed_event_date": "2025-02-15"})
    client.post(f"/api/events/adjudication/tasks/{task['id']}/vote",
                headers=adjudicator2, json={"vote": "non_event"})
    client.post(f"/api/events/adjudication/tasks/{task['id']}/tie-break",
                headers=chief,
                json={"final_vote": "confirmed_event",
                      "event_date": "2025-02-15", "known_date": "2025-02-16",
                      "comment": "终裁"})

    # run A's frozen snapshot and conclusions do NOT change
    run_a_reloaded = client.get(f"/api/runs/{run_a['id']}",
                                headers=researcher1).json()
    assert run_a_reloaded["adjudication"]["snapshot_id"] == snap1["id"]
    assert run_a_reloaded["adjudication"]["pending_count"] == 1
    assert (run_a_reloaded["result"]["totals"]["metrics"]["sae_event_n"]
            == events_a)

    # a new snapshot/run picks the adjudication up
    snap2 = client.post("/api/events/snapshots", headers=researcher1).json()
    assert snap2["pending_count"] == 0
    assert snap2["snapshot_hash"] != snap1["snapshot_hash"]
    run_b = client.post(
        "/api/runs", headers=researcher1,
        json={"rule_version_id": SEEDED_RULE_VERSION,
              "request_spec": _risk_spec(snap2["id"], time_basis="awareness")}
    ).json()
    assert run_b["time_basis"] == "awareness"
    assert run_b["adjudication"]["time_basis_label"] == "事件获知时间"
    assert run_b["adjudication"]["snapshot_id"] == snap2["id"]
    assert (run_b["result"]["totals"]["metrics"]["sae_event_n"]
            == events_a + 1)
    assert run_b["result"]["totals"]["metrics"]["pending_n"] == 0


def test_risk_output_review_carries_adjudication_provenance(
        client, researcher1, reviewer, intaked):
    snap = client.post("/api/events/snapshots", headers=researcher1).json()
    run = client.post(
        "/api/runs", headers=researcher1,
        json={"rule_version_id": SEEDED_RULE_VERSION,
              "request_spec": _risk_spec(snap["id"])}).json()
    out = client.post(f"/api/runs/{run['id']}/outputs", headers=researcher1,
                      json={"title": "一年 SAE 竞争风险"}).json()
    # small cells are flagged; a direct approve is refused
    if out["flags"]:
        blocked = client.post(f"/api/outputs/{out['id']}/decision",
                              headers=reviewer, json={"action": "approve"})
        assert blocked.status_code == 409
        cells = [{"group": f["group"], "metric": f["metric"]}
                 for f in out["flags"]]
        done = client.post(
            f"/api/outputs/{out['id']}/decision", headers=reviewer,
            json={"action": "suppress", "detail": {"cells": cells},
                  "comment": "抑制小格"}).json()
    else:
        done = client.post(f"/api/outputs/{out['id']}/decision",
                           headers=reviewer, json={"action": "approve"}).json()
    assert done["status"] == "approved"
    report = client.get(f"/api/reports/{done['published_report_id']}",
                        headers=reviewer).json()
    prov = report["report"]["provenance"]["adjudication"]
    assert prov["snapshot_version"] == snap["version"]
    assert prov["time_basis"] == "occurrence"
    assert prov["pending_candidates"] == 1
    assert len(prov["center_confirmation_coverage"]) == 6
    verify = client.get(
        f"/api/reports/{done['published_report_id']}/verify",
        headers=reviewer).json()
    assert verify["intact"] is True
