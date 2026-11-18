"""Entity de-duplication: fuse candidate records that describe the SAME
clinical event so it is never counted twice (including across hospitals).

Two candidates belong to one cluster when any auditable linkage connects them:

  1. explicit ``linked_candidate_id`` reference (mild symptom -> later receipt,
     repeated admission, death-before-confirmation note);
  2. a shared external-hospital receipt id (the cross-center transfer anchor —
     e.g. two centers both cite ``XFER-2210``);
  3. transitive closure of (1)/(2), computed with a union-find.

Candidates without any linkage stay as singleton clusters. The rules are
intentionally conservative: de-duplication only follows explicit evidence
carried in the source records, never fuzzy name/date guessing, so every fused
cluster has a human-readable reason and is fully traceable.
"""
from __future__ import annotations


class _UnionFind:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # keep the lexicographically smaller id as the root for stability
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def build_clusters(candidates: list[dict]) -> list[dict]:
    """candidates: EventCandidate-like dicts (must include the string
    ``candidate_id``). Returns cluster specs ordered by their anchor id:

    [{"cluster_key": "CL-...", "members": [candidate_id, ...],
      "patient_ids": [...], "center_codes": [...], "reasons": [...]}]
    """
    if not candidates:
        return []
    ids = [c["candidate_id"] for c in candidates]
    by_id = {c["candidate_id"]: c for c in candidates}
    uf = _UnionFind(ids)
    reasons: dict[tuple, list[str]] = {}

    def _edge(a: str, b: str, reason: str) -> None:
        if a not in by_id or b not in by_id:
            return
        ra, rb = uf.find(a), uf.find(b)
        uf.union(a, b)
        root = uf.find(a)
        reasons.setdefault((root, a, b), []).append(reason)

    # Rule 1: explicit linkage fields.
    for c in candidates:
        linked = c.get("linked_candidate_id")
        if linked:
            _edge(c["candidate_id"], linked,
                  f"显式关联字段 linked_candidate_id={linked}")

    # Rule 2: shared external-hospital receipt id (cross-center capable).
    by_receipt: dict[str, list[str]] = {}
    for c in candidates:
        receipt = c.get("external_receipt_id")
        if receipt:
            by_receipt.setdefault(receipt, []).append(c["candidate_id"])
    for receipt, members in by_receipt.items():
        anchor = members[0]
        for other in members[1:]:
            _edge(anchor, other, f"共用外院/转院回执 {receipt}")

    # Collect components.
    components: dict[str, list[str]] = {}
    for cid in ids:
        components.setdefault(uf.find(cid), []).append(cid)

    clusters = []
    for anchor in sorted(components):
        members = sorted(components[anchor])
        patients, centers = set(), set()
        edge_reasons: list[str] = []
        for mid in members:
            c = by_id[mid]
            patients.add(c["patient_id"])
            centers.add(c.get("report_center") or c.get("center_code") or "")
        for (root, a, b), rs in reasons.items():
            if uf.find(root) == anchor:
                edge_reasons.extend(rs)
        # stable, human-readable key derived from the anchor candidate
        cluster_key = f"CL-{anchor}"
        clusters.append({
            "cluster_key": cluster_key,
            "members": members,
            "patient_ids": sorted(patients),
            "center_codes": sorted(x for x in centers if x),
            "reasons": sorted(set(edge_reasons)) or ["无外部关联，独立候选"],
        })
    return clusters
