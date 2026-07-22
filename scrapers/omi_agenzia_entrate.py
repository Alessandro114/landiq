"""
OMI Agenzia Entrate scraper.

Endpoint (public, no auth):
    https://www1.agenziaentrate.gov.it/servizi/geopoi_omi/index.php
Landing page with form:
    https://www1.agenziaentrate.gov.it/servizi/Consultazione/ricerca.htm
Official docs (semestrale quotations, how to consult):
    https://www.agenziaentrate.gov.it/portale/schede/fabbricatiterreni/omi/banche-dati/quotazioni-immobiliari
PDF guide (form fields, zones encoding):
    https://www.agenziaentrate.gov.it/portale/documents/20143/264034/guida_cons_quotOMI_Guida+alla+Consultazione+delle+Quotazioni+OMI.pdf

Form fields (from Consultazione/ricerca.htm):
    - regione      (codice ISTAT)
    - provincia    (sigla 2-letter)
    - comune       (code or name)
    - zona OMI     (e.g. B1, B3, R1)  -- optional, defaults to all
    - tipologia    (e.g. "Abitazioni civili", "Alberghi e pensioni")
    - stato        ("NORMALE", "OTTIMO", "SCADENTE")
    - semestre     (e.g. "2025-2")

Multi-step form flow (discovered by reverse-engineering):
    level=0  ->  GET ricerca.htm?level=0  (province select)
    level=1  ->  POST pr=XX              (comune + semester select)
    level=2  ->  POST pr+co+anno_semestre (zone select via linkzonastrada)
    level=4  ->  POST linkzonastrada     (utilizzo select + hidden fields populated)
    bt1      ->  POST all hidden fields + utilizzo + bt1="Mostra valori"
               => returns HTML table with quotation data

Status:
    - V1: real scraper with requests.Session + multi-step POST + BS4 table parsing.
    - Mock fallback for Gaeta retained.
    - Caching layer with 30-day TTL in /app/data/omi_cache/.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests  # type: ignore
from bs4 import BeautifulSoup  # type: ignore

logger = logging.getLogger(__name__)

BASE_URL = "https://www1.agenziaentrate.gov.it/servizi/geopoi_omi/index.php"
SEARCH_URL = "https://www1.agenziaentrate.gov.it/servizi/Consultazione/ricerca.htm"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36 LandIQ/0.1"
)

CACHE_DIR = Path("/app/data/omi_cache")
CACHE_TTL_DAYS = 30

# Utilizzo types available in the OMI form
UTILIZZO_TYPES = ["Residenziale", "Commerciale", "Terziaria", "Produttiva"]


@dataclass
class OmiZone:
    code: str
    description: str
    residential_min: float | None
    residential_max: float | None
    rental_min: float | None
    rental_max: float | None
    touristic_min: float | None = None
    touristic_max: float | None = None
    state: str = "NORMALE"
    semester: str = ""


@dataclass
class OmiQuotation:
    """Single row from the OMI quotation table."""
    zone_code: str
    zone_description: str
    tipologia: str
    stato: str
    compravendita_min: float | None
    compravendita_max: float | None
    superficie_compravendita: str  # "L" (lorda) or "N" (netta)
    locazione_min: float | None
    locazione_max: float | None
    superficie_locazione: str  # "L" or "N"
    semester: str = ""


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "it-IT,it;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": SEARCH_URL,
    })
    return s


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------

def _cache_key(comune: str, provincia: str, semester: str) -> str:
    """Generate a filesystem-safe cache key."""
    safe_comune = re.sub(r'[^a-zA-Z0-9]', '_', comune.lower())
    safe_prov = provincia.upper()
    safe_sem = semester.replace("-", "")
    return f"{safe_comune}_{safe_prov}_{safe_sem}"


def _cache_path(comune: str, provincia: str, semester: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{_cache_key(comune, provincia, semester)}.json"


def _load_cache(comune: str, provincia: str, semester: str) -> dict[str, Any] | None:
    """Load cached result if exists and not expired."""
    path = _cache_path(comune, provincia, semester)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_at > timedelta(days=CACHE_TTL_DAYS):
            logger.info("Cache expired for %s %s %s", comune, provincia, semester)
            return None
        logger.info("Cache HIT for %s %s %s", comune, provincia, semester)
        return data
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("Cache read error for %s: %s", path, exc)
        return None


def _save_cache(comune: str, provincia: str, semester: str, data: dict[str, Any]) -> None:
    """Save result to cache with timestamp."""
    path = _cache_path(comune, provincia, semester)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data_with_meta = dict(data)
    data_with_meta["_cached_at"] = datetime.now().isoformat()
    try:
        path.write_text(json.dumps(data_with_meta, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Cached result to %s", path)
    except OSError as exc:
        logger.warning("Cache write error: %s", exc)


# ---------------------------------------------------------------------------
# Multi-step form scraper
# ---------------------------------------------------------------------------

def _normalize_semester(semester: str) -> str:
    """Convert '2025-2' or '2025-1' to '20252' or '20251' for the form."""
    s = semester.replace("-", "").replace(" ", "")
    # If already in compact form like '20252', return as-is
    if re.match(r'^\d{5}$', s):
        return s
    # If in form '2025-2', strip dash
    m = re.match(r'^(\d{4})-?(\d)$', semester.strip())
    if m:
        return m.group(1) + m.group(2)
    return s


def _parse_float_it(text: str) -> float | None:
    """Parse Italian number format (comma as decimal sep) to float."""
    if not text or text.strip() in ("-", "", "nd", "n.d."):
        return None
    cleaned = text.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _discover_comuni(
    session: requests.Session,
    provincia: str,
    semester: str,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """
    Navigate through form levels 0-2 to discover the comune code and available zones.

    Returns:
        (comuni_map, semester_options)
        comuni_map: {comune_name_upper: codice_catastale}
        semester_options: [(value, label), ...]
    """
    # Level 0: load initial page
    session.get(SEARCH_URL + "?level=0", timeout=30)

    # Level 1: POST province to get comuni list
    r1 = session.post(SEARCH_URL, data={
        "level": "1",
        "lingua": "IT",
        "pr": provincia.upper(),
    }, timeout=30)
    r1.raise_for_status()

    soup = BeautifulSoup(r1.text, "html.parser")
    form = soup.find("form", {"id": "IlForm1"})
    if not form:
        raise RuntimeError("Could not find IlForm1 in level 1 response")

    # Parse comuni
    co_select = form.find("select", {"name": "co"})
    comuni_map: dict[str, str] = {}
    if co_select:
        for opt in co_select.find_all("option"):
            val = opt.get("value", "")
            name = opt.get_text(strip=True).upper()
            if val:
                comuni_map[name] = val

    # Parse semesters
    sem_select = form.find("select", {"name": "anno_semestre"})
    semester_options: list[tuple[str, str]] = []
    if sem_select:
        for opt in sem_select.find_all("option"):
            semester_options.append((opt.get("value", ""), opt.get_text(strip=True)))

    return comuni_map, semester_options


def _discover_zones(
    session: requests.Session,
    provincia: str,
    comune_code: str,
    semester_code: str,
) -> list[tuple[str, str]]:
    """
    Navigate to level 2 to get available zones for a comune.

    Returns:
        [(zone_link_value, zone_description), ...]
    """
    r2 = session.post(SEARCH_URL, data={
        "level": "2",
        "lingua": "IT",
        "pr": provincia.upper(),
        "co": comune_code,
        "anno_semestre": semester_code,
    }, timeout=30)
    r2.raise_for_status()

    soup = BeautifulSoup(r2.text, "html.parser")
    form = soup.find("form", {"id": "IlForm1"})
    if not form:
        raise RuntimeError("Could not find IlForm1 in level 2 response")

    zone_select = form.find("select", {"name": "linkzonastrada"})
    zones: list[tuple[str, str]] = []
    if zone_select:
        for opt in zone_select.find_all("option"):
            val = opt.get("value", "")
            desc = opt.get_text(strip=True)
            if val:
                zones.append((val, desc))

    return zones


def _fetch_zone_quotations(
    session: requests.Session,
    provincia: str,
    comune_code: str,
    semester_code: str,
    zone_link: str,
    utilizzo: str = "Residenziale",
) -> tuple[dict[str, str], list[OmiQuotation]]:
    """
    Navigate through levels 4 to get quotation table for a specific zone.

    Returns:
        (zone_info, quotations)
        zone_info: dict with codzona, fasciazona, etc.
        quotations: list of parsed OmiQuotation records
    """
    # Re-navigate to level 2 to reset form state for this zone
    session.post(SEARCH_URL, data={
        "level": "2",
        "lingua": "IT",
        "pr": provincia.upper(),
        "co": comune_code,
        "anno_semestre": semester_code,
    }, timeout=30)

    # Level 4: select zone
    r3 = session.post(SEARCH_URL, data={
        "level": "4",
        "lingua": "IT",
        "pr": provincia.upper(),
        "co": comune_code,
        "anno_semestre": semester_code,
        "linkzonastrada": zone_link,
    }, timeout=30)
    r3.raise_for_status()

    soup3 = BeautifulSoup(r3.text, "html.parser")
    form3 = soup3.find("form", {"id": "IlForm1"})
    if not form3:
        raise RuntimeError("Could not find IlForm1 in level 4 response")

    # Collect hidden fields
    hidden_fields: dict[str, str] = {}
    for inp in form3.find_all("input", {"type": "hidden"}):
        name = inp.get("name", "")
        if name:
            hidden_fields[name] = inp.get("value", "")

    zone_info = {
        "codzona": hidden_fields.get("codzona", ""),
        "fasciazona": hidden_fields.get("fasciazona", ""),
        "linkzona": hidden_fields.get("linkzona", ""),
    }

    # Submit with utilizzo and button
    post_data = dict(hidden_fields)
    post_data["utilizzo"] = utilizzo
    post_data["bt1"] = "Mostra valori"

    # The form action is "risultato.php" (not ricerca.htm)
    form_action = form3.get("action", "risultato.php")
    if not form_action.startswith("http"):
        result_url = f"https://www1.agenziaentrate.gov.it/servizi/Consultazione/{form_action.lstrip('/')}"
    else:
        result_url = form_action

    r4 = session.post(result_url, data=post_data, timeout=30)
    r4.raise_for_status()

    quotations = _parse_quotation_table(
        r4.text,
        zone_code=zone_info["codzona"],
        zone_description=zone_info["fasciazona"],
        semester=semester_code,
    )

    return zone_info, quotations


def _parse_quotation_table(
    html: str,
    zone_code: str = "",
    zone_description: str = "",
    semester: str = "",
) -> list[OmiQuotation]:
    """
    Parse the OMI response HTML to extract quotation rows.

    Expected table columns:
        Tipologia | Stato conservativo | Min (compravendita) | Max (compravendita) |
        Superficie L/N | Min (locazione) | Max (locazione) | Superficie L/N
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        logger.warning("No tables found in quotation response")
        return []

    quotations: list[OmiQuotation] = []

    for table in tables:
        rows = table.find_all("tr")
        # Skip header rows (typically first 2 rows are headers)
        data_rows = []
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 6:  # Data rows have at least 6 cells
                data_rows.append(cells)

        for cells in data_rows:
            texts = [c.get_text(strip=True) for c in cells]
            if len(texts) < 8:
                # Pad with empty strings if fewer columns
                texts.extend([""] * (8 - len(texts)))

            tipologia = texts[0]
            stato = texts[1]
            comp_min = _parse_float_it(texts[2])
            comp_max = _parse_float_it(texts[3])
            sup_comp = texts[4] if texts[4] in ("L", "N") else "L"
            loc_min = _parse_float_it(texts[5])
            loc_max = _parse_float_it(texts[6])
            sup_loc = texts[7] if texts[7] in ("L", "N") else "N"

            quotations.append(OmiQuotation(
                zone_code=zone_code,
                zone_description=zone_description,
                tipologia=tipologia,
                stato=stato,
                compravendita_min=comp_min,
                compravendita_max=comp_max,
                superficie_compravendita=sup_comp,
                locazione_min=loc_min,
                locazione_max=loc_max,
                superficie_locazione=sup_loc,
                semester=semester,
            ))

    return quotations


