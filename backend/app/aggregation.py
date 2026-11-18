"""Merge per-center aggregate results into one table with explicit coverage.

Centers that failed or lacked mappings are reported as coverage gaps — their
patients are NEVER counted as zero in the totals.
"""
from __future__ import annotations


def merge_group_metrics(groups: list[dict]) -> dict:
    """Sum count metrics and recompute means/rates across same-label groups."""
    out: dict = {}
    for g in groups:
        for m, v in g["metrics"].items():
            if v is None:
                continue
            if m.startswith("_") and m.endswith("_sum"):
                out[m] = round(out.get(m, 0) + v, 4)
            elif m.startswith("_") and m.endswith("_n"):
                out[m] = out.get(m, 0) + v
            elif m.endswith("_rate"):
                continue  # recomputed below
            elif isinstance(v, (int, float)):
                out[m] = out.get(m, 0) + v
    for m in list(out):
        sum_key, n_key = f"_{m}_sum", f"_{m}_n"
        if m.endswith("_mean") and sum_key in out:
            out[m] = round(out[sum_key] / out[n_key], 4) if out[n_key] else None
    return out


def aggregate(center_results: list[dict], count_metrics: list[str]) -> dict:
    """center_results: [{center, status, aggregates}]. Only success rows count."""
    included, excluded = [], []
    buckets: dict[str, dict] = {}
    totals_from: list[dict] = []

    for cr in center_results:
        if cr["status"] == "success" and cr.get("aggregates"):
            included.append(cr["center"])
            totals_from.append(cr)
            for g in cr["aggregates"]["groups"]:
                lbl = g["label"]
                if lbl not in buckets:
                    buckets[lbl] = {"key": g["key"], "label": lbl, "n": 0,
                                    "metrics": {}, "_parts": []}
                buckets[lbl]["_parts"].append(g)
        else:
            excluded.append({"center": cr["center"], "status": cr["status"],
                             "reason": cr.get("error") or "center did not return results"})

    groups = []
    for lbl in sorted(buckets):
        b = buckets[lbl]
        parts = b.pop("_parts")
        b["n"] = sum(p["n"] for p in parts)
        b["metrics"] = merge_group_metrics(parts)
        # recompute rates from merged counts
        for m in list(b["metrics"]):
            if m.endswith("_n") and not m.startswith("_"):
                base = m[:-2]
                rate_key = f"{base}_rate"
                if any(rate_key in p["metrics"] for p in parts):
                    b["metrics"][rate_key] = (
                        round(b["metrics"][m] / b["n"], 4) if b["n"] else None
                    )
        b["suppressed"] = []
        groups.append(b)

    total_n = sum(g["n"] for g in groups)
    total_metrics = merge_group_metrics(
        [{"metrics": g["metrics"]} for g in groups]
    )
    for m in list(total_metrics):
        if m.endswith("_n") and not m.startswith("_"):
            base = m[:-2]
            total_metrics[f"{base}_rate"] = (
                round(total_metrics[m] / total_n, 4) if total_n else None
            )

    return {
        "groups": groups,
        "totals": {"n": total_n, "metrics": total_metrics},
        "count_metrics": count_metrics,
        "coverage": {
            "included_centers": included,
            "excluded_centers": excluded,
            "complete": not excluded,
        },
    }


def count_metrics_for(request_spec: dict) -> list[str]:
    ms = ["n"]
    for m in request_spec.get("metrics", []):
        if m["type"] == "outcome_count":
            ms.append(f"outcome_{m['value']}_n")
    return ms
