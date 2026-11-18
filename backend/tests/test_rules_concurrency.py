"""Concurrent rule editing: condition-level diff, merge confirmation, and
protection of restrictions that already entered review."""
from __future__ import annotations

COND_DX = {"cid": "inc-dx", "kind": "include", "variable": "diagnosis.code",
           "op": "in", "value": ["RD01"]}
COND_EXC = {"cid": "exc-old", "kind": "exclude", "variable": "patient.birth_year",
            "op": "gt", "value": 2015}
COND_SEX = {"cid": "inc-sex", "kind": "include", "variable": "patient.sex",
            "op": "eq", "value": "F"}


def _create(client, auth, conditions):
    r = client.post("/api/rules", json={"name": "并发规则", "conditions": conditions},
                    headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _version(rule, no):
    return next(v for v in rule["versions"] if v["version_no"] == no)


def test_concurrent_save_returns_condition_diff_and_merges(client, researcher1, researcher2):
    rule = _create(client, researcher1, [COND_DX, COND_EXC])
    v1 = _version(rule, 1)

    # researcher1 adds a condition (v1 -> v2)
    r = client.put(f"/api/rules/{rule['id']}/save", headers=researcher1, json={
        "base_version_id": v1["id"],
        "conditions": [COND_DX, COND_EXC, COND_SEX],
    })
    assert r.status_code == 200, r.text
    v2 = _version(r.json()["rule"], 2)

    # researcher2, still based on v1, removes the exclusion -> 409 with diffs
    r = client.put(f"/api/rules/{rule['id']}/save", headers=researcher2, json={
        "base_version_id": v1["id"],
        "conditions": [COND_DX],
    })
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"] == "concurrent_modification"
    assert [c["cid"] for c in body["their_diff"]["added"]] == ["inc-sex"]
    assert [c["cid"] for c in body["your_diff"]["removed"]] == ["exc-old"]
    # non-conflicting merge keeps both edits
    merged = body["merged"]
    assert merged is not None
    assert {c["cid"] for c in merged} == {"inc-dx", "inc-sex"}

    # researcher2 confirms the merge against the new base
    r = client.put(f"/api/rules/{rule['id']}/save", headers=researcher2, json={
        "base_version_id": v2["id"],
        "conditions": merged,
        "confirm_merge": True,
    })
    assert r.status_code == 200, r.text
    v3 = _version(r.json()["rule"], 3)
    assert {c["cid"] for c in v3["conditions"]} == {"inc-dx", "inc-sex"}


def test_reviewed_exclusion_cannot_be_dropped_silently(client, researcher1, researcher2, reviewer):
    rule = _create(client, researcher1, [COND_DX, COND_EXC])
    v1 = _version(rule, 1)
    r = client.post(f"/api/rules/{rule['id']}/versions/{v1['id']}/submit", headers=researcher1)
    assert r.status_code == 200

    # dropping the in-review exclusion is refused without acknowledgement
    r = client.put(f"/api/rules/{rule['id']}/save", headers=researcher2, json={
        "base_version_id": v1["id"],
        "conditions": [COND_DX],
    })
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "restriction_removal"
    assert body["removed_restrictions"][0]["cid"] == "exc-old"

    # explicit acknowledgement creates a new draft; the in-review version survives
    r = client.put(f"/api/rules/{rule['id']}/save", headers=researcher2, json={
        "base_version_id": v1["id"],
        "conditions": [COND_DX],
        "acknowledge_restriction_removal": True,
    })
    assert r.status_code == 200, r.text
    rule = r.json()["rule"]
    assert _version(rule, 1)["status"] == "in_review"
    assert _version(rule, 2)["status"] == "draft"

    # reviewer can still approve v1; only approved versions can start runs
    r = client.post(f"/api/rules/versions/{v1['id']}/approve",
                    headers=reviewer, json={"comment": "ok"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


def test_run_requires_approved_rule(client, researcher1):
    rule = _create(client, researcher1, [COND_DX])
    v1 = _version(rule, 1)
    r = client.post("/api/runs", headers=researcher1, json={
        "rule_version_id": v1["id"],
        "request_spec": {"group_by": ["treatment.path"], "metrics": [{"type": "count"}]},
    })
    assert r.status_code == 409
    assert "approved" in r.json()["detail"]
