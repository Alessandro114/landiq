"""
Shared base for table-driven connectors (city benchmarks in EUR/sqm + fixed tax/DCF constants).

The country files (montenegro.py, uae.py, bulgaria.py) only hold data. Benchmarks are
published ranges, not live feeds: `source` always says so, and the CONTRIBUTING rule
"AI estimates only in GenericConnector" is respected (no AI call here).
"""
from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorBase, MarketData, UrbanisticData

logger = logging.getLogger(__name__)


class BenchmarkConnector(ConnectorBase):
    currency = "EUR"
    eur_rate = 1.0

    # city_lower -> (min, mid, max) EUR/sqm, residential
    CITY_PRICES: dict[str, tuple[float, float, float]] = {}
    DEFAULT_CITY: str = ""
    # city_lower -> urban dict (plan_type, buildable_ratio, max_height_m, allowed_uses, constraints)
    CITY_URBAN: dict[str, dict[str, Any]] = {}
    DEFAULT_URBAN: dict[str, Any] = {}
    PRICE_SOURCE: str = ""
    URBAN_SOURCE: str = ""
    ASSUMPTIONS: dict[str, Any] = {}

    def fetch_market_data(self, city: str, address: str | None = None, use_type: str = "residential") -> MarketData:
        key = city.strip().lower()
        prices = self.CITY_PRICES.get(key)
        if prices is None:
            logger.warning("%s: no benchmark for '%s', using %s", type(self).__name__, city, self.DEFAULT_CITY)
            prices = self.CITY_PRICES[self.DEFAULT_CITY]
        p_min, p_mid, p_max = prices
        return MarketData(
            city=city,
            country=self.country_code,
            price_per_sqm=p_mid,
            price_min=p_min,
            price_max=p_max,
            currency="EUR",
            source=self.PRICE_SOURCE,
            zones=[{"city": city, "price_eur_sqm_mid": p_mid, "benchmark_found": key in self.CITY_PRICES}],
            raw={"eur_prices": prices, "benchmark_found": key in self.CITY_PRICES},
        )

    def fetch_urbanistic_data(self, city: str, address: str | None = None) -> UrbanisticData:
        u = self.CITY_URBAN.get(city.strip().lower(), self.DEFAULT_URBAN)
        return UrbanisticData(
            city=city,
            country=self.country_code,
            plan_type=u["plan_type"],
            buildable_ratio=u["buildable_ratio"],
            max_height_m=u["max_height_m"],
            allowed_uses=list(u["allowed_uses"]),
            constraints=list(u["constraints"]),
            source=self.URBAN_SOURCE,
            raw=dict(u),
        )

    def default_assumptions(self) -> dict[str, Any]:
        return dict(self.ASSUMPTIONS)


def build_assumptions(mid: float, lo: float, hi: float, seaview: float, touristic: float, build: float,
                      cgt: float, transfer: float, cap_tour: float, cap_quo: float, wacc: float,
                      adr: float, nights: float, opex_res: float, opex_tour: float) -> dict[str, Any]:
    """Same keys as GeorgiaConnector._DEFAULTS / LandIQEngine.DEFAULT_ASSUMPTIONS."""
    return {
        "sale_price_residential_eur_sqm": mid,
        "sale_price_residential_min": lo,
        "sale_price_residential_max": hi,
        "sale_price_residential_seaview": seaview,
        "conversion_cost_eur_sqm": build,
        "conversion_cost_min": round(build * 0.85, 0),
        "conversion_cost_max": round(build * 1.30, 0),
        "refurb_touristic_eur_sqm": round(build * 0.40, 0),
        "omi_touristic_eur_sqm": touristic,
        "soft_cost_pct": 0.12,
        "contingency_pct": 0.12,
        "annual_opex_eur_sqm_residential": opex_res,
        "annual_opex_eur_sqm_touristic": opex_tour,
        "touristic_adr_eur": adr,
        "touristic_occupancy_nights": nights,
        "touristic_ebitda_margin": 0.30,
        "cap_rate_touristic": cap_tour,
        "cap_rate_status_quo": cap_quo,
        "wacc": wacc,
        "capital_gains_tax_pct": cgt,
        "land_transfer_tax_pct": transfer,
    }