# ---------------------------------------------------------------------------
# Quotation aggregation -> OmiZone
# ---------------------------------------------------------------------------

def _quotations_to_omi_zones(
    zone_code: str,
    zone_description: str,
    quotations: list[OmiQuotation],
    semester: str,
) -> OmiZone:
    """
    Aggregate OmiQuotation rows for a zone into a single OmiZone record.

    Uses "Abitazioni civili" NORMALE as primary residential values.
    Falls back to any "Abitazion*" row if "civili" is not found.
    Uses "Ville e Villini" as touristic proxy if available.
    """
    residential_min = None
    residential_max = None
    rental_min = None
    rental_max = None
    touristic_min = None
    touristic_max = None
    state = "NORMALE"

    # Find best residential row
    residential_row = None
    for q in quotations:
        if "civili" in q.tipologia.lower() and q.stato.upper() == "NORMALE":
            residential_row = q
            break
    if not residential_row:
        for q in quotations:
            if "abitazion" in q.tipologia.lower() and q.stato.upper() == "NORMALE":
                residential_row = q
                break
    if not residential_row:
        for q in quotations:
            if "abitazion" in q.tipologia.lower():
                residential_row = q
                break

    if residential_row:
        residential_min = residential_row.compravendita_min
        residential_max = residential_row.compravendita_max
        rental_min = residential_row.locazione_min
        rental_max = residential_row.locazione_max
        state = residential_row.stato

    # Find touristic row (Ville e Villini as proxy)
    for q in quotations:
        if "ville" in q.tipologia.lower() and q.stato.upper() == "NORMALE":
            touristic_min = q.compravendita_min
            touristic_max = q.compravendita_max
            break

    # Extract zone code from description if not provided
    # Description format: "Centrale/ZONA CENTRALE-VIA MARINA DI SERAPO..."
    parsed_code = zone_code
    if "/" in zone_description and not parsed_code:
        parsed_code = zone_description.split("/")[0].strip()

    return OmiZone(
        code=parsed_code or zone_code,
        description=zone_description,
        residential_min=residential_min,
        residential_max=residential_max,
        rental_min=rental_min,
        rental_max=rental_max,
        touristic_min=touristic_min,
        touristic_max=touristic_max,
        state=state,
        semester=semester,
    )


