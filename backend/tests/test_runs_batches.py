"""Runs: locked-batch snapshots, batch replacement, coverage gaps."""
from __future__ import annotations

from conftest import run_payload

SEEDED_RULE_VERSION = 1  # created by seed (approved)


def test_run_snapshots_locked_batches_and_ignores_replacement(
        client, researcher1, admin):
    # run A starts with the seed batch set
    r = client.post("/api/runs", headers=researcher1,
                    json=run_payload(SEEDED_RULE_VERSION))
    assert r.status_code == 201, r.text
    run_a = r.json()
    assert run_a["status"] == "completed"
    batches_a = {b["center_code"]: b["batch_id"] for b in run_a["batches"]}
    totals_a = run_a["result"]["totals"]["n"]

    # data admin replaces C2's batch mid-flight: register + lock a new one
    r = client.post("/api/admin/centers/C2/batches/sync", headers=admin,
                    json={"label": "2026Q4"})
    assert r.status_code == 200, r.text
    new_batch = r.json()
    r = client.post(f"/api/admin/batches/{new_batch['id']}/lock", headers=admin)
    assert r.status_code == 200

    # run A still references the batch locked at its start
    r = client.get(f"/api/runs/{run_a['id']}", headers=researcher1)
    batches_a_after = {b["center_code"]: b["batch_id"] for b in r.json()["batches"]}
    assert batches_a_after == batches_a
    assert r.json()["result"]["totals"]["n"] == totals_a

    # a new run picks up the replacement batch
    r = client.post("/api/runs", headers=researcher1,
                    json=run_payload(SEEDED_RULE_VERSION))
    run_b = r.json()
    batches_b = {b["center_code"]: b["batch_id"] for b in run_b["batches"]}
    assert batches_b["C2"] == new_batch["id"]
    assert batches_b["C2"] != batches_a["C2"]
    for code in ["C1", "C3", "C4", "C5", "C6"]:
        assert batches_b[code] == batches_a[code]


def test_missing_mapping_center_is_coverage_gap_not_zero(client, researcher1):
    # baseline run without the lab metric: all six centers succeed
    r = client.post("/api/runs", headers=researcher1,
                    json=run_payload(SEEDED_RULE_VERSION, with_lab=False))
    full = r.json()
    assert full["status"] == "completed"
    c5 = next(c for c in full["center_results"] if c["center_code"] == "C5")
    assert c5["status"] == "success" and c5["total_n"] > 0

    # same request + lab mean: C5 lacks the field -> missing_mapping gap
    r = client.post("/api/runs", headers=researcher1,
                    json=run_payload(SEEDED_RULE_VERSION, with_lab=True))
    gapped = r.json()
    assert gapped["status"] == "completed_with_gaps"
    c5g = next(c for c in gapped["center_results"] if c["center_code"] == "C5")
    assert c5g["status"] == "missing_mapping"
    assert c5g["total_n"] is None  # not counted as zero
    cov = gapped["result"]["coverage"]
    assert cov["complete"] is False
    assert [e["center"] for e in cov["excluded_centers"]] == ["C5"]
    # totals aggregate only the five successful centers
    assert gapped["result"]["totals"]["n"] == full["result"]["totals"]["n"] - c5["total_n"]


def test_run_compare_endpoint(client, researcher1):
    r = client.post("/api/runs", headers=researcher1, json=run_payload(SEEDED_RULE_VERSION))
    a = r.json()["id"]
    r = client.post("/api/runs", headers=researcher1, json=run_payload(SEEDED_RULE_VERSION))
    b = r.json()["id"]
    r = client.get(f"/api/runs/compare?a={a}&b={b}", headers=researcher1)
    assert r.status_code == 200
    body = r.json()
    assert body["a"]["result"]["totals"]["n"] == body["b"]["result"]["totals"]["n"]
    assert body["a"]["batches"] and body["b"]["batches"]
