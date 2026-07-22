"""
PGT / PRG Parser — Italian Municipal Urban Planning Documents.

Downloads and parses Piano di Governo del Territorio (PGT) or
Piano Regolatore Generale (PRG) documents from Italian municipalities.
Extracts urbanistic zone parameters (indice fondiario, altezza max,
copertura, destinazioni, distanze) via LLM (Gemini 2.5 Flash).

Supported comuni (initial 10):
    Milano, Roma, Napoli, Torino, Bologna, Firenze, Gaeta, Genova,
    Palermo, Bari.

Usage:
    from scrapers.pgt_parser import get_pgt
    result = get_pgt("Gaeta", "LT")
    for z in result.zone:
        print(f"{z.codice} — IF={z.indice_fondiario}")

Cache:
    Extracted results are cached as JSON in
    /app/data/pgt_cache/{comune_lower}/result.json
    with a 90-day TTL (plans rarely change).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_DIR = Path("/app/data/pgt_cache")
CACHE_TTL_DAYS = 90
GEMINI_RPM_LIMIT = 5  # free tier: 5 requests/minute
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 LandIQ/1.0"
)
REQUEST_TIMEOUT = 60

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ZonaUrbanistica:
    codice: str                              # e.g. "B1", "C2", "F"
    denominazione: str                       # e.g. "Zona residenziale di completamento"
    indice_fondiario: float | None = None    # IF in mc/mq or mq/mq
    indice_territoriale: float | None = None # IT
    altezza_max_m: float | None = None       # Hmax (metres)
    copertura_max_pct: float | None = None   # rapporto di copertura (%)
    destinazioni_ammesse: list[str] = field(default_factory=list)
    destinazioni_vietate: list[str] = field(default_factory=list)
    distanza_confini_m: float | None = None
    distanza_strade_m: float | None = None
    prescrizioni: str = ""                   # free text
    fonte_documento: str = ""                # PDF filename
    pagina: int | None = None


@dataclass
class PgtResult:
    comune: str
    provincia: str
    tipo_piano: str                          # "PGT" | "PRG" | "PS+RU"
    anno_approvazione: int | None = None
    zone: list[ZonaUrbanistica] = field(default_factory=list)
    nta_url: str | None = None               # URL to NTA PDF
    cartografia_url: str | None = None
    source_urls: list[str] = field(default_factory=list)
    extraction_method: str = "llm"           # "llm" | "manual" | "cached"
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# URL registry — manually curated for top 10 comuni
# ---------------------------------------------------------------------------

# Each entry maps to a dict with:
#   tipo_piano, anno, nta_urls (list of PDFs to try), landing, cartografia
# nta_urls are ordered by preference; first downloadable wins.

URL_REGISTRY: dict[str, dict[str, Any]] = {
    "milano": {
        "tipo_piano": "PGT",
        "anno": 2019,
        "landing": "https://www.comune.milano.it/servizi/piano-di-governo-del-territorio-pgt",
        "nta_urls": [
            "https://www.pgt.comune.milano.it/sites/default/files/allegati/PR_NTA_Norme_Tecniche_Attuazione.pdf",
        ],
        "cartografia": "https://www.pgt.comune.milano.it/piano-delle-regole/cartografia",
    },
    "roma": {
        "tipo_piano": "PRG",
        "anno": 2008,
        "landing": "https://www.urbanistica.comune.roma.it/prg-vigente.html",
        "nta_urls": [
            "https://www.urbanistica.comune.roma.it/images/prg-vigente/nta-prg-vigente.pdf",
        ],
        "cartografia": "https://www.urbanistica.comune.roma.it/prg-vigente/prg-elaborati-cartografici.html",
    },
    "napoli": {
        "tipo_piano": "PRG",
        "anno": 2004,
        "landing": "https://www.comune.napoli.it/flex/cm/pages/ServeBLOB.php/L/IT/IDPagina/1020",
        "nta_urls": [
            # Napoli PRG NTA — URL may change, last verified 2026-04
            "https://www.comune.napoli.it/flex/cm/pages/ServeAttachment.php/L/IT/D/1%252F9%252F3%252FD.ccd6fe7e6c3b07c3ab6e/P/BLOB%3AID%3D1020/E/pdf",
        ],
        "cartografia": None,
    },
    "torino": {
        "tipo_piano": "PRG",
        "anno": 1995,
        "landing": "https://www.comune.torino.it/urbanistica/strumenti-urbanistici/piano-regolatore/",
        "nta_urls": [
            "http://www.comune.torino.it/urbanistica/strumenti-urbanistici/piano-regolatore/norme-di-attuazione.pdf",
        ],
        "cartografia": "https://www.comune.torino.it/urbanistica/strumenti-urbanistici/piano-regolatore/elaborati-grafici/",
    },
    "bologna": {
        "tipo_piano": "PUG",
        "anno": 2021,
        "landing": "https://www.comune.bologna.it/piano-urbanistico-generale",
        "nta_urls": [
            # Bologna PUG (Piano Urbanistico Generale) — Disciplina del Piano
            "https://sitmappe.comune.bologna.it/pug/documenti/Disciplina_del_Piano.pdf",
        ],
        "cartografia": "https://sitmappe.comune.bologna.it/pug/",
    },
    "firenze": {
        "tipo_piano": "PS+RU",
        "anno": 2015,
        "landing": "https://www.comune.fi.it/pagina/piano-operativo",
        "nta_urls": [
            # Firenze Piano Operativo NTA
            "https://pianostrutturale.comune.fi.it/export/sites/pianostrutturale/materiali/norme/NTA_PO.pdf",
        ],
        "cartografia": "https://pianostrutturale.comune.fi.it/",
    },
    "gaeta": {
        "tipo_piano": "PRG",
        "anno": 2001,
        "landing": "https://www.comune.gaeta.lt.it/urbanistica",
        "nta_urls": [
            # Gaeta PRG NTA — smaller municipality, URL structure varies
            "https://www.comune.gaeta.lt.it/sites/default/files/nta_prg_gaeta.pdf",
        ],
        "cartografia": None,
    },
    "genova": {
        "tipo_piano": "PUC",
        "anno": 2012,
        "landing": "https://www.comune.genova.it/content/piano-urbanistico-comunale",
        "nta_urls": [
            "https://www.comune.genova.it/sites/default/files/puc/NTA_PUC.pdf",
        ],
        "cartografia": "https://mappe.comune.genova.it/sit/puc/",
    },
    "palermo": {
        "tipo_piano": "PRG",
        "anno": 2004,
        "landing": "https://www.comune.palermo.it/settore_urbanistica.php",
        "nta_urls": [
            # Palermo PRG NTA
            "https://www.comune.palermo.it/js/server/uploads/trasparenza_all/_04122013125757.pdf",
        ],
        "cartografia": None,
    },
    "bari": {
        "tipo_piano": "PUG",
        "anno": 2018,
        "landing": "https://www.comune.bari.it/web/urbanistica-e-edilizia-privata/piano-urbanistico-generale",
        "nta_urls": [
            "https://www.comune.bari.it/documents/20181/0/NTA_PUG_BARI.pdf",
        ],
        "cartografia": None,
    },
}


# ---------------------------------------------------------------------------
# Gemini rate-limiter
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Simple sliding-window rate limiter for Gemini free tier."""

    def __init__(self, max_calls: int = GEMINI_RPM_LIMIT, window_s: float = 60.0):
        self._max = max_calls
        self._window = window_s
        self._calls: list[float] = []

    def wait(self) -> None:
        now = time.monotonic()
        self._calls = [t for t in self._calls if now - t < self._window]
        if len(self._calls) >= self._max:
            sleep_for = self._window - (now - self._calls[0]) + 0.5
            if sleep_for > 0:
                logger.info("Rate-limit: sleeping %.1fs before Gemini call", sleep_for)
                time.sleep(sleep_for)
        self._calls.append(time.monotonic())


