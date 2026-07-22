"""
PVP (Portale delle Vendite Pubbliche) scraper for Italian judicial auctions.

Source: https://pvp.giustizia.it/pvp/
Public government website — scraping is legal.

The PVP portal exposes a public search interface. This scraper:
  1. Tries the JSON API at /pvp/it/ricerca_avanzata (POST with form data)
  2. Falls back to HTML scraping if the API is not directly accessible
  3. Caches results locally with 24h TTL

Rate-limited to 1 request/second with polite User-Agent.
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
from typing import Any, Optional
from urllib.parse import urljoin, urlencode, quote

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://pvp.giustizia.it/pvp/"
SEARCH_URL = "https://pvp.giustizia.it/pvp/it/ricerca_avanzata"
SEARCH_API_URL = "https://pvp.giustizia.it/pvp/api/ricerca"
DETAIL_URL_TPL = "https://pvp.giustizia.it/pvp/it/dettaglio_annuncio/{auction_id}"

CACHE_DIR = Path("/app/data/pvp_cache")
CACHE_TTL_HOURS = 24

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36 LandIQ/0.2 "
    "(research bot; ale@get-scala.com)"
)

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RATE_LIMIT_SECONDS = 1.0

LOG = logging.getLogger("pvp_aste")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

# ---------------------------------------------------------------------------
# Regions / property types mappings (PVP codes)
# ---------------------------------------------------------------------------

REGIONS = {
    "abruzzo": "13", "basilicata": "17", "calabria": "18",
    "campania": "15", "emilia-romagna": "08", "friuli-venezia-giulia": "06",
    "lazio": "12", "liguria": "07", "lombardia": "03", "marche": "11",
    "molise": "14", "piemonte": "01", "puglia": "16", "sardegna": "20",
    "sicilia": "19", "toscana": "09", "trentino-alto-adige": "04",
    "umbria": "10", "valle-d-aosta": "02", "veneto": "05",
}

PROPERTY_TYPES = {
    "immobile": "IMMOBILE",
    "terreno": "TERRENO",
    "bene_mobile": "BENE_MOBILE",
    "azienda": "AZIENDA",
    "altro": "ALTRO",
}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class PvpAuction:
    """Single judicial auction listing from PVP."""

    id: str = ""
    tribunal: str = ""
    procedure_number: str = ""
    title: str = ""
    description: str = ""
    property_type: str = ""
    address: str = ""
    city: str = ""
    province: str = ""
    region: str = ""
    base_price: Optional[float] = None
    minimum_offer: Optional[float] = None
    appraisal_value: Optional[float] = None
    auction_date: Optional[str] = None
    deadline: Optional[str] = None
    surface_sqm: Optional[float] = None
    rooms: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    source_url: str = ""
    photos: list[str] = field(default_factory=list)
    custodian_info: str = ""
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PvpAuction:
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_key(params: dict[str, Any]) -> str:
    """Deterministic cache key from search params."""
    raw = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def _load_cache(key: str) -> Optional[list[dict]]:
    """Load cached results if fresh (< CACHE_TTL_HOURS)."""
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text("utf-8"))
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        if datetime.now() - cached_at > timedelta(hours=CACHE_TTL_HOURS):
            LOG.info("Cache expired for key %s", key)
            return None
        LOG.info("Cache hit for key %s (%d items)", key, len(data.get("results", [])))
        return data["results"]
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def _save_cache(key: str, results: list[dict]) -> None:
    """Persist results with timestamp."""
    p = _cache_path(key)
    p.write_text(
        json.dumps(
            {"cached_at": datetime.now().isoformat(), "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    LOG.info("Cached %d results to %s", len(results), p)


# ---------------------------------------------------------------------------
# HTTP session with retry + rate-limit
# ---------------------------------------------------------------------------


class PvpSession:
    """Managed requests.Session with retries and rate limiting."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
        self._last_request_ts: float = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_ts
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)

    def get(self, url: str, **kwargs) -> requests.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        last_exc: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            self._rate_limit()
            try:
                LOG.debug("%s %s (attempt %d)", method, url, attempt)
                resp = self.session.request(method, url, **kwargs)
                self._last_request_ts = time.time()
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                wait = 2 ** attempt
                LOG.warning(
                    "Request failed (%s), retry %d/%d in %ds: %s",
                    exc, attempt, MAX_RETRIES, wait, url,
                )
                time.sleep(wait)

        raise ConnectionError(
            f"Failed after {MAX_RETRIES} retries: {url}"
        ) from last_exc


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(r"[\d.,]+")


