"""
LandIQ Portugal Connector.

Data sources:
  - Market values : INE Portugal + Confidencial Imobiliário benchmarks
  - Urban planning: PDM (Plano Diretor Municipal) per municipality
  - Currency      : EUR (native)

Portugal real estate facts (2025-2026):
  - Lisbon prime (Chiado/Príncipe Real): €6.000-10.000/sqm
  - Porto (Bonfim/Foz do Douro): €3.000-6.000/sqm
  - Algarve (Lagos/Albufeira seafront): €3.500-8.000/sqm
  - Cascais/Estoril: €4.000-9.000/sqm
  - Capital gains tax: 28% (individuals, non-primary residence; 0% after 5y hold w/ re-investment)
  - IMT (transfer tax): 0-8% progressive on transaction value
  - IMI (annual property tax): 0.3-0.8% urban / 0.8% rural
  - Construction costs: €800-1.400/sqm mid-grade (2025)
  - NHR tax regime (Non-Habitual Resident): 20% flat for 10 years — key for expat buyers
  - Source: INE, Portal das Finanças, Confidencial Imobiliário
"""
from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorBase, MarketData, UrbanisticData, register

logger = logging.getLogger(__name__)


# Price benchmarks per city (EUR/sqm, residential, mid-grade, 2025)
# Source: INE Índice de Preços da Habitação + Confidencial Imobiliário Q1-2025
_CITY_PRICES_EUR: dict[str, tuple[float, float, float]] = {
    # city_lower: (p_min, p_mid, p_max)
    "lisboa":      (4500.0, 7000.0, 10000.0),
    "lisbon":      (4500.0, 7000.0, 10000.0),
    "porto":       (2800.0, 4500.0,  7000.0),
    "cascais":     (4000.0, 6500.0,  9500.0),
    "estoril":     (4000.0, 6000.0,  9000.0),
    "sintra":      (2000.0, 3200.0,  5000.0),
    "setubal":     (1200.0, 1900.0,  3000.0),
    "algarve":     (3000.0, 5000.0,  8500.0),
    "faro":        (2200.0, 3500.0,  5500.0),
    "lagos":       (3500.0, 5500.0,  9000.0),
    "albufeira":   (2800.0, 4500.0,  8000.0),
    "vilamoura":   (3500.0, 5500.0,  9000.0),
    "braga":       (1400.0, 2200.0,  3500.0),
    "coimbra":     (1200.0, 1900.0,  3000.0),
    "aveiro":      (1500.0, 2400.0,  3800.0),
    "guimaraes":   (1000.0, 1600.0,  2600.0),
    "madeira":     (2500.0, 4000.0,  7000.0),
    "funchal":     (2200.0, 3800.0,  6500.0),
    "azores":      (1200.0, 2000.0,  3500.0),
}

# Urban planning per city
_CITY_URBAN: dict[str, dict[str, Any]] = {
    "lisboa": {
        "plan_type": "PDM de Lisboa (revisão 2012, amendments to 2024)",
        "buildable_ratio": 2.0,
        "max_height_m": 28.0,
        "allowed_uses": ["residential", "commercial", "office", "mixed", "touristic"],
        "constraints": [
            "ARU (Áreas de Reabilitação Urbana) designation in Alfama/Mouraria/Intendente",
            "ACRRU classification for buildings pre-1951 — specific restoration rules",
            "Alojamento Local (AL) restrictions: city centre moratorium since 2023",
            "Environmental protection: Monsanto Forest Park buffer zone",
            "EIA (Estudo de Impacto Ambiental) required >10.000 sqm GFA",
        ],
    },
    "porto": {
        "plan_type": "PDM do Porto (2006, amendments to 2023)",
        "buildable_ratio": 1.8,
        "max_height_m": 22.0,
        "allowed_uses": ["residential", "commercial", "mixed", "touristic"],
        "constraints": [
            "UNESCO World Heritage buffer zone (Ribeira/Barredo) — strict restoration rules",
            "Alojamento Local moratorium in historic centre (ACRRU) since 2022",
            "RJUE (Regime Jurídico da Urbanização e Edificação) for licensing",
            "Flood zone study required for riverside plots (PROT-N)",
        ],
    },
    "algarve": {
        "plan_type": "PROT Algarve + individual PDMs (Faro, Loulé, Albufeira, etc.)",
        "buildable_ratio": 0.4,
        "max_height_m": 10.0,
        "allowed_uses": ["touristic", "residential", "commercial"],
        "constraints": [
            "RAN (Reserva Agrícola Nacional) covers ~20% of Algarve — no construction",
            "REN (Reserva Ecológica Nacional) coastal buffer",
            "POOC (Plano de Ordenamento da Orla Costeira) — 500m coastal restriction",
            "DPH (Domínio Público Hídrico) riverbank setback",
            "CCDR-Algarve approval required for developments >2ha",
        ],
    },
    "cascais": {
        "plan_type": "PDM de Cascais (2015, amendments to 2024)",
        "buildable_ratio": 0.8,
        "max_height_m": 15.0,
        "allowed_uses": ["residential", "touristic", "commercial"],
        "constraints": [
            "Sintra-Cascais Natural Park buffer zones (Parque Natural)",
            "Estuário do Tejo / Ribas natural reserve setbacks",
            "High demand area — housing license delays up to 18-24 months",
            "POOC restrictions on coastal construction",
        ],
    },
}

