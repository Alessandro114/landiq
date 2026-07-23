"""LandIQ — Tbilisi (Georgia) demo report — different city from Batumi."""
from __future__ import annotations
import os, sys, time
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from landiq_core import FeasibilityInput, LandIQEngine

def main() -> int:
    t0 = time.time()
    out_path = Path(__file__).resolve().parent.parent / "reports" / "tbilisi_ge_v1.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    engine = LandIQEngine()
    inp = FeasibilityInput(
        address="Rustaveli Avenue 12, Tbilisi, Georgia",
        sqm=450,
        current_use="office",
        target_use="residential",
        budget=600_000,
        horizon_years=5,
        country="GE",
        city="Tbilisi",
        parcel_area_sqm=600,
        client_name="LandIQ Demo (Tbilisi)",
        notes="Soviet-era office block conversion to apartments in Tbilisi CBD",
    )
    print(f"[landiq] {inp.address} | {inp.country} | {inp.city}")
    report = engine.run(inp)
    print(f"[landiq] Price: €{report.market_data['price_per_sqm']}/sqm | Recommended: {report.recommended_scenario}")
    engine.export_pdf(report, out_path)
    print(f"[landiq] Done in {time.time()-t0:.1f}s → {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
