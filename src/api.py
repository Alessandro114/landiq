"""
LandIQ Agent API — FastAPI server exposing the autonomous feasibility agent.

Endpoints:
    GET  /health              — health check
    POST /analyze             — run the agent: full autonomous feasibility analysis
    POST /report/pdf          — agent generates and returns PDF report
    GET  /omi/{comune}        — raw OMI market data (Italy)
    GET  /vincoli/{lat}/{lng} — raw environmental/landscape constraints

The agent autonomously selects the right country connector, fetches market
and urbanistic data, runs DCF + Monte Carlo, and produces a GO/NO-GO verdict.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# Ensure project root is importable
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from src.landiq_core import (
    LandIQEngine,
    FeasibilityInput,
    UseType,
    __version__,
)

app = FastAPI(
    title="LandIQ Agent API",
    description="Autonomous AI Agent for Real Estate Feasibility — give it an address, get a GO/NO-GO verdict",
    version=__version__,
)

# Singleton engine (reuses cache dir, Gemini key from env)
_engine: LandIQEngine | None = None


def get_engine() -> LandIQEngine:
    global _engine
    if _engine is None:
        cache_dir = os.getenv("LANDIQ_CACHE_DIR", str(_PKG_ROOT / "data" / "cache"))
        _engine = LandIQEngine(cache_dir=cache_dir)
    return _engine


# ─── Auto-detect provincia from comune ─────────────────────
COMUNE_TO_PROVINCIA = {
    "roma": "RM", "milano": "MI", "napoli": "NA", "torino": "TO",
    "palermo": "PA", "genova": "GE", "bologna": "BO", "firenze": "FI",
    "bari": "BA", "catania": "CT", "venezia": "VE", "verona": "VR",
    "messina": "ME", "padova": "PD", "trieste": "TS", "taranto": "TA",
    "brescia": "BS", "parma": "PR", "modena": "MO", "reggio calabria": "RC",
    "reggio emilia": "RE", "perugia": "PG", "ravenna": "RA", "livorno": "LI",
    "cagliari": "CA", "foggia": "FG", "rimini": "RN", "salerno": "SA",
    "ferrara": "FE", "sassari": "SS", "latina": "LT", "giugliano in campania": "NA",
    "monza": "MB", "siracusa": "SR", "bergamo": "BG", "pescara": "PE",
    "trento": "TN", "forl\u00ec": "FC", "vicenza": "VI", "terni": "TR",
    "bolzano": "BZ", "novara": "NO", "piacenza": "PC", "ancona": "AN",
    "andria": "BT", "arezzo": "AR", "udine": "UD", "cesena": "FC",
    "lecce": "LE", "pesaro": "PU", "gaeta": "LT", "como": "CO",
}

def _resolve_provincia(comune: str, explicit: str | None) -> str:
    """Return provincia: use explicit if given, otherwise auto-detect from comune."""
    if explicit and explicit != "MI":  # MI is the default, might be wrong
        return explicit
    key = comune.strip().lower()
    return COMUNE_TO_PROVINCIA.get(key, explicit or "MI")

# ─── Request / Response Models ───────────────────────────


class AnalyzeRequest(BaseModel):
    address: str = Field(..., max_length=500, description="Full address of the property")
    sqm: float = Field(..., gt=0, description="Surface area in square meters")
    current_use: str = Field(..., description="Current use type")
    target_use: str = Field(..., description="Target use type")
    budget: float = Field(..., gt=0, description="Total capital budget in EUR")
    horizon_years: int = Field(default=5, ge=1, le=20)
    # Country / city — works for any country
    country: str = Field(default="IT", max_length=2, description="ISO 3166-1 alpha-2 country code")
    city: Optional[str] = Field(default=None, max_length=200, description="City name")
    # Italy-specific (kept for backward compat)
    comune: str = Field(default="Milano", max_length=100)
    provincia: str = Field(default="MI", max_length=5)
    cap: Optional[str] = Field(default=None, max_length=10)
    particella_catastale: Optional[str] = Field(default=None, max_length=50)
    parcel_area_sqm: Optional[float] = Field(default=None, gt=0)
    existing_building_volume_mc: Optional[float] = Field(default=None, ge=0)
    client_name: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=2000)


class ReportRequest(BaseModel):
    address: str = Field(..., max_length=500)
    sqm: float = Field(..., gt=0)
    current_use: str = Field(...)
    target_use: str = Field(...)
    budget: float = Field(..., gt=0)
    horizon_years: int = Field(default=5, ge=1, le=20)
    country: str = Field(default="IT", max_length=2, description="ISO 3166-1 alpha-2 country code")
    city: Optional[str] = Field(default=None, max_length=200)
    comune: str = Field(default="Milano", max_length=100)
    provincia: str = Field(default="MI", max_length=5)
    client_name: Optional[str] = Field(default=None, max_length=200)
    parcel_area_sqm: Optional[float] = Field(default=None, gt=0)
    output_filename: Optional[str] = Field(default=None, max_length=200)


class AnalyzeResponse(BaseModel):
    status: str = "ok"
    version: str
    comune: str
    country: str = "IT"
    recommended_scenario: str
    feasibility_score: float
    scenarios: list[dict[str, Any]]
    monte_carlo: list[dict[str, Any]]
    urbanistic_summary: dict[str, Any]
    market_summary: dict[str, Any]
    volumetry: dict[str, Any]
    ai_verdict: str
    generated_at: str
    sources: list[str]


# ─── Endpoints ───────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "landiq-engine",
        "version": __version__,
    }


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """Run full feasibility analysis for a land parcel or building."""
    engine = get_engine()

    try:
        inp = FeasibilityInput(
            address=request.address,
            sqm=request.sqm,
            current_use=request.current_use,
            target_use=request.target_use,
            budget=request.budget,
            horizon_years=request.horizon_years,
            country=request.country.upper(),
            city=request.city or request.comune,
            comune=request.comune,
            provincia=_resolve_provincia(request.comune, request.provincia),
            cap=request.cap,
            particella_catastale=request.particella_catastale,
            parcel_area_sqm=request.parcel_area_sqm,
            existing_building_volume_mc=request.existing_building_volume_mc,
            client_name=request.client_name,
            notes=request.notes,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {e}")

    try:
        report = engine.run(inp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    # Compute a simple feasibility score (0-10) from the recommended scenario
    best = max(report.scenarios, key=lambda s: s.npv / (s.risk_score + 1))
    score = 5.0
    if best.irr > 0.20:
        score += 2
    elif best.irr > 0.10:
        score += 1
    if best.npv > 0:
        score += 1
    if best.risk_score < 5:
        score += 1
    elif best.risk_score > 7:
        score -= 1
    # MC probability bonus
    mc_best = next((m for m in report.monte_carlo if m.scenario_name == best.name), None)
    if mc_best and mc_best.prob_irr_above_target > 0.5:
        score += 0.5
    if mc_best and mc_best.prob_npv_negative < 0.15:
        score += 0.5
    score = max(0, min(10, score))

    return AnalyzeResponse(
        version=__version__,
        comune=request.city or request.comune,
        country=request.country.upper(),
        recommended_scenario=report.recommended_scenario,
        feasibility_score=round(score, 1),
        scenarios=[asdict(s) for s in report.scenarios],
        monte_carlo=[asdict(m) for m in report.monte_carlo],
        urbanistic_summary={
            "plan_type": report.urbanistic_data.get("plan_type"),
            "approval_date": report.urbanistic_data.get("approval_date"),
            "variante_in_corso": report.urbanistic_data.get("variante_in_corso"),
            "vincoli": report.urbanistic_data.get("vincoli", []),
            "zones_count": len(report.urbanistic_data.get("zones", [])),
        },
        market_summary={
            "zones_count": len(report.market_data.get("zones", [])),
            "semester": report.market_data.get("semester"),
            "source": report.market_data.get("source", "OMI Agenzia Entrate"),
        },
        volumetry=report.volumetry,
        ai_verdict=report.ai_verdict,
        generated_at=report.generated_at,
        sources=report.sources,
    )


@app.post("/report/pdf")
async def generate_pdf(request: ReportRequest):
    """Generate PDF report and return as downloadable file."""
    engine = get_engine()

    try:
        inp = FeasibilityInput(
            address=request.address,
            sqm=request.sqm,
            current_use=request.current_use,
            target_use=request.target_use,
            budget=request.budget,
            horizon_years=request.horizon_years,
            country=request.country.upper(),
            city=request.city or request.comune,
            comune=request.comune,
            provincia=_resolve_provincia(request.comune, request.provincia),
            client_name=request.client_name,
            parcel_area_sqm=request.parcel_area_sqm,
        )
        report = engine.run(inp)

        # Generate PDF to reports dir
        reports_dir = _PKG_ROOT / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        filename = request.output_filename or f"landiq_{request.comune.lower()}_{report.generated_at[:10]}.pdf"
        if not filename.endswith(".pdf"):
            filename += ".pdf"

        pdf_path = engine.export_pdf(report, reports_dir / filename)

        return FileResponse(
            path=str(pdf_path),
            media_type="application/pdf",
            filename=filename,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")


@app.get("/omi/{comune}")
async def get_omi(comune: str, provincia: str = "MI", zona: Optional[str] = None):
    """Get OMI market data for a municipality."""
    engine = get_engine()

    try:
        data = engine.fetch_omi(comune, provincia=provincia, zona=zona)
        return {"status": "ok", "comune": comune, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OMI fetch failed: {e}")


@app.get("/vincoli/{lat}/{lng}")
async def get_vincoli(lat: float, lng: float):
    """Get environmental/landscape constraints for a location."""
    try:
        from scrapers import vincoli_sitap_pai as vincoli
        results = vincoli.check_vincoli_sitap(lat, lng)
        return {
            "status": "ok",
            "lat": lat,
            "lng": lng,
            "vincoli": [asdict(v) if hasattr(v, '__dataclass_fields__') else v for v in results],
            "count": len(results),
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="Vincoli scraper not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vincoli check failed: {e}")


@app.get("/puc/{comune}")
async def get_puc(comune: str):
    """Get urbanistic plan data for a municipality."""
    engine = get_engine()
    try:
        data = engine.fetch_puc(comune)
        return {"status": "ok", "comune": comune, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PUC fetch failed: {e}")