_DEFAULT_URBAN = {
    "plan_type": "PDM Municipal (Plano Diretor Municipal)",
    "buildable_ratio": 0.8,
    "max_height_m": 12.0,
    "allowed_uses": ["residential", "commercial", "mixed"],
    "constraints": [
        "Verify with Câmara Municipal — PDM rules vary significantly by municipality",
        "IMT transfer tax: 0-8% progressive on declared value",
        "NHR regime (Non-Habitual Resident) — 20% flat tax for 10 years for eligible foreign buyers",
        "ARU status unlocks tax incentives: IMI exemption up to 12y, IRC/IRS deductions on renovations",
        "AL (Alojamento Local) licence may be required for touristic rental",
    ],
}


@register
class PortugalConnector(ConnectorBase):
    """Portugal connector. Benchmarks from INE PT + Confidencial Imobiliário (Q1-2025)."""

    country_code = "PT"
    currency = "EUR"
    eur_rate = 1.0

    def fetch_market_data(
        self,
        city: str,
        address: str | None = None,
        use_type: str = "residential",
    ) -> MarketData:
        key = city.strip().lower()
        prices = _CITY_PRICES_EUR.get(key)

        if prices is None:
            for known_city, p in _CITY_PRICES_EUR.items():
                if known_city in key or key in known_city:
                    prices = p
                    break

        if prices is None:
            logger.warning("PortugalConnector: no benchmark for %s — using national average", city)
            prices = (1200.0, 2200.0, 4000.0)

        p_min, p_mid, p_max = prices
        multipliers = {"residential": 1.0, "commercial": 1.05, "touristic": 1.20, "office": 1.08}
        m = multipliers.get(use_type.lower(), 1.0)

        return MarketData(
            city=city,
            country="PT",
            price_per_sqm=round(p_mid * m, 0),
            price_min=round(p_min * m, 0),
            price_max=round(p_max * m, 0),
            currency="EUR",
            source=(
                "LandIQ PT connector — INE Portugal Índice de Preços da Habitação "
                "+ Confidencial Imobiliário Q1-2025"
            ),
            zones=[{"name": city, "price_eur_sqm_min": p_min, "price_eur_sqm_mid": p_mid, "price_eur_sqm_max": p_max}],
            raw={"connector": "portugal", "use_type_multiplier": m},
        )

    def fetch_urbanistic_data(
        self,
        city: str,
        address: str | None = None,
    ) -> UrbanisticData:
        key = city.strip().lower()
        urban = _CITY_URBAN.get(key, _DEFAULT_URBAN)
        return UrbanisticData(
            city=city,
            country="PT",
            plan_type=urban["plan_type"],
            buildable_ratio=urban["buildable_ratio"],
            max_height_m=urban["max_height_m"],
            allowed_uses=urban["allowed_uses"],
            constraints=urban["constraints"],
            source="LandIQ PT connector — PDM / Portal Dados Abertos / CCDR",
        )

    def default_assumptions(self) -> dict[str, Any]:
        return {
            "sale_price_residential_eur_sqm": 3000.0,
            "sale_price_residential_min": 1200.0,
            "sale_price_residential_max": 7000.0,
            "sale_price_residential_seaview": 5500.0,
            "conversion_cost_eur_sqm": 950.0,
            "conversion_cost_min": 750.0,
            "conversion_cost_max": 1400.0,
            "refurb_touristic_eur_sqm": 350.0,
            "omi_touristic_eur_sqm": 3500.0,
            "soft_cost_pct": 0.14,
            "contingency_pct": 0.12,
            "annual_opex_eur_sqm_residential": 9.0,
            "annual_opex_eur_sqm_touristic": 20.0,
            "touristic_adr_eur": 110.0,
            "touristic_occupancy_nights": 170.0,
            "touristic_ebitda_margin": 0.34,
            "cap_rate_touristic": 0.055,
            "cap_rate_status_quo": 0.060,
            "wacc": 0.08,
            "capital_gains_tax_pct": 0.28,  # 28% individuals; 0% if re-invested in primary residence
            "land_transfer_tax_pct": 0.065,  # IMT: 0-8% progressive, avg ~6.5%
        }
