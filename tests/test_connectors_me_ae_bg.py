"""Connectors ME / AE / BG (landiq issues #1-#3). Run: python -m pytest tests/ or python tests/test_connectors_me_ae_bg.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import connectors.bulgaria  # noqa: F401
import connectors.montenegro  # noqa: F401
import connectors.uae  # noqa: F401
from connectors.base import get_connector

# Ranges from the issue bodies: country -> city -> (min, max) EUR/sqm
ISSUE_RANGES = {
    "ME": {"Tivat": (3500, 7000), "Budva": (2500, 5000), "Kotor": (2000, 4000)},
    "AE": {"Palm Jumeirah": (8000, 20000), "Downtown Dubai": (6000, 14000), "Dubai Marina": (4000, 9000), "JVC": (2000, 4000)},
    "BG": {"Sofia": (1500, 3000), "Bansko": (800, 2000), "Sunny Beach": (600, 1500), "Sozopol": (600, 1500)},
}
TAX = {"ME": (0.09, 0.03), "AE": (0.0, 0.04), "BG": (0.10, 0.025)}
BASE_KEYS = set(get_connector("GE").default_assumptions())


def test_registered_not_generic():
    for cc in ISSUE_RANGES:
        c = get_connector(cc)
        assert c.country_code == cc and type(c).__name__ != "GenericConnector"


def test_prices_match_issue_ranges():
    for cc, cities in ISSUE_RANGES.items():
        c = get_connector(cc)
        for city, (lo, hi) in cities.items():
            md = c.fetch_market_data(city)
            assert (md.price_min, md.price_max) == (lo, hi), (cc, city)
            assert lo <= md.price_per_sqm <= hi
            assert md.currency == "EUR" and "verify" in md.source.lower()
            assert md.raw["benchmark_found"] is True


def test_unknown_city_falls_back_and_says_so():
    for cc in ISSUE_RANGES:
        md = get_connector(cc).fetch_market_data("Atlantis")
        assert md.raw["benchmark_found"] is False and md.price_per_sqm > 0


def test_assumptions_same_keys_as_georgia_and_taxes():
    for cc, (cgt, transfer) in TAX.items():
        a = get_connector(cc).default_assumptions()
        assert set(a) == BASE_KEYS
        assert (a["capital_gains_tax_pct"], a["land_transfer_tax_pct"]) == (cgt, transfer)
        assert a["conversion_cost_min"] < a["conversion_cost_eur_sqm"] < a["conversion_cost_max"]


def test_urbanistic_data():
    for cc in ISSUE_RANGES:
        u = get_connector(cc).fetch_urbanistic_data("Atlantis")
        assert u.country == cc and u.buildable_ratio > 0 and u.max_height_m > 0 and u.constraints
    assert get_connector("ME").fetch_urbanistic_data("Kotor").max_height_m < get_connector("ME").fetch_urbanistic_data("Budva").max_height_m


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("connector tests passed")