_gemini_limiter = _RateLimiter()


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

def _ensure_cache_dir(comune: str) -> Path:
    """Create and return the cache directory for a comune."""
    d = DATA_DIR / comune.lower().replace(" ", "_")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download_nta(comune: str) -> Path | None:
    """
    Download the NTA PDF for *comune* from the URL registry.
    Returns the local Path on success, None on failure.
    """
    key = comune.lower().replace(" ", "_")
    entry = URL_REGISTRY.get(key)
    if not entry:
        logger.warning("No URL registry entry for comune '%s'", comune)
        return None

    cache_dir = _ensure_cache_dir(comune)

    for url in entry.get("nta_urls", []):
        # Derive a stable filename from the URL
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        filename = f"nta_{key}_{url_hash}.pdf"
        local_path = cache_dir / filename

        # If already downloaded and <90 days old, reuse
        if local_path.exists():
            age = datetime.now() - datetime.fromtimestamp(local_path.stat().st_mtime)
            if age < timedelta(days=CACHE_TTL_DAYS):
                logger.info("Using cached PDF: %s", local_path)
                return local_path
            else:
                logger.info("Cached PDF expired (%d days), re-downloading", age.days)

        try:
            logger.info("Downloading NTA PDF: %s", url)
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type and "octet-stream" not in content_type:
                logger.warning(
                    "Unexpected Content-Type '%s' for %s — skipping", content_type, url
                )
                continue

            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)

            size_mb = local_path.stat().st_size / (1024 * 1024)
            logger.info("Downloaded %.1f MB -> %s", size_mb, local_path)
            return local_path

        except requests.RequestException as exc:
            logger.warning("Failed to download %s: %s", url, exc)
            continue

    logger.error("All NTA download URLs failed for '%s'", comune)
    return None


