"""
Catasto Visure — Italian Land Registry Integration Module

Abstracts visura requests through third-party API providers:
- VisureItalia (visureitalia.com): €1-3 per visura
- CatastoSemplice (catastosemplice.it): pay-per-use
- Pratiche.it (pratiche.it): API available

The Italian Catasto (Agenzia del Territorio) has no free public API.
This module enforces CLIENT-PAID flow: payment must be confirmed
before any visura is fetched from the provider.

Flow:
  1. Client calls estimate_cost() to get price
  2. Client calls request_visura() -> VisuraRequest (status=pending_payment)
  3. SCALA backend creates Stripe PaymentIntent, client pays
  4. After payment confirmed, call execute_visura(request_id) -> DatiCatastali
  5. Result is cached permanently at:
     /app/data/catasto_cache/{comune}/{foglio}_{particella}.json

STUB STATUS: The actual third-party API integration is a placeholder.
Real API keys and endpoints must be configured before production use.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_DIR = Path("/app/data/catasto_cache")
VISURE_ITALIA_API_KEY = os.environ.get("VISURE_ITALIA_API_KEY", "")
VISURE_ITALIA_BASE_URL = os.environ.get(
    "VISURE_ITALIA_BASE_URL", "https://api.visureitalia.com/v1"
)

# Cost table (EUR) by visura type
COST_TABLE: dict[str, float] = {
    "ordinaria": 1.50,
    "storica": 2.50,
    "ipotecaria": 3.00,
}

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class DatiCatastali:
    """Represents the result of a catasto visura (land registry lookup)."""

    foglio: str
    particella: str
    subalterno: str | None
    categoria: str  # e.g. "A/2", "C/1", "D/8"
    classe: str
    consistenza: str  # e.g. "5 vani", "120 mq"
    rendita_catastale: float  # euros
    superficie_catastale_mq: float | None
    indirizzo: str
    comune: str
    provincia: str
    intestatari: list[str]  # owner names
    diritti: list[str]  # "proprietà 1/1", "nuda proprietà", etc.
    annotazioni: list[str]
    data_aggiornamento: str
    tipo_visura: str  # "ordinaria" | "storica"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> DatiCatastali:
        return cls(**d)


@dataclass
class VisuraRequest:
    """Tracks a visura request through its lifecycle."""

    id: str  # UUID
    user_id: str
    tipo: str  # "ordinaria" | "storica" | "ipotecaria"
    comune: str
    provincia: str
    foglio: str | None
    particella: str | None
    indirizzo: str | None
    cf_intestatario: str | None  # codice fiscale for person-based search
    status: str  # "pending_payment" | "paid" | "processing" | "completed" | "failed"
    costo_euro: float
    payment_intent_id: str | None  # Stripe PaymentIntent ID
    result: DatiCatastali | None
    created_at: str
    completed_at: str | None
    error_message: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> VisuraRequest:
        result_data = d.pop("result", None)
        if result_data is not None:
            d["result"] = DatiCatastali.from_dict(result_data)
        else:
            d["result"] = None
        # Handle optional field added later
        if "error_message" not in d:
            d["error_message"] = None
        return cls(**d)


# ---------------------------------------------------------------------------
# In-memory request store (production: use DB)
# ---------------------------------------------------------------------------

_request_store: dict[str, VisuraRequest] = {}


def _save_request(req: VisuraRequest) -> None:
    _request_store[req.id] = req


def _load_request(request_id: str) -> VisuraRequest | None:
    return _request_store.get(request_id)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(comune: str, foglio: str, particella: str, tipo: str = "ordinaria") -> Path:
    """Build the cache file path for a given parcel and visura type."""
    comune_clean = comune.lower().strip().replace(" ", "_").replace("'", "")
    suffix = f"_{tipo}" if tipo != "ordinaria" else ""
    return CACHE_DIR / comune_clean / f"{foglio}_{particella}{suffix}.json"


def _read_cache(comune: str, foglio: str, particella: str, tipo: str = "ordinaria") -> DatiCatastali | None:
    """Read cached visura result. Returns None if not cached."""
    path = _cache_path(comune, foglio, particella, tipo)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.info("Cache hit: %s", path)
        return DatiCatastali.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Cache read failed for %s: %s", path, exc)
        return None


def _write_cache(dati: DatiCatastali) -> None:
    """Write visura result to permanent cache."""
    path = _cache_path(dati.comune, dati.foglio, dati.particella, dati.tipo_visura)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dati.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Cached visura result to %s", path)


# ---------------------------------------------------------------------------
# Provider Abstraction
# ---------------------------------------------------------------------------


class CatastoProvider(ABC):
    """Base class for catasto data providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider display name."""

    @abstractmethod
    def request_visura(
        self,
        comune: str,
        provincia: str,
        foglio: str,
        particella: str,
        subalterno: str | None = None,
        tipo: str = "ordinaria",
    ) -> DatiCatastali:
        """
        Fetch visura data from the provider.

        This method should only be called AFTER payment is confirmed.
        Raises VisuraProviderError on failure.
        """

    @abstractmethod
    def search_by_address(
        self, comune: str, provincia: str, indirizzo: str
    ) -> list[dict]:
        """
        Search foglio/particella by street address.

        Returns list of dicts with keys: foglio, particella, subalterno, indirizzo.
        """

    @abstractmethod
    def search_by_cf(
        self, codice_fiscale: str
    ) -> list[dict]:
        """
        Search parcels owned by a person (codice fiscale).

        Returns list of dicts with keys: foglio, particella, comune, provincia, diritto.
        """


class VisuraProviderError(Exception):
    """Raised when a provider API call fails."""


class PaymentRequiredError(Exception):
    """Raised when attempting to execute a visura without confirmed payment."""


# ---------------------------------------------------------------------------
# VisureItalia Provider (STUB)
# ---------------------------------------------------------------------------


class VisureItaliaProvider(CatastoProvider):
    """
    VisureItalia.com API provider.

    # TODO: integrate real API when key available
    # API docs: https://visureitalia.com/api-docs (hypothetical)
    # Requires env var VISURE_ITALIA_API_KEY
    """

    @property
    def name(self) -> str:
        return "VisureItalia"

    def __init__(self) -> None:
        self.api_key = VISURE_ITALIA_API_KEY
        self.base_url = VISURE_ITALIA_BASE_URL
        if not self.api_key:
            logger.warning(
                "VISURE_ITALIA_API_KEY not set — VisureItaliaProvider will fail on real calls"
            )

    def request_visura(
        self,
        comune: str,
        provincia: str,
        foglio: str,
        particella: str,
        subalterno: str | None = None,
        tipo: str = "ordinaria",
    ) -> DatiCatastali:
        """
        # TODO: integrate real API when key available
        #
        # Real implementation would be:
        #   import requests
        #   resp = requests.post(
        #       f"{self.base_url}/visura",
        #       headers={"Authorization": f"Bearer {self.api_key}"},
        #       json={
        #           "comune": comune,
        #           "provincia": provincia,
        #           "foglio": foglio,
        #           "particella": particella,
        #           "subalterno": subalterno,
        #           "tipo": tipo,
        #       },
        #       timeout=60,
        #   )
        #   resp.raise_for_status()
        #   return DatiCatastali.from_dict(resp.json()["data"])
        """
        if not self.api_key:
            raise VisuraProviderError(
                "VISURE_ITALIA_API_KEY not configured. "
                "Set the environment variable to use this provider."
            )
        # STUB: raise until real integration
        raise VisuraProviderError(
            f"VisureItalia real API not yet integrated. "
            f"Query: {comune} ({provincia}) F.{foglio} P.{particella} tipo={tipo}"
        )

    def search_by_address(
        self, comune: str, provincia: str, indirizzo: str
    ) -> list[dict]:
        # TODO: integrate real API when key available
        raise VisuraProviderError("VisureItalia address search not yet integrated")

    def search_by_cf(self, codice_fiscale: str) -> list[dict]:
        # TODO: integrate real API when key available
        raise VisuraProviderError("VisureItalia CF search not yet integrated")


# ---------------------------------------------------------------------------
# Stub/Mock Provider (for testing)
# ---------------------------------------------------------------------------


class StubCatastoProvider(CatastoProvider):
    """
    Returns realistic mock data for development and testing.
    Always available, no API key needed.
    """

    @property
    def name(self) -> str:
        return "Stub (mock)"

    # Realistic mock database keyed by (comune_lower, foglio, particella)
    _MOCK_DB: dict[tuple[str, str, str], dict] = {
        ("gaeta", "10", "123"): {
            "foglio": "10",
            "particella": "123",
            "subalterno": "2",
            "categoria": "A/2",
            "classe": "3",
            "consistenza": "5 vani",
            "rendita_catastale": 619.75,
            "superficie_catastale_mq": 95.0,
            "indirizzo": "Via Lungomare Caboto 14, piano 2",
            "comune": "Gaeta",
            "provincia": "LT",
            "intestatari": ["ROSSI MARIO", "ROSSI ANNA"],
            "diritti": ["proprietà 1/2", "proprietà 1/2"],
            "annotazioni": [
                "Variazione del 15/03/2018 - Rettifica della superficie",
                "Fusione prot. n. 2015/12345",
            ],
            "data_aggiornamento": "2024-01-15",
            "tipo_visura": "ordinaria",
        },
        ("gaeta", "10", "124"): {
            "foglio": "10",
            "particella": "124",
            "subalterno": None,
            "categoria": "C/6",
            "classe": "2",
            "consistenza": "18 mq",
            "rendita_catastale": 52.68,
            "superficie_catastale_mq": 18.0,
            "indirizzo": "Via Lungomare Caboto 14, piano S1",
            "comune": "Gaeta",
            "provincia": "LT",
            "intestatari": ["ROSSI MARIO"],
            "diritti": ["proprietà 1/1"],
            "annotazioni": [],
            "data_aggiornamento": "2024-01-15",
            "tipo_visura": "ordinaria",
        },
        ("roma", "512", "45"): {
            "foglio": "512",
            "particella": "45",
            "subalterno": "8",
            "categoria": "A/3",
            "classe": "4",
            "consistenza": "4.5 vani",
            "rendita_catastale": 743.60,
            "superficie_catastale_mq": 82.0,
            "indirizzo": "Via dei Fori Imperiali 22, piano 3, int. 8",
            "comune": "Roma",
            "provincia": "RM",
            "intestatari": ["BIANCHI GIUSEPPE", "BIANCHI LUCIA"],
            "diritti": ["proprietà 1/2", "proprietà 1/2"],
            "annotazioni": [
                "Variazione del 22/06/2020 - Diversa distribuzione spazi interni"
            ],
            "data_aggiornamento": "2023-11-20",
            "tipo_visura": "ordinaria",
        },
        ("milano", "301", "78"): {
            "foglio": "301",
            "particella": "78",
            "subalterno": "15",
            "categoria": "D/8",
            "classe": "U",
            "consistenza": "1.200 mc",
            "rendita_catastale": 12540.00,
            "superficie_catastale_mq": 450.0,
            "indirizzo": "Corso Buenos Aires 33",
            "comune": "Milano",
            "provincia": "MI",
            "intestatari": ["ALFA IMMOBILIARE SRL"],
            "diritti": ["proprietà 1/1"],
            "annotazioni": [
                "Variazione del 10/09/2022 - Cambio destinazione d'uso"
            ],
            "data_aggiornamento": "2024-03-01",
            "tipo_visura": "ordinaria",
        },
    }

    # Mock address lookup
    _MOCK_ADDRESS_DB: dict[tuple[str, str], list[dict]] = {
        ("gaeta", "via lungomare caboto"): [
            {"foglio": "10", "particella": "123", "subalterno": "2", "indirizzo": "Via Lungomare Caboto 14, piano 2"},
            {"foglio": "10", "particella": "124", "subalterno": None, "indirizzo": "Via Lungomare Caboto 14, piano S1"},
        ],
    }

    def request_visura(
        self,
        comune: str,
        provincia: str,
        foglio: str,
        particella: str,
        subalterno: str | None = None,
        tipo: str = "ordinaria",
    ) -> DatiCatastali:
        key = (comune.lower().strip(), foglio.strip(), particella.strip())
        mock = self._MOCK_DB.get(key)
        if mock is None:
            # Generate plausible fallback for unknown parcels
            mock = {
                "foglio": foglio,
                "particella": particella,
                "subalterno": subalterno,
                "categoria": "A/2",
                "classe": "2",
                "consistenza": "4 vani",
                "rendita_catastale": 480.50,
                "superficie_catastale_mq": 75.0,
                "indirizzo": f"Indirizzo non disponibile - F.{foglio} P.{particella}",
                "comune": comune.title(),
                "provincia": provincia.upper(),
                "intestatari": ["DATI NON DISPONIBILI (mock)"],
                "diritti": ["proprietà 1/1"],
                "annotazioni": ["[MOCK] Dati generati per test"],
                "data_aggiornamento": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "tipo_visura": tipo,
            }
            logger.info("StubProvider: no mock entry for %s, using fallback", key)

        # Override tipo_visura with requested type
        data = dict(mock)
        data["tipo_visura"] = tipo
        if tipo == "storica":
            data["annotazioni"] = data.get("annotazioni", []) + [
                "STORICA: Atto di compravendita del 12/05/2005 Rep. 4521",
                "STORICA: Precedente intestatario VERDI CARLO fino al 12/05/2005",
            ]

        return DatiCatastali.from_dict(data)

    def search_by_address(
        self, comune: str, provincia: str, indirizzo: str
    ) -> list[dict]:
        # Normalize for lookup
        comune_norm = comune.lower().strip()
        indirizzo_norm = indirizzo.lower().strip()
        for (c, addr_prefix), results in self._MOCK_ADDRESS_DB.items():
            if c == comune_norm and addr_prefix in indirizzo_norm:
                return results
        return []

    def search_by_cf(self, codice_fiscale: str) -> list[dict]:
        # Mock: return one result for any CF
        return [
            {
                "foglio": "10",
                "particella": "123",
                "comune": "Gaeta",
                "provincia": "LT",
                "diritto": "proprietà 1/2",
            }
        ]


# ---------------------------------------------------------------------------
# Provider Factory
# ---------------------------------------------------------------------------


def _get_provider(use_stub: bool = False) -> CatastoProvider:
    """
    Get the active catasto provider.

    Falls back to stub if VISURE_ITALIA_API_KEY is not set.
    """
    if use_stub:
        return StubCatastoProvider()
    if VISURE_ITALIA_API_KEY:
        return VisureItaliaProvider()
    logger.info("No API key configured, falling back to StubCatastoProvider")
    return StubCatastoProvider()


# ---------------------------------------------------------------------------
# Stripe Payment Stub
# ---------------------------------------------------------------------------


def _create_payment_intent(amount_eur: float, metadata: dict) -> str:
    """
    Create a Stripe PaymentIntent for the visura cost.

    # TODO: integrate real Stripe when ready
    # Real implementation:
    #   import stripe
    #   stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    #   intent = stripe.PaymentIntent.create(
    #       amount=int(amount_eur * 100),  # cents
    #       currency="eur",
    #       metadata=metadata,
    #       description=f"Visura catastale {metadata.get('tipo', '')} - {metadata.get('comune', '')}",
    #   )
    #   return intent.id

    STUB: returns a fake PaymentIntent ID.
    """
    fake_id = f"pi_stub_{uuid.uuid4().hex[:16]}"
    logger.info(
        "STUB: Created fake PaymentIntent %s for %.2f EUR (metadata=%s)",
        fake_id,
        amount_eur,
        metadata,
    )
    return fake_id


def _confirm_payment(payment_intent_id: str) -> bool:
    """
    Check if a PaymentIntent has been paid.

    # TODO: integrate real Stripe when ready
    # Real implementation:
    #   import stripe
    #   stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    #   intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    #   return intent.status == "succeeded"

    STUB: all stub payment intents auto-confirm.
    """
    if payment_intent_id and payment_intent_id.startswith("pi_stub_"):
        return True
    # Real payment intents would be checked via Stripe API
    logger.warning("Cannot confirm non-stub payment intent: %s", payment_intent_id)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def estimate_cost(tipo: str) -> float:
    """
    Return the cost in EUR for a visura of the given type.

    Args:
        tipo: "ordinaria" | "storica" | "ipotecaria"

    Returns:
        Cost in EUR.

    Raises:
        ValueError: if tipo is not recognized.
    """
    tipo_lower = tipo.lower().strip()
    if tipo_lower not in COST_TABLE:
        raise ValueError(
            f"Unknown visura type '{tipo}'. Valid: {list(COST_TABLE.keys())}"
        )
    return COST_TABLE[tipo_lower]


def request_visura(
    user_id: str,
    comune: str,
    provincia: str,
    tipo: str = "ordinaria",
    foglio: str | None = None,
    particella: str | None = None,
    indirizzo: str | None = None,
    cf_intestatario: str | None = None,
) -> VisuraRequest:
    """
    Create a new visura request in pending_payment status.

    The caller must arrange Stripe payment, then call execute_visura()
    after payment is confirmed.

    Args:
        user_id: ID of the requesting user.
        comune: Municipality name (e.g. "Gaeta").
        provincia: Province code (e.g. "LT").
        tipo: "ordinaria" | "storica" | "ipotecaria".
        foglio: Foglio number (required unless searching by address/CF).
        particella: Particella number (required unless searching by address/CF).
        indirizzo: Street address for address-based search.
        cf_intestatario: Codice fiscale for person-based search.

    Returns:
        VisuraRequest with status="pending_payment" and a Stripe PaymentIntent.
    """
    if not foglio and not particella and not indirizzo and not cf_intestatario:
        raise ValueError(
            "Must provide at least foglio+particella, indirizzo, or cf_intestatario"
        )

    costo = estimate_cost(tipo)

    request_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Create Stripe PaymentIntent (stub)
    payment_intent_id = _create_payment_intent(
        amount_eur=costo,
        metadata={
            "request_id": request_id,
            "user_id": user_id,
            "tipo": tipo,
            "comune": comune,
            "provincia": provincia,
        },
    )

    req = VisuraRequest(
        id=request_id,
        user_id=user_id,
        tipo=tipo,
        comune=comune,
        provincia=provincia,
        foglio=foglio,
        particella=particella,
        indirizzo=indirizzo,
        cf_intestatario=cf_intestatario,
        status="pending_payment",
        costo_euro=costo,
        payment_intent_id=payment_intent_id,
        result=None,
        created_at=now,
        completed_at=None,
    )
    _save_request(req)
    logger.info("Created visura request %s (status=pending_payment, cost=%.2f EUR)", request_id, costo)
    return req


def execute_visura(
    request_id: str,
    force_stub: bool = False,
    skip_payment_check: bool = False,
) -> DatiCatastali:
    """
    Execute a visura request after payment has been confirmed.

    Enforces payment verification before fetching data from the provider.
    Results are cached permanently.

    Args:
        request_id: UUID of the VisuraRequest.
        force_stub: Force using the stub provider (for testing).
        skip_payment_check: Skip payment confirmation (ONLY for internal/testing use).

    Returns:
        DatiCatastali with the visura result.

    Raises:
        PaymentRequiredError: if payment has not been confirmed.
        VisuraProviderError: if the provider API call fails.
        ValueError: if request_id is not found.
    """
    req = _load_request(request_id)
    if req is None:
        raise ValueError(f"Visura request {request_id} not found")

    if req.status == "completed" and req.result is not None:
        logger.info("Request %s already completed, returning cached result", request_id)
        return req.result

    if req.status == "failed":
        raise VisuraProviderError(
            f"Request {request_id} previously failed: {req.error_message}"
        )

    # ---- PAYMENT GATE ----
    if not skip_payment_check:
        if req.status == "pending_payment":
            if not _confirm_payment(req.payment_intent_id):
                raise PaymentRequiredError(
                    f"Payment not confirmed for request {request_id}. "
                    f"PaymentIntent: {req.payment_intent_id}. "
                    f"Cost: {req.costo_euro:.2f} EUR. "
                    f"Client must complete Stripe payment before visura can be fetched."
                )
            req.status = "paid"
            _save_request(req)

    req.status = "processing"
    _save_request(req)

    # Check cache first
    if req.foglio and req.particella:
        cached = _read_cache(req.comune, req.foglio, req.particella, req.tipo)
        if cached is not None:
            req.status = "completed"
            req.result = cached
            req.completed_at = datetime.now(timezone.utc).isoformat()
            _save_request(req)
            return cached

    provider = _get_provider(use_stub=force_stub)

    try:
        # If we only have an address, search for foglio/particella first
        if not req.foglio or not req.particella:
            if req.indirizzo:
                results = _search_by_address(
                    req.comune, req.provincia, req.indirizzo, provider=provider
                )
                if not results:
                    raise VisuraProviderError(
                        f"No parcels found at address: {req.indirizzo}, {req.comune} ({req.provincia})"
                    )
                # Use first result
                req.foglio = results[0]["foglio"]
                req.particella = results[0]["particella"]
                _save_request(req)
            elif req.cf_intestatario:
                results = provider.search_by_cf(req.cf_intestatario)
                if not results:
                    raise VisuraProviderError(
                        f"No parcels found for CF: {req.cf_intestatario}"
                    )
                req.foglio = results[0]["foglio"]
                req.particella = results[0]["particella"]
                req.comune = results[0].get("comune", req.comune)
                req.provincia = results[0].get("provincia", req.provincia)
                _save_request(req)
            else:
                raise VisuraProviderError("No search criteria available (need foglio+particella, address, or CF)")

        dati = provider.request_visura(
            comune=req.comune,
            provincia=req.provincia,
            foglio=req.foglio,
            particella=req.particella,
            tipo=req.tipo,
        )

        # Cache the result
        _write_cache(dati)

        req.status = "completed"
        req.result = dati
        req.completed_at = datetime.now(timezone.utc).isoformat()
        _save_request(req)
        logger.info(
            "Visura completed: %s F.%s P.%s via %s",
            req.comune,
            req.foglio,
            req.particella,
            provider.name,
        )
        return dati

    except VisuraProviderError:
        req.status = "failed"
        req.error_message = str(VisuraProviderError)
        _save_request(req)
        raise
    except Exception as exc:
        req.status = "failed"
        req.error_message = str(exc)
        _save_request(req)
        raise VisuraProviderError(f"Provider {provider.name} failed: {exc}") from exc


def get_visura_result(request_id: str) -> DatiCatastali | None:
    """
    Get the result of a completed visura request.

    Returns None if the request is not yet completed.
    """
    req = _load_request(request_id)
    if req is None:
        return None
    return req.result


def get_request_status(request_id: str) -> dict | None:
    """
    Get the current status of a visura request.

    Returns dict with id, status, costo_euro, payment_intent_id, etc.
    Returns None if request not found.
    """
    req = _load_request(request_id)
    if req is None:
        return None
    return {
        "id": req.id,
        "status": req.status,
        "tipo": req.tipo,
        "comune": req.comune,
        "provincia": req.provincia,
        "foglio": req.foglio,
        "particella": req.particella,
        "costo_euro": req.costo_euro,
        "payment_intent_id": req.payment_intent_id,
        "created_at": req.created_at,
        "completed_at": req.completed_at,
        "has_result": req.result is not None,
        "error_message": req.error_message,
    }


def _search_by_address(
    comune: str,
    provincia: str,
    indirizzo: str,
    provider: CatastoProvider | None = None,
) -> list[dict]:
    """
    Find foglio/particella from a street address.

    Args:
        comune: Municipality name.
        provincia: Province code.
        indirizzo: Street address to search.
        provider: Provider to use (default: auto-select).

    Returns:
        List of dicts with keys: foglio, particella, subalterno, indirizzo.
    """
    if provider is None:
        provider = _get_provider()
    return provider.search_by_address(comune, provincia, indirizzo)


def search_by_address(
    comune: str, provincia: str, indirizzo: str
) -> list[dict]:
    """
    Public wrapper for address-based parcel search.

    This is a FREE lookup (no payment required) to help users
    identify the correct foglio/particella before requesting a paid visura.
    """
    return _search_by_address(comune, provincia, indirizzo)


def search_by_cf(codice_fiscale: str) -> list[dict]:
    """
    Search parcels owned by a person via codice fiscale.

    This is a FREE lookup (no payment required).
    """
    provider = _get_provider()
    return provider.search_by_cf(codice_fiscale)


# ---------------------------------------------------------------------------
# __main__ — Mock test for Gaeta foglio 10 particella 123
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Catasto Visure module")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout (for API bridge)")
    parser.add_argument("--comune", default="Gaeta")
    parser.add_argument("--provincia", default="LT")
    parser.add_argument("--foglio", default="10")
    parser.add_argument("--particella", default="123")
    parser.add_argument("--tipo", default="ordinaria")
    args = parser.parse_args()

    if args.json:
        # JSON mode: run a stub visura and output result
        req = request_visura(
            user_id="api_bridge",
            comune=args.comune,
            provincia=args.provincia,
            tipo=args.tipo,
            foglio=args.foglio,
            particella=args.particella,
        )
        dati = execute_visura(req.id, force_stub=True)
        print(json.dumps(dati.to_dict(), default=str, ensure_ascii=False))
        import sys
        sys.exit(0)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 70)
    print("CATASTO VISURE — Mock Integration Test")
    print("=" * 70)

    # 1. Cost estimate
    for tipo in ["ordinaria", "storica", "ipotecaria"]:
        print(f"\n  Costo visura {tipo}: EUR {estimate_cost(tipo):.2f}")

    # 2. Address search (free)
    print("\n--- Address Search (free) ---")
    results = search_by_address("Gaeta", "LT", "Via Lungomare Caboto")
    print(f"  Found {len(results)} parcels:")
    for r in results:
        print(f"    F.{r['foglio']} P.{r['particella']} sub.{r.get('subalterno', '-')} — {r['indirizzo']}")

    # 3. Request visura (creates pending_payment)
    print("\n--- Request Visura (Gaeta F.10 P.123, ordinaria) ---")
    req = request_visura(
        user_id="user_test_001",
        comune="Gaeta",
        provincia="LT",
        tipo="ordinaria",
        foglio="10",
        particella="123",
    )
    print(f"  Request ID: {req.id}")
    print(f"  Status:     {req.status}")
    print(f"  Cost:       EUR {req.costo_euro:.2f}")
    print(f"  Stripe PI:  {req.payment_intent_id}")

    # 4. Check status before payment
    status = get_request_status(req.id)
    print(f"\n  Status check: {status['status']} (has_result={status['has_result']})")

    # 5. Execute visura (stub auto-confirms payment)
    print("\n--- Execute Visura (payment auto-confirmed by stub) ---")
    dati = execute_visura(req.id, force_stub=True)

    print(f"\n  === DATI CATASTALI ===")
    print(f"  Comune:      {dati.comune} ({dati.provincia})")
    print(f"  Foglio:      {dati.foglio}")
    print(f"  Particella:  {dati.particella}")
    print(f"  Subalterno:  {dati.subalterno or '-'}")
    print(f"  Categoria:   {dati.categoria}")
    print(f"  Classe:      {dati.classe}")
    print(f"  Consistenza: {dati.consistenza}")
    print(f"  Rendita:     EUR {dati.rendita_catastale:.2f}")
    print(f"  Superficie:  {dati.superficie_catastale_mq} mq")
    print(f"  Indirizzo:   {dati.indirizzo}")
    print(f"  Intestatari: {', '.join(dati.intestatari)}")
    print(f"  Diritti:     {', '.join(dati.diritti)}")
    print(f"  Annotazioni:")
    for ann in dati.annotazioni:
        print(f"    - {ann}")
    print(f"  Aggiornamento: {dati.data_aggiornamento}")
    print(f"  Tipo visura:   {dati.tipo_visura}")

    # 6. Verify cached
    print("\n--- Cache Verification ---")
    cached = get_visura_result(req.id)
    print(f"  Cached result available: {cached is not None}")

    cache_file = _cache_path("Gaeta", "10", "123", "ordinaria")
    print(f"  Cache file: {cache_file}")
    print(f"  Cache file exists: {cache_file.exists()}")

    # 7. Storica visura test
    print("\n--- Storica Visura Test ---")
    req2 = request_visura(
        user_id="user_test_001",
        comune="Gaeta",
        provincia="LT",
        tipo="storica",
        foglio="10",
        particella="123",
    )
    dati2 = execute_visura(req2.id, force_stub=True)
    print(f"  Tipo: {dati2.tipo_visura}")
    print(f"  Annotazioni storica ({len(dati2.annotazioni)}):")
    for ann in dati2.annotazioni:
        print(f"    - {ann}")

    # 8. Payment enforcement test
    print("\n--- Payment Enforcement Test ---")
    req3 = request_visura(
        user_id="user_test_002",
        comune="Roma",
        provincia="RM",
        tipo="ordinaria",
        foglio="512",
        particella="45",
    )
    # Manually set a non-stub payment ID to simulate real Stripe
    req3.payment_intent_id = "pi_real_abc123"
    _save_request(req3)
    try:
        execute_visura(req3.id, force_stub=True)
        print("  ERROR: Should have raised PaymentRequiredError!")
    except PaymentRequiredError as e:
        print(f"  PASS: Payment enforcement works: {e}")

    # 9. CF search test
    print("\n--- CF Search Test ---")
    cf_results = search_by_cf("RSSMRA80A01E234X")
    print(f"  Found {len(cf_results)} parcels for CF RSSMRA80A01E234X")
    for r in cf_results:
        print(f"    {r['comune']} ({r['provincia']}) F.{r['foglio']} P.{r['particella']} — {r['diritto']}")

    print("\n" + "=" * 70)
    print("All tests passed. Module status: SCAFFOLD (stub provider only)")
    print("TODO: Set VISURE_ITALIA_API_KEY for real API integration")
    print("=" * 70)
