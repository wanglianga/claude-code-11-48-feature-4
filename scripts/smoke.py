#!/usr/bin/env python
"""End-to-end smoke verification against a RUNNING platform (real PostgreSQL +
HTTP center agents). Exercises every acceptance scenario:

  1. seeded users / centers / locked batches
  2. run over 6 centers; missing-mapping center is a coverage gap, not a zero
  3. batch replacement mid-stream: old run keeps its snapshot, new run picks up
  4. concurrent rule editing: 409 + condition-level diff + confirmed merge;
     in-review restrictions cannot be dropped silently
  5. small-sample output: review blocks, merge+suppress releases, published
     report carries full provenance and hash-verifies
  6. run comparison endpoint

Usage: .venv/bin/python scripts/smoke.py  [BASE_URL]

Feature 4 additionally exercises the SAE adjudication workflow & competing-risk
service: audited intake, cross-center de-duplication, AR-v1 five-category
classification, blinded votes with chief tie-break, run-freeze boundaries
(mid-run adjudication only enters the next run), mutually-exclusive risk
categories and adjudication provenance on the published report.
"""
from __future__ import annotations

import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
CHECKS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(1)


def login(username: str, password: str) -> dict:
    r = httpx.post(f"{BASE}/api/auth/login",
                   json={"username": username, "password": password}, timeout=10)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def main() -> None:
    admin = login("admin", "admin123")
    r1 = login("researcher1", "research123")
    r2 = login("researcher2", "research123")
    rev = login("reviewer", "review123")
    check("四类账号登录", True)
    adj1 = login("adjudicator1", "adjudicate123")
    adj2 = login("adjudicator2", "adjudicate123")
    chief = login("chief", "adjudicate123")
    check("裁定专家账号登录（含首席）", True)

    centers = httpx.get(f"{BASE}/api/admin/centers", headers=admin).json()
    check("六家中心均已注册并锁定批次",
          len(centers) == 6 and all(c["locked_batch"] for c in centers))

    # ---- 2. runs & coverage gaps ------------------------------------------------
    spec = {"group_by": ["treatment.path"],
            "metrics": [{"type": "count"},
                        {"type": "outcome_count", "variable": "followup.outcome",
                         "value": "remission"}]}
    run_full = httpx.post(f"{BASE}/api/runs", headers=r1,
                          json={"rule_version_id": 1, "request_spec": spec}).json()
    check("全量运行完成（6/6 中心成功）",
          run_full["status"] == "completed" and run_full["centers_failed"] == 0)
    c5_n = next(c for c in run_full["center_results"]
                if c["center_code"] == "C5")["total_n"]

    spec_lab = dict(spec, metrics=spec["metrics"] + [
        {"type": "mean", "variable": "followup.lab_value"}])
    run_gap = httpx.post(f"{BASE}/api/runs", headers=r1,
                         json={"rule_version_id": 1, "request_spec": spec_lab}).json()
    c5 = next(c for c in run_gap["center_results"] if c["center_code"] == "C5")
    cov = run_gap["result"]["coverage"]
    check("C5 缺 lab 字段 → 标记 missing_mapping 而非零例",
          run_gap["status"] == "completed_with_gaps"
          and c5["status"] == "missing_mapping" and c5["total_n"] is None
          and [e["center"] for e in cov["excluded_centers"]] == ["C5"])
    check("覆盖缺口不计入总计",
          run_gap["result"]["totals"]["n"] == run_full["result"]["totals"]["n"] - c5_n,
          f"{run_gap['result']['totals']['n']} = "
          f"{run_full['result']['totals']['n']} - {c5_n}")

    # ---- 3. batch replacement keeps run snapshots -------------------------------
    batches_a = {b["center_code"]: b["batch_id"] for b in run_full["batches"]}
    new_batch = httpx.post(f"{BASE}/api/admin/centers/C2/batches/sync",
                           headers=admin, json={"label": "2026Q4"}).json()
    httpx.post(f"{BASE}/api/admin/batches/{new_batch['id']}/lock", headers=admin)
    again = httpx.get(f"{BASE}/api/runs/{run_full['id']}", headers=r1).json()
    check("批次替换后旧运行仍引用启动时锁定的批次",
          {b["center_code"]: b["batch_id"] for b in again["batches"]} == batches_a
          and again["result"]["totals"]["n"] == run_full["result"]["totals"]["n"])
    run_new = httpx.post(f"{BASE}/api/runs", headers=r1,
                         json={"rule_version_id": 1, "request_spec": spec}).json()
    batches_b = {b["center_code"]: b["batch_id"] for b in run_new["batches"]}
    check("新运行使用替换后的批次", batches_b["C2"] == new_batch["id"]
          and batches_b["C2"] != batches_a["C2"])

    cmp_res = httpx.get(f"{BASE}/api/runs/compare?a={run_full['id']}&b={run_new['id']}",
                        headers=r1).json()
    check("运行对比接口返回两次运行的批次与结果",
          cmp_res["a"]["batches"] and cmp_res["b"]["batches"])

    # ---- 4. concurrent rule editing ---------------------------------------------
    conds = [
        {"cid": "inc-dx", "kind": "include", "variable": "diagnosis.code",
         "op": "in", "value": ["RD01", "RD02"]},
        {"cid": "exc-kid", "kind": "exclude", "variable": "patient.birth_year",
         "op": "gt", "value": 2015},
    ]
    rule = httpx.post(f"{BASE}/api/rules", headers=r1,
                      json={"name": "并发演示规则", "conditions": conds}).json()
    v1 = rule["versions"][0]
    httpx.post(f"{BASE}/api/rules/{rule['id']}/versions/{v1['id']}/submit", headers=r1)

    conds_r1 = conds + [{"cid": "inc-sex", "kind": "include",
                         "variable": "patient.sex", "op": "eq", "value": "F"}]
    saved = httpx.put(f"{BASE}/api/rules/{rule['id']}/save", headers=r1,
                      json={"base_version_id": v1["id"], "conditions": conds_r1}).json()
    v2 = next(v for v in saved["rule"]["versions"] if v["version_no"] == 2)

    conflict = httpx.put(f"{BASE}/api/rules/{rule['id']}/save", headers=r2,
                         json={"base_version_id": v1["id"],
                               "conditions": [conds[0]]})  # drops the exclusion
    check("并发保存返回 409 与条件级差异", conflict.status_code == 409
          and conflict.json()["error"] == "concurrent_modification"
          and conflict.json()["their_diff"]["added"])
    merged = conflict.json()["merged"]
    # merged drops the in-review exclusion -> must also be acknowledged
    refused = httpx.put(f"{BASE}/api/rules/{rule['id']}/save", headers=r2,
                        json={"base_version_id": v2["id"], "conditions": merged,
                              "confirm_merge": True})
    check("合并结果删除在审排除条件 → 需显式确认",
          refused.status_code == 409
          and refused.json()["error"] == "restriction_removal")
    confirmed = httpx.put(f"{BASE}/api/rules/{rule['id']}/save", headers=r2,
                          json={"base_version_id": v2["id"], "conditions": merged,
                                "confirm_merge": True,
                                "acknowledge_restriction_removal": True})
    check("确认合并后保存成功且在审版本未被抹掉",
          confirmed.status_code == 200
          and any(v["status"] == "in_review"
                  for v in confirmed.json()["rule"]["versions"]))

    # ---- 5. small-sample review flow --------------------------------------------
    small_conds = [
        {"cid": "inc-dx", "kind": "include", "variable": "diagnosis.code",
         "op": "eq", "value": "RD03"},
        {"cid": "inc-yob", "kind": "include", "variable": "patient.birth_year",
         "op": "ge", "value": 2005},
    ]
    srule = httpx.post(f"{BASE}/api/rules", headers=r1,
                       json={"name": "小样本队列", "conditions": small_conds}).json()
    sv1 = srule["versions"][0]
    httpx.post(f"{BASE}/api/rules/{srule['id']}/versions/{sv1['id']}/submit", headers=r1)
    httpx.post(f"{BASE}/api/rules/versions/{sv1['id']}/approve",
               headers=rev, json={"comment": "同意"})
    srun = httpx.post(f"{BASE}/api/runs", headers=r1, json={
        "rule_version_id": sv1["id"],
        "request_spec": {"group_by": ["treatment.path", "patient.sex"],
                         "metrics": [{"type": "count"},
                                     {"type": "outcome_count",
                                      "variable": "followup.outcome",
                                      "value": "remission"}]}}).json()
    out = httpx.post(f"{BASE}/api/runs/{srun['id']}/outputs", headers=r1,
                     json={"title": "小样本输出（冒烟）"}).json()
    check("小样本输出被标记并进入审查队列", out["flag_count"] > 0
          and out["status"] == "pending_review")
    blocked = httpx.post(f"{BASE}/api/outputs/{out['id']}/decision",
                         headers=rev, json={"action": "approve"})
    check("存在未处理标记时批准被拒绝", blocked.status_code == 409)
    step = httpx.post(f"{BASE}/api/outputs/{out['id']}/decision", headers=rev,
                      json={"action": "merge",
                            "detail": {"groups": ["C / F", "C / M"],
                                       "into": "C（合并）"}}).json()
    cells = [{"group": f["group"], "metric": f["metric"]} for f in step["flags"]]
    done = httpx.post(f"{BASE}/api/outputs/{out['id']}/decision", headers=rev,
                      json={"action": "suppress", "detail": {"cells": cells},
                            "comment": "合并+抑制后放行"}).json()
    check("合并 + 抑制后输出获批并发布",
          done["status"] == "approved" and done.get("published_report_id"))
    rep = httpx.get(f"{BASE}/api/reports/{done['published_report_id']}",
                    headers=rev).json()
    prov = rep["report"]["provenance"]
    check("发布报告引用完整运行版本（规则/批次/审查）",
          prov["run_id"] == srun["id"] and len(prov["batches"]) == 6
          and prov["conditions_hash"] and prov["review"]["action"] == "suppress")
    ver = httpx.get(f"{BASE}/api/reports/{done['published_report_id']}/verify",
                    headers=rev).json()
    check("报告哈希可复现校验", ver["intact"] is True)

    # ======================================================================
    # Feature 4: SAE adjudication workflow & competing-risk data service
    # ======================================================================
    # 1. audited intake for every center (seed already intakes; re-pull to
    #    exercise the endpoint and confirm hash-stamped copies)
    for code in ["C1", "C2", "C3", "C4", "C5", "C6"]:
        r = httpx.post(f"{BASE}/api/events/intake/{code}", headers=admin,
                       json={"label": "smoke-events"})
        assert r.status_code == 201, r.text
    intakes = httpx.get(f"{BASE}/api/events/intakes", headers=admin).json()
    check("六中心事件候选均已审计接入且带原始快照哈希",
          {i["center_code"] for i in intakes} == set(
              ["C1", "C2", "C3", "C4", "C5", "C6"])
          and all(i["source_snapshot_hash"] for i in intakes))

    clusters = {c["cluster_key"]: c for c in
                httpx.get(f"{BASE}/api/events/clusters", headers=r1).json()}

    # S2: one clinical event across two centers is de-duplicated once
    s2 = clusters["CL-C2-EVT-S2-01"]
    check("S2 双中心同一事件去重为单簇（共用回执 XFER-2210）",
          s2["center_codes"] == ["C2", "C4"] and s2["member_count"] == 3
          and "XFER-2210" in s2["dedup_reason"])

    # S1: late external receipt -> earliest severe occurrence, not known date
    s1 = clusters["CL-C1-EVT-S1-01"]
    check("S1 外院晚回执：事件日取最早阈值记录而非回执获知日",
          s1["rule_classification"] == "confirmed_event"
          and s1["rule_event_date"] == "2025-02-10"
          and s1["rule_known_date"] == "2025-04-18")
    # S4: escalation over repeated admission -> confirmed at severe date
    check("S4 严重程度升级（重复住院）自动确认",
          clusters["CL-C1-EVT-S4-01"]["rule_classification"] == "confirmed_event"
          and clusters["CL-C1-EVT-S4-01"]["rule_event_date"] == "2025-03-20")
    # S3: death before confirmation -> competing termination, never "no event"
    check("S3 死亡先于确认 → 竞争终止",
          clusters["CL-C3-EVT-S3-01"]["rule_classification"]
          == "competing_termination")
    # lost fu / transfer / insufficient land in their own categories
    check("失访=删失、转院=无法判定、资料缺失=信息不足",
          clusters["CL-C1-EVT-LFU-01"]["rule_classification"] == "censored"
          and clusters["CL-C3-EVT-TRF-01"]["rule_classification"] == "indeterminate"
          and clusters["CL-C2-EVT-INS-01"]["rule_classification"] == "insufficient")

    # 2. S5: a conflict candidate is waiting in the blinded manual queue.
    #    Freeze the FIRST adjudication list (still 1 pending) and run A before
    #    the panel votes, so the run-freeze boundary is actually exercised.
    queue = httpx.get(f"{BASE}/api/events/adjudication/queue",
                      headers=adj1).json()
    check("S5 冲突候选进入盲法人工队列", len(queue) == 1
          and queue[0]["cluster_key"] == "CL-C2-EVT-S5-01"
          and queue[0]["status"] == "pending")
    tid = queue[0]["id"]

    snap1 = httpx.post(f"{BASE}/api/events/snapshots", headers=r1).json()
    check("首份裁定清单保留 1 项待裁定", snap1["pending_count"] == 1)
    risk_spec = {"analysis_type": "competing_risk",
                 "group_by": ["treatment.path"], "horizon_days": 365}
    run_a = httpx.post(f"{BASE}/api/runs", headers=r1, json={
        "rule_version_id": 1,
        "request_spec": {**risk_spec, "time_basis": "occurrence",
                         "adjudication_snapshot_id": snap1["id"]}}).json()
    m_a = run_a["result"]["totals"]["metrics"]
    check("竞争风险运行：五类互斥且穷尽（死亡/失访/转院均非“未发生”）",
          (m_a["sae_event_n"] + m_a["competing_death_n"] + m_a["censored_n"]
           + m_a["indeterminate_n"] + m_a["pending_n"])
          == run_a["result"]["totals"]["n"]
          and m_a["competing_death_n"] >= 1 and m_a["indeterminate_n"] >= 1
          and m_a["pending_n"] == 1)
    check("审查输出声明时间基准=事件发生时间与各中心确认覆盖",
          run_a["adjudication"]["time_basis_label"] == "事件发生时间"
          and len(run_a["adjudication"]["center_confirmation_coverage"]) == 6)

    # 3. only AFTER run A started do the blinded experts vote (split) and the
    #    chief breaks the tie — this adjudication can only enter the NEXT run.
    httpx.post(f"{BASE}/api/events/adjudication/tasks/{tid}/vote",
               headers=adj1, json={"vote": "confirmed_event",
                                   "proposed_event_date": "2025-02-15"})
    split = httpx.post(f"{BASE}/api/events/adjudication/tasks/{tid}/vote",
                       headers=adj2, json={"vote": "non_event"}).json()
    check("S5 投票分歧 → in_review 且标记 disagreement",
          split["status"] == "in_review" and split["disagreement"] is True)
    forbidden = httpx.post(
        f"{BASE}/api/events/adjudication/tasks/{tid}/tie-break",
        headers=adj1, json={"final_vote": "confirmed_event",
                            "event_date": "2025-02-15", "comment": "x"})
    check("非首席专家不能终裁分歧", forbidden.status_code == 403)
    httpx.post(f"{BASE}/api/events/adjudication/tasks/{tid}/tie-break",
               headers=chief,
               json={"final_vote": "confirmed_event",
                     "event_date": "2025-02-15", "known_date": "2025-02-16",
                     "comment": "首席采信住院记录"})

    # 4. mid-run adjudication only enters the NEXT run (freeze boundary)
    events_a = m_a["sae_event_n"]
    run_a_reload = httpx.get(f"{BASE}/api/runs/{run_a['id']}", headers=r1).json()
    check("运行冻结：旧运行的裁定清单与待裁定数不随后续裁定改变",
          run_a_reload["adjudication"]["snapshot_id"] == snap1["id"]
          and run_a_reload["adjudication"]["pending_count"] == 1)
    snap2 = httpx.post(f"{BASE}/api/events/snapshots", headers=r1).json()
    check("新清单纳入刚完成裁定（待裁定清零，哈希不同）",
          snap2["pending_count"] == 0
          and snap2["snapshot_hash"] != snap1["snapshot_hash"])
    run_b = httpx.post(f"{BASE}/api/runs", headers=r1, json={
        "rule_version_id": 1,
        "request_spec": {**risk_spec, "time_basis": "awareness",
                         "adjudication_snapshot_id": snap2["id"]}}).json()
    check("下一次运行采用新清单且时间基准=获知时间，事件数 +1",
          run_b["time_basis"] == "awareness"
          and run_b["result"]["totals"]["metrics"]["sae_event_n"] == events_a + 1
          and run_b["result"]["totals"]["metrics"]["pending_n"] == 0)
    check("裁定清单哈希可复现校验",
          httpx.get(f"{BASE}/api/events/snapshots/{snap1['id']}/verify",
                    headers=r1).json()["intact"] is True)

    print(f"\n全部 {len(CHECKS)} 项冒烟检查通过")


if __name__ == "__main__":
    main()
