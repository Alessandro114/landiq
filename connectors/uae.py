"""
LandIQ UAE / Dubai Connector (issue #2).

Benchmarks (EUR/sqm, 2025) as published in the connector request: Palm Jumeirah 8-20k, Downtown 6-14k,
Dubai Marina 4-9k, JVC 2-4k, Business Bay (no range given: placed between JVC and Marina, flagged below).
Sources named there: DLD (Dubai Land Department), Bayut, Property Finder. AED/EUR 0.25 (verify).
Tax: 0% CGT, DLD transfer fee 4%, service charge about AED 15-30/sqft/year.
Mid values are range midpoints; benchmarks, not live data - verify before use.
Construction cost: connectors/generic.py table (AE: 1100 USD/sqm). Cap rate 6%.
"""
from __future__ import annotations

from connectors._benchmark import BenchmarkConnector, build_assumptions
from connectors.base import register

AED_EUR = 0.25


@register
class UAEConnector(BenchmarkConnector):
    country_code = "AE"

    CITY_PRICES = {
        "palm jumeirah": (8000.0, 14000.0, 20000.0),
        "downtown dubai": (6000.0, 10000.0, 14000.0),
        "downtown": (6000.0, 10000.0, 14000.0),
        "dubai marina": (4000.0, 6500.0, 9000.0),
        "marina": (4000.0, 6500.0, 9000.0),
        "business bay": (4000.0, 5500.0, 7000.0),   # range NOT in the request: interpolated, verify
        "jvc": (2000.0, 3000.0, 4000.0),
        "jumeirah village circle": (2000.0, 3000.0, 4000.0),
        "dubai": (2000.0, 6500.0, 14000.0),          # city-wide: JVC floor to Downtown ceiling
    }
    DEFAULT_CITY = "dubai"
    PRICE_SOURCE = "LandIQ UAE benchmarks 2025 (DLD / Bayut / Property Finder ranges, midpoint; AED/EUR 0.25) - verify before use"
    URBAN_SOURCE = "LandIQ UAE defaults (Dubai Municipality / DLD zoning - verify per master plan)"
    DEFAULT_URBAN = {
        "plan_type": "Dubai 2040 Urban Master Plan zoning",
        "buildable_ratio": 3.0,
        "max_height_m": 100.0,
        "allowed_uses": ["residential", "commercial", "mixed", "touristic"],
        "constraints": [
            "Freehold ownership by foreigners only in designated freehold areas",
            "DLD transfer fee 4% plus registration costs",
            "Developer master-plan and community guidelines can cap height/FAR below the municipal limit",
        ],
    }
    ASSUMPTIONS = build_assumptions(
        mid=6500.0, lo=2000.0, hi=14000.0, seaview=10000.0, touristic=6500.0, build=round(1100 * 0.93, 0),
        cgt=0.0, transfer=0.04, cap_tour=0.06, cap_quo=0.065, wacc=0.09,
        adr=170.0, nights=250.0, opex_res=45.0, opex_tour=70.0,
    )
