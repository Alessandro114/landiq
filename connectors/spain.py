"""
LandIQ Spain Connector.

Data sources:
  - Market values : INE (Instituto Nacional de Estadística) + idealista.com benchmarks
  - Urban planning: PGOU / PGM (Plan General de Ordenación Urbana / Municipal)
  - Currency      : EUR (native)

Spain real estate facts (2025-2026):
  - Madrid prime (Salamanca/Chamberí): €5.000-8.500/sqm
  - Barcelona prime (Eixample/Gràcia): €4.500-8.000/sqm
  - Marbella/Golden Mile: €4.000-12.000/sqm
  - Valencia (Russafa/El Carmen): €2.000-3.500/sqm
  - Sevilla (centro): €2.500-4.000/sqm
  - Capital gains tax: 19-28% (progressive, 19% up to €6k, 21% €6-50k, 23% €50-200k, 27% >€200k)
  - ITP (transfer tax): 6-10% depending on autonomous community
  - IBI (annual property tax): ~0.4-1.1% cadastral value
  - Plusvalía municipal: local capital gain tax on land value increase
  - Source: INE, Catastro, Colegio de Registradores
"""
from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorBase, MarketData, UrbanisticData, register

logger = logging.getLogger(__name__)


# Price benchmarks per city (EUR/sqm, residential, mid-grade, 2025)
# Source: INE Estadística de Precios de la Vivienda + idealista.com Q1-2025
_CITY_PRICES_EUR: dict[str, tuple[float, float, float]] = {
    # city_lower: (p_min, p_mid, p_max)
    "madrid":        (3500.0, 5200.0,  8500.0),
    "barcelona":     (3200.0, 4800.0,  8000.0),
    "marbella":      (3500.0, 6500.0, 12000.0),
    "malaga":        (2200.0, 3500.0,  5500.0),
    "valencia":      (1800.0, 2600.0,  4000.0),
    "sevilla":       (1800.0, 2800.0,  4500.0),
    "bilbao":        (2500.0, 3600.0,  5500.0),
    "san sebastian": (3500.0, 5000.0,  7500.0),
    "zaragoza":      (1200.0, 1800.0,  2800.0),
    "palma":         (2500.0, 4000.0,  7000.0),  # Mallorca
    "alicante":      (1400.0, 2200.0,  3800.0),
    "granada":       (1200.0, 1900.0,  3000.0),
    "cadiz":         (1000.0, 1600.0,  2800.0),
    "murcia":        (900.0,  1400.0,  2200.0),
    "ibiza":         (4500.0, 7000.0, 12000.0),
    "costa del sol": (2500.0, 4000.0,  8000.0),
    "menorca":       (2000.0, 3500.0,  6000.0),
}

# Urban planning config per city (approximate — verify with ayuntamiento)
_CITY_URBAN: dict[str, dict[str, Any]] = {
    "madrid": {
        "plan_type": "Plan General de Ordenación Urbana de Madrid (PGOUM)",
        "buildable_ratio": 2.5,
        "max_height_m": 30.0,
        "allowed_uses": ["residential", "commercial", "office", "mixed"],
        "constraints": [
            "APNR zones (no further development) cover ~40% of city",
            "Historic center BIC protection (UNESCO, LPCM 3/2013)",
            "COAM (Colegio de Arquitectos) prior report required",
            "Plusvalía municipal applies on land value gains",
        ],
    },
    "barcelona": {
        "plan_type": "Pla General Metropolità (PGM 1976, successive amendments)",
        "buildable_ratio": 2.0,
        "max_height_m": 25.0,
        "allowed_uses": ["residential", "commercial", "mixed", "office"],
        "constraints": [
            "PEUAT tourism apartment plan restricts new tourist licenses in Eixample/Gràcia",
            "Superilla Barcelona zoning changes underway (2024-2026)",
            "22@ innovation district special urban regime",
            "Heritage protection: Catàleg del Patrimoni Arquitectònic",
        ],
    },
    "marbella": {
        "plan_type": "Plan General de Ordenación Urbanística de Marbella (PGOU 2010)",
        "buildable_ratio": 0.5,
        "max_height_m": 12.0,
        "allowed_uses": ["residential", "touristic", "commercial"],
        "constraints": [
            "DPMT (Dominio Público Marítimo-Terrestre) coastal setback 100m",
            "PGOU under revision — uncertainty in some zones until approval",
            "Junta de Andalucía OCA report required for coastal projects",
            "Large high-end projects require EIA (Evaluación de Impacto Ambiental)",
        ],
    },
}