# ---------------------------------------------------------------------------
# Real scraper: full flow
# ---------------------------------------------------------------------------

def _scrape_omi_real(
    comune: str,
    provincia: str,
    zona: str | None = None,
    semester: str = "2025-2",
    utilizzo: str = "Residenziale",
) -> dict[str, Any]:
    """
    Real OMI scraper using multi-step form POST flow.

    Steps:
        1. Discover comuni for the province
        2. Find the comune code (codice catastale)
        3. Discover available zones
        4. For each zone (or filtered zone), fetch quotation table
        5. Parse and aggregate into OmiZone records
    """
    semester_code = _normalize_semester(semester)
    session = _session()

    # Step 1: Discover comuni
    logger.info("OMI scraper: discovering comuni for province %s", provincia)
    comuni_map, available_semesters = _discover_comuni(session, provincia, semester_code)

    if not comuni_map:
        raise RuntimeError(f"No comuni found for province {provincia}")

    # Step 2: Find comune code
    comune_upper = comune.upper().strip()
    comune_code = comuni_map.get(comune_upper)

    if not comune_code:
        # Try fuzzy match
        for name, code in comuni_map.items():
            if comune_upper in name or name in comune_upper:
                comune_code = code
                logger.info("Fuzzy matched comune '%s' -> '%s' (%s)", comune, name, code)
                break

    if not comune_code:
        available = sorted(comuni_map.keys())[:20]
        raise RuntimeError(
            f"Comune '{comune}' not found in province {provincia}. "
            f"Available: {', '.join(available)}..."
        )

    # Validate semester
    available_sem_codes = [s[0] for s in available_semesters]
    if semester_code not in available_sem_codes and available_sem_codes:
        logger.warning(
            "Semester %s not in available list. Available: %s. Using most recent.",
            semester_code, available_sem_codes[:5]
        )
        semester_code = available_sem_codes[0]  # Most recent

    # Step 3: Discover zones
    logger.info("OMI scraper: discovering zones for %s (%s)", comune, comune_code)
    available_zones = _discover_zones(session, provincia, comune_code, semester_code)

    if not available_zones:
        raise RuntimeError(f"No OMI zones found for {comune} ({provincia})")

    # Filter zones if zona parameter is provided
    target_zones = available_zones
    if zona:
        zona_upper = zona.upper().strip()
        target_zones = [
            (link, desc) for link, desc in available_zones
            if desc.upper().startswith(zona_upper + "/") or
               desc.upper().startswith(zona_upper + " ") or
               desc.split("/")[0].strip().upper() == zona_upper
        ]
        if not target_zones:
            logger.warning(
                "Zone '%s' not found. Available: %s. Scraping all zones.",
                zona, [d.split("/")[0] for _, d in available_zones]
            )
            target_zones = available_zones

    # Step 4: Fetch quotations for each zone
    all_quotations: list[OmiQuotation] = []
    omi_zones: list[OmiZone] = []

    for zone_link, zone_desc in target_zones:
        try:
            logger.info("OMI scraper: fetching quotations for zone %s", zone_desc[:60])
            zone_info, quotations = _fetch_zone_quotations(
                session, provincia, comune_code, semester_code, zone_link, utilizzo
            )
            all_quotations.extend(quotations)

            if quotations:
                omi_zone = _quotations_to_omi_zones(
                    zone_code=zone_info.get("codzona", ""),
                    zone_description=zone_info.get("fasciazona", ""),
                    quotations=quotations,
                    semester=semester,
                )
                omi_zones.append(omi_zone)
            else:
                logger.warning("No quotation rows for zone %s", zone_desc[:60])

            # Small delay to be polite to the server
            time.sleep(0.5)
        except Exception as exc:
            logger.error("Error fetching zone %s: %s", zone_desc[:60], exc)
            continue

    if not omi_zones:
        raise RuntimeError(
            f"No quotation data extracted for {comune} ({provincia}). "
            f"Zones attempted: {len(target_zones)}"
        )

    # Format semester back to display form
    sem_display = semester
    if len(semester_code) == 5:
        sem_display = f"{semester_code[:4]}-{semester_code[4]}"

    result: dict[str, Any] = {
        "comune": comune.title(),
        "provincia": provincia.upper(),
        "semester": sem_display,
        "zones": [asdict(z) for z in omi_zones],
        "raw_quotations": [asdict(q) for q in all_quotations],
        "source_url": SEARCH_URL,
        "scraper_version": "v1-real",
        "scraped_at": datetime.now().isoformat(),
        "mocked": False,
    }

    return result


