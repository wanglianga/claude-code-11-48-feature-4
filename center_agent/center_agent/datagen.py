"""Deterministic fictional registry data generator for the six demo centers.

Each center gets its own local field names (exercising variable mapping) and
C5 deliberately lacks the lab value field (exercising missing-mapping gaps).
C6 is a tiny center whose small groups trigger small-cell suppression.

Usage:
    python -m center_agent.datagen --root data/centers            # all centers
    python -m center_agent.datagen --root data/centers --center C2 --seed 43
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

# canonical variable -> per-center local CSV column
CENTER_FIELDS = {
    "C1": {"patient.sex": "pat_sex", "patient.birth_year": "pat_byear",
           "diagnosis.code": "dx_code", "diagnosis.date": "dx_date",
           "treatment.path": "tx_path", "treatment.start_date": "tx_start",
           "followup.outcome": "fu_outcome", "followup.date": "fu_date",
           "followup.lab_value": "fu_lab"},
    "C2": {"patient.sex": "pat_sex", "patient.birth_year": "pat_byear",
           "diagnosis.code": "dx_code", "diagnosis.date": "dx_date",
           "treatment.path": "tx_path", "treatment.start_date": "tx_start",
           "followup.outcome": "fu_outcome", "followup.date": "fu_date",
           "followup.lab_value": "fu_lab"},
    "C3": {"patient.sex": "gender", "patient.birth_year": "yob",
           "diagnosis.code": "diag_code", "diagnosis.date": "diag_date",
           "treatment.path": "treat_path", "treatment.start_date": "treat_start",
           "followup.outcome": "outcome", "followup.date": "visit_date",
           "followup.lab_value": "lab"},
    "C4": {"patient.sex": "sex", "patient.birth_year": "birth_year",
           "diagnosis.code": "dx_code", "diagnosis.date": "dx_date",
           "treatment.path": "path", "treatment.start_date": "start_date",
           "followup.outcome": "outcome", "followup.date": "fu_date",
           "followup.lab_value": "lab_value"},
    "C5": {"patient.sex": "pat_sex", "patient.birth_year": "pat_byear",
           "diagnosis.code": "dx_code", "diagnosis.date": "dx_date",
           "treatment.path": "tx_path", "treatment.start_date": "tx_start",
           "followup.outcome": "fu_outcome", "followup.date": "fu_date"},
    # C5 has NO followup.lab_value column -> missing mapping
    "C6": {"patient.sex": "SEX", "patient.birth_year": "BYEAR",
           "diagnosis.code": "DX", "diagnosis.date": "DXD",
           "treatment.path": "PATH", "treatment.start_date": "TXD",
           "followup.outcome": "OUTCOME", "followup.date": "FUD",
           "followup.lab_value": "LAB"},
}

# C2 stores outcomes as local codes; the center mapping carries this value map.
C2_OUTCOME_CODES = {"R": "remission", "S": "stable", "P": "progression", "D": "death"}

CENTER_SIZES = {"C1": 180, "C2": 150, "C3": 220, "C4": 160, "C5": 140, "C6": 12}
DEFAULT_SEEDS = {"C1": 4201, "C2": 4202, "C3": 4203, "C4": 4204, "C5": 4205, "C6": 4206}

DIAGNOSES = ["RD01", "RD02", "RD03", "RD04"]
PATHS = ["A", "B", "C"]
PATH_WEIGHTS = [0.45, 0.45, 0.10]
OUTCOMES = ["remission", "stable", "progression", "death"]
OUTCOME_WEIGHTS = [0.45, 0.30, 0.18, 0.07]


def generate_center(center: str, root: Path, seed: int | None = None,
                    size: int | None = None) -> dict:
    rng = random.Random(seed if seed is not None else DEFAULT_SEEDS[center])
    n = size if size is not None else CENTER_SIZES[center]
    fields = CENTER_FIELDS[center]
    from .eventgen import SCENARIO_PATIENTS

    scenario_nums = set(SCENARIO_PATIENTS.get(center, []))
    rows = []
    for i in range(1, n + 1):
        sex = rng.choice(["F", "M"])
        byear = rng.randint(1955, 2015)
        dx = rng.choice(DIAGNOSES)
        dx_date = f"{rng.randint(2019, 2024)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        path = rng.choices(PATHS, weights=PATH_WEIGHTS, k=1)[0]
        tx_start = f"{rng.randint(2020, 2025)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        # Scenario patients get a fixed treatment start shortly before their
        # 2025 SAE/death records so the one-year horizon actually contains them.
        # They also satisfy the seeded approved cohort rule (RD01-03, <=2015).
        if i in scenario_nums:
            sex = "F"
            byear = 2000
            dx = "RD02"
            dx_date = "2024-11-05"
            tx_start = "2024-12-15"
        outcome = rng.choices(OUTCOMES, weights=OUTCOME_WEIGHTS, k=1)[0]
        fu_date = f"{rng.randint(2021, 2026)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        lab = round(rng.gauss(6.0, 1.8), 2)
        row = {"patient_id": f"{center}-P{i:04d}"}
        for var, col in fields.items():
            if var == "patient.sex":
                row[col] = sex
            elif var == "patient.birth_year":
                row[col] = byear
            elif var == "diagnosis.code":
                row[col] = dx
            elif var == "diagnosis.date":
                row[col] = dx_date
            elif var == "treatment.path":
                row[col] = path
            elif var == "treatment.start_date":
                row[col] = tx_start
            elif var == "followup.outcome":
                if center == "C2":  # local outcome codes
                    row[col] = {"remission": "R", "stable": "S",
                                "progression": "P", "death": "D"}[outcome]
                else:
                    row[col] = outcome
            elif var == "followup.date":
                row[col] = fu_date
            elif var == "followup.lab_value":
                row[col] = lab
        rows.append(row)

    cdir = root / center
    cdir.mkdir(parents=True, exist_ok=True)
    csv_path = cdir / "registry.csv"
    header = ["patient_id"] + list(fields.values())
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    meta = {
        "center": center,
        "seed": seed if seed is not None else DEFAULT_SEEDS[center],
        "patients": n,
        "fields": header,
        "dx_years": sorted({r[fields["diagnosis.date"]][:4] for r in rows}),
    }
    (cdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/centers")
    ap.add_argument("--center", choices=list(CENTER_FIELDS), default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--size", type=int, default=None)
    args = ap.parse_args()
    root = Path(args.root)
    centers = [args.center] if args.center else list(CENTER_FIELDS)
    from . import eventgen

    for c in centers:
        meta = generate_center(c, root, seed=args.seed, size=args.size)
        print(f"{c}: {meta['patients']} patients -> {root / c / 'registry.csv'}")
        emeta = eventgen.generate_center_events(c, root)
        print(f"{c}: {emeta['candidate_count']} event candidates -> "
              f"{root / c / 'events.csv'}")


if __name__ == "__main__":
    main()
