"""Platform configuration, loaded from environment."""
from __future__ import annotations

import json
import os


class Settings:
    # PostgreSQL DSN, e.g. postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/ccdev
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/ccdev"
    )
    # HMAC secret for auth tokens
    secret_key: str = os.getenv("SECRET_KEY", "dev-only-secret-change-me")
    token_ttl_seconds: int = int(os.getenv("TOKEN_TTL_SECONDS", "43200"))
    # Small-cell suppression threshold
    small_cell_k: int = int(os.getenv("SMALL_CELL_K", "5"))
    # Center agent endpoints: {"C1": "http://127.0.0.1:9101", ...}
    center_agents: dict[str, str] = json.loads(
        os.getenv(
            "CENTER_AGENTS",
            json.dumps({f"C{i}": f"http://127.0.0.1:910{i}" for i in range(1, 7)}),
        )
    )
    # sync: run center executions inline (tests / demo); async: background task
    run_execution: str = os.getenv("RUN_EXECUTION", "sync")
    # Seed demo data on startup when DB is empty
    seed_on_startup: bool = os.getenv("SEED_ON_STARTUP", "1") == "1"
    # Directory with generated center CSVs (used only by the seed to report counts
    # when a center agent is not reachable in dev mode)
    center_data_root: str = os.getenv("CENTER_DATA_ROOT", "data/centers")


settings = Settings()
