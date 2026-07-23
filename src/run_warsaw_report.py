"""LandIQ — Warsaw (Poland) demo report — tests generic connector fallback."""
from __future__ import annotations
import os, sys, time
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from landiq_core import FeasibilityInput, LandIQEngine

def main() -> int:
    t0 = time.time()
    out_path = Path(__file__).resolve().parent.parent / "reports" / "warsaw_pl_v1.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    engine = LandIQEngine()
    inp = FeasibilityInput(
        address="ul. Marszałkowska 82, Warszawa, Poland",
        sqm=800,
        current_use="commercial",
        target_use="residential",
        budget=2_000_000,
        horizon_years=5,
        country="PL",
        city="Warsaw",
        parcel_area_sqm=1000,
        client_name="LandIQ Demo (Warsaw)",
        notes="High-street commercial unit conversion to apartments in Warsaw city centre",
    )
    print(f"[landiq] {inp.address} | {inp.country} | {inp.city}")
    report = engine.run(inp)
    print(f"[landiq] Price: €{report.market_data['price_per_sqm']}/sqm | Recommended: {report.recommended_scenario}")
    engine.export_pdf(report, out_path)
    print(f"[landiq] Done in {time.time()-t0:.1f}s → {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
