"""
LandIQ Country Connector — abstract base.

Each country connector implements this interface.
The core engine only talks to ConnectorBase — never to country-specific scrapers directly.

Adding a new country = one file, one class, ~100 lines.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Shared data models
# ---------------------------------------------------------------------------


@dataclass
class MarketData:
    """Normalised market value data returned by any connector."""

    city: str
    country: str
    price_per_sqm: float          # central estimate, local currency
    price_min: float
    price_max: float
    currency: str                  # ISO 4217, e.g. "EUR", "GEL", "GBP"
    source: str
    zones: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class UrbanisticData:
    """Normalised urbanistic/planning data returned by any connector."""

    city: str
    country: str
    plan_type: str                 # "PRG", "PGT", "zoning_map", "urban_plan", …
    buildable_ratio: float         # floor-area ratio or volumetric index (mc/sqm)
    max_height_m: float
    allowed_uses: list[str]        # e.g. ["residential", "mixed", "touristic"]
    constraints: list[str]         # plain-text list of legal/physical constraints
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Base connector
# ---------------------------------------------------------------------------


class ConnectorBase(ABC):
    """Abstract country connector.

    Implementors must supply:
      - country_code  : ISO 3166-1 alpha-2 ("IT", "GE", "ES", "UK", …)
      - currency      : ISO 4217 ("EUR", "GEL", …)
      - fetch_market_data()
      - fetch_urbanistic_data()
      - default_assumptions()

    The engine converts non-EUR prices using `eur_rate` before building DCF.
    """

    country_code: str = "XX"
    currency: str = "EUR"
    # 1 local_currency = eur_rate EUR  (1.0 for EUR-based countries)
    eur_rate: float = 1.0

    @abstractmethod
    def fetch_market_data(
        self,
        city: str,
        address: str | None = None,
        use_type: str = "residential",
    ) -> MarketData: ...

    @abstractmethod
    def fetch_urbanistic_data(
        self,
        city: str,
        address: str | None = None,
    ) -> UrbanisticData: ...

    @abstractmethod
    def default_assumptions(self) -> dict[str, Any]:
        """Return the baseline DCF/financial assumptions for this country.

        Keys must match LandIQEngine.DEFAULT_ASSUMPTIONS for drop-in override.
        """
        ...

    def to_eur(self, amount: float) -> float:
        """Convert local currency amount to EUR."""
        return amount * self.eur_rate


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[ConnectorBase]] = {}


def register(cls: type[ConnectorBase]) -> type[ConnectorBase]:
    """Decorator — auto-registers a connector by its country_code."""
    _REGISTRY[cls.country_code.upper()] = cls
    return cls


def get_connector(country_code: str) -> ConnectorBase:
    """Return a connector instance for the given ISO country code.

    Falls back to GenericConnector if the country is not explicitly supported.
    """
    code = country_code.upper()
    if code in _REGISTRY:
        return _REGISTRY[code]()
    # Lazy import to avoid circular deps
    from connectors.generic import GenericConnector
    return GenericConnector(country_code=code)
