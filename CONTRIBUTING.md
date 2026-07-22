# Contributing to LandIQ

## Adding a Country Connector

The fastest way to contribute is a new country connector.

### 1. Copy the template

```bash
cp connectors/generic.py connectors/your_country.py
```

### 2. Implement the 3 methods

```python
from connectors.base import ConnectorBase, MarketData, UrbanisticData, register

@register
class YourCountryConnector(ConnectorBase):
    country_code = "XX"   # ISO 3166-1 alpha-2
    currency = "EUR"      # or local currency ISO 4217
    eur_rate = 1.0        # 1 local_currency = eur_rate EUR

    def fetch_market_data(self, city, address=None, use_type="residential") -> MarketData:
        # Fetch real price per sqm from official or reliable source
        ...

    def fetch_urbanistic_data(self, city, address=None) -> UrbanisticData:
        # Return zoning rules: buildable_ratio, max_height, allowed_uses, constraints
        ...

    def default_assumptions(self) -> dict:
        # Return DCF assumptions for your country
        # Keys: see connectors/base.py ConnectorBase.default_assumptions docstring
        ...
```

### 3. Test it

```bash
python -c "
import sys; sys.path.insert(0, 'src')
import connectors.your_country
from connectors.base import get_connector
conn = get_connector('XX')
md = conn.fetch_market_data('YourCity')
print(md)
"
```

### 4. Add to README table + open PR

We review country connectors within 48h.

## Data source guidelines

- **Official first**: land registries, central banks, government portals
- **Commercial ok**: real estate portals (idealista, rightmove, immoscout24) with attribution
- **AI estimates**: only in `GenericConnector` fallback — never as primary data for a named connector
- **Currency**: always convert to EUR in `fetch_market_data()` or set `eur_rate` correctly

## Priority connectors

| Country | Suggested data source | Status |
|---|---|---|
| 🇪🇸 Spain | Catastro + idealista.com | wanted |
| 🇵🇹 Portugal | Confidencial Imobiliário + IMT | wanted |
| 🇲🇪 Montenegro | monstat.org + oglasnik.me | wanted |
| 🇧🇬 Bulgaria | NSSI + imot.bg | wanted |
| 🇦🇪 UAE | DLD (Dubai Land Department) | wanted |
| 🇬🇧 UK | HM Land Registry | wanted |
| 🇩🇪 Germany | Gutachterausschüsse + ImmobilienScout24 | wanted |
