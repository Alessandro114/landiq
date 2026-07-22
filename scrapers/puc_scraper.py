"""
PUC / PRG scraper — municipality urbanistic plan downloader.

First target: Comune di Gaeta (LT).

Key URLs (verified 11 April 2026):
    - Homepage comune:
        https://www.comune.gaeta.lt.it/
    - Documenti pubblici — Doc. pre. di indirizzo della variante generale al PRG:
        https://www.comune.gaeta.lt.it/it/documenti_pubblici/doc-pre-di-indirizzo-della-variante-generale-al-p-r-g
    - News PRG linee guida:
        https://www.comune.gaeta.lt.it/News/Piano-Regolatore-Generale-Ecco-le-linee-guida
    - News PRG "dopo 42 anni Gaeta cambia":
        http://www.comune.gaeta.lt.it/News/Piano-Regolatore-Generale-dopo-42-anni-Gaeta-cambia

Documents to fetch for the Gaeta case study (26 PDFs listed on variante page):
    - elab_00_dp01_documento_obiettivi.pdf   # objectives preliminary doc
    - elab_00_dp02_rap.pdf                   # Rapporto Ambientale Preliminare
    - elab_00_dp03_schema-preliminare.pdf    # schema preliminare
    - elab_b01_zoning_prg_vigente.pdf        # CURRENT zoning PRG 1973 (KEY for our case)
    - elab_d01..elab_d04                     # geologico
    - elab_e01..elab_e03                     # agronomico / forestale

Status:
    - MVP: stub that lists the target docs and knows the URL pattern.
    - V1:  downloader using requests + sha256 cache + LLM extraction of NTA zone
           indices/hmax/destinations from PDF text (Gemini 2.5 Flash multimodal).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests  # type: ignore

GAETA_BASE = "https://www.comune.gaeta.lt.it"
GAETA_VARIANTE_PAGE = (
    f"{GAETA_BASE}/it/documenti_pubblici/doc-pre-di-indirizzo-della-variante-generale-al-p-r-g"
)

# Known document slugs found on the variante page (20 Sep 2025 snapshot)
GAETA_TARGET_DOCS = [
    "elab_00_dp01_documento_obiettivi.pdf",
    "elab_00_dp02_rap.pdf",
    "elab_00_dp03_schema-preliminare.pdf",
    "elab_b01_zoning_prg_vigente.pdf",
    "elab_d01_relazione_geologica.pdf",
    "elab_d02_carta_geolitologica.pdf",
    "elab_d03_carta_geomorfologica.pdf",
    "elab_d04_carta_microzonazione.pdf",
    "elab_e01_relazione_agronomica.pdf",
    "elab_e02_carta_uso_suolo.pdf",
    "elab_e03_carta_capacita_uso.pdf",
]

USER_AGENT = "LandIQ/0.1 (+https://get-scala.com) PUC-scraper"


@dataclass
class PucDocument:
    comune: str
    filename: str
    url: str
    local_path: Path | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    content_type: str | None = None


def list_gaeta_docs() -> list[PucDocument]:
    """Return the list of known Gaeta PRG variante documents (without downloading)."""
    return [
        PucDocument(
            comune="Gaeta",
            filename=slug,
            url=f"{GAETA_BASE}/documenti/{slug}",  # TODO: confirm actual path after scraping landing page
        )
        for slug in GAETA_TARGET_DOCS
    ]


def download_puc(comune: str, out_dir: Path | str = "data/puc") -> list[PucDocument]:
    """Download the PUC/PRG documents for a comune.

    Currently supports: Gaeta.
    """
    out = Path(out_dir) / comune.lower()
    out.mkdir(parents=True, exist_ok=True)

    if comune.lower() != "gaeta":
        raise NotImplementedError(
            f"TODO: generic PUC scraper for {comune}. MVP supports only Gaeta."
        )

    # TODO real implementation:
    #   1. GET GAETA_VARIANTE_PAGE, parse HTML with bs4
    #   2. Extract all <a href=".pdf"> links matching elab_*.pdf pattern
    #   3. Download each with streaming + sha256 + cache
    #   4. Return populated PucDocument list
    raise NotImplementedError(
        "TODO: implement Gaeta PRG variante downloader. "
        f"Start from {GAETA_VARIANTE_PAGE}, parse <a> tags, stream PDFs to {out}."
    )


def extract_nta_zones(pdf_path: Path) -> dict[str, Any]:
    """OCR + LLM-extract zone indices (IF, Hmax, destinations) from NTA PDF.

    Pipeline:
        1. PyPDF2 text extraction (if digital PDF)
        2. Tesseract OCR fallback (if scanned)
        3. Gemini 2.5 Flash prompt: "estrai tutte le zone con IF fondiario, altezza max,
           destinazioni ammesse, prescrizioni particolari" → JSON schema
        4. Validate with pydantic
    """
    _ = pdf_path
    raise NotImplementedError(
        "TODO: PDF -> text -> Gemini extraction. See reference_ai_fallback.md for "
        "shared AI client. Model: gemini-2.5-flash, temp 0.1, JSON mode."
    )


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


if __name__ == "__main__":
    docs = list_gaeta_docs()
    print(f"Gaeta PRG variante — {len(docs)} target documents:")
    for d in docs:
        print(f"  - {d.filename}")
    print(f"\nLanding page: {GAETA_VARIANTE_PAGE}")
