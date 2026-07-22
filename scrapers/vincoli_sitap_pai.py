#!/usr/bin/env python3
"""
vincoli_sitap_pai.py — Query Italian environmental/landscape constraints (vincoli)
from SITAP (MiC) and PAI/IdroGEO (ISPRA) public databases.

Data sources:
  1. SITAP — vincoli paesaggistici D.Lgs 42/2004
     WFS/ArcGIS REST: https://www.sitap.beniculturali.it/arcgis/rest/services/
  2. PAI / IdroGEO (ISPRA) — rischio idrogeologico (frane + alluvioni)
     API: https://idrogeo.isprambiente.it/api/

Cache: /app/data/vincoli_cache/ (7-day TTL)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_DIR = Path("/app/data/vincoli_cache")
CACHE_TTL_DAYS = 7

SITAP_ARCGIS_BASE = "https://www.sitap.beniculturali.it/arcgis/rest/services"
SITAP_WFS_BASE = "http://www.sitap.beniculturali.it/geoserver/wfs"

# Known SITAP ArcGIS MapServer layer IDs
SITAP_LAYERS = {
    "vincoli_dichiarativi": f"{SITAP_ARCGIS_BASE}/Vincoli/Vincoli_Dichiarativi/MapServer",
    "vincoli_ricognitivi": f"{SITAP_ARCGIS_BASE}/Vincoli/Vincoli_Ricognitivi/MapServer",
    "immobili_aree": f"{SITAP_ARCGIS_BASE}/Vincoli/Immobili_Aree_Interesse/MapServer",
}

# ISPRA IdroGEO endpoints
IDROGEO_API_BASE = "https://idrogeo.isprambiente.it/api"
IDROGEO_FRANE = f"{IDROGEO_API_BASE}/frane/geometry"
IDROGEO_ALLUVIONI = f"{IDROGEO_API_BASE}/alluvioni/geometry"
IDROGEO_INDICATORI = f"{IDROGEO_API_BASE}/indicatori/comune"

# Nominatim for geocoding comuni
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

REQUEST_TIMEOUT = 30
USER_AGENT = "LandIQ/1.0 (vincoli check; ale@get-scala.com)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class VincoloResult:
    """Single constraint/risk record returned by a public datasource."""

    tipo: str  # paesaggistico | idrogeologico | archeologico | sismico | ...
    codice: str  # reference code from the source DB
    descrizione: str
    normativa: str  # e.g. "D.Lgs 42/2004 art.136"
    livello_rischio: Optional[str] = None  # R1-R4, P1-P4 for PAI
    geometry_wkt: Optional[str] = None
    source: str = ""  # SITAP | PAI | ISPRA
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_key(prefix: str, **kwargs) -> str:
    """Deterministic cache filename from query params."""
    raw = json.dumps(kwargs, sort_keys=True)
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}_{h}.json"


def _cache_get(key: str) -> Optional[list[dict]]:
    """Return cached data if fresh, else None."""
    path = CACHE_DIR / key
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        if datetime.utcnow() - cached_at > timedelta(days=CACHE_TTL_DAYS):
            logger.debug("Cache expired: %s", key)
            return None
        return data.get("results", [])
    except Exception:
        return None


def _cache_put(key: str, results: list[dict]) -> None:
    """Write results to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "_cached_at": datetime.utcnow().isoformat(),
        "results": results,
    }
    (CACHE_DIR / key).write_text(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _http_get(url: str, params: dict | None = None, timeout: int = REQUEST_TIMEOUT) -> requests.Response:
    """GET with standard headers and error handling."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _bbox_from_point(lat: float, lon: float, radius_m: int) -> str:
    """Approximate bounding box string for ArcGIS queries (WGS84).
    Returns 'xmin,ymin,xmax,ymax' in lon/lat order (EPSG:4326).
    """
    # ~111320 m per degree latitude, longitude varies with cos(lat)
    import math

    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    return f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}"


def _ring_to_wkt(rings: list) -> str | None:
    """Convert ArcGIS JSON rings to WKT POLYGON."""
    if not rings:
        return None
    try:
        coords = ", ".join(f"{pt[0]} {pt[1]}" for pt in rings[0])
        return f"POLYGON(({coords}))"
    except Exception:
        return None


def _geojson_geom_to_wkt(geom: dict) -> str | None:
    """Simplified GeoJSON geometry -> WKT."""
    if not geom:
        return None
    gtype = geom.get("type", "")
    coords = geom.get("coordinates")
    if not coords:
        return None
    try:
        if gtype == "Point":
            return f"POINT({coords[0]} {coords[1]})"
        if gtype == "Polygon":
            ring = ", ".join(f"{c[0]} {c[1]}" for c in coords[0])
            return f"POLYGON(({ring}))"
        if gtype == "MultiPolygon":
            polys = []
            for poly in coords:
                ring = ", ".join(f"{c[0]} {c[1]}" for c in poly[0])
                polys.append(f"(({ring}))")
            return f"MULTIPOLYGON({', '.join(polys)})"
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# SITAP queries
# ---------------------------------------------------------------------------


def _query_sitap_arcgis_layer(
    layer_url: str, lat: float, lon: float, radius_m: int, layer_id: int = 0
) -> list[dict]:
    """Query a single SITAP ArcGIS MapServer layer via the identify or query endpoint."""
    bbox = _bbox_from_point(lat, lon, radius_m)
    # Use the /query endpoint on a specific layer
    url = f"{layer_url}/{layer_id}/query"
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": radius_m,
        "units": "esriSRUnit_Meter",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": "*",
        "returnGeometry": "true",
        "f": "json",
    }
    try:
        resp = _http_get(url, params=params)
        data = resp.json()
        return data.get("features", [])
    except Exception as exc:
        logger.warning("SITAP ArcGIS query failed (%s layer %d): %s", layer_url, layer_id, exc)
        return []


def _query_sitap_wfs(lat: float, lon: float, radius_m: int) -> list[dict]:
    """Query SITAP WFS GetFeature for vincoli within radius (GeoJSON)."""
    import math

    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    bbox_str = f"{lat - dlat},{lon - dlon},{lat + dlat},{lon + dlon}"

    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": "sitap:vincoli_paesaggistici",
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "bbox": bbox_str,
        "count": "100",
    }
    try:
        resp = _http_get(SITAP_WFS_BASE, params=params)
        data = resp.json()
        return data.get("features", [])
    except Exception as exc:
        logger.warning("SITAP WFS query failed: %s", exc)
        return []


def _parse_sitap_arcgis_feature(feat: dict, tipo_layer: str) -> VincoloResult:
    """Convert an ArcGIS JSON feature to VincoloResult."""
    attrs = feat.get("attributes", {})
    geom = feat.get("geometry", {})

    # Common SITAP attribute names (vary by layer)
    codice = str(
        attrs.get("CODICE", attrs.get("COD_VINCOLO", attrs.get("OBJECTID", "")))
    )
    desc_fields = ["DENOMINAZIONE", "DESCRIZIONE", "NOME", "DEN", "DENOM"]
    descrizione = ""
    for df in desc_fields:
        if attrs.get(df):
            descrizione = str(attrs[df])
            break
    if not descrizione:
        descrizione = "; ".join(f"{k}={v}" for k, v in attrs.items() if v and k != "OBJECTID")

    norm_fields = ["NORMA", "NORMATIVA", "PROVVEDIMENTO", "TIPO_VINCOLO"]
    normativa = ""
    for nf in norm_fields:
        if attrs.get(nf):
            normativa = str(attrs[nf])
            break
    if not normativa:
        normativa = "D.Lgs 42/2004"

    # Determine tipo
    tipo = "paesaggistico"
    if "archeolog" in tipo_layer.lower() or "archeolog" in descrizione.lower():
        tipo = "archeologico"

    wkt = _ring_to_wkt(geom.get("rings", []))

    return VincoloResult(
        tipo=tipo,
        codice=codice,
        descrizione=descrizione.strip(),
        normativa=normativa,
        livello_rischio=None,
        geometry_wkt=wkt,
        source="SITAP",
    )


def _parse_sitap_wfs_feature(feat: dict) -> VincoloResult:
    """Convert a WFS GeoJSON feature to VincoloResult."""
    props = feat.get("properties", {})
    geom = feat.get("geometry")

    codice = str(props.get("codice", props.get("cod_vincolo", props.get("id", ""))))
    descrizione = str(
        props.get("denominazione", props.get("descrizione", props.get("nome", "")))
    )
    normativa = str(props.get("normativa", props.get("norma", "D.Lgs 42/2004")))

    tipo = "paesaggistico"
    if any(k in descrizione.lower() for k in ("archeolog",)):
        tipo = "archeologico"

    return VincoloResult(
        tipo=tipo,
        codice=codice,
        descrizione=descrizione.strip(),
        normativa=normativa,
        geometry_wkt=_geojson_geom_to_wkt(geom),
        source="SITAP",
    )


def check_vincoli_sitap(lat: float, lon: float, radius_m: int = 500) -> list[VincoloResult]:
    """
    Query SITAP for landscape/archaeological constraints near (lat, lon).

    Tries ArcGIS REST first (multiple layers), then WFS as fallback.
    Results are cached for 7 days.
    """
    cache_key = _cache_key("sitap", lat=round(lat, 5), lon=round(lon, 5), radius=radius_m)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("SITAP: returning %d cached results", len(cached))
        return [VincoloResult(**r) for r in cached]

    results: list[VincoloResult] = []
    seen_codes: set[str] = set()

    # --- ArcGIS REST layers ---
    for layer_name, layer_url in SITAP_LAYERS.items():
        # Try layer IDs 0 through 2 (most SITAP services expose 0-2)
        for lid in range(3):
            features = _query_sitap_arcgis_layer(layer_url, lat, lon, radius_m, layer_id=lid)
            for feat in features:
                vr = _parse_sitap_arcgis_feature(feat, layer_name)
                if vr.codice not in seen_codes:
                    seen_codes.add(vr.codice)
                    results.append(vr)

    # --- WFS fallback / supplement ---
    wfs_features = _query_sitap_wfs(lat, lon, radius_m)
    for feat in wfs_features:
        vr = _parse_sitap_wfs_feature(feat)
        if vr.codice not in seen_codes:
            seen_codes.add(vr.codice)
            results.append(vr)

    logger.info("SITAP: found %d vincoli near (%.4f, %.4f) r=%dm", len(results), lat, lon, radius_m)
    _cache_put(cache_key, [asdict(r) for r in results])
    return results


# ---------------------------------------------------------------------------
# PAI / IdroGEO queries
# ---------------------------------------------------------------------------


def _query_idrogeo_frane(lat: float, lon: float) -> list[dict]:
    """Query ISPRA IdroGEO for landslide (frana) data at point."""
    params = {
        "lat": lat,
        "lon": lon,
        "buffer": 100,  # meters
        "format": "json",
    }
    try:
        resp = _http_get(IDROGEO_FRANE, params=params)
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("features", data.get("results", []))
    except Exception as exc:
        logger.warning("IdroGEO frane query failed: %s", exc)
        return []


def _query_idrogeo_alluvioni(lat: float, lon: float) -> list[dict]:
    """Query ISPRA IdroGEO for flood (alluvione) data at point."""
    params = {
        "lat": lat,
        "lon": lon,
        "buffer": 100,
        "format": "json",
    }
    try:
        resp = _http_get(IDROGEO_ALLUVIONI, params=params)
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("features", data.get("results", []))
    except Exception as exc:
        logger.warning("IdroGEO alluvioni query failed: %s", exc)
        return []


def _query_idrogeo_indicatori_comune(istat_code: str) -> dict:
    """Query ISPRA IdroGEO for municipal-level risk indicators."""
    url = f"{IDROGEO_INDICATORI}/{istat_code}"
    try:
        resp = _http_get(url)
        return resp.json()
    except Exception as exc:
        logger.warning("IdroGEO indicatori query failed for ISTAT %s: %s", istat_code, exc)
        return {}


def _rischio_label(value: Any) -> str | None:
    """Normalize risk level strings."""
    if value is None:
        return None
    s = str(value).strip().upper()
    # Accept R1-R4, P1-P4
    if s in ("R1", "R2", "R3", "R4", "P1", "P2", "P3", "P4"):
        return s
    # Map numeric / descriptive
    mapping = {
        "MODERATO": "R1",
        "MEDIO": "R2",
        "ELEVATO": "R3",
        "MOLTO ELEVATO": "R4",
        "1": "R1",
        "2": "R2",
        "3": "R3",
        "4": "R4",
    }
    return mapping.get(s)


def _parse_idrogeo_frana(item: dict) -> VincoloResult:
    """Parse a single frana record from IdroGEO."""
    props = item.get("properties", item)
    geom = item.get("geometry")

    codice = str(props.get("id_frana", props.get("codice", props.get("id", ""))))
    desc = str(
        props.get("descrizione", props.get("tipo_movimento", props.get("tipo", "Frana")))
    )
    rischio = _rischio_label(
        props.get("rischio", props.get("livello_rischio", props.get("classe_rischio")))
    )
    pericolosita = _rischio_label(
        props.get("pericolosita", props.get("classe_pericolosita"))
    )

    normativa = "D.Lgs 49/2010; PAI"

    return VincoloResult(
        tipo="idrogeologico",
        codice=codice,
        descrizione=f"Frana: {desc}",
        normativa=normativa,
        livello_rischio=rischio or pericolosita,
        geometry_wkt=_geojson_geom_to_wkt(geom),
        source="ISPRA/IdroGEO",
    )


def _parse_idrogeo_alluvione(item: dict) -> VincoloResult:
    """Parse a single alluvione record from IdroGEO."""
    props = item.get("properties", item)
    geom = item.get("geometry")

    codice = str(props.get("id_alluvione", props.get("codice", props.get("id", ""))))
    scenario = str(props.get("scenario", props.get("tempo_ritorno", "")))
    desc = f"Alluvione (scenario: {scenario})" if scenario else "Alluvione"

    pericolosita = _rischio_label(
        props.get("pericolosita", props.get("classe_pericolosita", props.get("livello")))
    )

    return VincoloResult(
        tipo="idrogeologico",
        codice=codice,
        descrizione=desc,
        normativa="D.Lgs 49/2010; Direttiva 2007/60/CE",
        livello_rischio=pericolosita,
        geometry_wkt=_geojson_geom_to_wkt(geom),
        source="ISPRA/IdroGEO",
    )


def check_vincoli_pai(lat: float, lon: float) -> list[VincoloResult]:
    """
    Query PAI/IdroGEO for hydrogeological risk (frane + alluvioni) at (lat, lon).
    Results are cached for 7 days.
    """
    cache_key = _cache_key("pai", lat=round(lat, 5), lon=round(lon, 5))
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("PAI: returning %d cached results", len(cached))
        return [VincoloResult(**r) for r in cached]

    results: list[VincoloResult] = []

    # Frane
    frane = _query_idrogeo_frane(lat, lon)
    for item in frane:
        results.append(_parse_idrogeo_frana(item))

    # Alluvioni
    alluvioni = _query_idrogeo_alluvioni(lat, lon)
    for item in alluvioni:
        results.append(_parse_idrogeo_alluvione(item))

    logger.info("PAI: found %d vincoli at (%.4f, %.4f)", len(results), lat, lon)
    _cache_put(cache_key, [asdict(r) for r in results])
    return results


# ---------------------------------------------------------------------------
# Combined query
# ---------------------------------------------------------------------------


def check_all_vincoli(lat: float, lon: float, radius_m: int = 500) -> dict:
    """
    Query ALL sources (SITAP + PAI) and return combined results.

    Returns:
        {
            "coordinate": {"lat": ..., "lon": ...},
            "timestamp": "...",
            "sitap": [...],
            "pai": [...],
            "totale_vincoli": int,
            "warnings": [...]
        }

    Graceful degradation: if one source fails, partial results are returned
    with a warning appended.
    """
    warnings: list[str] = []
    sitap_results: list[VincoloResult] = []
    pai_results: list[VincoloResult] = []

    # SITAP
    try:
        sitap_results = check_vincoli_sitap(lat, lon, radius_m)
    except Exception as exc:
        msg = f"SITAP query failed: {exc}"
        logger.error(msg)
        warnings.append(msg)

    # PAI
    try:
        pai_results = check_vincoli_pai(lat, lon)
    except Exception as exc:
        msg = f"PAI query failed: {exc}"
        logger.error(msg)
        warnings.append(msg)

    return {
        "coordinate": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "timestamp": datetime.utcnow().isoformat(),
        "sitap": [asdict(v) for v in sitap_results],
        "pai": [asdict(v) for v in pai_results],
        "totale_vincoli": len(sitap_results) + len(pai_results),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Query by comune (geocoding via Nominatim)
# ---------------------------------------------------------------------------


def _geocode_comune(comune: str, provincia: str) -> tuple[float, float] | None:
    """Geocode an Italian municipality via Nominatim. Returns (lat, lon) or None."""
    params = {
        "q": f"{comune}, {provincia}, Italia",
        "format": "json",
        "limit": 1,
        "countrycodes": "it",
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as exc:
        logger.warning("Geocoding failed for %s (%s): %s", comune, provincia, exc)
    return None


def _lookup_istat_code(comune: str, provincia: str) -> str | None:
    """
    Attempt to find the ISTAT code for a comune.
    Uses a simple Nominatim + ISPRA lookup fallback.
    """
    # For now, return None — ISTAT code lookup requires a local DB or API
    # that maps comune names to 6-digit codes. The IdroGEO indicatori
    # endpoint is called only when the code is known.
    return None


def check_vincoli_by_comune(comune: str, provincia: str) -> dict:
    """
    Check all vincoli for a municipality by name.

    Steps:
        1. Geocode the comune to lat/lon
        2. Run check_all_vincoli on the centroid
        3. Optionally query ISPRA indicatori if ISTAT code is known

    Returns same structure as check_all_vincoli, plus 'comune' metadata.
    """
    coords = _geocode_comune(comune, provincia)
    if coords is None:
        return {
            "comune": comune,
            "provincia": provincia,
            "error": f"Impossibile geocodificare {comune} ({provincia})",
            "sitap": [],
            "pai": [],
            "totale_vincoli": 0,
            "warnings": [f"Geocoding fallito per {comune} ({provincia})"],
        }

    lat, lon = coords
    result = check_all_vincoli(lat, lon, radius_m=1000)
    result["comune"] = comune
    result["provincia"] = provincia

    # Try ISTAT indicators as supplemental data
    istat = _lookup_istat_code(comune, provincia)
    if istat:
        indicatori = _query_idrogeo_indicatori_comune(istat)
        if indicatori:
            result["indicatori_ispra"] = indicatori

    return result


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------


def _print_results(results: dict) -> None:
    """Human-readable console output."""
    print("\n" + "=" * 72)
    print(f"  VINCOLI CHECK — ({results['coordinate']['lat']}, {results['coordinate']['lon']})")
    if results.get("comune"):
        print(f"  Comune: {results['comune']} ({results.get('provincia', '')})")
    print(f"  Raggio: {results.get('radius_m', 'N/A')} m")
    print(f"  Timestamp: {results['timestamp']}")
    print("=" * 72)

    # SITAP
    sitap = results.get("sitap", [])
    print(f"\n  SITAP — Vincoli Paesaggistici/Archeologici: {len(sitap)}")
    print("  " + "-" * 50)
    if not sitap:
        print("  Nessun vincolo SITAP trovato nel raggio.")
    for i, v in enumerate(sitap, 1):
        print(f"  [{i}] {v['tipo'].upper()} — {v['codice']}")
        print(f"      {v['descrizione'][:100]}")
        print(f"      Normativa: {v['normativa']}")
        if v.get("geometry_wkt"):
            print(f"      Geometria: {v['geometry_wkt'][:60]}...")

    # PAI
    pai = results.get("pai", [])
    print(f"\n  PAI / IdroGEO — Rischio Idrogeologico: {len(pai)}")
    print("  " + "-" * 50)
    if not pai:
        print("  Nessun vincolo idrogeologico trovato.")
    for i, v in enumerate(pai, 1):
        rischio = v.get("livello_rischio") or "N/D"
        print(f"  [{i}] {v['tipo'].upper()} — {v['codice']} (Rischio: {rischio})")
        print(f"      {v['descrizione'][:100]}")
        print(f"      Normativa: {v['normativa']}")

    # Warnings
    warnings = results.get("warnings", [])
    if warnings:
        print(f"\n  AVVISI ({len(warnings)}):")
        for w in warnings:
            print(f"    ! {w}")

    print(f"\n  TOTALE VINCOLI: {results['totale_vincoli']}")
    print("=" * 72 + "\n")


# ---------------------------------------------------------------------------
# __main__ — test with Gaeta Serapo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Vincoli SITAP/PAI checker")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout (for API bridge)")
    parser.add_argument("--lat", type=float, default=41.2097)
    parser.add_argument("--lon", type=float, default=13.5712)
    parser.add_argument("--radius", type=int, default=500)
    args = parser.parse_args()

    combined = check_all_vincoli(args.lat, args.lon, radius_m=args.radius)

    if args.json:
        print(json.dumps(combined, default=str, ensure_ascii=False))
    else:
        print(f"Testing vincoli check for ({args.lat}, {args.lon})...")

        # Test individual sources
        print("\n--- SITAP ---")
        sitap = check_vincoli_sitap(args.lat, args.lon, radius_m=args.radius)
        print(f"SITAP results: {len(sitap)}")
        for v in sitap:
            print(f"  - [{v.tipo}] {v.codice}: {v.descrizione[:80]}")

        print("\n--- PAI ---")
        pai = check_vincoli_pai(args.lat, args.lon)
        print(f"PAI results: {len(pai)}")
        for v in pai:
            print(f"  - [{v.tipo}] {v.codice}: {v.descrizione[:80]} (rischio: {v.livello_rischio})")

        # Test combined
        print("\n--- ALL VINCOLI ---")
        _print_results(combined)

        # Save full output to JSON
        out_path = CACHE_DIR / "vincoli_test.json"
        out_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2))
        print(f"Full results saved to: {out_path}")