# ---------------------------------------------------------------------------
# CSV fallback (for when web scraping is blocked)
# ---------------------------------------------------------------------------

def _try_csv_fallback(
    comune: str,
    provincia: str,
    semester: str = "2025-2",
) -> dict[str, Any] | None:
    """
    Attempt to download and parse OMI CSV datasets.

    The Agenzia Entrate publishes bulk CSV datasets at:
    https://www.agenziaentrate.gov.it/portale/web/guest/schede/fabbricatiterreni/omi/banche-dati/quotazioni-immobiliari

    CSV format (semicolon-separated):
        Area_territoriale;Regione;Prov;Comune_ISTAT;Comune_cat;Sez;
        Comune_amm;Comune_descrizione;Linkzona;Cod_Tip;Descr_Tipologia;
        Stato;Compr_min;Compr_max;Sup_NL_compr;Loc_min;Loc_max;Sup_NL_loc

    This function checks for locally cached CSV files first, then attempts download.
    """
    csv_dir = CACHE_DIR / "csv_bulk"
    csv_dir.mkdir(parents=True, exist_ok=True)

    semester_code = _normalize_semester(semester)
    year = semester_code[:4]
    sem_num = semester_code[4]

    # Check for local CSV file
    # Naming convention: OMI_{year}_{sem}.csv
    csv_patterns = [
        csv_dir / f"OMI_{year}_{sem_num}.csv",
        csv_dir / f"quotazioni_{year}_{sem_num}.csv",
        csv_dir / f"omi_quotazioni_{year}_s{sem_num}.csv",
    ]

    csv_file = None
    for pattern in csv_patterns:
        if pattern.exists():
            csv_file = pattern
            break

    if not csv_file:
        # Try to download from known URLs
        download_urls = [
            f"https://www1.agenziaentrate.gov.it/servizi/Consultazione/download/OMI_{year}_{sem_num}.csv",
            f"https://www.agenziaentrate.gov.it/portale/documents/20143/264034/OMI_{year}_S{sem_num}.csv",
        ]

        session = _session()
        for url in download_urls:
            try:
                logger.info("Trying CSV download: %s", url)
                resp = session.get(url, timeout=60, stream=True)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    csv_file = csv_dir / f"OMI_{year}_{sem_num}.csv"
                    csv_file.write_bytes(resp.content)
                    logger.info("Downloaded CSV to %s (%d bytes)", csv_file, len(resp.content))
                    break
            except Exception as exc:
                logger.debug("CSV download failed for %s: %s", url, exc)
                continue

    if not csv_file or not csv_file.exists():
        logger.info("No CSV data available for %s %s", year, sem_num)
        return None

    # Parse CSV
    return _parse_omi_csv(csv_file, comune, provincia, semester)


