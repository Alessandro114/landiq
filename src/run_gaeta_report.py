"""
LandIQ — Gaeta Serapo driver script.

Runs the full pipeline end-to-end and emits the v1 PDF report for the
Gaeta Serapo case study (900 mq struttura ricettiva → analisi fattibilità).

Run from project root:
    ./.venv/bin/python src/run_gaeta_report.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Make src/ importable when invoked as a script.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from landiq_core import FeasibilityInput, LandIQEngine  # noqa: E402


def main() -> int:
    t0 = time.time()

    out_path = Path(__file__).resolve().parent.parent / "reports" / "gaeta_serapo_v1.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    engine = LandIQEngine(gemini_api_key=os.getenv("GEMINI_API_KEY"))

    inp = FeasibilityInput(
        address="Via Marina di Serapo 12, Gaeta (LT)",
        sqm=900,
        current_use="ricettivo_alberghiero",
        target_use="residenziale",
        budget=1_500_000,
        horizon_years=5,
        comune="Gaeta",
        provincia="LT",
        cap="04024",
        client_name="Demo Cliente (via Danny)",
        parcel_area_sqm=1300,
        notes="Struttura ricettiva 900 mq fronte mare, ipotesi cambio d'uso residenziale",
    )

    print(f"[landiq] Running pipeline for: {inp.address}")
    print(f"[landiq] SQM: {inp.sqm}  current_use: {inp.current_use}  target: {inp.target_use}")
    print(f"[landiq] Gemini key present: {bool(engine.gemini_api_key)}")

    t_run = time.time()
    report = engine.run(inp)
    print(f"[landiq] run() completed in {time.time() - t_run:.2f}s")

    print(f"\n[landiq] Scenarios ({len(report.scenarios)}):")
    for s in report.scenarios:
        print(
            f"  - {s.name}: CAPEX €{s.capex:,.0f}  NPV €{s.npv:,.0f}  "
            f"IRR {s.irr*100:.2f}%  Risk {s.risk_score:.1f}/10  "
            f"Payback {s.payback_months}m"
        )

    print(f"\n[landiq] Monte Carlo ({len(report.monte_carlo)}):")
    for m in report.monte_carlo:
        print(
            f"  - {m.scenario_name}: NPV P50 €{m.npv_p50:,.0f} "
            f"(P5 €{m.npv_p5:,.0f}, P95 €{m.npv_p95:,.0f}), "
            f"IRR P50 {m.irr_p50*100:.1f}%, "
            f"P(NPV<0)={m.prob_npv_negative*100:.0f}%, "
            f"P(IRR>15%)={m.prob_irr_above_target*100:.0f}%"
        )

    print(f"\n[landiq] Recommended scenario: {report.recommended_scenario}")
    print(f"\n[landiq] Verdict (first 500 chars):")
    print(report.ai_verdict[:500])
    print("...")

    t_pdf = time.time()
    pdf_path = engine.export_pdf(report, out_path)
    print(f"\n[landiq] export_pdf() took {time.time() - t_pdf:.2f}s")
    size_kb = pdf_path.stat().st_size / 1024
    print(f"[landiq] PDF written: {pdf_path}  ({size_kb:.1f} KB)")

    print(f"\n[landiq] Total elapsed: {time.time() - t0:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
