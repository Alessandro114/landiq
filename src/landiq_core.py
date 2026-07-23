"""
LandIQ — Autonomous AI Agent for Real Estate Feasibility Analysis.

Give the agent a property address. It autonomously:
  1. Identifies the country → selects the right data connector
  2. Researches market data (prices, benchmarks, comparable sales)
  3. Researches urbanistic constraints (zoning, FAR, height limits, permits)
  4. Builds 3 investment scenarios (residential / touristic / status-quo)
  5. Runs DCF analysis with sensitivity on key variables
  6. Runs Monte Carlo simulation (10,000 iterations) → P5/P50/P95
  7. Generates an AI-powered executive summary + GO/NO-GO verdict
  8. Produces a 15-20 page PDF report — no human in the loop

Usage:
    agent = LandIQEngine()
    inp = FeasibilityInput(
        address="Rustaveli 45, Batumi",
        sqm=600, country="GE", city="Batumi",
        current_use="commercial", target_use="touristic",
        budget=900_000, horizon_years=5,
    )
    report = agent.run(inp)    # fully autonomous
    agent.export_pdf(report, "reports/batumi_ge.pdf")
"""

from __future__ import annotations

import datetime as dt
import io
import json
import math
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

import numpy as np

try:
    import numpy_financial as npf  # type: ignore
    _HAVE_NPF = True
except ImportError:  # pragma: no cover
    _HAVE_NPF = False

# Ensure scrapers package is importable when running from project root.
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

__version__ = "0.3.0"

# -----------------------------------------------------------------------------
# DATA CLASSES
# -----------------------------------------------------------------------------

UseType = Literal[
    "residenziale",
    "ricettivo_alberghiero",
    "ricettivo_extralberghiero",
    "direzionale",
    "commerciale",
    "misto",
    "terreno_edificabile",
    "terreno_agricolo",
    # International aliases (used by non-IT connectors)
    "residential",
    "touristic",
    "commercial",
    "mixed",
    "office",
    "land",
]


@dataclass
class FeasibilityInput:
    """User-facing input for a feasibility analysis.

    country: ISO 3166-1 alpha-2 code (default "IT" for backward compat).
             The engine auto-selects the right connector from this field.
    city:    City name (language/transliteration of the target country).
             For Italy, also accepts `comune` (legacy alias).
    """

    address: str
    sqm: float
    current_use: UseType
    target_use: UseType
    budget: float  # euro (or local currency — connector normalises to EUR)
    horizon_years: int = 5
    # Country / location
    country: str = "IT"           # ISO 3166-1 alpha-2
    city: str | None = None       # canonical city field (any country)
    # Italy-specific enrichments (kept for backward compat — aliases for city/country)
    comune: str | None = None
    provincia: str | None = None
    cap: str | None = None
    particella_catastale: str | None = None
    parcel_area_sqm: float | None = None  # lot area (for volumetria calc)
    existing_building_volume_mc: float | None = None
    client_name: str | None = None
    notes: str | None = None


@dataclass
class ScenarioResult:
    """Output of one development scenario."""

    name: str
    description: str
    capex: float  # euro
    revenue: float  # euro, total over horizon
    opex_total: float  # euro, total operating costs over horizon
    npv: float  # euro, net present value @ WACC
    irr: float  # decimal, 0.15 = 15%
    payback_months: int
    risk_score: float  # 0..10, 10 = worst
    cashflows_by_year: list[float] = field(default_factory=list)
    assumptions: dict[str, Any] = field(default_factory=dict)


@dataclass
class MonteCarloResult:
    """Output of MC sensitivity — one per scenario."""

    scenario_name: str
    n_runs: int
    npv_p5: float
    npv_p50: float
    npv_p95: float
    irr_p5: float
    irr_p50: float
    irr_p95: float
    prob_npv_negative: float
    prob_irr_above_target: float
    target_irr: float
    tornado: dict[str, float] = field(default_factory=dict)


@dataclass
class FeasibilityReport:
    """Final bundle passed to PDF exporter + Gemini verdict."""

    input: FeasibilityInput
    urbanistic_data: dict[str, Any]
    market_data: dict[str, Any]
    volumetry: dict[str, Any]
    scenarios: list[ScenarioResult]
    monte_carlo: list[MonteCarloResult]
    ai_verdict: str
    recommended_scenario: str
    generated_at: str  # ISO 8601
    sources: list[str] = field(default_factory=list)


# -----------------------------------------------------------------------------
# ENGINE
# -----------------------------------------------------------------------------


