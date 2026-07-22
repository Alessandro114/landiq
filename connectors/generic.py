"""
LandIQ Generic Connector — fallback for unsupported countries.

Uses Gemini (or rule-based fallback) to estimate market values and
urban planning parameters from publicly available information.

This is the "community contribution starting point": copy this file,
rename to your country, fill in real data sources.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from connectors.base import ConnectorBase, MarketData, UrbanisticData

logger = logging.getLogger(__name__)

# World Bank approximate construction cost index (USD/sqm, 2025).
# Source: World Bank SAPI + Turner & Townsend International Construction
# Market Survey 2025. Converted to EUR at ~0.93.
_CONSTRUCTION_COST_USD_SQM: dict[str, float] = {
    "ES": 900.0,    # Spain
    "PT": 850.0,    # Portugal
    "GR": 800.0,    # Greece
    "HR": 750.0,    # Croatia
    "ME": 650.0,    # Montenegro
    "AL": 500.0,    # Albania
    "BG": 550.0,    # Bulgaria
    "RS": 600.0,    # Serbia
    "TR": 450.0,    # Turkey
    "AE": 1100.0,   # UAE/Dubai
    "TH": 600.0,    # Thailand
    "MX": 700.0,    # Mexico
    "BR": 650.0,    # Brazil
    "US": 1800.0,   # USA
    "GB": 2200.0,   # UK
    "DE": 1900.0,   # Germany
    "FR": 1700.0,   # France
    "AU": 1600.0,   # Australia
}

# Rough cap rates by country/market type
_CAP_RATES: dict[str, float] = {
    "ES": 0.055, "PT": 0.055, "GR": 0.065, "HR": 0.065, "ME": 0.075,
    "AL": 0.090, "BG": 0.080, "RS": 0.085, "TR": 0.090, "AE": 0.060,
    "TH": 0.070, "MX": 0.080, "BR": 0.085, "US": 0.055, "GB": 0.045,
    "DE": 0.040, "FR": 0.045, "AU": 0.050,
}

# Approximate capital gains tax rates (residential RE, individual)
_CGT: dict[str, float] = {
    "ES": 0.19, "PT": 0.28, "GR": 0.15, "HR": 0.10, "ME": 0.09,
    "AL": 0.15, "BG": 0.10, "RS": 0.15, "TR": 0.15, "AE": 0.00,
    "TH": 0.05, "MX": 0.30, "BR": 0.15, "US": 0.20, "GB": 0.24,
    "DE": 0.26, "FR": 0.30, "AU": 0.25,
}


class GenericConnector(ConnectorBase):
    """Fallback connector. Estimates from World Bank indices + Gemini AI if available."""

    currency = "EUR"
    eur_rate = 1.0  # all prices already converted to EUR in this connector

    def __init__(self, country_code: str = "XX") -> None:
        self.country_code = country_code.upper()
        self._gemini_key = os.getenv("GEMINI_API_KEY")

    def fetch_market_data(
        self,
        city: str,
        address: str | None = None,
        use_type: str = "residential",
    ) -> MarketData:
        logger.info(
            "GenericConnector: no native connector for %s — using AI/index estimates",
            self.country_code,
        )
        price = self._ai_estimate_price(city, use_type)
        spread = price * 0.25
        return MarketData(
            city=city,
            country=self.country_code,
            price_per_sqm=price,
            price_min=round(price - spread, 0),
            price_max=round(price + spread, 0),
            currency="EUR",
            source=f"LandIQ AI estimate (no {self.country_code} connector yet — contribute at github.com/get-scala/landiq)",
            zones=[],
            raw={"estimated": True, "country": self.country_code},
        )

    def fetch_urbanistic_data(
        self,
        city: str,
        address: str | None = None,
    ) -> UrbanisticData:
        return UrbanisticData(
            city=city,
            country=self.country_code,
            plan_type="Unknown (no connector)",
            buildable_ratio=1.5,
            max_height_m=15.0,
            allowed_uses=["residential", "commercial", "mixed"],
            constraints=[
                f"No {self.country_code} connector available — verify planning rules locally.",
                "Contribute a connector: github.com/get-scala/landiq/tree/main/connectors",
            ],
            source="LandIQ defaults (unsupported country)",
        )

    def default_assumptions(self) -> dict[str, Any]:
        cc = self.country_code
        build_usd = _CONSTRUCTION_COST_USD_SQM.get(cc, 900.0)
        build_eur = round(build_usd * 0.93, 0)
        cap = _CAP_RATES.get(cc, 0.07)
        cgt = _CGT.get(cc, 0.20)
        return {
            "sale_price_residential_eur_sqm": self._ai_estimate_price("", "residential"),
            "sale_price_residential_min": self._ai_estimate_price("", "residential") * 0.75,
            "sale_price_residential_max": self._ai_estimate_price("", "residential") * 1.35,
            "sale_price_residential_seaview": self._ai_estimate_price("", "residential") * 1.2,
            "conversion_cost_eur_sqm": build_eur,
            "conversion_cost_min": round(build_eur * 0.85, 0),
            "conversion_cost_max": round(build_eur * 1.30, 0),
            "refurb_touristic_eur_sqm": round(build_eur * 0.40, 0),
            "omi_touristic_eur_sqm": self._ai_estimate_price("", "touristic"),
            "soft_cost_pct": 0.15,
            "contingency_pct": 0.12,
            "annual_opex_eur_sqm_residential": 8.0,
            "annual_opex_eur_sqm_touristic": 20.0,
            "touristic_adr_eur": 80.0,
            "touristic_occupancy_nights": 150.0,
            "touristic_ebitda_margin": 0.32,
            "cap_rate_touristic": cap,
            "cap_rate_status_quo": cap + 0.005,
            "wacc": 0.09,
            "capital_gains_tax_pct": cgt,
            "land_transfer_tax_pct": 0.03,
        }

    def _ai_estimate_price(self, city: str, use_type: str) -> float:
        """Quick Gemini estimate for unsupported markets. Falls back to €1800."""
        if not self._gemini_key or not city:
            return 1800.0
        try:
            import google.generativeai as genai  # type: ignore
            genai.configure(api_key=self._gemini_key)
            model = genai.GenerativeModel("gemini-2.0-flash")
            prompt = (
                f"What is the approximate median residential real estate price per sqm in EUR "
                f"for {city}, {self.country_code}? "
                f"Reply with ONLY a number (no units, no text). Example: 2400"
            )
            resp = model.generate_content(prompt)
            return float(resp.text.strip().replace(",", "."))
        except Exception as e:
            logger.warning("GenericConnector AI estimate failed: %s", e)
            return 1800.0
