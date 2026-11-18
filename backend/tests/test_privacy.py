"""Small-cell and differencing analysis unit tests."""
from __future__ import annotations

from app.privacy import analyze_table, apply_merge, apply_suppression


def _table(ns):
    return {
        "groups": [{"key": {"p": chr(65 + i)}, "label": chr(65 + i), "n": n,
                    "metrics": {"remission_n": n - 1, "remission_rate": 0.8},
                    "suppressed": []}
                   for i, n in enumerate(ns)],
        "totals": {"n": sum(ns), "metrics": {"remission_n": sum(ns) - len(ns)}},
        "count_metrics": ["n", "remission_n"],
    }


def test_clean_table_has_no_flags():
    assert analyze_table(_table([20, 30, 40]), 5) == []


def test_small_group_flags_all_metrics():
    flags = analyze_table(_table([20, 3, 40]), 5)
    assert any(f["group"] == "B" and f["metric"] == "n" for f in flags)
    assert any(f["group"] == "B" and f["metric"] == "remission_rate"
               and f["reason"] == "small_group" for f in flags)


def test_single_small_cell_is_derivable_by_differencing():
    flags = analyze_table(_table([20, 4, 40]), 5)
    assert any(f["group"] == "B" and f["metric"] == "n"
               and f["reason"] == "differencing" for f in flags)


def test_two_small_cells_not_individually_derivable():
    flags = analyze_table(_table([20, 4, 3]), 5)
    assert not any(f["reason"] == "differencing" for f in flags)


def test_suppression_resolves_but_single_hidden_cell_still_derivable():
    t = _table([20, 4, 3])
    # suppress both small cells -> clean
    t2 = apply_suppression(t, [{"group": "B", "metric": "n"},
                               {"group": "C", "metric": "n"}])
    t2 = apply_suppression(t2, [{"group": "B", "metric": "remission_n"},
                                {"group": "C", "metric": "remission_n"}])
    t2 = apply_suppression(t2, [{"group": "B", "metric": "remission_rate"},
                                {"group": "C", "metric": "remission_rate"}])
    assert analyze_table(t2, 5) == []


def test_suppressing_the_only_small_cell_is_not_enough():
    t = _table([20, 4, 40])  # B is the single small group
    t = apply_suppression(t, [{"group": "B", "metric": "n"}])
    # still exactly one hidden cell in the n column with a visible total
    flags = analyze_table(t, 5)
    assert any(f["group"] == "B" and f["metric"] == "n"
               and f["reason"] == "differencing" for f in flags)
    # hiding the column total removes the differencing path
    t = apply_suppression(t, [{"group": "__total__", "metric": "n"}])
    assert not any(f["metric"] == "n" for f in analyze_table(t, 5))


def test_merge_combines_groups_and_recomputes():
    t = _table([20, 4, 3])
    merged = apply_merge(t, ["B", "C"], "B+C合并")
    g = next(g for g in merged["groups"] if g["label"] == "B+C合并")
    assert g["n"] == 7
    assert g["metrics"]["remission_n"] == 5
    assert analyze_table(merged, 5) == []


def test_merge_requires_two_groups():
    t = _table([20, 4, 3])
    try:
        apply_merge(t, ["B"], "x")
    except ValueError:
        return
    raise AssertionError("expected ValueError")
