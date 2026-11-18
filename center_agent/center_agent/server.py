"""HTTP wrapper around the center-local executor.

One agent process serves exactly one center. The orchestrator may ask for
metadata (record counts, available fields) and submit aggregate-only jobs;
individual records are never exposed on any endpoint.

Run: python -m center_agent.server --center C1 --port 9101 --data data/centers/C1
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import eventexec, executor


class Job(BaseModel):
    job_id: str | None = None
    dict_version: str | None = None
    conditions: list[dict] = []
    request_spec: dict
    mapping: dict[str, dict]


class RiskSetJob(BaseModel):
    job_id: str | None = None
    conditions: list[dict] = []
    request_spec: dict
    mapping: dict[str, dict]
    analysis_date: str | None = None
    adjudicated: list[dict] = []
    pending_patient_ids: list[str] = []


def create_app(center: str, data_dir: str) -> FastAPI:
    app = FastAPI(title=f"center-agent-{center}", docs_url=None, redoc_url=None)
    root = Path(data_dir)

    @app.get("/health")
    def health():
        return {"status": "ok", "center": center}

    @app.get("/meta")
    def meta():
        csv_path = root / "registry.csv"
        if not csv_path.exists():
            raise HTTPException(503, "no registry data loaded")
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            n = sum(1 for _ in reader)
        meta_file = root / "meta.json"
        extra = json.loads(meta_file.read_text()) if meta_file.exists() else {}
        return {
            "center": center,
            "patients": n,
            "fields": fields,
            "dx_years": extra.get("dx_years", []),
            "seed": extra.get("seed"),
        }

    @app.post("/execute")
    def execute(job: Job):
        try:
            return executor.execute(root, job.model_dump())
        except Exception as e:  # unexpected local failure -> reported, not zeroed
            return {"status": "failed", "error": f"{type(e).__name__}: {e}"}

    # ----- Serious Adverse Event workflow -------------------------------------
    # The center keeps the authoritative events.csv. Intake returns an audited
    # copy of the event-workflow evidence (adjudication needs severity/death/
    # receipt records); the statistical risk set is still computed here and
    # only aggregates ever leave the center.
    @app.get("/events/meta")
    def events_meta_endpoint():
        if not eventexec.events_csv_path(root).exists():
            raise HTTPException(503, "no event candidates loaded (events.csv)")
        return {"center": center, **eventexec.events_meta(root)}

    @app.post("/events/intake")
    def events_intake():
        rows = eventexec.load_event_candidates(root)
        if not rows:
            raise HTTPException(503, "no event candidates loaded")
        # Hash the canonical serialized rows so the workspace can prove the
        # stored snapshot matches the center's original file.
        payload = json.dumps(rows, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return {"center": center, "candidate_count": len(rows),
                "candidates": rows, "snapshot_hash": digest}

    @app.post("/risk-set")
    def risk_set(job: RiskSetJob):
        try:
            return eventexec.compute_risk_set(root, job.model_dump())
        except Exception as e:  # reported as failure, never silently zeroed
            return {"status": "failed", "error": f"{type(e).__name__}: {e}"}

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--center", required=True)
    ap.add_argument("--port", type=int, default=9101)
    ap.add_argument("--data", required=True)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    import uvicorn

    app = create_app(args.center, args.data)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
