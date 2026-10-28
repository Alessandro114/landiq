"""
LandIQ Montenegro Connector (issue #1).

Benchmarks (EUR/sqm, 2025) as published in the connector request: Tivat/Porto Montenegro 3.5-7k,
Budva seafront 2.5-5k, Kotor 2-4k. Sources named there: monstat.org, primera nepokretnost.
Tax: CGT 9%, transfer tax 3%, no annual property tax for foreigners.
Mid values are the midpoint of each range; they are benchmarks, not live data — verify before use.
Construction cost, cap rate: World Bank/Turner & Townsend table in connectors/generic.py (ME: 650 USD/sqm, 7.5%).
"""
from __future__ import annotations

from connectors._benchmark import BenchmarkConnector, build_assumptions
from connectors.base import register


@register
class MontenegroConnector(BenchmarkConnector):
    country_code = "ME"

    CITY_PRICES = {
        "tivat":   (3500.0, 5250.0, 7000.0),
        "porto montenegro": (3500.0, 5250.0, 7000.0),
        "budva":   (2500.0, 3750.0, 5000.0),
        "kotor":   (2000.0, 3000.0, 4000.0),
    }
    DEFAULT_CITY = "budva"
    PRICE_SOURCE = "LandIQ Montenegro benchmarks 2025 (monstat.org / primera nepokretnost ranges, midpoint) - verify before use"
    URBAN_SOURCE = "LandIQ Montenegro defaults (DUP/PUP - verify with the municipality)"
    DEFAULT_URBAN = {
        "plan_type": "DUP (Detailed Urban Plan)",
        "buildable_ratio": 1.2,
        "max_height_m": 18.0,
        "allowed_uses": ["residential", "touristic", "commercial", "mixed"],
        "constraints": [
            "Coastal zone: setback and public-maritime-domain rules apply",
            "Kotor Bay is UNESCO-listed: heritage buffer rules in Kotor and Perast",
            "Verify the current DUP/PUP for the parcel with the municipality",
        ],
    }
    CITY_URBAN = {
        "kotor": {**DEFAULT_URBAN, "plan_type": "Kotor DUP (UNESCO buffer)", "buildable_ratio": 0.8, "max_height_m": 12.0},
    }
    ASSUMPTIONS = build_assumptions(
        mid=3750.0, lo=2000.0, hi=7000.0, seaview=5250.0, touristic=3750.0, build=round(650 * 0.93, 0),
        cgt=0.09, transfer=0.03, cap_tour=0.075, cap_quo=0.08, wacc=0.10,
        adr=110.0, nights=150.0, opex_res=8.0, opex_tour=22.0,
    )