_DEFAULT_URBAN = {
    "plan_type": "PGOU Municipal (Plan General de Ordenación Urbana)",
    "buildable_ratio": 1.5,
    "max_height_m": 16.0,
    "allowed_uses": ["residential", "commercial", "mixed"],
    "constraints": [
        "Verify with local Ayuntamiento — PGOU rules vary significantly by municipality",
        "ITP transfer tax: 6-10% depending on Autonomous Community",
        "Plusvalía municipal: land capital gain tax payable on sale",
        "CEE (Certificado Eficiencia Energética) mandatory for all transactions",
    ],
}


@register
class SpainConnector(ConnectorBase):
    """Spain connector. Benchmarks from INE + idealista.com (Q1-2025)."""

    country_code = "ES"
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
            # Provincial fallback: check if city contains a known key
            for known_city, p in _CITY_PRICES_EUR.items():
                if known_city in key or key in known_city:
                    prices = p
                    break

        if prices is None:
            logger.warning("SpainConnector: no benchmark for %s — using national average", city)
            prices = (1500.0, 2400.0, 4000.0)

        p_min, p_mid, p_max = prices
        multipliers = {"residential": 1.0, "commercial": 1.1, "touristic": 1.25, "office": 1.05}
        m = multipliers.get(use_type.lower(), 1.0)

        return MarketData(
            city=city,
            country="ES",
            price_per_sqm=round(p_mid * m, 0),
            price_min=round(p_min * m, 0),
            price_max=round(p_max * m, 0),
            currency="EUR",
            source=(
                "LandIQ ES connector — INE Estadística Precios Vivienda "
                "+ idealista.com Q1-2025 benchmarks"
            ),
            zones=[{"name": city, "price_eur_sqm_min": p_min, "price_eur_sqm_mid": p_mid, "price_eur_sqm_max": p_max}],
            raw={"connector": "spain", "use_type_multiplier": m},
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
            country="ES",
            plan_type=urban["plan_type"],
            buildable_ratio=urban["buildable_ratio"],
            max_height_m=urban["max_height_m"],
            allowed_uses=urban["allowed_uses"],
            constraints=urban["constraints"],
            source="LandIQ ES connector — PGOU / Catastro / Ayuntamiento",
        )

    def default_assumptions(self) -> dict[str, Any]:
        return {
            "sale_price_residential_eur_sqm": 2400.0,
            "sale_price_residential_min": 1500.0,
            "sale_price_residential_max": 5200.0,
            "sale_price_residential_seaview": 4000.0,
            "conversion_cost_eur_sqm": 900.0,
            "conversion_cost_min": 700.0,
            "conversion_cost_max": 1300.0,
            "refurb_touristic_eur_sqm": 400.0,
            "omi_touristic_eur_sqm": 3000.0,
            "soft_cost_pct": 0.15,
            "contingency_pct": 0.12,
            "annual_opex_eur_sqm_residential": 10.0,
            "annual_opex_eur_sqm_touristic": 22.0,
            "touristic_adr_eur": 100.0,
            "touristic_occupancy_nights": 160.0,
            "touristic_ebitda_margin": 0.35,
            "cap_rate_touristic": 0.055,
            "cap_rate_status_quo": 0.060,
            "wacc": 0.08,
            "capital_gains_tax_pct": 0.21,  # 19-28% progressive; 21% mid bracket
            "land_transfer_tax_pct": 0.08,   # ITP: 6-10% by CCAA, ~8% avg
        }