# ---------------------------------------------------------------------------
# Text extraction from PDF
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(pdf_path: Path, max_pages: int = 80) -> str:
    """
    Extract text from a PDF using pdfplumber (preferred) with PyPDF2 fallback.
    Returns concatenated text with page markers.
    """
    text_parts: list[str] = []

    # --- Attempt 1: pdfplumber (better layout handling) ---
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(pdf_path)) as pdf:
            pages_to_read = min(len(pdf.pages), max_pages)
            for i in range(pages_to_read):
                page = pdf.pages[i]
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(f"\n--- PAGINA {i + 1} ---\n{page_text}")

        if text_parts:
            logger.info(
                "pdfplumber: extracted %d pages from %s", len(text_parts), pdf_path.name
            )
            return "\n".join(text_parts)

    except Exception as exc:
        logger.warning("pdfplumber failed on %s: %s — trying PyPDF2", pdf_path.name, exc)

    # --- Attempt 2: PyPDF2 ---
    try:
        from PyPDF2 import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        pages_to_read = min(len(reader.pages), max_pages)
        for i in range(pages_to_read):
            page_text = reader.pages[i].extract_text() or ""
            if page_text.strip():
                text_parts.append(f"\n--- PAGINA {i + 1} ---\n{page_text}")

        if text_parts:
            logger.info(
                "PyPDF2: extracted %d pages from %s", len(text_parts), pdf_path.name
            )
            return "\n".join(text_parts)

    except Exception as exc:
        logger.warning("PyPDF2 failed on %s: %s", pdf_path.name, exc)

    logger.error("Could not extract text from %s (both extractors failed)", pdf_path.name)
    return ""


# ---------------------------------------------------------------------------
# LLM extraction (Gemini)
# ---------------------------------------------------------------------------

_LLM_PROMPT_TEMPLATE = """Sei un esperto urbanista italiano. Analizza il seguente testo estratto \
dal documento NTA (Norme Tecniche di Attuazione) del {tipo_piano} di {comune} ({provincia}).

Estrai TUTTE le zone urbanistiche menzionate nel testo con i seguenti parametri. \
Rispondi ESCLUSIVAMENTE con un JSON array valido, senza testo aggiuntivo, senza markdown.

Ogni elemento del JSON array deve avere questa struttura:
{{
  "codice": "B1",
  "denominazione": "Zona residenziale di completamento",
  "indice_fondiario": 1.5,
  "indice_territoriale": null,
  "altezza_max_m": 10.5,
  "copertura_max_pct": 40.0,
  "destinazioni_ammesse": ["residenziale", "commerciale al piano terra"],
  "destinazioni_vietate": ["industriale"],
  "distanza_confini_m": 5.0,
  "distanza_strade_m": 7.5,
  "prescrizioni": "Obbligo di piano attuativo per lotti > 2000 mq",
  "pagina": 23
}}

Regole:
- L'indice fondiario (IF) puo' essere espresso in mc/mq o mq/mq — riporta il valore numerico.
- Se un parametro non e' menzionato, usa null.
- Il campo "pagina" deve corrispondere al numero di pagina indicato nel testo (es. "--- PAGINA 23 ---").
- Includi TUTTE le zone che trovi, anche le zone agricole (E), zone per servizi (F/S), zone produttive (D).
- Non inventare dati: se il testo non specifica un valore, metti null.

Testo NTA:
{text_chunk}
"""