def _parse_omi_csv(
    csv_file: Path,
    comune: str,
    provincia: str,
    semester: str,
) -> dict[str, Any] | None:
    """Parse an OMI bulk CSV file and extract rows for a specific comune."""
    comune_upper = comune.upper().strip()
    provincia_upper = provincia.upper().strip()

    matching_rows: list[dict[str, str]] = []

    try:
        # Try different encodings
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                with open(csv_file, "r", encoding=encoding) as f:
                    # Detect delimiter
                    sample = f.read(2000)
                    f.seek(0)
                    delimiter = ";" if ";" in sample else ","

                    reader = csv.DictReader(f, delimiter=delimiter)
                    for row in reader:
                        # Match by province + comune name
                        row_prov = (row.get("Prov", "") or row.get("prov", "")).strip().upper()
                        row_comune = (
                            row.get("Comune_descrizione", "") or
                            row.get("Comune_amm", "") or
                            row.get("comune", "")
                        ).strip().upper()

                        if row_prov == provincia_upper and (
                            row_comune == comune_upper or
                            comune_upper in row_comune or
                            row_comune in comune_upper
                        ):
                            matching_rows.append(dict(row))
                break  # Encoding worked
            except UnicodeDecodeError:
                continue
    except Exception as exc:
        logger.error("CSV parse error for %s: %s", csv_file, exc)
        return None

    if not matching_rows:
        logger.info("No rows matching %s (%s) in CSV %s", comune, provincia, csv_file)
        return None

    # Group by zone and build OmiZone records
    zones_data: dict[str, list[dict[str, str]]] = {}
    for row in matching_rows:
        zone_key = row.get("Linkzona", row.get("linkzona", "unknown"))
        zones_data.setdefault(zone_key, []).append(row)

    omi_zones: list[OmiZone] = []
    for zone_key, rows in zones_data.items():
        # Find residential row
        res_row = None
        for r in rows:
            tip = (r.get("Descr_Tipologia", "") or r.get("tipologia", "")).lower()
            stato = (r.get("Stato", "") or r.get("stato", "")).upper()
            if "civili" in tip and stato == "NORMALE":
                res_row = r
                break
        if not res_row:
            for r in rows:
                tip = (r.get("Descr_Tipologia", "") or r.get("tipologia", "")).lower()
                if "abitazion" in tip:
                    res_row = r
                    break

        if res_row:
            zone_code = zone_key.split("/")[0] if "/" in zone_key else zone_key[-2:]
            zone_desc = zone_key

            omi_zones.append(OmiZone(
                code=zone_code,
                description=zone_desc,
                residential_min=_parse_float_it(res_row.get("Compr_min", "")),
                residential_max=_parse_float_it(res_row.get("Compr_max", "")),
                rental_min=_parse_float_it(res_row.get("Loc_min", "")),
                rental_max=_parse_float_it(res_row.get("Loc_max", "")),
                state=res_row.get("Stato", "NORMALE"),
                semester=semester,
            ))

    if not omi_zones:
        return None

    sem_display = semester
    semester_code = _normalize_semester(semester)
    if len(semester_code) == 5:
        sem_display = f"{semester_code[:4]}-{semester_code[4]}"

    return {
        "comune": comune.title(),
        "provincia": provincia.upper(),
        "semester": sem_display,
        "zones": [asdict(z) for z in omi_zones],
        "source_url": str(csv_file),
        "scraper_version": "v1-csv-fallback",
        "scraped_at": datetime.now().isoformat(),
        "mocked": False,
        "csv_source": True,
    }


