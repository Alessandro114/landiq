# LandIQ — AI Feasibility Engine for Real Estate Developers

> **Turn any property address into a full investment feasibility report in 30 minutes.**
> No consultants. No weeks of waiting. Just data, scenarios, and a clear GO / NO-GO.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](Dockerfile)

---

## What it does

Given a land plot or building **anywhere in the world**, LandIQ produces a **15-20 page PDF report** with:

- **Market values** — real price per sqm from official sources (OMI in Italy, myhome.ge in Georgia, etc.)
- **Urban planning analysis** — buildable ratio, max height, allowed uses, planning constraints
- **3 development scenarios** — residential, mixed-use, status-quo refurb
- **DCF model** — NPV + IRR for each scenario over your investment horizon
- **Monte Carlo simulation** — 10,000 runs, P5/P50/P95 distribution, tornado chart
- **AI verdict** — 2-paragraph executive summary via Gemini 2.5 Flash

A report that normally takes 2-3 weeks of technical consultants costs €499 and runs in 30 minutes.

---

## Quick Start

### With Docker (recommended)

```bash
git clone https://github.com/get-scala/landiq
cd landiq
cp .env.example .env
# Add your Gemini API key to .env (free at https://aistudio.google.com)

docker compose up -d

# Run a report via API
curl -X POST http://localhost:8383/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "address": "Via Marina di Serapo 12, Gaeta, Italy",
    "sqm": 900,
    "current_use": "ricettivo_alberghiero",
    "target_use": "residenziale",
    "budget": 1500000,
    "country": "IT",
    "city": "Gaeta"
  }'

# Or generate a PDF directly (all demo reports)
docker exec landiq-landiq-1 python src/run_gaeta_report.py    # Italy
docker exec landiq-landiq-1 python src/run_batumi_report.py   # Georgia
docker exec landiq-landiq-1 python src/run_tbilisi_report.py  # Georgia (different city)
docker exec landiq-landiq-1 python src/run_warsaw_report.py   # Poland (generic connector)
```

### Without Docker

```bash
git clone https://github.com/get-scala/landiq
cd landiq
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add your GEMINI_API_KEY

# Italy — Gaeta (ricettivo → residenziale, 900 mq)
python src/run_gaeta_report.py        # → reports/gaeta_serapo_v1.pdf

# Georgia — Batumi (commercial → touristic, 600 mq)
python src/run_batumi_report.py       # → reports/batumi_ge_v1.pdf

# Georgia — Tbilisi (office → residential, 450 mq)
python src/run_tbilisi_report.py      # → reports/tbilisi_ge_v1.pdf

# Poland — Warsaw (generic connector fallback, 800 mq)
python src/run_warsaw_report.py       # → reports/warsaw_pl_v1.pdf
```

---

## Countries Supported

| Country | Connector | Market Data | Urban Planning |
|---|---|---|---|
| 🇮🇹 Italy | `ItalyConnector` | OMI Agenzia Entrate (official) | PGT/PRG/PUC municipal plans |
| 🇬🇪 Georgia | `GeorgiaConnector` | myhome.ge benchmarks | Tbilisi/Batumi city plans |
| 🌍 Any other | `GenericConnector` | AI-estimated via Gemini | Generic zoning template |

**Cities with full support:** Gaeta, Roma, Milano, Napoli, Torino, Bologna, Firenze, Venezia, Bergamo (IT) · Tbilisi, Batumi, Kutaisi, Kobuleti, Borjomi (GE)

**Any other country** runs via `GenericConnector` with AI-estimated prices. The report clearly labels the data as estimated and links to `github.com/get-scala/landiq/connectors` to contribute real data.

---

## Adding a New Country

LandIQ is built around a **connector pattern** — each country is a single file, ~100 lines.
The core engine (DCF, Monte Carlo, PDF) is 100% country-agnostic.

```python
# connectors/spain.py
from connectors.base import ConnectorBase, MarketData, UrbanisticData, register

@register
class SpainConnector(ConnectorBase):
    country_code = "ES"
    currency = "EUR"
    eur_rate = 1.0

    def fetch_market_data(self, city, address=None, use_type="residential"):
        # Call Catastro API or scrape idealista.com
        return MarketData(
            city=city, country="ES",
            price_per_sqm=2800.0,
            price_min=2000.0, price_max=4000.0,
            currency="EUR",
            source="Catastro Spain",
        )

    def fetch_urbanistic_data(self, city, address=None):
        return UrbanisticData(
            city=city, country="ES",
            plan_type="PGOU",
            buildable_ratio=2.0, max_height_m=20.0,
            allowed_uses=["residential", "commercial", "mixed"],
            constraints=["Verify with municipal planning office"],
            source="LandIQ ES connector",
        )

    def default_assumptions(self):
        return {
            "sale_price_residential_eur_sqm": 2800.0,
            "conversion_cost_eur_sqm": 900.0,
            "capital_gains_tax_pct": 0.19,
            "wacc": 0.07,
            # full key list: see connectors/base.py
        }
```

That's it. Send a PR — we review country connectors within 48h.

---

## API Reference

The FastAPI server exposes interactive docs at `http://localhost:8383/docs`

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/analyze` | POST | Full feasibility analysis (JSON) |
| `/report/pdf` | POST | Generate and download PDF |
| `/omi/{comune}` | GET | Raw OMI market data (Italy) |
| `/puc/{comune}` | GET | Raw urban planning data (Italy) |

### Example: Batumi (Georgia)

```bash
curl -X POST http://localhost:8383/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "address": "Rustaveli Avenue 45, Batumi",
    "sqm": 600,
    "current_use": "commercial",
    "target_use": "touristic",
    "budget": 900000,
    "country": "GE",
    "city": "Batumi",
    "horizon_years": 5
  }'
```

---

## Architecture

```
landiq/
├── connectors/               ← country connectors (add yours here)
│   ├── base.py               ← abstract interface + auto-registry
│   ├── italy.py              ← IT: OMI Agenzia Entrate + PGT/PRG
│   ├── georgia.py            ← GE: myhome.ge + Tbilisi/Batumi plans
│   └── generic.py            ← fallback: AI estimates for any country
├── src/
│   ├── landiq_core.py        ← DCF engine, Monte Carlo, PDF, AI verdict
│   ├── api.py                ← FastAPI server
│   ├── run_gaeta_report.py   ← Italy demo
│   └── run_batumi_report.py  ← Georgia demo
├── scrapers/                 ← data scrapers (OMI, PGT, PVP, catasto)
├── reports/                  ← generated PDFs
├── data/                     ← scraper cache
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Hosted Version

Don't want to self-host? **[landiq.get-scala.com](https://landiq.get-scala.com)**

| Plan | Price | What you get |
|---|---|---|
| Pay-per-report | €499 | Single property, instant PDF, no setup |
| Dev Pro | €499/month | Unlimited reports + deal sourcing alerts via WhatsApp |

---

## Contributing

1. Fork the repo
2. Create `connectors/<your_country>.py` (copy `generic.py` as template)
3. Add your country to the README table above
4. Open a PR

**Priority connectors wanted:** Spain, Portugal, Montenegro, Bulgaria, UAE, UK, Germany.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

---

## License

[AGPL-3.0](LICENSE) — free to use, self-host, and contribute.
If you run it as a SaaS you must open-source your modifications.

Built by [Alessandro Binda](https://linkedin.com/in/alessandrobindageneralmanager) · [S.C.A.L.A. AI OS](https://get-scala.com)