# Maximum characters per LLM chunk (Gemini 2.5 Flash handles ~1M tokens,
# but we stay conservative per chunk to get structured output)
_MAX_CHUNK_CHARS = 60_000


def _init_gemini() -> Any:
    """Initialize and return the Gemini generative model."""
    try:
        import google.generativeai as genai  # type: ignore
    except ImportError:
        raise RuntimeError(
            "google-generativeai is required. Install with: pip install google-generativeai"
        )

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        # Try loading from .env files
        try:
            from dotenv import load_dotenv  # type: ignore
            for env_file in [
                Path("/app/.env"),
                Path("/app/.env"),
                Path("/app/.env.local"),
            ]:
                if env_file.exists():
                    load_dotenv(env_file)
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        except ImportError:
            pass

    if not api_key:
        raise RuntimeError(
            "No Gemini API key found. Set GEMINI_API_KEY or GOOGLE_API_KEY env var."
        )

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-flash")
    return model


def _parse_llm_json(raw: str) -> list[dict[str, Any]]:
    """
    Parse JSON from LLM response, handling common issues
    (markdown fences, trailing commas, etc.).
    """
    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        # Remove first line (```json or ```)
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]  # drop closing fence
        text = "\n".join(lines).strip()

    # Remove trailing commas before ] or }
    text = re.sub(r",\s*([}\]])", r"\1", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("JSON parse failed: %s\nRaw (first 500 chars): %s", exc, text[:500])
        return []

    if isinstance(parsed, dict):
        # LLM sometimes wraps in {"zone": [...]}
        for key in ("zone", "zones", "data", "results"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        return []

    if isinstance(parsed, list):
        return parsed

    return []


def _dict_to_zona(d: dict[str, Any], pdf_name: str) -> ZonaUrbanistica:
    """Convert a raw dict from LLM output to a ZonaUrbanistica dataclass."""
    return ZonaUrbanistica(
        codice=str(d.get("codice", "")).strip(),
        denominazione=str(d.get("denominazione", "")).strip(),
        indice_fondiario=_to_float(d.get("indice_fondiario")),
        indice_territoriale=_to_float(d.get("indice_territoriale")),
        altezza_max_m=_to_float(d.get("altezza_max_m")),
        copertura_max_pct=_to_float(d.get("copertura_max_pct")),
        destinazioni_ammesse=_to_str_list(d.get("destinazioni_ammesse")),
        destinazioni_vietate=_to_str_list(d.get("destinazioni_vietate")),
        distanza_confini_m=_to_float(d.get("distanza_confini_m")),
        distanza_strade_m=_to_float(d.get("distanza_strade_m")),
        prescrizioni=str(d.get("prescrizioni") or ""),
        fonte_documento=pdf_name,
        pagina=_to_int(d.get("pagina")),
    )


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _to_str_list(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if x]
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return []


def _llm_extract_zones(
    text: str,
    comune: str,
    provincia: str = "",
    tipo_piano: str = "PGT",
) -> list[ZonaUrbanistica]:
    """
    Send text chunks to Gemini and extract zone urbanistiche.
    Returns a list of ZonaUrbanistica; empty list on failure.
    """
    if not text.strip():
        logger.warning("Empty text — nothing to send to LLM")
        return []

    try:
        model = _init_gemini()
    except RuntimeError as exc:
        logger.error("Cannot initialize Gemini: %s", exc)
        return []

    # Split text into chunks
    chunks: list[str] = []
    if len(text) <= _MAX_CHUNK_CHARS:
        chunks = [text]
    else:
        # Split on page markers to keep pages intact
        pages = re.split(r"(--- PAGINA \d+ ---)", text)
        current_chunk = ""
        for part in pages:
            if len(current_chunk) + len(part) > _MAX_CHUNK_CHARS and current_chunk:
                chunks.append(current_chunk)
                current_chunk = part
            else:
                current_chunk += part
        if current_chunk.strip():
            chunks.append(current_chunk)

    logger.info(
        "Sending %d chunk(s) to Gemini for '%s' (%d total chars)",
        len(chunks), comune, len(text),
    )

    all_zones: list[ZonaUrbanistica] = []
    seen_codici: set[str] = set()

    for i, chunk in enumerate(chunks):
        prompt = _LLM_PROMPT_TEMPLATE.format(
            tipo_piano=tipo_piano,
            comune=comune,
            provincia=provincia,
            text_chunk=chunk,
        )

        _gemini_limiter.wait()

        try:
            response = model.generate_content(prompt)
            raw_text = response.text or ""
        except Exception as exc:
            logger.error("Gemini call failed for chunk %d/%d: %s", i + 1, len(chunks), exc)
            continue

        items = _parse_llm_json(raw_text)
        logger.info("Chunk %d/%d: extracted %d zone(s)", i + 1, len(chunks), len(items))

        for item in items:
            zona = _dict_to_zona(item, f"nta_{comune.lower()}.pdf")
            # Deduplicate by codice
            if zona.codice and zona.codice not in seen_codici:
                seen_codici.add(zona.codice)
                all_zones.append(zona)
            elif not zona.codice:
                all_zones.append(zona)

    return all_zones


# ---------------------------------------------------------------------------
# High-level extraction from PDF
# ---------------------------------------------------------------------------

def extract_zones_from_pdf(
    pdf_path: Path,
    comune: str,
    provincia: str = "",
    tipo_piano: str = "PGT",
) -> list[ZonaUrbanistica]:
    """
    Extract zone urbanistiche from a local NTA PDF file.

    1. Extracts text via pdfplumber/PyPDF2
    2. Sends chunks to Gemini for structured extraction
    3. Returns list of ZonaUrbanistica
    """
    text = _extract_text_from_pdf(pdf_path)
    if not text:
        logger.error("No text extracted from %s", pdf_path)
        return []

    return _llm_extract_zones(text, comune, provincia, tipo_piano)


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def _cache_path(comune: str) -> Path:
    return _ensure_cache_dir(comune) / "result.json"


def _load_cache(comune: str) -> PgtResult | None:
    """Load cached PgtResult if it exists and is fresh (<90 days)."""
    path = _cache_path(comune)
    if not path.exists():
        return None

    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    if age > timedelta(days=CACHE_TTL_DAYS):
        logger.info("Cache expired for '%s' (%d days old)", comune, age.days)
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        zones = [
            ZonaUrbanistica(**z) for z in data.get("zone", [])
        ]
        result = PgtResult(
            comune=data["comune"],
            provincia=data["provincia"],
            tipo_piano=data["tipo_piano"],
            anno_approvazione=data.get("anno_approvazione"),
            zone=zones,
            nta_url=data.get("nta_url"),
            cartografia_url=data.get("cartografia_url"),
            source_urls=data.get("source_urls", []),
            extraction_method="cached",
            warnings=data.get("warnings", []),
        )
        logger.info("Loaded %d zone(s) from cache for '%s'", len(zones), comune)
        return result

    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Cache corrupt for '%s': %s", comune, exc)
        return None


def _save_cache(result: PgtResult) -> None:
    """Save PgtResult to JSON cache."""
    path = _cache_path(result.comune)
    data = asdict(result)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Cached result to %s", path)
    except OSError as exc:
        logger.warning("Failed to save cache: %s", exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_pgt(comune: str, provincia: str, force_refresh: bool = False) -> PgtResult:
    """
    Get PGT/PRG data for an Italian *comune*.

    Parameters
    ----------
    comune : str
        Municipality name (e.g. "Milano", "Gaeta").
    provincia : str
        Province abbreviation (e.g. "MI", "LT").
    force_refresh : bool
        If True, bypass cache and re-download + re-extract.

    Returns
    -------
    PgtResult
        Extracted urbanistic data. Check `result.warnings` for issues.
    """
    warnings: list[str] = []
    key = comune.lower().replace(" ", "_")

    # --- Check cache ---
    if not force_refresh:
        cached = _load_cache(comune)
        if cached is not None:
            return cached

    # --- Resolve registry entry ---
    entry = URL_REGISTRY.get(key)
    if not entry:
        warnings.append(
            f"Comune '{comune}' non presente nel registro URL. "
            f"Supportati: {', '.join(sorted(URL_REGISTRY.keys()))}"
        )
        return PgtResult(
            comune=comune,
            provincia=provincia,
            tipo_piano="UNKNOWN",
            warnings=warnings,
        )

    tipo_piano = entry.get("tipo_piano", "PGT")
    anno = entry.get("anno")
    landing = entry.get("landing", "")
    cartografia = entry.get("cartografia")
    source_urls = [landing] if landing else []

    # --- Download NTA PDF ---
    pdf_path = _download_nta(comune)
    if pdf_path is None:
        warnings.append(
            f"Impossibile scaricare NTA PDF per '{comune}'. "
            f"Verifica gli URL nel registro o scarica manualmente."
        )
        return PgtResult(
            comune=comune,
            provincia=provincia,
            tipo_piano=tipo_piano,
            anno_approvazione=anno,
            nta_url=entry["nta_urls"][0] if entry.get("nta_urls") else None,
            cartografia_url=cartografia,
            source_urls=source_urls,
            warnings=warnings,
        )

    source_urls.append(str(pdf_path))

    # --- Extract zones via LLM ---
    zones = extract_zones_from_pdf(pdf_path, comune, provincia, tipo_piano)

    if not zones:
        warnings.append(
            "Nessuna zona estratta. Il PDF potrebbe essere scansionato "
            "(OCR necessario) o il formato non e' supportato."
        )

    nta_url = entry["nta_urls"][0] if entry.get("nta_urls") else None

    result = PgtResult(
        comune=comune,
        provincia=provincia,
        tipo_piano=tipo_piano,
        anno_approvazione=anno,
        zone=zones,
        nta_url=nta_url,
        cartografia_url=cartografia,
        source_urls=source_urls,
        extraction_method="llm",
        warnings=warnings,
    )

    # --- Cache result ---
    _save_cache(result)

    return result


# ---------------------------------------------------------------------------
# CLI / __main__
# ---------------------------------------------------------------------------

def _print_result(result: PgtResult) -> None:
    """Pretty-print a PgtResult to stdout."""
    print(f"\n{'=' * 70}")
    print(f"  {result.comune} ({result.provincia})")
    print(f"  Piano: {result.tipo_piano}  |  Anno: {result.anno_approvazione or 'N/D'}")
    print(f"  Metodo: {result.extraction_method}")
    print(f"  NTA URL: {result.nta_url or 'N/D'}")
    print(f"  Cartografia: {result.cartografia_url or 'N/D'}")
    print(f"{'=' * 70}")

    if result.warnings:
        print("\n  AVVISI:")
        for w in result.warnings:
            print(f"    - {w}")

    if not result.zone:
        print("\n  Nessuna zona estratta.\n")
        return

    print(f"\n  Zone estratte: {len(result.zone)}\n")
    print(f"  {'Codice':<8} {'Denominazione':<40} {'IF':>6} {'Hmax':>6} {'Cop%':>6}")
    print(f"  {'-' * 8} {'-' * 40} {'-' * 6} {'-' * 6} {'-' * 6}")

    for z in result.zone:
        if_str = f"{z.indice_fondiario:.2f}" if z.indice_fondiario is not None else "—"
        h_str = f"{z.altezza_max_m:.1f}" if z.altezza_max_m is not None else "—"
        c_str = f"{z.copertura_max_pct:.0f}" if z.copertura_max_pct is not None else "—"
        print(f"  {z.codice:<8} {z.denominazione[:40]:<40} {if_str:>6} {h_str:>6} {c_str:>6}")

        if z.destinazioni_ammesse:
            print(f"           Dest. ammesse: {', '.join(z.destinazioni_ammesse[:5])}")
        if z.prescrizioni:
            print(f"           Prescrizioni: {z.prescrizioni[:80]}...")

    print()


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="PGT/PRG Parser")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout (for API bridge)")
    parser.add_argument("--comune", default="Gaeta")
    parser.add_argument("--provincia", default="LT")
    parser.add_argument("--force", action="store_true")
    # Backward compat positional args
    parser.add_argument("positional_args", nargs="*")
    args = parser.parse_args()

    target_comune = args.positional_args[0] if len(args.positional_args) > 0 else args.comune
    target_prov = args.positional_args[1] if len(args.positional_args) > 1 else args.provincia

    if not args.json:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    result = get_pgt(target_comune, target_prov, force_refresh=args.force)

    if args.json:
        print(json.dumps(asdict(result), default=str, ensure_ascii=False))
    else:
        print(f"\nPGT Parser — Extracting data for {target_comune} ({target_prov})")
        print(f"Force refresh: {args.force}\n")
        _print_result(result)
        json_out = _cache_path(target_comune)
        print(f"JSON cache: {json_out}")