# ---------------------------------------------------------------------------
# GeoPOI fallback
# ---------------------------------------------------------------------------

GEOPOI_URL = "https://www1.agenziaentrate.gov.it/servizi/geopoi_omi/index.php"


def _try_geopoi_fallback(
    comune: str,
    provincia: str,
    semester: str = "2025-2",
) -> dict[str, Any] | None:
    """
    Attempt to use the GeoPOI endpoint as fallback.

    The GeoPOI interface at geopoi_omi/index.php is primarily JS-rendered
    and may not work with simple requests. This tries common API patterns
    that the JS frontend uses internally.
    """
    session = _session()

    # The GeoPOI app uses internal AJAX calls. Try known patterns.
    semester_code = _normalize_semester(semester)

    # Try the search API endpoint
    search_endpoints = [
        f"{GEOPOI_URL}?action=ricerca&comune={comune}&provincia={provincia}&semestre={semester_code}",
        f"https://www1.agenziaentrate.gov.it/servizi/geopoi_omi/ricerca.php?comune={comune}&provincia={provincia}",
    ]

    for url in search_endpoints:
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200 and len(resp.text) > 500:
                # Check if it contains JSON data
                try:
                    data = resp.json()
                    if data and isinstance(data, (dict, list)):
                        logger.info("GeoPOI returned JSON data from %s", url)
                        # TODO: Parse GeoPOI JSON format if we discover its structure
                        return None  # Placeholder until format is known
                except (json.JSONDecodeError, ValueError):
                    pass

                # Check if it contains HTML with tables
                soup = BeautifulSoup(resp.text, "html.parser")
                tables = soup.find_all("table")
                if tables:
                    quotations = _parse_quotation_table(resp.text)
                    if quotations:
                        logger.info("GeoPOI returned quotation table from %s", url)
                        # Convert to standard format
                        # (would need zone info which we don't have here)
                        return None  # Placeholder
        except Exception as exc:
            logger.debug("GeoPOI endpoint %s failed: %s", url, exc)
            continue

    logger.info("GeoPOI fallback: no usable data found for %s (%s)", comune, provincia)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_omi(
    comune: str,
    provincia: str,
    zona: str | None = None,
    tipologia: str = "Abitazioni civili",
    semester: str = "2025-2",
) -> dict[str, Any]:
    """Fetch OMI quotations for a comune.

    Returns a dict compatible with LandIQEngine.fetch_omi() contract.

    Strategy (in order):
        1. Check cache (30-day TTL)
        2. Real scraper via multi-step form POST
        3. GeoPOI endpoint fallback
        4. CSV bulk dataset fallback
        5. Mock fallback (Gaeta only)
    """
    # --- 1. Check cache ---
    cached = _load_cache(comune, provincia, semester)
    if cached:
        # Remove internal cache metadata before returning
        result = {k: v for k, v in cached.items() if not k.startswith("_")}
        # If zona filter requested, filter cached zones
        if zona and "zones" in result:
            zona_upper = zona.upper().strip()
            filtered = [z for z in result["zones"] if z.get("code", "").upper() == zona_upper]
            if filtered:
                result["zones"] = filtered
        return result

    # --- 2. Real scraper ---
    try:
        logger.info("OMI real scraper: starting for %s (%s) semester %s", comune, provincia, semester)
        result = _scrape_omi_real(
            comune=comune,
            provincia=provincia,
            zona=zona,
            semester=semester,
            utilizzo="Residenziale",
        )
        # Cache the result
        _save_cache(comune, provincia, semester, result)
        logger.info(
            "OMI real scraper: SUCCESS for %s (%s) - %d zones extracted",
            comune, provincia, len(result.get("zones", []))
        )
        return result
    except Exception as exc:
        logger.warning("OMI real scraper failed for %s (%s): %s", comune, provincia, exc)

    # --- 3. GeoPOI fallback ---
    try:
        geopoi_result = _try_geopoi_fallback(comune, provincia, semester)
        if geopoi_result:
            _save_cache(comune, provincia, semester, geopoi_result)
            logger.info("OMI GeoPOI fallback: SUCCESS for %s (%s)", comune, provincia)
            return geopoi_result
    except Exception as exc:
        logger.warning("OMI GeoPOI fallback failed for %s (%s): %s", comune, provincia, exc)

    # --- 4. CSV fallback ---
    try:
        csv_result = _try_csv_fallback(comune, provincia, semester)
        if csv_result:
            _save_cache(comune, provincia, semester, csv_result)
            logger.info("OMI CSV fallback: SUCCESS for %s (%s)", comune, provincia)
            return csv_result
    except Exception as exc:
        logger.warning("OMI CSV fallback failed for %s (%s): %s", comune, provincia, exc)

    # --- 5. Mock fallback (Gaeta only) ---
    if comune.lower() == "gaeta":
        logger.info("OMI falling back to mock for Gaeta")
        result = _mock_gaeta(semester=semester)
        return result

    # --- 6. National average fallback for ANY city ---
    logger.warning(
        "All OMI scraping strategies failed for %s (%s). Using national average fallback.",
        comune, provincia,
    )
    return {
        "comune": comune,
        "provincia": provincia,
        "semestre": semester,
        "scraper_version": "v1-national-avg-fallback",
        "note": f"National average estimates — real OMI data unavailable for {comune}",
        "mocked": True,
        "zones": [
            {
                "code": "B1",
                "description": f"Zona centrale - {comune} (stima media nazionale)",
                "min_eur_sqm": 1200.0,
                "max_eur_sqm": 2500.0,
                "loc_eur_sqm_month": 5.5,
                "tipologia": "Abitazioni civili",
                "stato": "NORMALE",
            },
            {
                "code": "C1",
                "description": f"Zona semicentrale - {comune} (stima media nazionale)",
                "min_eur_sqm": 900.0,
                "max_eur_sqm": 1800.0,
                "loc_eur_sqm_month": 4.5,
                "tipologia": "Abitazioni civili",
                "stato": "NORMALE",
            },
            {
                "code": "D1",
                "description": f"Zona periferica - {comune} (stima media nazionale)",
                "min_eur_sqm": 600.0,
                "max_eur_sqm": 1200.0,
                "loc_eur_sqm_month": 3.5,
                "tipologia": "Abitazioni civili",
                "stato": "NORMALE",
            },
        ],
    }


