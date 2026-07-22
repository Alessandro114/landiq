"""
LandIQ — Batumi (Georgia) demo report.

Demonstrates multi-country support: same engine, different connector.
Run from project root:
    ./.venv/bin/python src/run_batumi_report.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from landiq_core import FeasibilityInput, LandIQEngine


def main() -> int:
    t0 = time.time()

    out_path = Path(__file__).resolve().parent.parent / "reports" / "batumi_ge_v1.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    engine = LandIQEngine(gemini_api_key=os.getenv("GEMINI_API_KEY"))

    inp = FeasibilityInput(
        address="Rustaveli Avenue 45, Batumi, Adjara, Georgia",
        sqm=600,
        current_use="commercial",
        target_use="touristic",
        budget=900_000,
        horizon_years=5,
        country="GE",
        city="Batumi",
        parcel_area_sqm=850,
        client_name="LandIQ Demo (Batumi)",
        notes="Mixed-use building on Rustaveli Ave, conversion to boutique hotel / serviced apartments",
    )

    print(f"[landiq] Running pipeline for: {inp.address}")
    print(f"[landiq] Country: {inp.country} | City: {inp.city}")
    print(f"[landiq] SQM: {inp.sqm}  current: {inp.current_use}  target: {inp.target_use}")

    report = engine.run(inp)

    print(f"[landiq] Market: {report.market_data['source']}")
    print(f"[landiq] Price: €{report.market_data['price_per_sqm']}/sqm")
    print(f"[landiq] Scenarios: {len(report.scenarios)}")
    print(f"[landiq] Recommended: {report.recommended_scenario}")
    print(f"[landiq] Exporting PDF → {out_path}")

    engine.export_pdf(report, out_path)

    elapsed = time.time() - t0
    print(f"[landiq] Done in {elapsed:.1f}s → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
