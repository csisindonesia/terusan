"""What the trade-competitiveness sources share: the countries, and the lists.

Revealed comparative advantage means nothing for one country alone. Indonesia's
RCA in a product is a statement about Indonesia against the world, and the
reading a policy question actually wants is Indonesia against the economies it
competes with for the same buyers. So the WITS source and the RCA seed both
cover one set of reporters, declared here once, rather than two lists that drift
apart the first time someone adds a country to one of them.

The environmental goods lists are reference data, not code: they are the HS
codes each negotiating group or agency has put on its list, concorded to HS
1988/92 so they apply to a trade series that runs back to 1995. They live in
`reference/trade/environmental-goods.csv`, and this module reads them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from pathlib import Path

#: ASEAN, Timor-Leste included since it joined in 2025.
ASEAN: tuple[str, ...] = (
    "IDN",
    "MYS",
    "THA",
    "VNM",
    "PHL",
    "SGP",
    "BRN",
    "KHM",
    "LAO",
    "MMR",
    "TLS",
)

#: The other RCEP economies, which are ASEAN's largest buyers and suppliers,
#: and the large emerging exporters Indonesia is most often benchmarked against.
PEERS: tuple[str, ...] = (
    "CHN",
    "JPN",
    "KOR",
    "IND",
    "AUS",
    "NZL",
    "BRA",
    "MEX",
    "TUR",
    "ZAF",
    "BGD",
)

#: Every economy these sources keep, Indonesia first.
REPORTERS: tuple[str, ...] = ASEAN + PEERS


@dataclass(frozen=True, slots=True)
class GoodsList:
    """One environmental goods list: the column it sits in, and who drew it up."""

    key: str
    name: str


#: In the order the reference file's columns run. `key` is the column name.
EG_LISTS: tuple[GoodsList, ...] = (
    GoodsList("tessd", "WTO TESSD environmental goods"),
    GoodsList("apec", "APEC environmental goods list"),
    GoodsList("accts", "ACCTS environmental goods list"),
    GoodsList("sagea", "SAGEA environmental goods list"),
    GoodsList("eunz", "EU–New Zealand FTA environmental goods"),
    GoodsList("uknz", "UK–New Zealand FTA environmental goods"),
    GoodsList("oecd", "OECD combined list of environmental goods"),
    GoodsList("unctad", "UNCTAD environmental goods"),
)

#: The union of every list: a product on at least one of them.
ANY_LIST = GoodsList("any", "Environmental goods on any list")

EG_REFERENCE = (
    Path(__file__).resolve().parents[3] / "reference" / "trade" / "environmental-goods.csv"
)


def hs6(code: object) -> str:
    """An HS subheading as the six-digit string it is.

    Stata and pandas both read `010111` as the integer 10111, which loses the
    leading zero that makes it chapter 01 and not chapter 10.
    """
    text = str(code).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


@cache
def environmental_goods(path: Path = EG_REFERENCE) -> dict[str, frozenset[str]]:
    """Each list's HS92 subheadings, keyed by list, plus `any` for the union."""
    lists: dict[str, set[str]] = {goods.key: set() for goods in (*EG_LISTS, ANY_LIST)}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(line for line in handle if not line.startswith("#"))
        for row in rows:
            code = hs6(row["hs92"])
            for goods in EG_LISTS:
                if row.get(goods.key) == "1":
                    lists[goods.key].add(code)
                    lists[ANY_LIST.key].add(code)
    return {key: frozenset(codes) for key, codes in lists.items()}