def fetch_omi_all_zones(
    comune: str,
    provincia: str,
    semester: str = "2025-2",
) -> dict[str, Any]:
    """Convenience: fetch OMI data for ALL zones of a comune."""
    return fetch_omi(comune=comune, provincia=provincia, zona=None, semester=semester)


def list_available_zones(
    comune: str,
    provincia: str,
    semester: str = "2025-2",
) -> list[dict[str, str]]:
    """
    List available OMI zones for a comune without fetching quotation data.

    Returns list of dicts with keys: link, code, fascia, description.
    """
    semester_code = _normalize_semester(semester)
    session = _session()

    comuni_map, _ = _discover_comuni(session, provincia, semester_code)
    comune_code = comuni_map.get(comune.upper().strip())
    if not comune_code:
        raise RuntimeError(f"Comune '{comune}' not found in province {provincia}")

    zones = _discover_zones(session, provincia, comune_code, semester_code)

    result = []
    for link, desc in zones:
        parts = desc.split("/", maxsplit=2)
        result.append({
            "link": link,
            "code": parts[0].strip() if len(parts) > 0 else "",
            "fascia": parts[1].strip() if len(parts) > 1 else "",
            "description": parts[2].strip() if len(parts) > 2 else desc,
        })

    return result


def list_comuni(provincia: str, semester: str = "2025-2") -> list[dict[str, str]]:
    """
    List all comuni available for a province.

    Returns list of dicts with keys: code, name.
    """
    semester_code = _normalize_semester(semester)
    session = _session()
    comuni_map, _ = _discover_comuni(session, provincia, semester_code)
    return [{"code": code, "name": name} for name, code in sorted(comuni_map.items())]


