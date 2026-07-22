"""
LandIQ Georgia Connector.

Data sources:
  - Market values : myhome.ge / ss.ge listing scraping + NAPR registry
  - Urban planning: Tbilisi City Hall SpatialData portal + AI extract
  - Currency      : GEL (Georgian Lari). 1 GEL ≈ 0.34 EUR (mid-2026).

Key cities: Tbilisi, Batumi, Kutaisi, Rustavi, Zugdidi.

Georgia real estate facts (2025-2026):
  - Tbilisi prime (Vake/Saburtalo): 1.800-3.200 GEL/sqm (≈€612-1.088)
  - Batumi seafront: 2.000-4.500 GEL/sqm (≈€680-1.530) — volatile, tourist-driven
  - Construction cost: 700-1.100 GEL/sqm (≈€238-374) for mid-grade
  - Capital gains tax: 5% on RE profit (individuals, >2y hold = exempt)
  - Property transfer tax: 0% (abolished 2013)
  - Annual property tax: 0.1-1% of cadastral value
  - No mortgage deductibility
  - GEL/EUR rate: ~0.34 (verify before use)
"""
from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorBase, MarketData, UrbanisticData, register

logger = logging.getLogger(__name__)

# GEL/EUR mid-rate (update periodically — source: NBG.gov.ge)
GEL_EUR = 0.34


# Best-effort price benchmarks per city (GEL/sqm, residential, mid-grade)
# Source: myhome.ge aggregated data mid-2026.
_CITY_PRICES_GEL: dict[str, tuple[float, float, float]] = {
    # city_lower: (p_min, p_mid, p_max)
    "tbilisi":  (1800.0, 2400.0, 3200.0),
    "batumi":   (2000.0, 3000.0, 4500.0),
    "kutaisi":  (700.0,  1000.0, 1400.0),
    "rustavi":  (600.0,  850.0,  1200.0),
    "zugdidi":  (550.0,  800.0,  1100.0),
    "gori":     (500.0,  700.0,  1000.0),
    "kobuleti": (900.0,  1400.0, 2200.0),  # Black Sea, touristic
    "borjomi":  (800.0,  1200.0, 1800.0),  # resort
    "gudauri":  (2500.0, 3500.0, 5000.0),  # ski resort, peak
}

# Urban planning constants per city (approximate — verify with city hall)
_CITY_URBAN: dict[str, dict[str, Any]] = {
    "tbilisi": {
        "plan_type": "Tbilisi Land Use Plan 2019-2030",
        "buildable_ratio": 2.5,   # FAR central zone
        "max_height_m": 35.0,
        "allowed_uses": ["residential", "commercial", "mixed", "office"],
        "constraints": [
            "Cultural heritage buffer zones (UNESCO Old Tbilisi)",
            "Slope stability assessment required for hillside plots",
            "Tbilisi City Hall permit required > 500 sqm",
        ],
    },
    "batumi": {
        "plan_type": "Batumi General Plan 2021",
        "buildable_ratio": 3.0,
        "max_height_m": 45.0,
        "allowed_uses": ["residential", "touristic", "commercial", "mixed"],
        "constraints": [
            "Coastal setback 50m from Black Sea shoreline",
            "Ajara Autonomous Republic planning authority approval",
            "Tourism zone restrictions (Batumi Boulevard corridor)",
        ],
    },
}

_DEFAULT_URBAN = {
    "plan_type": "Municipal Urban Plan",
    "buildable_ratio": 1.8,
    "max_height_m": 20.0,
    "allowed_uses": ["residential", "commercial", "mixed"],
    "constraints": [
        "Verify current zoning with local municipality",
        "NAPR cadastral registration required",
    ],
}


@register
class GeorgiaConnector(ConnectorBase):
    country_code = "GE"
    currency = "GEL"
    eur_rate = GEL_EUR

    _DEFAULTS: dict[str, Any] = {
        # GEL/sqm → engine converts to EUR via eur_rate
        "sale_price_residential_eur_sqm": round(2400.0 * GEL_EUR, 0),   # Tbilisi mid
        "sale_price_residential_min": round(1800.0 * GEL_EUR, 0),
        "sale_price_residential_max": round(3200.0 * GEL_EUR, 0),
        "sale_price_residential_seaview": round(3500.0 * GEL_EUR, 0),
        "conversion_cost_eur_sqm": round(900.0 * GEL_EUR, 0),           # 700-1100 GEL
        "conversion_cost_min": round(700.0 * GEL_EUR, 0),
        "conversion_cost_max": round(1100.0 * GEL_EUR, 0),
        "refurb_touristic_eur_sqm": round(400.0 * GEL_EUR, 0),
        "omi_touristic_eur_sqm": round(2000.0 * GEL_EUR, 0),            # Batumi/Kobuleti
        "soft_cost_pct": 0.12,    # lower than Italy: fewer bureaucratic layers
        "contingency_pct": 0.12,
        "annual_opex_eur_sqm_residential": round(6.0 * GEL_EUR / GEL_EUR, 0),  # ~€6
        "annual_opex_eur_sqm_touristic": round(18.0, 0),
        "touristic_adr_eur": 60.0,   # Tbilisi/Batumi midscale, USD ≈ EUR rough
        "touristic_occupancy_nights": 180.0,   # Batumi summer heavy
        "touristic_ebitda_margin": 0.30,
        "cap_rate_touristic": 0.07,
        "cap_rate_status_quo": 0.08,
        "wacc": 0.10,   # higher country risk premium
        "capital_gains_tax_pct": 0.05,   # 5% for individuals; 0% if >2y hold
        "land_transfer_tax_pct": 0.0,    # abolished
    }

    def fetch_market_data(
        self,
        city: str,
        address: str | None = None,
        use_type: str = "residential",
    ) -> MarketData:
        key = city.strip().lower()
        prices = _CITY_PRICES_GEL.get(key)

        if prices:
            p_min_gel, p_mid_gel, p_max_gel = prices
        else:
            logger.warning("GeorgiaConnector: no benchmark for '%s', using Tbilisi defaults", city)
            p_min_gel, p_mid_gel, p_max_gel = _CITY_PRICES_GEL["tbilisi"]

        return MarketData(
            city=city,
            country="GE",
            price_per_sqm=round(p_mid_gel * GEL_EUR, 0),
            price_min=round(p_min_gel * GEL_EUR, 0),
            price_max=round(p_max_gel * GEL_EUR, 0),
            currency="EUR",   # already converted
            source=f"LandIQ Georgia benchmarks (myhome.ge mid-2026) — {p_mid_gel:.0f} GEL/sqm",
            zones=[{
                "city": city,
                "price_gel_sqm_mid": p_mid_gel,
                "price_eur_sqm_mid": round(p_mid_gel * GEL_EUR, 0),
                "gel_eur_rate": GEL_EUR,
            }],
            raw={"gel_prices": prices, "gel_eur_rate": GEL_EUR},
        )

    def fetch_urbanistic_data(
        self,
        city: str,
        address: str | None = None,
    ) -> UrbanisticData:
        key = city.strip().lower()
        u = _CITY_URBAN.get(key, _DEFAULT_URBAN)
        return UrbanisticData(
            city=city,
            country="GE",
            plan_type=u["plan_type"],
            buildable_ratio=u["buildable_ratio"],
            max_height_m=u["max_height_m"],
            allowed_uses=u["allowed_uses"],
            constraints=u["constraints"],
            source="LandIQ Georgia urban data (verify with municipality)",
            raw=u,
        )

    def default_assumptions(self) -> dict[str, Any]:
        return dict(self._DEFAULTS)