class LandIQEngine:
    """Central orchestrator. Each stage is a separate method for testability."""

    DEFAULT_WACC = 0.08
    DEFAULT_TARGET_IRR = 0.15

    # Default assumptions for Italian real estate feasibility analysis.
    # These are national-average defaults; build_assumptions_from_omi()
    # overrides sale prices with real OMI data when available.
    DEFAULT_ASSUMPTIONS = {
        # Residential sale price — national average (overridden by OMI data)
        "sale_price_residential_eur_sqm": 2200.0,
        "sale_price_residential_min": 1500.0,
        "sale_price_residential_max": 3500.0,
        "sale_price_residential_seaview": 2800.0,
        # Construction/conversion cost — Prezzario medio nazionale 2025-2026
        "conversion_cost_eur_sqm": 1300.0,
        "conversion_cost_min": 1100.0,
        "conversion_cost_max": 1700.0,
        # Refurb light on existing structure
        "refurb_touristic_eur_sqm": 500.0,
        # Touristic daily market values
        "omi_touristic_eur_sqm": 1500.0,
        # Soft cost multiplier (permits, SUAP, design, DL, IVA, taxes)
        "soft_cost_pct": 0.18,
        # Contingency
        "contingency_pct": 0.10,
        # Operating costs (IMU, TARI, gestione, utilities)
        "annual_opex_eur_sqm_residential": 8.0,
        "annual_opex_eur_sqm_touristic": 22.0,
        # Touristic NOI defaults
        "touristic_adr_eur": 100.0,
        "touristic_occupancy_nights": 150.0,
        "touristic_ebitda_margin": 0.35,
        # Cap rates
        "cap_rate_touristic": 0.065,
        "cap_rate_status_quo": 0.07,
        # WACC
        "wacc": DEFAULT_WACC,
        # Capital gains tax (Italy real estate, < 5 years hold)
        "capital_gains_tax_pct": 0.26,
    }

    # Legacy alias for backward compatibility
    GAETA_ASSUMPTIONS = DEFAULT_ASSUMPTIONS

    def __init__(
        self,
        gemini_api_key: str | None = None,  # DEPRECATED — use GROQ_API_KEY or LANDIQ_AI_PROVIDER
        cache_dir: Path | str = Path("data/cache"),
        wacc: float = DEFAULT_WACC,
        connector=None,  # ConnectorBase instance; auto-detected if None
    ) -> None:
        # gemini_api_key kept for backward compat but ignored — AI now via ai_provider.py
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.wacc = wacc
        self._connector = connector  # set per-run in run() if None

    def _get_connector(self, country: str = "IT"):
        """Return connector for the given country, caching for the session."""
        if self._connector is not None:
            return self._connector
        # Lazy import to avoid circular at module load time
        _ROOT = Path(__file__).resolve().parent.parent
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        # Trigger registration of all built-in connectors
        import connectors.italy   # noqa: F401
        import connectors.georgia  # noqa: F401
        from connectors.base import get_connector
        return get_connector(country)

    def _observe_data_quality(
        self,
        market: dict[str, Any],
        urbanistic: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        """Observe step: evaluate data quality from the research phase.
        Returns a score (0-10) and list of issues found."""
        score = 10
        issues: list[str] = []

        # Market data checks
        price = market.get("price_per_sqm", 0)
        if not price or price <= 0:
            score -= 4
            issues.append("no_market_price")
        elif market.get("raw", {}).get("estimated"):
            score -= 2
            issues.append("market_price_is_ai_estimate")

        zones = market.get("zones", [])
        if not zones:
            score -= 1
            issues.append("no_zone_data")

        # Urbanistic data checks
        if not urbanistic.get("plan_type") or urbanistic.get("plan_type") == "Unknown (no connector)":
            score -= 2
            issues.append("no_urban_plan")

        constraints = urbanistic.get("constraints", [])
        if not constraints:
            score -= 1
            issues.append("no_constraints_data")

        # Connector type check
        if not plan.get("has_dedicated_connector"):
            score -= 1
            issues.append("using_generic_connector")

        return {"score": max(0, score), "issues": issues}

    @staticmethod
    def _connector_to_dict(md) -> dict[str, Any]:
        """Convert a MarketData dataclass to dict."""
        return {
            "city": md.city,
            "country": md.country,
            "price_per_sqm": md.price_per_sqm,
            "price_min": md.price_min,
            "price_max": md.price_max,
            "currency": md.currency,
            "source": md.source,
            "zones": md.zones,
            "raw": md.raw,
        }

    def build_assumptions(self, market_data: dict[str, Any], country: str = "IT") -> dict[str, Any]:
        """Build location-specific assumptions from connector defaults + live market data.

        Falls back to DEFAULT_ASSUMPTIONS (Italy) for backward compat when
        market_data is a raw OMI dict (legacy callers).
        """
        conn = self._get_connector(country)
        a = conn.default_assumptions()

        # Override with live prices from MarketData.zones if present
        zones = market_data.get("zones", [])
        if zones:
            prices = []
            for z in zones:
                # Italy OMI format
                for qt in z.get("quotations", []):
                    lo = qt.get("min_eur_sqm", 0) or 0
                    hi = qt.get("max_eur_sqm", 0) or 0
                    if lo > 0 and hi > 0:
                        prices.append((lo + hi) / 2)
                    elif hi > 0:
                        prices.append(hi)
                    elif lo > 0:
                        prices.append(lo)
                # Generic connector format: price_eur_sqm_mid
                mid = z.get("price_eur_sqm_mid")
                if mid:
                    prices.append(mid)

            if prices:
                avg_price = sum(prices) / len(prices)
                a["sale_price_residential_eur_sqm"] = round(avg_price, 0)
                a["sale_price_residential_min"] = round(min(prices), 0)
                a["sale_price_residential_max"] = round(max(prices), 0)
                a["sale_price_residential_seaview"] = round(max(prices) * 1.15, 0)

        return a

    # --- DATA INGESTION ------------------------------------------------------

    def fetch_market_data(self, city: str, country: str = "IT", **kwargs) -> dict[str, Any]:
        """Fetch market data for any city/country via the appropriate connector."""
        conn = self._get_connector(country)
        md = conn.fetch_market_data(city, **kwargs)
        # Return as dict for backward compat (callers expect dicts, not dataclasses)
        return {
            "city": md.city,
            "country": md.country,
            "price_per_sqm": md.price_per_sqm,
            "price_min": md.price_min,
            "price_max": md.price_max,
            "currency": md.currency,
            "source": md.source,
            "zones": md.zones,
            "raw": md.raw,
        }

    def fetch_omi(self, comune: str, provincia: str = "LT", zona: str | None = None) -> dict[str, Any]:
        """Legacy alias — fetch OMI quotations for Italian comuni. Prefer fetch_market_data()."""
        return self.fetch_market_data(comune, country="IT", provincia=provincia, zona=zona)

    def fetch_urbanistic_data(self, city: str, country: str = "IT", **kwargs) -> dict[str, Any]:
        """Fetch urbanistic/planning data for any city/country via connector."""
        conn = self._get_connector(country)
        ud = conn.fetch_urbanistic_data(city, **kwargs)
        return {
            "city": ud.city,
            "country": ud.country,
            "plan_type": ud.plan_type,
            "buildable_ratio": ud.buildable_ratio,
            "max_height_m": ud.max_height_m,
            "allowed_uses": ud.allowed_uses,
            "constraints": ud.constraints,
            "source": ud.source,
            "raw": ud.raw,
            # Italy-compat fields
            "zones": ud.raw.get("zones", []),
            "vincoli": ud.constraints,
        }

    # Gaeta-specific PUC data (legacy MVP reference)
    _GAETA_PUC: dict[str, Any] = {
        "comune": "Gaeta",
        "plan_type": "PRG",
        "approval_date": "1973",
        "variante_in_corso": True,
        "variante_status": (
            "Documento preliminare di indirizzo pubblicato 20 set 2025; "
            "incarico a RTP Mate/Stanghellini dal 2015. Misure di salvaguardia attive."
        ),
        "nta_url": (
            "https://www.comune.gaeta.lt.it/it/documenti_pubblici/"
            "doc-pre-di-indirizzo-della-variante-generale-al-p-r-g"
        ),
        "zoning_shapefile_url": None,
        "zones": [
            {
                "code": "F",
                "description": "Attrezzature turistico-ricettive, fascia costiera",
                "if_fondiario_mc_sqm": 1.5,
                "h_max_m": 13.0,
                "piani_max": 4,
                "destinazioni_ammesse": [
                    "ricettivo_alberghiero",
                    "ricettivo_extralberghiero",
                    "residenziale",
                ],
                "prescrizioni": [
                    "Vincolo paesaggistico fascia costiera 300m (art. 142 D.Lgs. 42/2004)",
                    "PTPR Lazio Ambito 14 — autorizzazione ex art. 146",
                ],
            },
        ],
        "vincoli": [
            "art. 142 D.Lgs. 42/2004 (fascia costiera 300m)",
            "PTPR Lazio D.C.R. 5/2021 — Ambito 14 Cassino/Gaeta/Ponza",
            "Possibile vincolo alberghiero (L.R. 8/2022 per svincolo)",
            "Misure di salvaguardia variante PRG in corso",
        ],
        "source_url": (
            "https://www.comune.gaeta.lt.it/it/documenti_pubblici/"
            "doc-pre-di-indirizzo-della-variante-generale-al-p-r-g"
        ),
    }

    def fetch_puc(self, comune: str) -> dict[str, Any]:
        """Return PUC/PRG data for any Italian comune.

        For Gaeta, returns curated data from the MVP reference case.
        For other cities, returns a generic urbanistic template with
        standard Italian zoning defaults (DM 1444/1968 categories).
        The scrapers/puc_scraper.py can enrich this with real data when available.
        """
        if comune.lower() == "gaeta":
            return dict(self._GAETA_PUC)  # return copy

        # Generic urbanistic template for any Italian city
        return {
            "comune": comune,
            "plan_type": "PRG/PGT",
            "approval_date": "n/a",
            "variante_in_corso": False,
            "variante_status": None,
            "nta_url": None,
            "zoning_shapefile_url": None,
            "zones": [
                {
                    "code": "B",
                    "description": f"Zona B — completamento residenziale ({comune})",
                    "if_fondiario_mc_sqm": 1.5,
                    "h_max_m": 12.5,
                    "piani_max": 4,
                    "destinazioni_ammesse": [
                        "residenziale",
                        "commerciale",
                        "direzionale",
                        "misto",
                    ],
                    "prescrizioni": [
                        "Verifica vincoli paesaggistici ex art. 142 D.Lgs. 42/2004",
                        "Verifica vincoli idrogeologici PAI",
                    ],
                },
                {
                    "code": "C",
                    "description": f"Zona C — espansione residenziale ({comune})",
                    "if_fondiario_mc_sqm": 1.0,
                    "h_max_m": 10.0,
                    "piani_max": 3,
                    "destinazioni_ammesse": [
                        "residenziale",
                        "misto",
                    ],
                    "prescrizioni": [
                        "Piano di lottizzazione obbligatorio",
                        "Standard urbanistici DM 1444/1968",
                    ],
                },
                {
                    "code": "D",
                    "description": f"Zona D — produttiva/commerciale ({comune})",
                    "if_fondiario_mc_sqm": 2.0,
                    "h_max_m": 15.0,
                    "piani_max": 3,
                    "destinazioni_ammesse": [
                        "commerciale",
                        "direzionale",
                        "industriale",
                    ],
                    "prescrizioni": [
                        "Standard parcheggi L. 122/1989",
                    ],
                },
            ],
            "vincoli": [
                "Verificare vincoli paesaggistici (D.Lgs. 42/2004)",
                "Verificare vincoli idrogeologici (PAI/PGRA)",
                "Verificare vincoli sismici (NTC 2018)",
            ],
            "source_url": None,
            "note": (
                f"Template generico per {comune}. Dati urbanistici reali da verificare "
                "presso l'ufficio tecnico comunale o tramite portale SIT regionale."
            ),
        }

    # --- URBANISTIC COMPUTE --------------------------------------------------

    def calculate_volumetry(
        self,
        puc_data: dict[str, Any],
        parcel: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute buildable volume / SUL given PUC zoning and parcel.

        When exact PRG/PGT zoning is unknown, treats the existing SUL as
        *legittimato* and works in cambio-d'uso mode (no new cubature).

        Returns a dict keyed by scenario code A/B/C with gross/net/floors/etc.
        """
        parcel_area = float(parcel.get("area_sqm") or 0.0)
        existing_sul = float(parcel.get("existing_sul_sqm") or 0.0)

        # Pick the zone F from PUC (only one in our working hypothesis).
        zone = next(
            (z for z in puc_data.get("zones", []) if z.get("code") == "F"),
            puc_data.get("zones", [{}])[0] if puc_data.get("zones") else {},
        )
        if_fondiario = float(zone.get("if_fondiario_mc_sqm") or 1.5)
        h_max = float(zone.get("h_max_m") or 13.0)

        # Max theoretical cubature if parcel area were known (3 m floor height).
        max_volume_mc = parcel_area * if_fondiario if parcel_area else existing_sul * 3.0
        max_sul_sqm = max_volume_mc / 3.0

        # With existing 900 mq ricettivi legittimati we assume same envelope
        # is preserved in all three scenarios — cambio d'uso only, no new cubature.
        base_common = {
            "existing_sul_sqm": existing_sul,
            "max_sul_theoretical_sqm": round(max_sul_sqm, 1),
            "if_fondiario": if_fondiario,
            "h_max_m": h_max,
            "parcel_area_sqm_assumed": parcel_area,
        }

        scenarios = {
            "A": {
                **base_common,
                "label": "100% residenziale",
                "gross_sqm": existing_sul,
                "residential_sqm": existing_sul,
                "touristic_sqm": 0.0,
                "floors": 3,
                "units": 9,
                "unit_avg_sqm": round(existing_sul / 9, 1),
                "constraints": [
                    "Cambio d'uso ex L.R. 7/2017 art. 4 (riscritto L.R. 12/2025)",
                    "Svincolo alberghiero ex L.R. 8/2022 se applicabile",
                    "Autorizzazione paesaggistica ex art. 146 D.Lgs. 42/2004",
                    "Verifica misure di salvaguardia variante PRG in corso",
                ],
            },
            "B": {
                **base_common,
                "label": "Mixed 60% residenziale + 40% ricettivo",
                "gross_sqm": existing_sul,
                "residential_sqm": round(existing_sul * 0.6, 1),
                "touristic_sqm": round(existing_sul * 0.4, 1),
                "floors": 3,
                "units": 6,
                "unit_avg_sqm": round((existing_sul * 0.6) / 6, 1),
                "constraints": [
                    "Cambio d'uso parziale solo sui piani superiori",
                    "Nessuno svincolo alberghiero (attività ricettiva mantenuta)",
                    "Autorizzazione paesaggistica ex art. 146",
                ],
            },
            "C": {
                **base_common,
                "label": "Status quo ricettivo (refurb)",
                "gross_sqm": existing_sul,
                "residential_sqm": 0.0,
                "touristic_sqm": existing_sul,
                "floors": 3,
                "units": 0,
                "constraints": [
                    "Nessun cambio d'uso",
                    "Refurb ordinario CILA/SCIA",
                ],
            },
        }
        scenarios["_meta"] = {
            "note": (
                f"Volumetria calcolata in modalità cambio d'uso: inviluppo esistente "
                f"{existing_sul:.0f} mq legittimato. Indice di zona e altezza da strumento "
                f"urbanistico vigente (working hypothesis). "
                f"Richiede verifica puntuale presso ufficio tecnico comunale."
            ),
            "source": (
                "Strumento urbanistico vigente (PRG/PGT) — ipotesi da verificare."
            ),
        }
        return scenarios

    # --- DCF HELPERS ---------------------------------------------------------

    def _dcf(
        self,
        cashflows_by_year: list[float],
        wacc: float | None = None,
    ) -> tuple[float, float]:
        """Compute NPV and IRR for a cashflow series (year 0 = first element).

        Returns (npv, irr). IRR may be NaN if no sign change or no convergence.
        """
        wacc = wacc if wacc is not None else self.wacc
        cf = np.asarray(cashflows_by_year, dtype=float)

        # NPV: Σ CFt / (1+wacc)^t starting at t=0
        years = np.arange(len(cf))
        npv = float(np.sum(cf / (1.0 + wacc) ** years))

        # IRR via numpy_financial if available; manual bisection otherwise.
        irr: float
        if _HAVE_NPF:
            try:
                val = npf.irr(cf.tolist())
                irr = float(val) if val is not None and not math.isnan(val) else float("nan")
            except Exception:
                irr = float("nan")
        else:
            irr = self._irr_bisect(cf)

        if math.isnan(irr):
            irr = self._irr_bisect(cf)
        return npv, irr

    @staticmethod
    def _irr_bisect(cf: np.ndarray, lo: float = -0.99, hi: float = 5.0) -> float:
        """Robust IRR bisection fallback."""
        def _npv(rate: float) -> float:
            t = np.arange(len(cf))
            return float(np.sum(cf / (1.0 + rate) ** t))

        f_lo, f_hi = _npv(lo), _npv(hi)
        if f_lo * f_hi > 0:
            return float("nan")
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            f_mid = _npv(mid)
            if abs(f_mid) < 1e-4:
                return mid
            if f_lo * f_mid < 0:
                hi, f_hi = mid, f_mid
            else:
                lo, f_lo = mid, f_mid
        return 0.5 * (lo + hi)

    @staticmethod
    def _payback_months(cashflows_by_year: list[float]) -> int:
        """Nominal payback in months (simple, not discounted)."""
        cum = 0.0
        for i, cf in enumerate(cashflows_by_year):
            prev = cum
            cum += cf
            if prev < 0 <= cum:
                # linear interpolation inside the crossing year
                frac = -prev / cf if cf != 0 else 0.0
                return int(round((i - 1 + frac) * 12)) if i > 0 else int(round(frac * 12))
        return -1  # not recovered in horizon

    # --- SCENARIOS -----------------------------------------------------------

    def build_scenarios(self, inp: FeasibilityInput, assumptions: dict[str, Any] | None = None) -> list[ScenarioResult]:
        """Build 3 scenarios (A/B/C) with multi-year DCF for any Italian property."""
        a = assumptions or self.DEFAULT_ASSUMPTIONS
        sqm = inp.sqm
        horizon = inp.horizon_years

        sale_price = a["sale_price_residential_eur_sqm"]
        conv_cost = a["conversion_cost_eur_sqm"]
        refurb_cost = a["refurb_touristic_eur_sqm"]
        soft_pct = a["soft_cost_pct"]
        conting_pct = a["contingency_pct"]
        opex_res = a["annual_opex_eur_sqm_residential"]
        opex_tur = a["annual_opex_eur_sqm_touristic"]
        adr = a["touristic_adr_eur"]
        occ_nights = a["touristic_occupancy_nights"]
        margin = a["touristic_ebitda_margin"]
        cap_rate_turistico_B = a["cap_rate_touristic"]
        cap_rate_status_quo = a["cap_rate_status_quo"]
        cgt = a["capital_gains_tax_pct"]

        scenarios: list[ScenarioResult] = []

        # -----------------------------
        # Scenario A — 100% residenziale
        # -----------------------------
        hard_A = conv_cost * sqm
        soft_A = hard_A * soft_pct
        conting_A = (hard_A + soft_A) * conting_pct
        capex_A = hard_A + soft_A + conting_A
        units_A = 9
        revenue_A_gross = sale_price * sqm  # ~€3.54M
        # Capital gains tax on the gap revenue - (capex + legacy basis).
        # Assume legacy basis negligible for MVP (cliente è già proprietario, conferimento).
        taxable_A = max(0.0, revenue_A_gross - capex_A)
        tax_A = taxable_A * cgt
        revenue_A_net = revenue_A_gross - tax_A

        # Cashflow distribution over 5 years:
        # Year 0: -15% capex (design, SCIA, permits, deposit)
        # Year 1: -55% capex (cantiere main)
        # Year 2: -30% capex (finiture)
        # Year 3: +30% revenue (prime vendite)
        # Year 4: +50% revenue
        # Year 5: +20% revenue
        cf_A = [
            -capex_A * 0.15,
            -capex_A * 0.55,
            -capex_A * 0.30,
            revenue_A_net * 0.30 - opex_res * sqm,
            revenue_A_net * 0.50 - opex_res * sqm,
            revenue_A_net * 0.20 - opex_res * sqm,
        ][: horizon + 1]
        # Pad to horizon+1 if shorter.
        while len(cf_A) < horizon + 1:
            cf_A.append(0.0)

        npv_A, irr_A = self._dcf(cf_A)
        opex_A_tot = opex_res * sqm * 3  # years 3-5 with held stock
        scenarios.append(
            ScenarioResult(
                name="A - 100% Residenziale",
                description=(
                    f"Conversione integrale cambio d'uso: {units_A} appartamenti "
                    f"(taglio medio {int(sqm/units_A)} mq). Vendita frazionata 3-5 anni."
                ),
                capex=capex_A,
                revenue=revenue_A_gross,
                opex_total=opex_A_tot,
                npv=npv_A,
                irr=irr_A,
                payback_months=self._payback_months(cf_A),
                risk_score=6.5,  # +2 regulatory (variante PRG), +1 market, +1 execution, -0.5 liquidity OK
                cashflows_by_year=cf_A,
                assumptions={
                    "sale_price_eur_sqm": sale_price,
                    "conversion_cost_eur_sqm": conv_cost,
                    "capex_breakdown": {
                        "hard_cost": hard_A,
                        "soft_cost": soft_A,
                        "contingency": conting_A,
                    },
                    "capital_gains_tax": tax_A,
                    "units": units_A,
                    "unit_avg_sqm": round(sqm / units_A, 1),
                    "risks_addressed": [
                        "regulatory (variante PRG attiva)",
                        "market (local absorption capacity)",
                        "execution (iter 12-14 mesi)",
                    ],
                },
            )
        )

        # -----------------------------
        # Scenario B — 60% res + 40% ricettivo
        # -----------------------------
        sqm_res_B = sqm * 0.6
        sqm_tur_B = sqm * 0.4
        hard_B = conv_cost * sqm_res_B + refurb_cost * sqm_tur_B
        soft_B = hard_B * soft_pct
        conting_B = (hard_B + soft_B) * conting_pct
        capex_B = hard_B + soft_B + conting_B

        revenue_B_res = sale_price * sqm_res_B  # ~€2.12M
        # Ricettivo: 10 camere mantenute, NOI annuale
        rooms_B = 10
        noi_turistico_annual = rooms_B * occ_nights * adr * margin  # ~75k
        # Terminal value del ricettivo @ anno 5 (cap rate)
        terminal_tur_B = noi_turistico_annual / cap_rate_turistico_B
        tax_B_res = max(0.0, revenue_B_res - (capex_B * (sqm_res_B / sqm))) * cgt
        revenue_B_res_net = revenue_B_res - tax_B_res

        cf_B = [
            -capex_B * 0.15,
            -capex_B * 0.55,
            -capex_B * 0.30 + noi_turistico_annual * 0.5,  # ricettivo parziale
            revenue_B_res_net * 0.40 + noi_turistico_annual - opex_tur * sqm_tur_B,
            revenue_B_res_net * 0.40 + noi_turistico_annual - opex_tur * sqm_tur_B,
            revenue_B_res_net * 0.20 + terminal_tur_B - opex_tur * sqm_tur_B,
        ][: horizon + 1]
        while len(cf_B) < horizon + 1:
            cf_B.append(0.0)

        npv_B, irr_B = self._dcf(cf_B)
        opex_B_tot = (opex_tur * sqm_tur_B) * 3  # anni 3-5
        scenarios.append(
            ScenarioResult(
                name="B - Mixed 60/40",
                description=(
                    f"Mantenimento boutique hotel {rooms_B} camere ({int(sqm_tur_B)} mq) "
                    f"+ {int(sqm_res_B)} mq residenziale piani superiori."
                ),
                capex=capex_B,
                revenue=revenue_B_res + terminal_tur_B + noi_turistico_annual * 3,
                opex_total=opex_B_tot,
                npv=npv_B,
                irr=irr_B,
                payback_months=self._payback_months(cf_B),
                risk_score=5.0,  # less regulatory risk, but execution + op mgmt
                cashflows_by_year=cf_B,
                assumptions={
                    "sqm_res": sqm_res_B,
                    "sqm_tur": sqm_tur_B,
                    "rooms": rooms_B,
                    "adr": adr,
                    "occupancy_nights": occ_nights,
                    "margin": margin,
                    "noi_annual_turistico": noi_turistico_annual,
                    "terminal_value_turistico": terminal_tur_B,
                    "cap_rate_turistico": cap_rate_turistico_B,
                    "capex_breakdown": {
                        "hard_cost": hard_B,
                        "soft_cost": soft_B,
                        "contingency": conting_B,
                    },
                    "risks_addressed": [
                        "no svincolo alberghiero (-regulatory)",
                        "flusso diversificato",
                    ],
                },
            )
        )

        # -----------------------------
        # Scenario C — Status quo ricettivo (refurb leggero)
        # -----------------------------
        refurb_C_hard = 150_000.0  # refurb modesto leggibile
        soft_C = refurb_C_hard * soft_pct
        capex_C = refurb_C_hard + soft_C  # no contingency significativa
        # NOI ricettivo con struttura 20 camere, 120 notti, 90€, 35%
        rooms_C = 22
        adr_C = 90.0
        occ_C = 120.0
        noi_C_annual = rooms_C * occ_C * adr_C * margin  # ~83k
        terminal_C = noi_C_annual / cap_rate_status_quo
        cf_C = [
            -capex_C * 0.5,
            -capex_C * 0.5,
            noi_C_annual - opex_tur * sqm,
            noi_C_annual - opex_tur * sqm,
            noi_C_annual - opex_tur * sqm,
            terminal_C + noi_C_annual - opex_tur * sqm,
        ][: horizon + 1]
        while len(cf_C) < horizon + 1:
            cf_C.append(0.0)

        npv_C, irr_C = self._dcf(cf_C)
        opex_C_tot = opex_tur * sqm * 4
        scenarios.append(
            ScenarioResult(
                name="C - Status Quo Ricettivo",
                description=(
                    f"Refurb leggero (€150k), mantenimento attività ricettiva esistente "
                    f"({rooms_C} camere, stagionalità {occ_C} notti/anno)."
                ),
                capex=capex_C,
                revenue=noi_C_annual * 4 + terminal_C,
                opex_total=opex_C_tot,
                npv=npv_C,
                irr=irr_C,
                payback_months=self._payback_months(cf_C),
                risk_score=4.0,  # lower regulatory, but market stagionale
                cashflows_by_year=cf_C,
                assumptions={
                    "rooms": rooms_C,
                    "adr": adr_C,
                    "occupancy_nights": occ_C,
                    "margin": margin,
                    "noi_annual": noi_C_annual,
                    "terminal_value": terminal_C,
                    "cap_rate": cap_rate_status_quo,
                    "risks_addressed": [
                        "stagionalità domanda",
                        "shock turismo (baseline)",
                    ],
                },
            )
        )

        return scenarios

    # --- MONTE CARLO ---------------------------------------------------------

    def monte_carlo(
        self,
        scenarios: list[ScenarioResult],
        n_runs: int = 10_000,
        target_irr: float = DEFAULT_TARGET_IRR,
        assumptions: dict[str, Any] | None = None,
    ) -> list[MonteCarloResult]:
        """Run vectorized MC on each scenario. Returns one MonteCarloResult per scenario."""
        rng = np.random.default_rng(seed=20260411)
        a = assumptions or self.DEFAULT_ASSUMPTIONS

        # Draw stochastic variables ONCE, reuse across scenarios for correlation consistency.
        price_draws = rng.normal(
            loc=a["sale_price_residential_eur_sqm"],
            scale=a["sale_price_residential_eur_sqm"] * 0.15,
            size=n_runs,
        ).clip(1500, 6000)

        cost_draws = rng.normal(
            loc=a["conversion_cost_eur_sqm"],
            scale=a["conversion_cost_eur_sqm"] * 0.15,
            size=n_runs,
        ).clip(600, 2500)

        # time to permit in months — uniform [8, 24]
        permit_months = rng.uniform(8, 24, size=n_runs)

        # WACC triangular (6%, 8%, 11%)
        wacc_draws = rng.triangular(0.06, 0.08, 0.11, size=n_runs)

        # Regulatory block event (Bernoulli p=0.12) — halves NPV on scenarios with cambio d'uso
        reg_block = rng.random(size=n_runs) < 0.12

        out: list[MonteCarloResult] = []
        for scen in scenarios:
            cf0 = np.asarray(scen.cashflows_by_year, dtype=float)
            n_years = len(cf0)
            years = np.arange(n_years)

            # Build an (n_runs, n_years) perturbed cashflow matrix.
            cf_matrix = np.tile(cf0, (n_runs, 1))

            # Perturb revenue years (positive CFs in years >= 3 are sale revenue) by price shock
            revenue_years_mask = np.zeros(n_years, dtype=bool)
            if n_years >= 4:
                revenue_years_mask[3:] = True

            price_ratio = price_draws / a["sale_price_residential_eur_sqm"]
            cost_ratio = cost_draws / a["conversion_cost_eur_sqm"]

            # Apply price shock to positive cashflows in revenue years (sale proceeds).
            pos_rev_mask = cf0 > 0
            price_years_mask = revenue_years_mask & pos_rev_mask
            if price_years_mask.any():
                cf_matrix[:, price_years_mask] = (
                    cf0[price_years_mask][None, :] * price_ratio[:, None]
                )

            # Apply cost shock to negative CFs (capex years 0-2)
            neg_mask = cf0 < 0
            if neg_mask.any():
                cf_matrix[:, neg_mask] = cf0[neg_mask][None, :] * cost_ratio[:, None]

            # Regulatory block: halves NPV on A (res) and B (mixed), no effect on C
            block_factor = np.ones(n_runs)
            if "Residenziale" in scen.name or "Mixed" in scen.name:
                block_factor[reg_block] = 0.5

            # NPV vectorized per scenario: divide each row by (1+wacc_i)^t then sum
            disc_factors = (1.0 + wacc_draws[:, None]) ** years[None, :]
            npv_vec = (cf_matrix / disc_factors).sum(axis=1) * block_factor

            # Permit delay softens year-3 revenues if >12 months
            delay_factor = np.where(permit_months > 12, 1.0 - (permit_months - 12) / 48, 1.0).clip(0.7, 1.0)
            npv_vec *= delay_factor

            # IRR vectorized — too slow via numpy_financial, use quick approximation
            # Profitability index as proxy, then solve for IRR on the row only on a subset
            # For n=10K runs we do a fast scalar IRR on 1000 samples, then extrapolate percentiles
            sample_idx = rng.choice(n_runs, size=min(1000, n_runs), replace=False)
            irr_samples = np.empty(len(sample_idx))
            for i, idx in enumerate(sample_idx):
                row = cf_matrix[idx] * block_factor[idx] * delay_factor[idx]
                if _HAVE_NPF:
                    try:
                        val = npf.irr(row.tolist())
                        irr_samples[i] = float(val) if val is not None and not math.isnan(val) else -1.0
                    except Exception:
                        irr_samples[i] = -1.0
                else:
                    irr_samples[i] = self._irr_bisect(row)
            # Clean NaNs/infs
            irr_samples = np.where(np.isfinite(irr_samples), irr_samples, -1.0)

            # Percentiles
            npv_p5 = float(np.percentile(npv_vec, 5))
            npv_p50 = float(np.percentile(npv_vec, 50))
            npv_p95 = float(np.percentile(npv_vec, 95))
            irr_p5 = float(np.percentile(irr_samples, 5))
            irr_p50 = float(np.percentile(irr_samples, 50))
            irr_p95 = float(np.percentile(irr_samples, 95))

            prob_neg = float(np.mean(npv_vec < 0))
            prob_above_target = float(np.mean(irr_samples > target_irr))

            # Tornado: variance contribution of each variable via univariate perturbation
            # Compute baseline NPV from original cf0 + wacc
            base_disc = (1.0 + a["wacc"]) ** years
            base_npv = float((cf0 / base_disc).sum())

            # Each "finger": move variable to +1 sigma / +max, keep others at baseline
            def _npv_at(price_mult=1.0, cost_mult=1.0, wacc_=a["wacc"], block=False, delay_m=10):
                cf = cf0.copy()
                if price_years_mask.any():
                    cf[price_years_mask] = cf0[price_years_mask] * price_mult
                if neg_mask.any():
                    cf[neg_mask] = cf0[neg_mask] * cost_mult
                disc = (1.0 + wacc_) ** years
                v = float((cf / disc).sum())
                if block:
                    v *= 0.5
                if delay_m > 12:
                    v *= max(0.7, 1.0 - (delay_m - 12) / 48)
                return v

            tornado = {
                "prezzo_vendita +15%": _npv_at(price_mult=1.15) - base_npv,
                "prezzo_vendita -15%": _npv_at(price_mult=0.85) - base_npv,
                "costo_conversione +15%": _npv_at(cost_mult=1.15) - base_npv,
                "costo_conversione -15%": _npv_at(cost_mult=0.85) - base_npv,
                "WACC 11%": _npv_at(wacc_=0.11) - base_npv,
                "WACC 6%": _npv_at(wacc_=0.06) - base_npv,
                "iter_permessi 24 mesi": _npv_at(delay_m=24) - base_npv,
                "blocco_regolatorio": (
                    (_npv_at(block=True) - base_npv)
                    if ("Residenziale" in scen.name or "Mixed" in scen.name)
                    else 0.0
                ),
            }

            out.append(
                MonteCarloResult(
                    scenario_name=scen.name,
                    n_runs=n_runs,
                    npv_p5=npv_p5,
                    npv_p50=npv_p50,
                    npv_p95=npv_p95,
                    irr_p5=irr_p5,
                    irr_p50=irr_p50,
                    irr_p95=irr_p95,
                    prob_npv_negative=prob_neg,
                    prob_irr_above_target=prob_above_target,
                    target_irr=target_irr,
                    tornado=tornado,
                )
            )
        return out

    # --- AI VERDICT ----------------------------------------------------------

    def generate_ai_verdict(
        self,
        inp: FeasibilityInput,
        scenarios: list[ScenarioResult],
        mc_results: list[MonteCarloResult] | None,
        urbanistic: dict[str, Any],
        market: dict[str, Any],
    ) -> str:
        """Call AI provider chain → narrative verdict. Rule-based fallback if unavailable."""
        from src.ai_provider import call_llm

        prompt = self._build_verdict_prompt(inp, scenarios, mc_results, urbanistic, market)
        system = (
            "You are a senior real estate investment advisor. "
            "Write concise, technically rigorous executive summaries. "
            "No bullet points. No hedging. Direct, data-driven analysis."
        )
        text = call_llm(prompt, system=system, max_tokens=500, temperature=0.3)
        if text:
            return text

        return self._rule_based_verdict(inp, scenarios, mc_results)

    def _build_verdict_prompt(
        self,
        inp: FeasibilityInput,
        scenarios: list[ScenarioResult],
        mc_results: list[MonteCarloResult] | None,
        urbanistic: dict[str, Any],
        market: dict[str, Any],
    ) -> str:
        scen_summary = []
        for s in scenarios:
            scen_summary.append(
                f"- {s.name}: CAPEX €{s.capex:,.0f}, Revenue €{s.revenue:,.0f}, "
                f"NPV €{s.npv:,.0f}, IRR {s.irr*100:.1f}%, Risk {s.risk_score:.1f}/10"
            )
        mc_summary = []
        if mc_results:
            for m in mc_results:
                mc_summary.append(
                    f"- {m.scenario_name}: NPV P50 €{m.npv_p50:,.0f} "
                    f"(P5 €{m.npv_p5:,.0f}, P95 €{m.npv_p95:,.0f}), "
                    f"IRR P50 {m.irr_p50*100:.1f}%, P(NPV<0) {m.prob_npv_negative*100:.0f}%, "
                    f"P(IRR>15%) {m.prob_irr_above_target*100:.0f}%"
                )

        city_name = inp.city or inp.comune or "N/D"
        country = (inp.country or "IT").upper()
        return (
            f"You are a senior real estate advisor for property developers in {country}.\n"
            f"Analyse the case in {city_name} and produce an executive verdict in 2 paragraphs.\n"
            f"Write in the language appropriate for {country} (Italian for IT, English otherwise).\n\n"
            f"INPUT:\n- Address: {inp.address}\n- SQM: {inp.sqm}\n"
            f"- Current use: {inp.current_use}\n- Target use: {inp.target_use}\n"
            f"- Budget: €{inp.budget:,.0f}\n- Horizon: {inp.horizon_years} years\n"
            f"- City: {city_name} | Country: {country}\n\n"
            f"URBAN PLANNING:\n- Plan type: {urbanistic.get('plan_type')}\n"
            f"- Buildable ratio: {urbanistic.get('buildable_ratio')}\n"
            f"- Max height: {urbanistic.get('max_height_m')}m\n"
            f"- Constraints: {', '.join(urbanistic.get('constraints', urbanistic.get('vincoli', [])))}\n\n"
            f"MARKET DATA:\n{json.dumps(market, indent=2, default=str)[:1000]}\n\n"
            f"DCF SCENARIOS ({inp.horizon_years}y, WACC {self.wacc*100:.0f}%):\n" + "\n".join(scen_summary) + "\n\n"
            f"MONTE CARLO (10K runs):\n" + "\n".join(mc_summary) + "\n\n"
            "Produce EXACTLY 2 paragraphs:\n"
            "Paragraph 1: Which scenario is best and why (risk-adjusted NPV, IRR, probability).\n"
            "Paragraph 2: Critical risks to mitigate and 3 concrete next steps for the investor.\n"
            "No bullet points. Executive style, technically rigorous."
        )

    def _rule_based_verdict(
        self,
        inp: FeasibilityInput,
        scenarios: list[ScenarioResult],
        mc_results: list[MonteCarloResult] | None,
    ) -> str:
        """Produce a deterministic verdict when Gemini isn't available."""
        # Rank by risk-adjusted score: NPV / (risk_score + 1)
        ranked = sorted(
            scenarios,
            key=lambda s: (s.npv / (s.risk_score + 1)),
            reverse=True,
        )
        best = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None

        mc_best = None
        if mc_results:
            mc_best = next((m for m in mc_results if m.scenario_name == best.name), None)

        p1 = (
            f"Sulla base dell'analisi DCF a 5 anni con WACC 8% e 10.000 simulazioni Monte Carlo, "
            f"lo scenario raccomandato per l'immobile di {int(inp.sqm)} mq in {inp.address} è "
            f"{best.name}. Questo scenario produce un NPV di €{best.npv:,.0f} e un IRR del "
            f"{best.irr*100:.1f}%, con un profilo di rischio ponderato ({best.risk_score:.1f}/10) "
            f"superiore alle alternative. "
        )
        if runner:
            p1 += (
                f"Lo scenario {runner.name}, pur presentando un NPV di €{runner.npv:,.0f}, "
                f"risulta meno efficiente dopo aggiustamento per il rischio regolatorio e di mercato. "
            )
        if mc_best:
            p1 += (
                f"Il Monte Carlo conferma la robustezza: mediana NPV €{mc_best.npv_p50:,.0f} "
                f"(P5 €{mc_best.npv_p5:,.0f}, P95 €{mc_best.npv_p95:,.0f}), "
                f"probabilità di IRR oltre il target 15% pari al "
                f"{mc_best.prob_irr_above_target*100:.0f}%, "
                f"probabilità NPV negativo {mc_best.prob_npv_negative*100:.0f}%."
            )

        comune_name = inp.comune or "il comune di riferimento"
        p2 = (
            f"I rischi critici da mitigare sono tre. Primo, il rischio regolatorio legato "
            f"allo strumento urbanistico vigente di {comune_name}: prima di qualsiasi impegno "
            f"di spesa occorre verificare l'assenza di misure di salvaguardia attive sul lotto "
            f"e la compatibilita della destinazione d'uso prevista. "
            f"Secondo, la verifica puntuale dei vincoli paesaggistici e ambientali "
            f"(D.Lgs. 42/2004, PAI, vincoli idrogeologici) e la classificazione precisa "
            f"nella zonizzazione PRG/PGT vigente che condiziona la procedura edilizia. "
            f"Terzo, il rischio di mercato sul pricing residenziale nella zona: "
            f"va confermato con comparables di ultimi 12 mesi su vendite chiuse. "
            f"I prossimi passi concreti: (1) commissionare perizia giurata urbanistica + visura SITAP entro 30 giorni, "
            f"(2) aprire tavolo pre-SUAP con il Comune per verifica fattibilita, "
            f"(3) ingaggiare 2 agenzie locali per comparables reali e test di assorbimento."
        )
        return p1 + "\n\n" + p2

    # --- PDF EXPORT ----------------------------------------------------------

    def export_pdf(
        self,
        report: FeasibilityReport,
        filename: str | Path,
    ) -> Path:
        """Render a 15-20 page PDF report using reportlab + matplotlib."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            Image,
            PageBreak,
            KeepTogether,
        )
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY

        filename = Path(filename)
        filename.parent.mkdir(parents=True, exist_ok=True)

        # SCALA brand palette
        NAVY = colors.HexColor("#0B1120")
        GOLD = colors.HexColor("#D4AF37")
        SLATE = colors.HexColor("#94A3B8")
        BG = colors.HexColor("#F8FAFC")

        doc = SimpleDocTemplate(
            str(filename),
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
            title=f"LandIQ — {report.input.address}",
            author="LandIQ (SCALA AI OS)",
        )

        styles = getSampleStyleSheet()
        styles.add(
            ParagraphStyle(
                name="CoverTitle",
                parent=styles["Title"],
                fontSize=22,
                textColor=NAVY,
                alignment=TA_CENTER,
                spaceAfter=12,
                leading=26,
            )
        )
        styles.add(
            ParagraphStyle(
                name="CoverSub",
                parent=styles["Normal"],
                fontSize=13,
                textColor=SLATE,
                alignment=TA_CENTER,
                spaceAfter=8,
            )
        )
        styles.add(
            ParagraphStyle(
                name="H1Navy",
                parent=styles["Heading1"],
                fontSize=18,
                textColor=NAVY,
                spaceAfter=10,
                spaceBefore=12,
            )
        )
        styles.add(
            ParagraphStyle(
                name="H2Gold",
                parent=styles["Heading2"],
                fontSize=14,
                textColor=GOLD,
                spaceAfter=6,
                spaceBefore=10,
            )
        )
        styles.add(
            ParagraphStyle(
                name="BodyJust",
                parent=styles["BodyText"],
                fontSize=10,
                alignment=TA_JUSTIFY,
                textColor=colors.black,
                leading=14,
                spaceAfter=6,
            )
        )
        styles.add(
            ParagraphStyle(
                name="Caption",
                parent=styles["Normal"],
                fontSize=8,
                textColor=SLATE,
                alignment=TA_LEFT,
                spaceAfter=4,
            )
        )

        story: list[Any] = []

        # =========== PAGE 1 — COVER ===========
        story.append(Spacer(1, 4 * cm))
        story.append(Paragraph("LANDIQ", ParagraphStyle(
            name="LogoMark",
            fontSize=28,
            textColor=GOLD,
            alignment=TA_CENTER,
            spaceAfter=4,
        )))
        story.append(Paragraph("AI Feasibility for Real Estate", styles["CoverSub"]))
        story.append(Spacer(1, 2 * cm))
        _country = (report.input.country or "IT").upper()
        comune_label = report.input.comune or report.input.city or "N/D"
        provincia_label = report.input.provincia or ""
        location_label = f"{comune_label} ({provincia_label})" if provincia_label else comune_label
        story.append(Paragraph(
            f"Analisi di Fattibilità<br/>Immobile {int(report.input.sqm)} mq",
            styles["CoverTitle"]
        ))
        story.append(Paragraph(location_label, styles["CoverSub"]))
        story.append(Spacer(1, 2 * cm))

        cover_info = [
            ["Cliente", report.input.client_name or "—"],
            ["Indirizzo", report.input.address],
            ["Superficie", f"{int(report.input.sqm)} mq"],
            ["Uso attuale", report.input.current_use.replace("_", " ")],
            ["Target", report.input.target_use.replace("_", " ")],
            ["Data report", report.generated_at[:10]],
            ["Preparato da", "LandIQ (SCALA AI OS)"],
            ["Modello AI", "Gemini 2.5 Flash + rule-based verdict"],
        ]
        cover_table = Table(cover_info, colWidths=[4 * cm, 10 * cm])
        cover_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), BG),
            ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, SLATE),
        ]))
        story.append(cover_table)
        story.append(Spacer(1, 2 * cm))
        story.append(Paragraph(
            "<i>Disclaimer: analisi a scopo investigativo preliminare, non sostituisce "
            "perizia giurata, consulenza legale o due diligence formale. Dati aggregati "
            "da fonti pubbliche al 11 aprile 2026.</i>",
            styles["Caption"]
        ))
        story.append(PageBreak())

        # =========== PAGE 2 — EXECUTIVE SUMMARY ===========
        story.append(Paragraph("Executive Summary", styles["H1Navy"]))
        story.append(Paragraph(
            f"<b>Verdetto:</b> {report.recommended_scenario} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Cliente:</b> {report.input.client_name or '—'}",
            styles["BodyJust"]
        ))
        story.append(Spacer(1, 0.2 * cm))

        # Summary table
        header = ["Scenario", "CAPEX €", "Ricavi €", "NPV €", "IRR", "Payback", "Risk"]
        rows = [header]
        for s in report.scenarios:
            rows.append([
                s.name,
                f"{s.capex:,.0f}",
                f"{s.revenue:,.0f}",
                f"{s.npv:,.0f}",
                f"{s.irr*100:.1f}%",
                f"{s.payback_months}m" if s.payback_months >= 0 else "n/a",
                f"{s.risk_score:.1f}/10",
            ])
        tbl = Table(rows, colWidths=[4.2 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 1.5 * cm, 1.4 * cm, 1.4 * cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.25, SLATE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.4 * cm))

        # First verdict paragraph
        verdict_paragraphs = report.ai_verdict.split("\n\n")
        if verdict_paragraphs:
            story.append(Paragraph("<b>Verdetto sintetico AI:</b>", styles["H2Gold"]))
            story.append(Paragraph(verdict_paragraphs[0].replace("\n", " "), styles["BodyJust"]))

        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph("<b>Key risks:</b>", styles["H2Gold"]))
        vincoli_list = report.urbanistic_data.get("vincoli", [])
        risks_items = [f"• {v}" for v in vincoli_list[:3]] if vincoli_list else [
            "• Verificare vincoli paesaggistici e ambientali.",
            "• Verificare classificazione urbanistica del lotto.",
            "• Verificare compatibilita destinazione d'uso.",
        ]
        risks_bullets = "<br/>".join(risks_items)
        story.append(Paragraph(risks_bullets, styles["BodyJust"]))
        story.append(PageBreak())

        # =========== PAGES 3-4 — INQUADRAMENTO URBANISTICO ===========
        story.append(Paragraph("1. Inquadramento Urbanistico", styles["H1Navy"]))
        u = report.urbanistic_data
        story.append(Paragraph(
            f"<b>Strumento vigente:</b> {u.get('plan_type')} di {comune_label} "
            f"({u.get('approval_date', 'n/a')}).",
            styles["BodyJust"]
        ))
        if u.get("variante_in_corso"):
            story.append(Paragraph(
                f"<b>{'Variante in corso' if _country == 'IT' else 'Plan amendment in progress'}:</b> {u.get('variante_status', 'Sì')}",
                styles["BodyJust"]
            ))
            story.append(Paragraph(
                "La presenza di una variante urbanistica in corso impone verifica puntuale "
                "delle misure di salvaguardia prima di qualsiasi impegno di spesa."
                if _country == "IT" else
                "An active plan amendment requires verification of any safeguard measures "
                "before committing capital.",
                styles["BodyJust"]
            ))
        else:
            story.append(Paragraph(
                "Non risultano varianti urbanistiche in corso. Si raccomanda comunque "
                "la verifica presso l'ufficio tecnico comunale."
                if _country == "IT" else
                "No active plan amendments on record. Verify current zoning status with the "
                "relevant planning authority before proceeding.",
                styles["BodyJust"]
            ))
        if _country == "IT":
            story.append(Paragraph("Vincoli Paesaggistici e Ambientali", styles["H2Gold"]))
            story.append(Paragraph(
                f"Verificare il Piano Territoriale Paesistico Regionale applicabile a {comune_label}. "
                f"L'immobile potrebbe ricadere in aree soggette a vincolo ex art. 142 D.Lgs. 42/2004 "
                f"(fascia costiera 300m, corsi d'acqua, boschi). "
                f"L'autorizzazione paesaggistica ex art. 146 potrebbe essere necessaria per interventi "
                f"di trasformazione esterna.",
                styles["BodyJust"]
            ))
            story.append(Paragraph("Zonizzazione lavoro (ipotesi)", styles["H2Gold"]))
            for z in u.get("zones", []):
                story.append(Paragraph(
                    f"<b>Zona {z.get('code')}</b>: {z.get('description')}<br/>"
                    f"Indice fondiario {z.get('if_fondiario_mc_sqm')} mc/mq · "
                    f"H max {z.get('h_max_m')} m · Piani {z.get('piani_max')}<br/>"
                    f"Destinazioni ammesse: {', '.join(z.get('destinazioni_ammesse', []))}",
                    styles["BodyJust"]
                ))
        else:
            story.append(Paragraph("Environmental & Planning Constraints", styles["H2Gold"]))
            story.append(Paragraph(
                f"The property may be subject to national and local environmental protection rules "
                f"applicable in {comune_label}. Verify coastal/green setbacks, flood zone status, "
                f"heritage buffer zones and any protected area overlaps before committing capital.",
                styles["BodyJust"]
            ))
            story.append(Paragraph("Zoning (working hypothesis)", styles["H2Gold"]))
            buildable_ratio = u.get("buildable_ratio", "—")
            max_height = u.get("max_height_m", "—")
            allowed = ", ".join(u.get("allowed_uses", []))
            story.append(Paragraph(
                f"<b>FAR / buildable ratio:</b> {buildable_ratio} · "
                f"<b>Max height:</b> {max_height} m<br/>"
                f"<b>Allowed uses:</b> {allowed}<br/>"
                f"<b>Source:</b> {u.get('source', 'LandIQ connector data — verify with municipality')}",
                styles["BodyJust"]
            ))

        story.append(Paragraph("Vincoli operanti" if _country == "IT" else "Active Constraints", styles["H2Gold"]))
        for v in u.get("vincoli", u.get("constraints", [])):
            story.append(Paragraph(f"• {v}", styles["BodyJust"]))
        story.append(PageBreak())

        # =========== PAGES 5-6 — LEGAL FRAMEWORK (country-aware) ===========
        if _country == "IT":
            story.append(Paragraph("2. Quadro Normativo Cambio d'Uso", styles["H1Navy"]))
            story.append(Paragraph("L.R. Lazio 7/2017 art. 4 (riscritto da L.R. 12/2025)", styles["H2Gold"]))
            story.append(Paragraph(
                "La Legge Regionale Lazio 18 luglio 2017 n. 7 \"Disposizioni per la rigenerazione "
                "urbana e il recupero edilizio\" disciplina all'art. 4 il mutamento di destinazione d'uso. "
                "Le destinazioni dichiarate compatibili includono residenziale, turistico-ricettivo, "
                "direzionale, servizi e commerciale di vicinato: il passaggio <b>ricettivo ↔ residenziale "
                "è intra-funzionale</b> nel quadro regionale. I comuni possono deliberare norme specifiche "
                "per cambi d'uso fino a 15.000 mq, ben oltre i 900 mq del caso.",
                styles["BodyJust"]
            ))
            story.append(Paragraph(
                "La <b>L.R. Lazio 30 luglio 2025 n. 12</b> ha integralmente riscritto l'art. 4 per "
                "recepire la sentenza della <b>Corte Costituzionale n. 51 del 18 aprile 2025</b>, che aveva "
                "dichiarato incostituzionale l'art. 4 c. 4 nella versione transitoria. Il framework è "
                "quindi in assestamento e qualsiasi nuovo progetto di cambio d'uso deve seguire il testo "
                "aggiornato 2025 con verifica puntuale dello strumento urbanistico comunale.",
                styles["BodyJust"]
            ))
            story.append(Paragraph("L.R. Lazio 8/2022 — Svincolo Alberghiero", styles["H2Gold"]))
            story.append(Paragraph(
                "La Legge Regionale 24 maggio 2022 n. 8 disciplina specificamente la rimozione del "
                "vincolo di destinazione alberghiera in caso di interventi edilizi. Se l'immobile "
                "presenta tale vincolo (tipico per strutture finanziate con contributi regionali "
                "o istituite in zona F alberghiera), lo svincolo è possibile ma deve seguire la "
                "procedura ex L.R. 8/2022. <b>Verifica preliminare obbligatoria sulla visura catastale "
                "e sulla scheda SUAP prima di qualsiasi progetto di cambio d'uso.</b>",
                styles["BodyJust"]
            ))
            story.append(Paragraph("Salva Casa (DL 69/2024 → L. 105/2024)", styles["H2Gold"]))
            story.append(Paragraph(
                "A livello nazionale il DL 69/2024 \"Salva Casa\" ha ampliato i casi di cambio d'uso "
                "\"libero\" tra categorie compatibili nelle zone A, B, C del DM 1444/68. Integra il "
                "quadro regionale ma non lo sostituisce: il comune mantiene la facoltà regolamentare.",
                styles["BodyJust"]
            ))
            story.append(Paragraph("Checklist Procedurale", styles["H2Gold"]))
            story.append(Paragraph(
                "1. Verifica vincolo alberghiero (visura + atto costitutivo)<br/>"
                "2. Permesso di costruire convenzionato (o SCIA alternativa se applicabile)<br/>"
                "3. Autorizzazione paesaggistica ex art. 146 D.Lgs. 42/2004 (tempi 90-180 giorni)<br/>"
                "4. Conformità Piano Paesaggistico Regionale (NTA conservazione paesaggio)<br/>"
                "5. Verifica misure di salvaguardia variante urbanistica in corso (se applicabile)<br/>"
                "<b>Tempi stimati iter: 10-18 mesi</b> (ottimista 10, realistico 14, pessimista 18).",
                styles["BodyJust"]
            ))
        elif _country == "GE":
            story.append(Paragraph("2. Regulatory Framework — Change of Use (Georgia)", styles["H1Navy"]))
            story.append(Paragraph("Georgian Building Code & Urban Planning Law", styles["H2Gold"]))
            story.append(Paragraph(
                "Georgia's <b>Law on Spatial Arrangement and Urban Planning (2019)</b> and the "
                "<b>Georgian Building Code (PN 01.01-09)</b> govern construction and change of use. "
                "Permits are issued by the relevant municipality (Tbilisi City Hall / Ajara ARPA for Batumi). "
                "Change of use between residential, touristic and commercial is generally permitted "
                "within approved zoning — subject to technical compliance and fire-safety sign-off.",
                styles["BodyJust"]
            ))
            story.append(Paragraph("Tax Framework", styles["H2Gold"]))
            story.append(Paragraph(
                "<b>Capital gains tax:</b> 5% for individuals on RE profits; <b>0% if held > 2 years</b> "
                "(individuals). Legal entities pay 15% CIT on profit. "
                "<b>Property transfer tax:</b> abolished since 2013 (0%). "
                "<b>Annual property tax:</b> 0.1–1% of cadastral value depending on municipality and use. "
                "<b>VAT:</b> 18% applies to commercial RE transactions; residential often exempt.",
                styles["BodyJust"]
            ))
            story.append(Paragraph("Procedural Checklist", styles["H2Gold"]))
            story.append(Paragraph(
                "1. Verify NAPR cadastral registration and ownership title<br/>"
                "2. Obtain architectural project approval from municipality (Ajara ARPA if Batumi)<br/>"
                "3. Construction permit (up to 60 days for standard projects)<br/>"
                "4. Fire safety and sanitary inspection sign-off<br/>"
                "5. Commissioning certificate (akti miğeba-Chêbara) before occupancy<br/>"
                "<b>Estimated permitting timeline: 3–8 months</b> (optimistic 3, realistic 5, pessimistic 8).<br/>"
                "Source: NAPR.gov.ge · tbilisi.gov.ge · ajara.gov.ge",
                styles["BodyJust"]
            ))
        else:
            story.append(Paragraph(f"2. Regulatory Framework — Change of Use ({_country})", styles["H1Navy"]))
            story.append(Paragraph("Local Planning Law", styles["H2Gold"]))
            story.append(Paragraph(
                f"Change-of-use permissions in {comune_label} are governed by local municipal planning "
                f"law and national building regulations for {_country}. Zoning compliance, building "
                f"permits and any heritage/environmental approvals must be verified with the competent "
                f"authority before any capital commitment.",
                styles["BodyJust"]
            ))
            story.append(Paragraph("Procedural Checklist", styles["H2Gold"]))
            story.append(Paragraph(
                "1. Confirm current zoning classification with municipality<br/>"
                "2. Apply for change-of-use / construction permit<br/>"
                "3. Environmental / heritage impact assessment (if required)<br/>"
                "4. Final commissioning inspection<br/>"
                "<b>Note:</b> timelines vary by country and municipality — verify locally.",
                styles["BodyJust"]
            ))
        story.append(PageBreak())

        # =========== PAGES 7-8 — MARKET DATA (country-aware) ===========
        m = report.market_data
        ap = m.get("asking_prices", {})
        if _country == "IT":
            story.append(Paragraph(f"3. Mercato Immobiliare {comune_label}", styles["H1Navy"]))
            story.append(Paragraph(f"Valori OMI Agenzia Entrate ({comune_label})", styles["H2Gold"]))
            omi_rows = [["Zona", "Descrizione", "Res. min €/mq", "Res. max €/mq", "Loc. €/mq/mese"]]
            for z in m.get("zones", []):
                omi_rows.append([
                    z.get("code"),
                    z.get("description", "")[:45],
                    f"{(z.get('residential_min') or z.get('min_eur_sqm') or 0):,.0f}",
                    f"{(z.get('residential_max') or z.get('max_eur_sqm') or 0):,.0f}",
                    f"{(z.get('rental_min') or z.get('loc_eur_sqm_month') or 0):.1f}-{(z.get('rental_max') or z.get('loc_eur_sqm_month') or 0):.1f}",
                ])
            omi_tbl = Table(omi_rows, colWidths=[1.5 * cm, 7 * cm, 2.5 * cm, 2.5 * cm, 3 * cm])
            omi_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.25, SLATE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG]),
            ]))
            story.append(omi_tbl)
            story.append(Spacer(1, 0.4 * cm))
            story.append(Paragraph(
                f"Quotazioni OMI ufficiali per {comune_label} (semestre "
                f"{m.get('semester', 'ultimo disponibile')}). OMI è il dato ufficiale Agenzia Entrate, "
                f"tipicamente sottovalutato rispetto ai listini di mercato.",
                styles["BodyJust"]
            ))
            story.append(Paragraph(f"Comparables di mercato ({comune_label})", styles["H2Gold"]))
            listino_rows = [["Metrica", "€/mq"]]
            if ap.get("city_avg_eur_sqm"):
                listino_rows.append([f"Media richiesta {comune_label}", f"{ap.get('city_avg_eur_sqm', 0):,.0f}"])
            if ap.get("city_peak_eur_sqm"):
                listino_rows.append(["Picco ultimi 2 anni", f"{ap.get('city_peak_eur_sqm', 0):,.0f}"])
            for z in m.get("zones", []):
                zone_code = z.get("code", "")
                for qt in z.get("quotations", []):
                    val_min = qt.get("min_eur_sqm", 0) or 0
                    val_max = qt.get("max_eur_sqm", 0) or 0
                    if val_min or val_max:
                        listino_rows.append([
                            f"Zona {zone_code} — {qt.get('tipologia', 'Residenziale')}",
                            f"{val_min:,.0f} - {val_max:,.0f}",
                        ])
            if len(listino_rows) < 2:
                listino_rows.append(["Dati OMI non disponibili", "—"])
        else:
            # Non-IT: show connector benchmark data with proper sourcing
            story.append(Paragraph(f"3. Real Estate Market — {comune_label}", styles["H1Navy"]))
            data_source = m.get("source", "LandIQ connector benchmarks")
            story.append(Paragraph(f"Market Benchmarks ({comune_label})", styles["H2Gold"]))
            price_mid = m.get("price_per_sqm", 0)
            price_min = m.get("price_min", 0)
            price_max = m.get("price_max", 0)
            currency = m.get("currency", "EUR")
            listino_rows = [["Metric", f"{currency}/sqm"]]
            if price_mid:
                listino_rows.append([f"Residential mid — {comune_label}", f"{price_mid:,.0f}"])
            if price_min:
                listino_rows.append(["Range low", f"{price_min:,.0f}"])
            if price_max:
                listino_rows.append(["Range high", f"{price_max:,.0f}"])
            # Add any zone-level data
            for z in m.get("zones", []):
                mid = z.get("price_eur_sqm_mid") or z.get("price_gel_sqm_mid")
                if mid:
                    gel_rate = z.get("gel_eur_rate")
                    label = z.get("city", comune_label)
                    val_str = f"{mid:,.0f} GEL ({round(mid*(gel_rate or 1)):,.0f} EUR)" if gel_rate else f"{mid:,.0f}"
                    listino_rows.append([f"Benchmark — {label}", val_str])
            if len(listino_rows) < 2:
                listino_rows.append(["No benchmark data available", "—"])
            story.append(Paragraph(
                f"<b>Data source:</b> {data_source}",
                styles["BodyJust"]
            ))
            story.append(Spacer(1, 0.2 * cm))

        listino_tbl = Table(listino_rows, colWidths=[10.5 * cm, 6 * cm])
        listino_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.25, SLATE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG]),
        ]))
        story.append(listino_tbl)
        story.append(Spacer(1, 0.3 * cm))
        if _country == "IT":
            story.append(Paragraph(
                f"I valori riportati riflettono le quotazioni disponibili per {comune_label}. "
                f"Si raccomanda la verifica con comparables reali (vendite chiuse ultimi 12 mesi) "
                f"tramite agenzie locali.",
                styles["BodyJust"]
            ))
        else:
            story.append(Paragraph(
                f"Benchmarks sourced from {data_source}. "
                f"Verify with closed-transaction comparables (last 12 months) via local agents before committing capital.",
                styles["BodyJust"]
            ))
        story.append(PageBreak())

        # =========== VOLUMETRIA (state of fact) ===========
        story.append(Paragraph("4. Stato di Fatto e Volumetria", styles["H1Navy"]))
        vol = report.volumetry or {}
        meta = vol.get("_meta", {})
        story.append(Paragraph(
            f"L'immobile oggetto di analisi si trova a {location_label}, "
            f"con una superficie di circa <b>{int(report.input.sqm)} mq SUL</b>. "
            f"Uso attuale: {report.input.current_use.replace('_', ' ')}. "
            f"Destinazione target: {report.input.target_use.replace('_', ' ')}.",
            styles["BodyJust"]
        ))
        story.append(Paragraph(
            meta.get("note", ""),
            styles["BodyJust"]
        ))
        story.append(Spacer(1, 0.2 * cm))

        vol_rows = [["Scenario", "Residenziale mq", "Ricettivo mq", "Unità", "Piani", "Taglio medio"]]
        for code in ("A", "B", "C"):
            s_vol = vol.get(code, {})
            if not s_vol:
                continue
            vol_rows.append([
                f"{code} — {s_vol.get('label', '')}",
                f"{s_vol.get('residential_sqm', 0):,.0f}",
                f"{s_vol.get('touristic_sqm', 0):,.0f}",
                str(s_vol.get("units", 0)),
                str(s_vol.get("floors", "-")),
                f"{s_vol.get('unit_avg_sqm', 0):,.0f} mq" if s_vol.get("unit_avg_sqm") else "-",
            ])
        vol_tbl = Table(vol_rows, colWidths=[5.5 * cm, 2.5 * cm, 2.5 * cm, 1.5 * cm, 1.2 * cm, 2.3 * cm])
        vol_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.25, SLATE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG]),
        ]))
        story.append(vol_tbl)
        story.append(Spacer(1, 0.3 * cm))
        _vol_zone_label = "Vincoli zonali (ipotesi F PRG 1973)" if _country == "IT" else "Urban Zoning Constraints (working hypothesis)"
        story.append(Paragraph(_vol_zone_label, styles["H2Gold"]))
        if vol.get("A", {}).get("if_fondiario"):
            story.append(Paragraph(
                f"• Indice fondiario: <b>{vol['A']['if_fondiario']} mc/mq</b><br/>"
                f"• Altezza massima: <b>{vol['A']['h_max_m']} m</b><br/>"
                f"• SUL teorica massima: <b>{vol['A']['max_sul_theoretical_sqm']:,.0f} mq</b> "
                f"(calcolata su area lotto stimata {vol['A']['parcel_area_sqm_assumed']:,.0f} mq)<br/>"
                f"• SUL esistente legittimata: <b>{vol['A']['existing_sul_sqm']:,.0f} mq</b>",
                styles["BodyJust"]
            ))
        if _country == "IT":
            story.append(Paragraph(
                "<b>Nota metodologica:</b> l'analisi è condotta in modalità cambio d'uso — "
                "l'inviluppo edilizio esistente è mantenuto in tutti i tre scenari; non è prevista "
                "nuova cubatura. Questa scelta riflette la working hypothesis che il lotto sia "
                "saturo rispetto all'indice di zona o che la fascia di rispetto paesaggistica "
                "precluda ampliamenti esterni. La verifica puntuale richiede elab_b01 e NTA PRG.",
                styles["BodyJust"]
            ))
        else:
            story.append(Paragraph(
                "<b>Methodology note:</b> analysis conducted in change-of-use mode — "
                "the existing building envelope is maintained across all three scenarios; no new gross "
                "floor area is assumed. This reflects the working hypothesis that the plot is at or near "
                "its permitted FAR, or that setback/height restrictions prevent external extension. "
                "Detailed verification requires review of the applicable municipal zoning plan.",
                styles["BodyJust"]
            ))
        story.append(PageBreak())

        # =========== PAGES 9-14 — SCENARIOS ===========
        def _add_scenario(scen: ScenarioResult, scen_idx: int) -> None:
            story.append(Paragraph(f"4.{scen_idx+1}. Scenario {scen.name}", styles["H1Navy"]))
            story.append(Paragraph(scen.description, styles["BodyJust"]))
            story.append(Spacer(1, 0.2 * cm))

            # KPI table
            kpi_rows = [
                ["CAPEX", f"€{scen.capex:,.0f}"],
                ["Ricavi totali orizzonte", f"€{scen.revenue:,.0f}"],
                ["OPEX totali", f"€{scen.opex_total:,.0f}"],
                ["NPV (WACC 8%)", f"€{scen.npv:,.0f}"],
                ["IRR", f"{scen.irr*100:.2f}%"],
                ["Payback", f"{scen.payback_months} mesi" if scen.payback_months >= 0 else "n/a"],
                ["Risk score", f"{scen.risk_score:.1f}/10"],
            ]
            kpi_tbl = Table(kpi_rows, colWidths=[7 * cm, 6 * cm])
            kpi_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), BG),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.25, SLATE),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(kpi_tbl)
            story.append(Spacer(1, 0.3 * cm))

            # Cashflow chart
            fig, ax = plt.subplots(figsize=(6, 2.8))
            years = list(range(len(scen.cashflows_by_year)))
            colors_bar = ["#D4AF37" if v >= 0 else "#DC2626" for v in scen.cashflows_by_year]
            ax.bar(years, scen.cashflows_by_year, color=colors_bar, edgecolor="#0B1120")
            ax.axhline(0, color="#0B1120", linewidth=0.8)
            ax.set_xlabel("Anno")
            ax.set_ylabel("Cashflow €")
            ax.set_title(f"Cashflow 5 anni — {scen.name}", color="#0B1120")
            ax.grid(True, axis="y", alpha=0.3)
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"€{x/1000:.0f}k"))
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            story.append(Image(buf, width=15 * cm, height=7 * cm))
            story.append(Spacer(1, 0.3 * cm))

            # Assumptions
            story.append(Paragraph("Assunzioni chiave", styles["H2Gold"]))
            for k, v in scen.assumptions.items():
                if isinstance(v, dict):
                    sub = ", ".join(f"{sk}: €{sv:,.0f}" if isinstance(sv, (int, float)) else f"{sk}: {sv}" for sk, sv in v.items())
                    story.append(Paragraph(f"• <b>{k}</b>: {sub}", styles["BodyJust"]))
                elif isinstance(v, list):
                    story.append(Paragraph(f"• <b>{k}</b>: {', '.join(str(x) for x in v)}", styles["BodyJust"]))
                elif isinstance(v, float):
                    story.append(Paragraph(f"• <b>{k}</b>: {v:,.2f}", styles["BodyJust"]))
                else:
                    story.append(Paragraph(f"• <b>{k}</b>: {v}", styles["BodyJust"]))
            story.append(PageBreak())

        for i, scen in enumerate(report.scenarios):
            _add_scenario(scen, i)

        # =========== MONTE CARLO ===========
        story.append(Paragraph("5. Monte Carlo Sensitivity", styles["H1Navy"]))
        story.append(Paragraph(
            "Per ciascuno scenario sono state eseguite 10.000 simulazioni Monte Carlo con le "
            "seguenti variabili stocastiche:",
            styles["BodyJust"]
        ))
        story.append(Paragraph(
            "• Prezzo di vendita residenziale: N(€3.930, σ=€590) troncata a [€1.500, €6.000]<br/>"
            "• Costo conversione €/mq: N(€1.300, σ=€195) troncata a [€600, €2.500]<br/>"
            "• Durata iter autorizzativo: Uniform(8, 24) mesi<br/>"
            "• WACC: triangolare (6%, 8%, 11%)<br/>"
            "• Evento blocco regolatorio: Bernoulli p=12% (dimezza NPV per scenari con cambio d'uso)",
            styles["BodyJust"]
        ))
        story.append(Spacer(1, 0.2 * cm))

        mc_rows = [["Scenario", "NPV P5", "NPV P50", "NPV P95", "IRR P50", "P(NPV<0)", "P(IRR>15%)"]]
        for m_res in report.monte_carlo:
            mc_rows.append([
                m_res.scenario_name,
                f"€{m_res.npv_p5:,.0f}",
                f"€{m_res.npv_p50:,.0f}",
                f"€{m_res.npv_p95:,.0f}",
                f"{m_res.irr_p50*100:.1f}%",
                f"{m_res.prob_npv_negative*100:.0f}%",
                f"{m_res.prob_irr_above_target*100:.0f}%",
            ])
        mc_tbl = Table(mc_rows, colWidths=[4.2 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 1.4 * cm, 1.6 * cm, 1.6 * cm])
        mc_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.25, SLATE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG]),
        ]))
        story.append(mc_tbl)
        story.append(Spacer(1, 0.4 * cm))

        # Tornado chart for recommended scenario
        rec_name = report.recommended_scenario
        mc_rec = next((x for x in report.monte_carlo if x.scenario_name == rec_name), report.monte_carlo[0])
        tornado = sorted(mc_rec.tornado.items(), key=lambda kv: abs(kv[1]), reverse=True)
        labels = [k for k, _ in tornado]
        values = [v for _, v in tornado]
        fig, ax = plt.subplots(figsize=(6, 3.5))
        colors_tor = ["#D4AF37" if v >= 0 else "#DC2626" for v in values]
        ax.barh(labels, values, color=colors_tor, edgecolor="#0B1120")
        ax.axvline(0, color="#0B1120", linewidth=0.8)
        ax.set_xlabel("Impatto su NPV (€)")
        ax.set_title(f"Tornado — {rec_name}", color="#0B1120")
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"€{x/1000:.0f}k"))
        ax.grid(True, axis="x", alpha=0.3)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        story.append(Image(buf, width=15 * cm, height=9 * cm))
        story.append(PageBreak())

        # NPV distribution comparison
        story.append(Paragraph("Distribuzione NPV per scenario", styles["H2Gold"]))
        fig, ax = plt.subplots(figsize=(6, 3.5))
        for m_res in report.monte_carlo:
            # Approximate normal distribution from P5/P50/P95
            mean = m_res.npv_p50
            std = (m_res.npv_p95 - m_res.npv_p5) / 3.29
            if std > 0:
                xs = np.linspace(m_res.npv_p5 - std, m_res.npv_p95 + std, 200)
                pdf = (1 / (std * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((xs - mean) / std) ** 2)
                ax.plot(xs, pdf, label=m_res.scenario_name, linewidth=2)
        ax.axvline(0, color="#DC2626", linestyle="--", alpha=0.6, label="NPV=0")
        ax.set_xlabel("NPV €")
        ax.set_ylabel("Densità")
        ax.set_title("Distribuzioni NPV Monte Carlo (approx normale)", color="#0B1120")
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"€{x/1000:.0f}k"))
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        story.append(Image(buf, width=15 * cm, height=9 * cm))
        story.append(PageBreak())

        # =========== AI VERDICT ===========
        story.append(Paragraph("6. Verdetto AI + Raccomandazione", styles["H1Navy"]))
        story.append(Paragraph(
            f"<b>Scenario raccomandato:</b> {report.recommended_scenario}",
            styles["BodyJust"]
        ))
        story.append(Spacer(1, 0.2 * cm))
        for para in report.ai_verdict.split("\n\n"):
            if para.strip():
                story.append(Paragraph(para.replace("\n", " "), styles["BodyJust"]))
                story.append(Spacer(1, 0.2 * cm))

        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(
            "<i>Nota: il verdetto è prodotto da un modello deterministico basato su regole "
            "(rule-based) quando la chiave Gemini non è disponibile nell'ambiente. "
            "Per il deployment produzione SCALA AI OS è previsto Gemini 2.5 Flash con temperatura 0.3.</i>",
            styles["Caption"]
        ))
        story.append(PageBreak())

        # =========== APPENDIX A — SOURCES ===========
        story.append(Paragraph("Appendice A — Fonti e Citazioni", styles["H1Navy"]))
        story.append(Paragraph(
            "Tutte le fonti sotto elencate sono state consultate nell'ambito della preparazione "
            "di questo report (data accesso: 11 aprile 2026). Le URL sono cliccabili nella "
            "versione elettronica del documento.",
            styles["BodyJust"]
        ))
        story.append(Spacer(1, 0.2 * cm))
        for src in report.sources:
            story.append(Paragraph(f"• {src}", styles["BodyJust"]))
        story.append(PageBreak())

        # =========== APPENDIX B — GLOSSARY ===========
        story.append(Paragraph("Appendice B — Glossario", styles["H1Navy"]))
        glossary = [
            ("PRG", "Piano Regolatore Generale — strumento urbanistico comunale che disciplina uso del suolo, indici e destinazioni."),
            ("PUC / PUG", "Piano Urbanistico Comunale / Generale — evoluzione moderna del PRG in molte regioni."),
            ("NTA", "Norme Tecniche di Attuazione — articolato normativo del PRG che fissa indici e vincoli per ciascuna zona."),
            ("IF", "Indice Fondiario — mc edificabili per mq di lotto (es. 1,5 mc/mq significa 1,5 metri cubi di costruzione per ogni metro quadro di terreno)."),
            ("SUL", "Superficie Utile Lorda — superficie calpestabile + murature perimetrali e divisorie, usata per calcolo volumetrie e oneri."),
            ("OMI", "Osservatorio del Mercato Immobiliare (Agenzia Entrate) — banca dati semestrale dei valori minimi/massimi immobiliari per zona."),
            ("PTPR", "Piano Territoriale Paesistico Regionale — strumento paesaggistico regionale che impone vincoli sovraordinati al PRG."),
            ("Cap rate", "Capitalization rate — NOI annuo / valore di mercato. Usato per stimare valori di uscita di asset a reddito."),
            ("WACC", "Weighted Average Cost of Capital — tasso di sconto usato nel DCF per portare a valore attuale i cashflow futuri."),
            ("NPV", "Net Present Value — valore attuale netto dei cashflow. NPV > 0 indica creazione di valore al tasso WACC."),
            ("IRR", "Internal Rate of Return — tasso interno di rendimento che rende NPV = 0. Confrontato col target di investimento."),
            ("SCIA / CILA", "Segnalazione Certificata di Inizio Attività / Comunicazione Inizio Lavori Asseverata — titoli edilizi semplificati."),
            ("Permesso di costruire convenzionato", "Titolo edilizio con accordo comune-privato su opere pubbliche accessorie."),
            ("Vincolo ope legis art. 142", "Vincolo paesaggistico automatico ex lege (fasce 300m costa, 150m fiumi, parchi, boschi)."),
            ("Autorizzazione paesaggistica art. 146", "Procedura abilitativa sovraordinata per interventi in aree tutelate; tempi 90-180 giorni."),
            ("Ambito paesaggistico", "Partizione territoriale del Piano Paesaggistico Regionale con disciplina di tutela specifica."),
            ("Monte Carlo", "Tecnica di simulazione stocastica che ripete il calcolo N volte variando gli input secondo distribuzioni probabilistiche."),
            ("Tornado chart", "Visualizzazione sensitivity che ordina le variabili per impatto assoluto sul risultato target (qui NPV)."),
        ]
        gloss_rows = [["Termine", "Definizione"]]
        for term, defi in glossary:
            gloss_rows.append([term, defi])
        gloss_tbl = Table(gloss_rows, colWidths=[3.5 * cm, 13 * cm])
        gloss_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 1), (0, -1), NAVY),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, SLATE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(gloss_tbl)
        story.append(PageBreak())

        # =========== APPENDIX C — DISCLAIMERS + NEXT STEPS ===========
        story.append(Paragraph("Appendice C — Disclaimer & Next Step", styles["H1Navy"]))
        story.append(Paragraph("Limitazioni del Report", styles["H2Gold"]))
        story.append(Paragraph(
            "Il presente documento è un'analisi di fattibilità preliminare basata su dati "
            "pubblici aggregati al 11 aprile 2026. Non costituisce perizia giurata, consulenza "
            "legale o fiscale, due diligence notarile, né si sostituisce a pareri tecnici "
            "professionali. Le seguenti voci rappresentano ipotesi di lavoro che richiedono "
            "verifica documentale puntuale presso fonti primarie:",
            styles["BodyJust"]
        ))
        story.append(Paragraph(
            "• Classificazione urbanistica del lotto nel PRG 1973 (zona esatta, indice, altezza)<br/>"
            "• Presenza/assenza di vincolo di destinazione alberghiera sull'immobile<br/>"
            "• Perimetro preciso di eventuali vincoli paesaggistici dichiarativi<br/>"
            "• Misure di salvaguardia della variante generale PRG (Delibere C.C. adozione)<br/>"
            "• Comparables di mercato verificati negli ultimi 12 mesi (vendite chiuse, non listini)<br/>"
            "• Voci specifiche del Prezzario Lazio 2025-2026 per la conversione ricettivo → residenziale",
            styles["BodyJust"]
        ))

        story.append(Paragraph("Checklist Documenti Cliente", styles["H2Gold"]))
        story.append(Paragraph(
            "Per passare dall'analisi preliminare a una due diligence formale sono necessari: "
            "(1) visura catastale aggiornata e planimetrie, (2) atto di provenienza e titoli "
            "edilizi storici, (3) APE energetico, (4) eventuale perizia strutturale recente, "
            "(5) visura ipotecaria per vincoli/gravami, (6) estratto di mappa e CTR, "
            "(7) scheda SUAP se attività ricettiva attiva, (8) bilanci ultimi 3 anni se struttura operativa.",
            styles["BodyJust"]
        ))

        story.append(Paragraph("Prossimo step — Due Diligence Approfondita", styles["H2Gold"]))
        story.append(Paragraph(
            "LandIQ può gestire la fase successiva in partnership con tecnico abilitato del "
            f"territorio: perizia giurata urbanistica, verifica SITAP soprintendenza, "
            f"scavo RGM Agenzia Entrate su comparables, pre-SUAP con Comune di {comune_label}, "
            f"calibrazione DCF con dati reali del Prezzario Regionale. "
            "<b>Budget orientativo: €2.500-5.000 · tempi 4-6 settimane.</b>",
            styles["BodyJust"]
        ))

        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(
            f"<i>Report generato il {report.generated_at} da LandIQ v{__version__} — "
            f"SCALA AI OS · get-scala.com · contatto: ale@get-scala.com</i>",
            styles["Caption"]
        ))

        # Build PDF
        doc.build(story)
        return filename

    # --- ORCHESTRATION -------------------------------------------------------

    def run(self, inp: FeasibilityInput) -> FeasibilityReport:
        """Autonomous agent loop: plan → research → observe → analyze → verdict.

        The agent:
        1. PLAN: identifies country, selects connector, determines data needs
        2. RESEARCH: fetches market + urbanistic data via connector tools
        3. OBSERVE: evaluates data quality — retries with fallback if insufficient
        4. ANALYZE: builds scenarios, runs DCF + Monte Carlo
        5. VERDICT: generates AI-powered executive summary + GO/NO-GO
        """
        country = (inp.country or "IT").upper()
        city = inp.city or inp.comune or "Milano"

        # ── STEP 1: PLAN ─────────────────────────────────────────
        conn = self._get_connector(country)
        plan = {
            "country": country,
            "city": city,
            "connector": conn.__class__.__name__,
            "has_dedicated_connector": conn.__class__.__name__ != "GenericConnector",
        }
        print(f"[agent] PLAN: {city}, {country} → {plan['connector']}", file=sys.stderr)

        # ── STEP 2: RESEARCH ─────────────────────────────────────
        urbanistic = self.fetch_urbanistic_data(city, country=country)
        market = self.fetch_market_data(city, country=country)

        # ── STEP 3: OBSERVE — evaluate data quality ──────────────
        data_quality = self._observe_data_quality(market, urbanistic, plan)
        print(f"[agent] OBSERVE: quality={data_quality['score']}/10, issues={data_quality['issues']}", file=sys.stderr)

        # If data is poor and we used a dedicated connector, retry with generic
        if data_quality["score"] < 4 and plan["has_dedicated_connector"]:
            print(f"[agent] RETRY: dedicated connector data poor — trying generic fallback", file=sys.stderr)
            from connectors.generic import GenericConnector
            fallback = GenericConnector(country)
            market_fb = self._connector_to_dict(fallback.fetch_market_data(city))
            # Merge: keep urbanistic (country-specific is always better), update market if fallback is richer
            if market_fb.get("price_per_sqm", 0) > 0:
                market["price_per_sqm"] = market.get("price_per_sqm") or market_fb["price_per_sqm"]
                if not market.get("zones"):
                    market["zones"] = market_fb.get("zones", [])
                market["source"] = f"{market.get('source', '')} + GenericConnector fallback"

        # If market price is still zero/missing, use a safe default
        if not market.get("price_per_sqm") or market["price_per_sqm"] <= 0:
            market["price_per_sqm"] = 1800.0
            market["source"] = market.get("source", "") + " [default: no data available]"
            print(f"[agent] OBSERVE: no market price found — using €1800 default", file=sys.stderr)

        # ── STEP 4: ANALYZE ──────────────────────────────────────
        assumptions = self.build_assumptions(market, country=country)
        # Higher contingency if planning situation is uncertain
        if urbanistic.get("variante_in_corso") or not urbanistic.get("source"):
            assumptions["contingency_pct"] = max(assumptions.get("contingency_pct", 0.10), 0.12)
        # Higher contingency if data quality is low
        if data_quality["score"] < 6:
            assumptions["contingency_pct"] = max(assumptions.get("contingency_pct", 0.10), 0.15)
            print(f"[agent] ANALYZE: low data quality → contingency raised to {assumptions['contingency_pct']:.0%}", file=sys.stderr)

        zones = urbanistic.get("zones", [])
        zone_code = zones[0].get("code", "B") if zones else "B"

        volumetry = self.calculate_volumetry(
            urbanistic,
            {
                "area_sqm": inp.parcel_area_sqm or inp.sqm * 1.4,
                "zone_code": zone_code,
                "existing_sul_sqm": inp.sqm,
            },
        )
        scenarios = self.build_scenarios(inp, assumptions=assumptions)
        mc = self.monte_carlo(scenarios, n_runs=10_000, target_irr=self.DEFAULT_TARGET_IRR, assumptions=assumptions)

        # ── STEP 5: VERDICT ──────────────────────────────────────
        verdict = self.generate_ai_verdict(inp, scenarios, mc, urbanistic, market)
        print(f"[agent] VERDICT: {len(verdict)} chars, {len(scenarios)} scenarios analyzed", file=sys.stderr)

        # Recommended scenario: best risk-adjusted NPV
        ranked = sorted(scenarios, key=lambda s: s.npv / (s.risk_score + 1), reverse=True)
        recommended = ranked[0].name

        # Sources: connector-specific + generic
        conn = self._get_connector(country)
        sources = [
            f"Market data: {market.get('source', 'LandIQ connector')}",
            f"Urban planning: {urbanistic.get('source', 'LandIQ connector')}",
            f"Country: {country} | Connector: {conn.__class__.__name__}",
        ]
        if country == "IT":
            sources += [
                "OMI Agenzia Entrate: https://www.agenziaentrate.gov.it/portale/schede/fabbricatiterreni/omi/banche-dati/quotazioni-immobiliari",
                "Salva Casa DL 69/2024 — L. 105/2024",
                "DM 1444/1968 — Standard urbanistici",
                "D.Lgs. 42/2004 — Codice beni culturali e paesaggio",
            ]
        elif country == "GE":
            sources += [
                "NBG.gov.ge — GEL/EUR exchange rate",
                "myhome.ge — Georgia real estate market data",
                "NAPR.gov.ge — National Agency of Public Registry",
            ]

        report = FeasibilityReport(
            input=inp,
            urbanistic_data=urbanistic,
            market_data=market,
            volumetry=volumetry,
            scenarios=scenarios,
            monte_carlo=mc,
            ai_verdict=verdict,
            recommended_scenario=recommended,
            generated_at=dt.datetime.now().isoformat(timespec="seconds"),
            sources=sources,
        )
        return report


# -----------------------------------------------------------------------------
# SANITY CHECK
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    demo = FeasibilityInput(
        address="Via Marina di Serapo 12, Gaeta LT",
        sqm=900,
        current_use="ricettivo_alberghiero",
        target_use="residenziale",
        budget=1_500_000,
        horizon_years=5,
        comune="Gaeta",
        provincia="LT",
        client_name="Demo Cliente",
    )
    engine = LandIQEngine()
    print(f"LandIQ v{__version__} — input OK: {demo.address}, {demo.sqm} mq")
    report = engine.run(demo)
    print(f"Generated report with {len(report.scenarios)} scenarios, recommended={report.recommended_scenario}")
    for s in report.scenarios:
        print(f"  {s.name}: NPV €{s.npv:,.0f}, IRR {s.irr*100:.1f}%")
