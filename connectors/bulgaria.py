"""
LandIQ Bulgaria Connector (issue #3).

Benchmarks (EUR/sqm, 2025) as published in the connector request: Sofia prime 1.5-3k, Bansko ski resort
800-2k, Black Sea coast (Sunny Beach, Sozopol) 600-1.5k. Sources named there: imot.bg, NSI, Registry Agency.
Tax: CGT 10% flat, transfer tax 2-3% (2.5% used), annual tax 0.1-0.45%.
Mid values are range midpoints; benchmarks, not live data - verify before use.
Construction cost: connectors/generic.py table (BG: 550 USD/sqm). Cap rate 8%.
"""
from __future__ import annotations

from connectors._benchmark import BenchmarkConnector, build_assumptions
from connectors.base import register


@register
class BulgariaConnector(BenchmarkConnector):
    country_code = "BG"

    CITY_PRICES = {
        "sofia": (1500.0, 2250.0, 3000.0),
        "bansko": (800.0, 1400.0, 2000.0),
        "sunny beach": (600.0, 1050.0, 1500.0),
        "sozopol": (600.0, 1050.0, 1500.0),
    }
    DEFAULT_CITY = "sofia"
    PRICE_SOURCE = "LandIQ Bulgaria benchmarks 2025 (imot.bg / NSI ranges, midpoint) - verify before use"
    URBAN_SOURCE = "LandIQ Bulgaria defaults (ZUT / municipal general plan - verify with the municipality)"
    DEFAULT_URBAN = {
        "plan_type": "General Urban Plan (OUP) / Detailed Plan (PUP)",
        "buildable_ratio": 2.0,
        "max_height_m": 25.0,
        "allowed_uses": ["residential", "commercial", "mixed"],
        "constraints": [
            "Detailed development plan (PUP) required before a building permit",
            "Foreigners cannot own agricultural land directly; EU citizens can via a company",
            "Verify cadastre extract at the Registry Agency",
        ],
    }
    CITY_URBAN = {
        "bansko": {**DEFAULT_URBAN, "plan_type": "Bansko OUP (Pirin National Park buffer)", "buildable_ratio": 1.2, "max_height_m": 15.0,
                   "allowed_uses": ["residential", "touristic", "mixed"]},
        "sunny beach": {**DEFAULT_URBAN, "plan_type": "Nesebar OUP (Black Sea coastal zone)", "allowed_uses": ["residential", "touristic", "mixed"]},
        "sozopol": {**DEFAULT_URBAN, "plan_type": "Sozopol OUP (Black Sea coast, old-town protection)", "buildable_ratio": 1.0, "max_height_m": 12.0,
                    "allowed_uses": ["residential", "touristic", "mixed"]},
    }
    ASSUMPTIONS = build_assumptions(
        mid=2250.0, lo=600.0, hi=3000.0, seaview=1500.0, touristic=1050.0, build=round(550 * 0.93, 0),
        cgt=0.10, transfer=0.025, cap_tour=0.08, cap_quo=0.085, wacc=0.10,
        adr=75.0, nights=140.0, opex_res=6.0, opex_tour=18.0,
    )
