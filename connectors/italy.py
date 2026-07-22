"""
LandIQ Italy Connector.

Data sources:
  - Market values : OMI (Osservatorio del Mercato Immobiliare) — Agenzia Entrate
  - Urban planning: PGT/PRG/PUC scrapers + curated Gaeta data
  - Currency      : EUR
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure project root on path so scrapers/ is importable.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.base import ConnectorBase, MarketData, UrbanisticData, register


@register
class ItalyConnector(ConnectorBase):
    country_code = "IT"
    currency = "EUR"
    eur_rate = 1.0

    # National-average defaults (Prezzario CRESME 2025-2026 + OMI medie nazionali).
    # Overridden with real OMI data when `fetch_market_data` succeeds.
    _DEFAULTS: dict[str, Any] = {
        "sale_price_residential_eur_sqm": 2200.0,
        "sale_price_residential_min": 1500.0,
        "sale_price_residential_max": 3500.0,
        "sale_price_residential_seaview": 2800.0,
        "conversion_cost_eur_sqm": 1300.0,
        "conversion_cost_min": 1100.0,
        "conversion_cost_max": 1700.0,
        "refurb_touristic_eur_sqm": 500.0,
        "omi_touristic_eur_sqm": 1500.0,
        "soft_cost_pct": 0.18,
        "contingency_pct": 0.10,
        "annual_opex_eur_sqm_residential": 8.0,
        "annual_opex_eur_sqm_touristic": 22.0,
        "touristic_adr_eur": 100.0,
        "touristic_occupancy_nights": 150.0,
        "touristic_ebitda_margin": 0.35,
        "cap_rate_touristic": 0.065,
        "cap_rate_status_quo": 0.07,
        "wacc": 0.08,
        "capital_gains_tax_pct": 0.26,   # Italy RE < 5y hold
        "land_transfer_tax_pct": 0.09,   # imposta di registro 9% (2a casa)
    }

    def fetch_market_data(
        self,
        city: str,
        address: str | None = None,
        use_type: str = "residential",
    ) -> MarketData:
        """Fetch OMI quotations for any Italian comune."""
        try:
            from scrapers import omi_agenzia_entrate as omi
            raw = omi.fetch_omi(city, provincia=self._provincia(city))
        except Exception:
            raw = {}

        zones = raw.get("zones", [])
        prices = []
        for z in zones:
            for qt in z.get("quotations", []):
                lo = qt.get("min_eur_sqm", 0) or 0
                hi = qt.get("max_eur_sqm", 0) or 0
                if lo > 0 and hi > 0:
                    prices.append((lo + hi) / 2)
                elif hi > 0:
                    prices.append(hi)
                elif lo > 0:
                    prices.append(lo)

        if prices:
            p50 = round(sum(prices) / len(prices), 0)
            p_min = round(min(prices), 0)
            p_max = round(max(prices), 0)
        else:
            p50 = self._DEFAULTS["sale_price_residential_eur_sqm"]
            p_min = self._DEFAULTS["sale_price_residential_min"]
            p_max = self._DEFAULTS["sale_price_residential_max"]

        return MarketData(
            city=city,
            country="IT",
            price_per_sqm=p50,
            price_min=p_min,
            price_max=p_max,
            currency="EUR",
            source="OMI Agenzia Entrate" if zones else "LandIQ defaults (Italy)",
            zones=zones,
            raw=raw,
        )

    def fetch_urbanistic_data(
        self,
        city: str,
        address: str | None = None,
    ) -> UrbanisticData:
        """Return PGT/PRG data for any Italian comune."""
        try:
            from scrapers.pgt_parser import get_pgt
            result = get_pgt(city, self._provincia(city))
            if result and result.zone:
                z0 = result.zone[0]
                return UrbanisticData(
                    city=city,
                    country="IT",
                    plan_type=result.plan_type or "PGT/PRG",
                    buildable_ratio=float(z0.indice_fondiario or 1.5),
                    max_height_m=float(z0.h_max_m or 12.5),
                    allowed_uses=z0.destinazioni or ["residenziale", "misto"],
                    constraints=z0.prescrizioni or [],
                    source=result.source_url or "PGT parser",
                    raw={"zones": [z.__dict__ for z in result.zone]},
                )
        except Exception:
            pass

        # Fallback: generic Italian zoning template
        return UrbanisticData(
            city=city,
            country="IT",
            plan_type="PRG/PGT",
            buildable_ratio=1.5,
            max_height_m=12.5,
            allowed_uses=["residenziale", "commerciale", "direzionale", "misto"],
            constraints=[
                "Verificare vincoli paesaggistici (D.Lgs. 42/2004)",
                "Verificare vincoli idrogeologici (PAI/PGRA)",
                "Dati urbanistici reali da verificare presso ufficio tecnico comunale",
            ],
            source="LandIQ defaults (Italy generic)",
        )

    def default_assumptions(self) -> dict[str, Any]:
        return dict(self._DEFAULTS)

    @staticmethod
    def _provincia(city: str) -> str:
        """Best-effort comuni→provincia lookup."""
        MAP = {
            "roma": "RM", "milano": "MI", "napoli": "NA", "torino": "TO",
            "palermo": "PA", "genova": "GE", "bologna": "BO", "firenze": "FI",
            "bari": "BA", "catania": "CT", "venezia": "VE", "verona": "VR",
            "brescia": "BS", "parma": "PR", "modena": "MO", "perugia": "PG",
            "cagliari": "CA", "salerno": "SA", "bergamo": "BG", "trento": "TN",
            "vicenza": "VI", "bolzano": "BZ", "novara": "NO", "ancona": "AN",
            "arezzo": "AR", "udine": "UD", "lecce": "LE", "gaeta": "LT",
            "como": "CO", "latina": "LT", "reggio emilia": "RE",
            "reggio calabria": "RC", "livorno": "LI", "ravenna": "RA",
            "ferrara": "FE", "sassari": "SS", "rimini": "RN", "foggia": "FG",
            "pescara": "PE", "forlì": "FC", "cesena": "FC", "piacenza": "PC",
            "siracusa": "SR", "taranto": "TA", "messina": "ME", "padova": "PD",
            "trieste": "TS", "monza": "MB", "andria": "BT", "terni": "TR",
            "pesaro": "PU",
        }
        return MAP.get(city.strip().lower(), "MI")