# ---------------------------------------------------------------------------
# Mock data (kept as fallback)
# ---------------------------------------------------------------------------

def _mock_gaeta(semester: str = "2025-2") -> dict[str, Any]:
    """Verified data aggregated from public sources 11 April 2026.

    Sources:
      - mercato-immobiliare.info/lazio/latina/gaeta.html
      - immobiliare.it/mercato-immobiliare/lazio/gaeta/
    OMI raw values confirmed: Zone B3 includes Via Marina di Serapo.
    """
    zones = [
        OmiZone(
            code="B3",
            description="ZONA CENTRALE - Via Marina di Serapo, Via Fontania, Via Garibaldi",
            residential_min=1900.0,
            residential_max=2780.0,
            rental_min=8.0,
            rental_max=12.5,
            # touristic values NOT publicly aggregated — placeholder heuristic (~60% of residential)
            touristic_min=1200.0,
            touristic_max=1800.0,
            semester=semester,
        ),
        OmiZone(
            code="R1",
            description="Zona agricola del comune",
            residential_min=1130.0,
            residential_max=1500.0,
            rental_min=5.3,
            rental_max=7.5,
            semester=semester,
        ),
    ]
    return {
        "comune": "Gaeta",
        "provincia": "LT",
        "semester": semester,
        "zones": [asdict(z) for z in zones],
        "asking_prices": {
            # from immobiliare.it September 2025 / February 2025 peak
            "city_avg_eur_sqm": 3318,
            "city_peak_eur_sqm": 3340,
            "serapo_avg_eur_sqm": 3930,
            "serapo_min_eur_sqm": 2465,
            "serapo_max_eur_sqm": 4500,
            "serapo_seaview_avg_eur_sqm": 4411,
        },
        "source_url": BASE_URL,
        "sources_aux": [
            "https://www.mercato-immobiliare.info/lazio/latina/gaeta.html",
            "https://www.immobiliare.it/mercato-immobiliare/lazio/gaeta/",
        ],
        "mocked": True,
        "mocked_reason": "MVP skeleton — real scraper pending. Values manually verified 11 Apr 2026.",
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="OMI Agenzia Entrate scraper")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout (for API bridge)")
    parser.add_argument("--comune", default="Gaeta")
    parser.add_argument("--provincia", default="LT")
    parser.add_argument("--zona", default=None)
    parser.add_argument("--semester", default="2025-2")
    # Also accept positional args for backward compatibility
    parser.add_argument("positional_args", nargs="*")
    args = parser.parse_args()

    # Backward compat: positional args override named args
    comune = args.positional_args[0] if len(args.positional_args) > 0 else args.comune
    provincia = args.positional_args[1] if len(args.positional_args) > 1 else args.provincia
    zona = args.positional_args[2] if len(args.positional_args) > 2 else args.zona
    semester = args.positional_args[3] if len(args.positional_args) > 3 else args.semester

    if not args.json:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    data = fetch_omi(comune, provincia, zona=zona, semester=semester)

    if args.json:
        print(json.dumps(data, default=str, ensure_ascii=False))
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        print(f"\n{'='*60}")
        print(f"  OMI Scraper - {comune} ({provincia})")
        print(f"  Zona: {zona or 'ALL'}  |  Semester: {semester}")
        print(f"{'='*60}\n")

        print(json.dumps(data, indent=2, ensure_ascii=False))

        # Print summary
        zones = data.get("zones", [])
        print(f"\n--- Summary: {len(zones)} zone(s) ---")
        for z in zones:
            print(
                f"  {z['code']:4s} | "
                f"€{z.get('residential_min', 'n/a')}-{z.get('residential_max', 'n/a')}/mq | "
                f"Rent €{z.get('rental_min', 'n/a')}-{z.get('rental_max', 'n/a')}/mq/mese | "
                f"{z.get('state', '')}"
            )
        print(f"  Mocked: {data.get('mocked', 'unknown')}")
        print(f"  Source: {data.get('scraper_version', data.get('source_url', ''))}")
