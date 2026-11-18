"""Clients talking to center agents. HTTP in dev/compose; in-process for tests."""
from __future__ import annotations

import os
import sys

import httpx

from .config import settings


class HttpAgentClient:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def meta(self, center_code: str) -> dict:
        url = settings.center_agents[center_code]
        r = httpx.get(f"{url}/meta", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def execute(self, center_code: str, job: dict) -> dict:
        url = settings.center_agents[center_code]
        try:
            r = httpx.post(f"{url}/execute", json=job, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            return {"status": "failed", "error": f"agent unreachable: {e}"}

    def events_meta(self, center_code: str) -> dict:
        url = settings.center_agents[center_code]
        r = httpx.get(f"{url}/events/meta", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def events_intake(self, center_code: str) -> dict:
        url = settings.center_agents[center_code]
        r = httpx.post(f"{url}/events/intake", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def risk_set(self, center_code: str, job: dict) -> dict:
        url = settings.center_agents[center_code]
        try:
            r = httpx.post(f"{url}/risk-set", json=job, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            return {"status": "failed", "error": f"agent unreachable: {e}"}


class LocalAgentClient:
    """In-process center agents (pytest). Requires center_agent on sys.path."""

    def __init__(self, data_root: str):
        center_agent_dir = os.path.join(os.path.dirname(__file__), "..", "..", "center_agent")
        if center_agent_dir not in sys.path:
            sys.path.insert(0, os.path.abspath(center_agent_dir))
        from center_agent import eventexec, executor  # noqa: PLC0415

        self._executor = executor
        self._eventexec = eventexec
        self.data_root = data_root

    def _dir(self, center_code: str) -> str:
        return os.path.join(self.data_root, center_code)

    def meta(self, center_code: str) -> dict:
        import csv
        import json

        d = self._dir(center_code)
        with open(os.path.join(d, "registry.csv"), newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            n = sum(1 for _ in reader)
        meta_path = os.path.join(d, "meta.json")
        extra = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
        return {"center": center_code, "patients": n, "fields": fields,
                "dx_years": extra.get("dx_years", []), "seed": extra.get("seed")}

    def execute(self, center_code: str, job: dict) -> dict:
        try:
            return self._executor.execute(self._dir(center_code), job)
        except Exception as e:
            return {"status": "failed", "error": f"{type(e).__name__}: {e}"}

    def events_meta(self, center_code: str) -> dict:
        import json

        d = self._dir(center_code)
        rows = self._eventexec.load_event_candidates(d)
        meta_path = os.path.join(d, "events_meta.json")
        extra = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
        by_type: dict[str, int] = {}
        for r in rows:
            by_type[r.get("event_type", "")] = by_type.get(r.get("event_type", ""), 0) + 1
        return {"center": center_code, "candidate_count": len(rows),
                "by_type": by_type, "schema_version": extra.get("schema_version"),
                "seed": extra.get("seed")}

    def events_intake(self, center_code: str) -> dict:
        import hashlib
        import json

        d = self._dir(center_code)
        rows = self._eventexec.load_event_candidates(d)
        payload = json.dumps(rows, sort_keys=True, ensure_ascii=False)
        return {"center": center_code, "candidate_count": len(rows),
                "candidates": rows,
                "snapshot_hash": hashlib.sha256(payload.encode()).hexdigest()}

    def risk_set(self, center_code: str, job: dict) -> dict:
        try:
            return self._eventexec.compute_risk_set(self._dir(center_code), job)
        except Exception as e:
            return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


_client = None


def get_agent_client():
    global _client
    if _client is None:
        _client = HttpAgentClient()
    return _client


def set_agent_client(client) -> None:
    global _client
    _client = client