def _parse_price(text: str | None) -> Optional[float]:
    """Extract a float price from text like '€ 123.456,78'."""
    if not text:
        return None
    text = text.replace(".", "").replace(",", ".")
    m = _PRICE_RE.search(text)
    if m:
        try:
            return float(m.group())
        except ValueError:
            return None
    return None


def _parse_date(text: str | None) -> Optional[str]:
    """Parse Italian date formats to ISO string."""
    if not text:
        return None
    text = text.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return text


def _clean(text: str | None) -> str:
    """Strip and collapse whitespace."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _extract_sqm(text: str) -> Optional[float]:
    """Extract surface in sqm from description text."""
    m = re.search(r"(\d+[.,]?\d*)\s*(?:mq|m²|metri\s*quadr)", text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    return None


def _extract_rooms(text: str) -> Optional[int]:
    """Extract number of rooms/vani from text."""
    m = re.search(r"(\d+)\s*(?:vani|locali|stanze|camere)", text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Core scraping logic
# ---------------------------------------------------------------------------

_session: Optional[PvpSession] = None


def _get_session() -> PvpSession:
    global _session
    if _session is None:
        _session = PvpSession()
    return _session


def _try_api_search(params: dict[str, Any], page: int = 0,
                    page_size: int = 25) -> Optional[list[dict]]:
    """
    Attempt to use the PVP JSON API endpoint.
    Returns raw list of dicts if successful, None if API is not available.
    """
    session = _get_session()
    body: dict[str, Any] = {
        "tipoBene": params.get("property_type", "IMMOBILE"),
        "pagina": page,
        "elementiPerPagina": page_size,
        "ordinamento": "DATA_VENDITA",
        "direzioneOrdinamento": "ASC",
    }

    if params.get("region"):
        region_key = params["region"].lower().replace(" ", "-")
        body["codiceRegione"] = REGIONS.get(region_key, params["region"])
    if params.get("province"):
        body["siglaProvincia"] = params["province"].upper()
    if params.get("city"):
        body["comune"] = params["city"]
    if params.get("min_price") is not None:
        body["prezzoMinimo"] = params["min_price"]
    if params.get("max_price") is not None:
        body["prezzoMassimo"] = params["max_price"]

    try:
        resp = session.post(
            SEARCH_API_URL,
            json=body,
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()
        if isinstance(data, dict) and "risultati" in data:
            return data["risultati"]
        if isinstance(data, list):
            return data
        LOG.info("API returned unexpected structure: %s", list(data.keys()) if isinstance(data, dict) else type(data))
        return None
    except (requests.RequestException, json.JSONDecodeError, ConnectionError) as exc:
        LOG.info("API endpoint not available (%s), falling back to HTML scraping", exc)
        return None


def _build_search_url(params: dict[str, Any], page: int = 1) -> str:
    """Build the HTML search URL with query params for fallback scraping."""
    qp: dict[str, str] = {
        "tipo_bene": params.get("property_type", "IMMOBILE"),
        "pagina": str(page),
    }
    if params.get("region"):
        region_key = params["region"].lower().replace(" ", "-")
        qp["regione"] = REGIONS.get(region_key, params["region"])
    if params.get("province"):
        qp["provincia"] = params["province"].upper()
    if params.get("city"):
        qp["comune"] = params["city"]
    if params.get("min_price") is not None:
        qp["prezzo_min"] = str(int(params["min_price"]))
    if params.get("max_price") is not None:
        qp["prezzo_max"] = str(int(params["max_price"]))

    return f"{SEARCH_URL}?{urlencode(qp)}"


def _parse_search_results_html(html: str) -> list[PvpAuction]:
    """Parse a PVP search results page into PvpAuction objects."""
    soup = BeautifulSoup(html, "html.parser")
    auctions: list[PvpAuction] = []

    # PVP uses various container classes; try common patterns
    cards = (
        soup.select(".risultato-ricerca, .annuncio-card, .card-annuncio") or
        soup.select("div[class*='risultat'], div[class*='annunci']") or
        soup.select("article, .list-group-item")
    )

    if not cards:
        # Try table rows as fallback
        rows = soup.select("table tbody tr")
        for row in rows:
            cols = row.select("td")
            if len(cols) < 3:
                continue
            auction = _parse_table_row(cols)
            if auction:
                auctions.append(auction)
        if auctions:
            return auctions

        # Last resort: look for any links to detail pages
        links = soup.select("a[href*='dettaglio'], a[href*='annuncio']")
        for link in links:
            href = link.get("href", "")
            auction_id = _extract_id_from_url(href)
            if auction_id:
                auctions.append(PvpAuction(
                    id=auction_id,
                    title=_clean(link.get_text()),
                    source_url=urljoin(BASE_URL, href),
                ))
        return auctions

    for card in cards:
        auction = _parse_card(card)
        if auction and auction.id:
            auctions.append(auction)

    return auctions


def _parse_card(card: BeautifulSoup) -> Optional[PvpAuction]:
    """Parse a single result card element."""
    a = PvpAuction()

    # ID from link
    link = card.select_one("a[href*='dettaglio'], a[href*='annuncio']")
    if link:
        href = link.get("href", "")
        a.id = _extract_id_from_url(href)
        a.source_url = urljoin(BASE_URL, href)
        a.title = _clean(link.get_text())

    # Title fallback
    if not a.title:
        title_el = card.select_one("h3, h4, h5, .titolo, .title, strong")
        if title_el:
            a.title = _clean(title_el.get_text())

    # Tribunal
    trib_el = card.select_one("[class*='tribunal'], [class*='ufficio']")
    if trib_el:
        a.tribunal = _clean(trib_el.get_text())
    else:
        trib_match = re.search(
            r"(?:Tribunale|Trib\.)\s+(?:di\s+)?(\w[\w\s]+)",
            card.get_text(), re.IGNORECASE,
        )
        if trib_match:
            a.tribunal = _clean(trib_match.group(0))

    # Price
    price_el = card.select_one("[class*='prezz'], [class*='price'], [class*='base']")
    if price_el:
        a.base_price = _parse_price(price_el.get_text())
    else:
        price_match = re.search(
            r"(?:prezzo\s*base|base\s*d.asta)[:\s]*[€]?\s*([\d.,]+)",
            card.get_text(), re.IGNORECASE,
        )
        if price_match:
            a.base_price = _parse_price(price_match.group(1))

    # Minimum offer
    min_match = re.search(
        r"(?:offerta\s*minima)[:\s]*[€]?\s*([\d.,]+)",
        card.get_text(), re.IGNORECASE,
    )
    if min_match:
        a.minimum_offer = _parse_price(min_match.group(1))

    # Location
    loc_el = card.select_one("[class*='luogo'], [class*='location'], [class*='indirizzo']")
    loc_text = _clean(loc_el.get_text()) if loc_el else ""
    if loc_text:
        a.address = loc_text
    else:
        loc_match = re.search(
            r"(?:ubicazione|indirizzo|luogo)[:\s]*([^\n]+)",
            card.get_text(), re.IGNORECASE,
        )
        if loc_match:
            a.address = _clean(loc_match.group(1))

    # Province from parentheses like "Roma (RM)"
    prov_match = re.search(r"\(([A-Z]{2})\)", card.get_text())
    if prov_match:
        a.province = prov_match.group(1)

    # Date
    date_el = card.select_one("[class*='data'], [class*='date']")
    if date_el:
        a.auction_date = _parse_date(date_el.get_text())
    else:
        date_match = re.search(
            r"(\d{2}[/-]\d{2}[/-]\d{4}(?:\s+\d{2}:\d{2})?)",
            card.get_text(),
        )
        if date_match:
            a.auction_date = _parse_date(date_match.group(1))

    # Property type
    type_el = card.select_one("[class*='tipo'], [class*='categoria']")
    if type_el:
        a.property_type = _clean(type_el.get_text())

    # Image
    img = card.select_one("img[src]")
    if img:
        src = img.get("src", "")
        if src and not src.endswith((".svg", ".gif")):
            a.photos.append(urljoin(BASE_URL, src))

    # Status
    status_el = card.select_one("[class*='stato'], [class*='status'], .badge")
    if status_el:
        a.status = _clean(status_el.get_text())

    # Extract sqm/rooms from full text
    full_text = card.get_text()
    a.surface_sqm = _extract_sqm(full_text)
    a.rooms = _extract_rooms(full_text)

    return a if (a.id or a.title) else None


def _parse_table_row(cols: list) -> Optional[PvpAuction]:
    """Parse a table row into an auction (fallback for table-based layouts)."""
    a = PvpAuction()
    texts = [_clean(c.get_text()) for c in cols]

    # Try to find a link for ID
    for c in cols:
        link = c.select_one("a[href]")
        if link:
            href = link.get("href", "")
            a.id = _extract_id_from_url(href)
            a.source_url = urljoin(BASE_URL, href)
            break

    if len(texts) >= 1:
        a.title = texts[0]
    if len(texts) >= 2:
        a.tribunal = texts[1]
    if len(texts) >= 3:
        a.base_price = _parse_price(texts[2])
    if len(texts) >= 4:
        a.auction_date = _parse_date(texts[3])
    if len(texts) >= 5:
        a.status = texts[4]

    return a if (a.id or a.title) else None


def _extract_id_from_url(url: str) -> str:
    """Extract auction ID from a PVP URL."""
    # Pattern: /dettaglio_annuncio/12345 or ?id=12345
    m = re.search(r"(?:dettaglio|annuncio)[/_](\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=(\d+)", url)
    if m:
        return m.group(1)
    # Use last numeric segment
    m = re.search(r"/(\d{4,})(?:[/?#]|$)", url)
    if m:
        return m.group(1)
    return ""


def _has_next_page(html: str) -> bool:
    """Check if there is a next page of results."""
    soup = BeautifulSoup(html, "html.parser")
    # Look for pagination links
    next_link = soup.select_one(
        "a[class*='next'], a[aria-label='Next'], "
        "li.next a, a[rel='next'], .pagination .active + li a"
    )
    if next_link:
        return True
    # Check for page number pattern
    pages = soup.select(".pagination li, .pager a, [class*='pagin'] a")
    return len(pages) > 1


def _parse_api_result(item: dict) -> PvpAuction:
    """Convert a JSON API result item to PvpAuction."""
    a = PvpAuction()
    a.id = str(item.get("id", item.get("idAnnuncio", item.get("codice", ""))))
    a.tribunal = item.get("tribunale", item.get("ufficio", ""))
    a.procedure_number = item.get("numeroProcedura", item.get("rge", ""))
    a.title = item.get("titolo", item.get("descrizioneBreve", ""))
    a.description = item.get("descrizione", item.get("descrizioneLunga", ""))
    a.property_type = item.get("tipoBene", item.get("categoria", ""))
    a.address = item.get("indirizzo", "")
    a.city = item.get("comune", item.get("citta", ""))
    a.province = item.get("provincia", item.get("siglaProvincia", ""))
    a.region = item.get("regione", "")
    a.base_price = _safe_float(item.get("prezzoBase", item.get("valoreBase")))
    a.minimum_offer = _safe_float(item.get("offertaMinima"))
    a.appraisal_value = _safe_float(item.get("valoreStima", item.get("valorePerizia")))
    a.auction_date = item.get("dataVendita", item.get("dataAsta"))
    a.deadline = item.get("dataScadenza", item.get("terminePresentazione"))
    a.surface_sqm = _safe_float(item.get("superficie", item.get("mq")))
    a.rooms = _safe_int(item.get("vani", item.get("locali")))
    a.latitude = _safe_float(item.get("latitudine", item.get("lat")))
    a.longitude = _safe_float(item.get("longitudine", item.get("lng", item.get("lon"))))
    a.status = item.get("stato", item.get("statoVendita", ""))
    a.custodian_info = item.get("custode", item.get("delegato", ""))

    # Source URL
    if a.id:
        a.source_url = DETAIL_URL_TPL.format(auction_id=a.id)

    # Photos
    foto = item.get("foto", item.get("immagini", []))
    if isinstance(foto, list):
        for f in foto:
            if isinstance(f, str):
                a.photos.append(urljoin(BASE_URL, f))
            elif isinstance(f, dict):
                url = f.get("url", f.get("src", ""))
                if url:
                    a.photos.append(urljoin(BASE_URL, url))

    # Try extracting sqm/rooms from description if not in fields
    if a.description:
        if a.surface_sqm is None:
            a.surface_sqm = _extract_sqm(a.description)
        if a.rooms is None:
            a.rooms = _extract_rooms(a.description)

    return a


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return _parse_price(str(v))


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_auctions(
    region: str = "",
    province: str = "",
    city: str = "",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    property_type: str = "immobile",
    max_pages: int = 5,
) -> list[PvpAuction]:
    """
    Search PVP for judicial auctions matching the given filters.

    Args:
        region:        Italian region name (e.g. 'lazio')
        province:      Province code (e.g. 'RM')
        city:          City name (e.g. 'Roma')
        min_price:     Minimum base price in EUR
        max_price:     Maximum base price in EUR
        property_type: One of: immobile, terreno, bene_mobile, azienda, altro
        max_pages:     Maximum pages to fetch (default 5)

    Returns:
        List of PvpAuction dataclass instances.
    """
    params = {
        "region": region,
        "province": province,
        "city": city,
        "min_price": min_price,
        "max_price": max_price,
        "property_type": PROPERTY_TYPES.get(property_type.lower(), property_type.upper()),
    }

    # Check cache
    ck = _cache_key(params)
    cached = _load_cache(ck)
    if cached is not None:
        return [PvpAuction.from_dict(d) for d in cached]

    LOG.info("Searching PVP auctions: %s", {k: v for k, v in params.items() if v})

    all_auctions: list[PvpAuction] = []

    # --- Strategy 1: Try JSON API ---
    api_results = _try_api_search(params, page=0)
    if api_results is not None:
        LOG.info("API returned %d results on first page", len(api_results))
        for item in api_results:
            all_auctions.append(_parse_api_result(item))

        # Paginate API
        page = 1
        while len(api_results) >= 25 and page < max_pages:
            api_results = _try_api_search(params, page=page)
            if not api_results:
                break
            for item in api_results:
                all_auctions.append(_parse_api_result(item))
            page += 1
            LOG.info("API page %d: %d results (total: %d)", page, len(api_results), len(all_auctions))

    else:
        # --- Strategy 2: HTML scraping fallback ---
        LOG.info("Falling back to HTML scraping")
        session = _get_session()

        # First, hit the base page to get cookies/session
        try:
            session.get(BASE_URL)
        except ConnectionError:
            LOG.warning("Could not reach PVP base URL")

        for page in range(1, max_pages + 1):
            url = _build_search_url(params, page=page)
            LOG.info("Fetching page %d: %s", page, url)

            try:
                resp = session.get(url)
                page_auctions = _parse_search_results_html(resp.text)
                if not page_auctions:
                    LOG.info("No results on page %d, stopping", page)
                    break
                all_auctions.extend(page_auctions)
                LOG.info("Page %d: %d results (total: %d)", page, len(page_auctions), len(all_auctions))

                if not _has_next_page(resp.text):
                    break
            except ConnectionError as exc:
                LOG.error("Failed to fetch page %d: %s", page, exc)
                break

    # Deduplicate by ID
    seen: set[str] = set()
    unique: list[PvpAuction] = []
    for a in all_auctions:
        key = a.id or a.title
        if key and key not in seen:
            seen.add(key)
            unique.append(a)

    LOG.info("Total unique auctions found: %d", len(unique))

    # Cache
    _save_cache(ck, [a.to_dict() for a in unique])

    return unique


def get_auction_detail(auction_id: str) -> PvpAuction:
    """
    Fetch full detail for a single auction by its PVP ID.

    Args:
        auction_id: The PVP auction ID (numeric string).

    Returns:
        PvpAuction with all available fields populated.
    """
    # Check cache
    ck = _cache_key({"detail": auction_id})
    cached = _load_cache(ck)
    if cached and len(cached) > 0:
        return PvpAuction.from_dict(cached[0])

    session = _get_session()
    url = DETAIL_URL_TPL.format(auction_id=auction_id)
    LOG.info("Fetching auction detail: %s", url)

    # Try API first
    api_url = f"https://pvp.giustizia.it/pvp/api/annuncio/{auction_id}"
    try:
        resp = session.get(api_url, headers={"Accept": "application/json"})
        data = resp.json()
        if isinstance(data, dict) and ("id" in data or "titolo" in data):
            auction = _parse_api_result(data)
            _save_cache(ck, [auction.to_dict()])
            return auction
    except (requests.RequestException, json.JSONDecodeError, ConnectionError):
        LOG.debug("API detail not available, trying HTML")

    # HTML fallback
    try:
        resp = session.get(url)
    except ConnectionError as exc:
        LOG.error("Failed to fetch detail for %s: %s", auction_id, exc)
        return PvpAuction(id=auction_id, source_url=url)

    soup = BeautifulSoup(resp.text, "html.parser")
    a = PvpAuction(id=auction_id, source_url=url)

    # Title
    title_el = soup.select_one("h1, h2, .titolo-annuncio, [class*='title']")
    if title_el:
        a.title = _clean(title_el.get_text())

    # Description
    desc_el = soup.select_one(
        ".descrizione, .description, [class*='descrizion'], "
        "#descrizione, .dettaglio-descrizione"
    )
    if desc_el:
        a.description = _clean(desc_el.get_text())

    # Parse detail fields from definition lists or tables
    # PVP detail pages often use <dl><dt>Label</dt><dd>Value</dd></dl>
    dts = soup.select("dt, th, .label, .campo-label")
    for dt in dts:
        label = _clean(dt.get_text()).lower()
        dd = dt.find_next_sibling("dd") or dt.find_next_sibling("td")
        if not dd:
            continue
        value = _clean(dd.get_text())

        if "tribunale" in label or "ufficio" in label:
            a.tribunal = value
        elif "procedura" in label or "rge" in label:
            a.procedure_number = value
        elif "prezzo base" in label or "base d'asta" in label:
            a.base_price = _parse_price(value)
        elif "offerta minima" in label:
            a.minimum_offer = _parse_price(value)
        elif "stima" in label or "perizia" in label or "valore" in label:
            a.appraisal_value = _parse_price(value)
        elif "data" in label and ("vendita" in label or "asta" in label):
            a.auction_date = _parse_date(value)
        elif "scadenza" in label or "termine" in label:
            a.deadline = _parse_date(value)
        elif "superficie" in label or "mq" in label:
            a.surface_sqm = _safe_float(value.replace(",", "."))
        elif "vani" in label or "locali" in label:
            a.rooms = _safe_int(value)
        elif "indirizzo" in label or "ubicazione" in label:
            a.address = value
        elif "comune" in label or "citta" in label:
            a.city = value
        elif "provincia" in label:
            a.province = value
        elif "regione" in label:
            a.region = value
        elif "tipo" in label or "categoria" in label:
            a.property_type = value
        elif "custode" in label or "delegato" in label:
            a.custodian_info = value
        elif "stato" in label:
            a.status = value

    # Photos
    imgs = soup.select(
        "img[src*='foto'], img[src*='image'], img[src*='photo'], "
        ".gallery img, .carousel img, .foto img"
    )
    for img in imgs:
        src = img.get("src", "")
        if src and not src.endswith((".svg", ".gif", ".ico")):
            a.photos.append(urljoin(url, src))

    # Coordinates from embedded map or data attributes
    map_el = soup.select_one("[data-lat], [data-latitude]")
    if map_el:
        a.latitude = _safe_float(map_el.get("data-lat", map_el.get("data-latitude")))
        a.longitude = _safe_float(map_el.get("data-lng", map_el.get("data-longitude", map_el.get("data-lon"))))

    # Try extracting coords from script tags (Google Maps embed)
    for script in soup.select("script"):
        text = script.string or ""
        coord_match = re.search(
            r"(?:lat|latitude)[:\s=]+(-?\d+\.\d+).*?(?:lng|lon|longitude)[:\s=]+(-?\d+\.\d+)",
            text, re.IGNORECASE | re.DOTALL,
        )
        if coord_match and a.latitude is None:
            a.latitude = _safe_float(coord_match.group(1))
            a.longitude = _safe_float(coord_match.group(2))
            break

    # Extract from description if fields are still empty
    if a.description:
        if a.surface_sqm is None:
            a.surface_sqm = _extract_sqm(a.description)
        if a.rooms is None:
            a.rooms = _extract_rooms(a.description)

    _save_cache(ck, [a.to_dict()])
    return a


# ---------------------------------------------------------------------------
# Main — test with Roma province
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PVP Aste Giudiziarie scraper")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout (for API bridge)")
    parser.add_argument("--region", default="")
    parser.add_argument("--province", default="")
    parser.add_argument("--city", default="")
    parser.add_argument("--min_price", type=float, default=None)
    parser.add_argument("--max_price", type=float, default=None)
    parser.add_argument("--property_type", default="immobile")
    parser.add_argument("--max_pages", type=int, default=3)
    args = parser.parse_args()

    results = search_auctions(
        region=args.region,
        province=args.province,
        city=args.city,
        min_price=args.min_price,
        max_price=args.max_price,
        property_type=args.property_type,
        max_pages=args.max_pages,
    )

    if args.json:
        import json as _json
        output = {
            "total": len(results),
            "auctions": [a.to_dict() for a in results],
        }
        print(_json.dumps(output, default=str, ensure_ascii=False))
    else:
        print("=" * 70)
        print("PVP Aste Giudiziarie — Scraper Test")
        print("=" * 70)
        print(f"Searching auctions: region={args.region or 'any'} province={args.province or 'any'}...")
        print()

        if not results:
            print("No results found (the API/site may require different parameters).")
        else:
            print(f"Found {len(results)} auctions:\n")
            for i, a in enumerate(results[:10], 1):
                print(f"  {i}. [{a.id}] {a.title}")
                if a.tribunal:
                    print(f"     Tribunale: {a.tribunal}")
                if a.base_price:
                    print(f"     Prezzo base: EUR {a.base_price:,.2f}")
                if a.auction_date:
                    print(f"     Data: {a.auction_date}")
                if a.address:
                    print(f"     Indirizzo: {a.address}")
                if a.province:
                    print(f"     Provincia: {a.province}")
                if a.status:
                    print(f"     Stato: {a.status}")
                print(f"     URL: {a.source_url}")
                print()

            if len(results) > 10:
                print(f"  ... and {len(results) - 10} more.")

        print("=" * 70)
        print("Done.")
