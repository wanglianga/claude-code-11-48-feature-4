"""Condition-level diff and three-way merge for cohort rules.

A condition is a dict: {"cid", "kind": include|exclude, "variable", "op", "value"}.
`cid` is a stable client-generated id so concurrent edits can be matched up.
"""
from __future__ import annotations

import json


def cond_signature(c: dict) -> str:
    return json.dumps(
        {k: c.get(k) for k in ("kind", "variable", "op", "value")},
        sort_keys=True,
        ensure_ascii=False,
    )


def _by_cid(conditions: list[dict]) -> dict[str, dict]:
    return {c["cid"]: c for c in conditions}


def diff_conditions(base: list[dict], other: list[dict]) -> dict:
    """Condition-level diff of `other` relative to `base`."""
    b, o = _by_cid(base), _by_cid(other)
    added = [c for cid, c in o.items() if cid not in b]
    removed = [c for cid, c in b.items() if cid not in o]
    changed = [
        {"cid": cid, "from": b[cid], "to": o[cid]}
        for cid in b.keys() & o.keys()
        if cond_signature(b[cid]) != cond_signature(o[cid])
    ]
    return {"added": added, "removed": removed, "changed": changed}


def is_empty(diff: dict) -> bool:
    return not diff["added"] and not diff["removed"] and not diff["changed"]


def three_way_merge(base: list[dict], theirs: list[dict], yours: list[dict]) -> dict:
    """Merge your changes (base->yours) onto the current version (base->theirs).

    Returns {merged, conflicts, your_diff, their_diff}. `merged` is None when
    conflicts exist — the client must resolve and resubmit an explicit list.
    """
    your_diff = diff_conditions(base, yours)
    their_diff = diff_conditions(base, theirs)

    b, t, y = _by_cid(base), _by_cid(theirs), _by_cid(yours)
    conflicts: list[dict] = []

    # both added a condition with the same cid but different content
    for cid in {c["cid"] for c in your_diff["added"]} & {c["cid"] for c in their_diff["added"]}:
        if cond_signature(y[cid]) != cond_signature(t[cid]):
            conflicts.append({"cid": cid, "type": "both_added", "theirs": t[cid], "yours": y[cid]})

    for cid in b:
        in_y, in_t = cid in y, cid in t
        y_changed = in_y and cond_signature(y[cid]) != cond_signature(b[cid])
        t_changed = in_t and cond_signature(t[cid]) != cond_signature(b[cid])
        if not in_y and t_changed:
            conflicts.append({"cid": cid, "type": "you_removed_they_changed",
                              "theirs": t[cid], "yours": None})
        elif not in_t and y_changed:
            conflicts.append({"cid": cid, "type": "they_removed_you_changed",
                              "theirs": None, "yours": y[cid]})
        elif in_y and in_t and y_changed and t_changed and cond_signature(y[cid]) != cond_signature(t[cid]):
            conflicts.append({"cid": cid, "type": "both_changed",
                              "theirs": t[cid], "yours": y[cid]})

    if conflicts:
        return {"merged": None, "conflicts": conflicts,
                "your_diff": your_diff, "their_diff": their_diff}

    # Non-conflicting: start from theirs, apply your delta.
    merged: dict[str, dict] = dict(t)
    for c in your_diff["added"]:
        merged[c["cid"]] = c
    for c in your_diff["removed"]:
        merged.pop(c["cid"], None)
    for ch in your_diff["changed"]:
        merged[ch["cid"]] = ch["to"]

    # Stable order: theirs first (original order), then your additions.
    order = [c["cid"] for c in theirs] + [c["cid"] for c in your_diff["added"]]
    merged_list = [merged[cid] for cid in order if cid in merged]
    return {"merged": merged_list, "conflicts": [],
            "your_diff": your_diff, "their_diff": their_diff}


def removed_restrictions(reviewed: list[dict], incoming: list[dict]) -> list[dict]:
    """Exclusion conditions present in a reviewed (in_review/approved) version
    that the incoming edit removes or loosens. These can never be dropped
    silently — the save endpoint demands explicit acknowledgement."""
    inc = _by_cid(incoming)
    out = []
    for c in reviewed:
        if c.get("kind") != "exclude":
            continue
        if c["cid"] not in inc:
            out.append({"cid": c["cid"], "change": "removed", "condition": c})
        elif cond_signature(inc[c["cid"]]) != cond_signature(c):
            out.append({"cid": c["cid"], "change": "modified",
                        "condition": c, "new": inc[c["cid"]]})
    return out
