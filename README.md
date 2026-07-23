# LandIQ — Autonomous AI Agent for Real Estate Feasibility

> **Give the agent a property address. It does the rest.**
> Market research, urban planning analysis, DCF, Monte Carlo, risk matrix, GO/NO-GO verdict — fully autonomous, no human in the loop.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](Dockerfile)
[![Countries](https://img.shields.io/badge/countries-5%20connectors%20%2B%20worldwide-green.svg)](#countries-supported)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Good First Issues](https://img.shields.io/github/issues/Alessandro114/landiq/good%20first%20issue?color=7057ff&label=good%20first%20issues)](https://github.com/Alessandro114/landiq/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)

---

## What the Agent Does

You provide: **an address + sqm + intended use**. The agent autonomously:

1. **Identifies the country** → selects the right data connector (or generic fallback)
2. **Researches market data** — real prices/sqm from official sources (OMI, INE, myhome.ge, etc.)
3. **Researches urbanistic constraints** — zoning, FAR, height limits, permits, heritage buffers
4. **Builds 3 investment scenarios** — residential, touristic/mixed, status-quo refurb
5. **Runs DCF analysis** — NPV + IRR for each scenario over your investment horizon
6. **Runs Monte Carlo simulation** — 10,000 iterations, P5/P50/P95 distribution, tornado chart
7. **Generates AI verdict** — 2-paragraph executive summary via Gemini 2.5 Flash with GO/NO-GO
8. **Exports a 15-20 page PDF report** — professional, investor-ready, with charts and tables

**What took consultants 2-3 weeks and ~$15K, the agent does in under 5 minutes.**

---

## Quick Start

### With Docker (recommended)

```bash
git clone https://github.com/Alessandro114/landiq
cd landiq
cp .env.example .env
# Add your Gemini API key to .env (free at https://aistudio.google.com)

docker compose up -d

# Run the agent via API
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

# Or generate PDF reports directly
docker exec landiq-landiq-1 python src/run_gaeta_report.py    # Italy — Gaeta
docker exec landiq-landiq-1 python src/run_batumi_report.py   # Georgia — Batumi
docker exec landiq-landiq-1 python src/run_tbilisi_report.py  # Georgia — Tbilisi
docker exec landiq-landiq-1 python src/run_warsaw_report.py   # Poland — Warsaw (generic)
```

### Without Docker

```bash
git clone https://github.com/Alessandro114/landiq
cd landiq
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add your GEMINI_API_KEY

# Let the agent run — each command produces a full PDF report
python src/run_gaeta_report.py        # Italy — reports/gaeta_serapo_v1.pdf
python src/run_batumi_report.py       # Georgia — reports/batumi_ge_v1.pdf
python src/run_tbilisi_report.py      # Georgia — reports/tbilisi_ge_v1.pdf
python src/run_warsaw_report.py       # Poland — reports/warsaw_pl_v1.pdf
```

---

## How the Agent Works

```
INPUT: address + sqm + country + intended use
          │
          ▼
  ┌───────────────────┐
  │  COUNTRY ROUTER   │ ← auto-selects IT/ES/PT/GE/generic connector
  └───────┬───────────┘
          │
    ┌─────┴─────┐
    ▼           ▼
┌────────┐ ┌──────────┐
│ MARKET │ │ URBANIST │  ← parallel data fetching
│  DATA  │ │   DATA   │
└───┬────┘ └────┬─────┘
    │           │
    ▼           ▼
  ┌───────────────────┐
  │ SCENARIO BUILDER  │ ← 3 development scenarios (residential/touristic/mixed)
  └───────┬───────────┘
          │
    ┌─────┴─────┐
    ▼           ▼
┌────────┐ ┌──────────┐
│  DCF   │ │  MONTE   │  ← financial modeling
│ MODEL  │ │  CARLO   │
│        │ │ (10K sim)│
└───┬────┘ └────┬─────┘
    │           │
    ▼           ▼
  ┌───────────────────┐
  │   AI VERDICT      │ ← Gemini 2.5 Flash executive summary + GO/NO-GO
  └───────┬───────────┘
          │
          ▼
  ┌───────────────────┐
  │  PDF REPORT       │ ← 15-20 pages, charts, tables, country-aware legal framework
  └───────────────────┘
```

The agent is **fully autonomous** — no human intervention between input and output.
Each country connector is a pluggable "tool" the agent uses to fetch local data.

---

## Countries Supported

| Country | Connector | Data Source | Cities |
|---|---|---|---|
| Italy | `ItalyConnector` | OMI Agenzia Entrate (official) | All Italian municipalities |
| Spain | `SpainConnector` | INE + idealista.com (Q1-2025) | Madrid, Barcelona, Marbella, Valencia, Sevilla, Bilbao, Palma, Ibiza + 9 more |
| Portugal | `PortugalConnector` | INE PT + Confidencial Imobiliario | Lisboa, Porto, Algarve, Cascais, Madeira, Azores + 13 more |
| Georgia | `GeorgiaConnector` | myhome.ge benchmarks | Tbilisi, Batumi, Kutaisi, Kobuleti, Gudauri + 4 more |
| **Any other** | `GenericConnector` | **AI-estimated via Gemini** | **Any city worldwide** |

The generic connector means the agent works for **any country in the world** — it just gets better with a dedicated connector.

**Want to add your country?** Check [open issues](https://github.com/Alessandro114/landiq/issues?q=is%3Aissue+is%3Aopen+label%3Aconnector) or send a PR — a connector is ~120 lines of Python.

---

## Adding a New Country Connector (Agent Tool)

Each country connector is a "tool" the agent uses. The pattern:

```python
# connectors/montenegro.py
from connectors.base import ConnectorBase, MarketData, UrbanisticData, register

@register
class MontenegroConnector(ConnectorBase):
    country_code = "ME"
    currency = "EUR"
    eur_rate = 1.0

    def fetch_market_data(self, city, address=None, use_type="residential"):
        return MarketData(city=city, country="ME", price_per_sqm=3500.0, ...)

    def fetch_urbanistic_data(self, city, address=None):
        return UrbanisticData(city=city, country="ME", plan_type="DUP", ...)

    def default_assumptions(self):
        return {"capital_gains_tax_pct": 0.09, "wacc": 0.08, ...}
```

~120 lines. The agent automatically discovers and uses any registered connector.

---

## API Reference

The agent exposes a FastAPI server with interactive docs at `http://localhost:8383/docs`

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/analyze` | POST | **Run the agent** — full autonomous analysis (JSON response) |
| `/report/pdf` | POST | **Run the agent** — returns PDF report file |
| `/omi/{comune}` | GET | Raw OMI market data (Italy only) |
| `/puc/{comune}` | GET | Raw urban planning data (Italy only) |

### Example: Agent analyzes a property in Batumi (Georgia)

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
├── connectors/               ← agent tools (one per country)
│   ├── base.py               ← abstract interface + auto-registry
│   ├── italy.py              ← IT: OMI Agenzia Entrate + PGT/PRG
│   ├── spain.py              ← ES: INE + idealista.com (17 cities)
│   ├── portugal.py           ← PT: INE PT + Confidencial Imobiliario (19 cities)
│   ├── georgia.py            ← GE: myhome.ge + Tbilisi/Batumi plans
│   └── generic.py            ← fallback: AI estimates for any country worldwide
├── src/
│   ├── landiq_core.py        ← agent brain: DCF, Monte Carlo, scenario builder, AI verdict
│   ├── api.py                ← FastAPI server exposing the agent
│   ├── run_gaeta_report.py   ← Italy demo
│   ├── run_batumi_report.py  ← Georgia demo
│   ├── run_tbilisi_report.py ← Georgia demo (Tbilisi)
│   └── run_warsaw_report.py  ← Poland demo (generic connector)
├── scrapers/                 ← data scrapers (OMI, PGT, PVP, catasto)
├── reports/                  ← generated PDFs (agent output)
├── data/                     ← scraper cache
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Hosted Version

Don't want to self-host? **[get-scala.com/landiq-reports](https://get-scala.com/en/landiq-reports)** — the agent runs for you, pay per report.

| Plan | Price | What you get |
|---|---|---|
| Basic | EUR 199 | Location intel + 2 scenarios + executive summary |
| Pro | EUR 299 | + DCF 10y + risk matrix + incentives scan + AI render |
| Enterprise | EUR 499 | + pool/bubble study + 3 renders + strategy call + 24h delivery |

---

## Contributing

1. Fork the repo
2. Create `connectors/<your_country>.py` (copy `generic.py` as template)
3. Add your country to the README table
4. Open a PR — we review country connectors within 48h

**Priority connectors wanted:** Montenegro, Bulgaria, UAE, UK, Germany, France, Greece, Croatia, Turkey.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

---

## License

[AGPL-3.0](LICENSE) — free to use, self-host, and contribute.
If you run it as a SaaS you must open-source your modifications.

Built by [Alessandro Binda](https://linkedin.com/in/alessandrobindageneralmanager) · [S.C.A.L.A. AI OS](https://get-scala.com)
