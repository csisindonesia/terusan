"""ADB's Key Indicators Database into Bronze records.

Two readers, for the two things `adb-kidb` lands.

**`KidbDataExtractor`** reads the SDMX-CSV: one row per indicator and year,
already long. The generic CSV reader would carry it through column for column,
and that would be nearly enough — but three things need saying on the row that
the file only implies:

- `indicator` is the identifier the series carries in Silver, derived from
  ADB's code (`NGDP_XDC`) and namespaced by the source, so a series another
  publisher also calls `NGDP_XDC` does not land on it. Composed here so that
  one `normalize-each --by indicator` publishes every series.
- `unit` folds in `UNIT_MULT`. KIDB states GDP as `1389.76985` in `IDR` with a
  multiplier of 12; a unit of `IDR` alone would say Indonesia's GDP in 2000 was
  fourteen hundred rupiah. The value itself is left as published — scaling it
  here would be Bronze deciding what a number means.
- `country` is the place's name. ADB codes Indonesia `INO`, which is not the
  ISO code the geography registry resolves.

One cleaning step is taken, and it is narrow: a row repeated exactly — same
indicator, year and value — is kept once. KIDB serves the World Bank's $2.15
poverty headcount (`SI_POV_DDAY`) twice for every year, from two of its
topical flows, and Silver refuses a period holding two figures, so the series
would not normalize at all. A repeat with a *different* value is kept, and
fails loudly in Silver, because choosing between them is not Bronze's call.

**`KidbCodelistExtractor`** reads the indicator codelist, one record per code:
what ADB calls the series and what it says the series counts. It is the
`--describe-dataset` for the figures, so a paragraph of definition is held
once rather than on every observation.

Neither reader bumps `PARSER_VERSION`: nothing has been extracted from this
source before.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from typing import Any

from ..identifiers import short_id
from .base import ExtractionError, Extractor, Landed
from .tabular import decode

SOURCE = "adb-kidb"

#: ADB's economy codes, where they are not the name the geography registry
#: resolves. Only Indonesia is collected; an unknown code passes through as
#: itself and stays visibly unresolved in Silver rather than being guessed.
ECONOMIES = {"INO": "Indonesia"}

#: SDMX's `UNIT_MULT` is a power of ten.
SCALES = {0: "", 3: "thousand", 6: "million", 9: "billion", 12: "trillion"}


def indicator_id(code: str) -> str:
    """The Silver identifier for one KIDB indicator code."""
    return short_id(SOURCE, code)


def unit_label(unit: str, multiplier: str) -> str:
    """`IDR` at multiplier 12 is `IDR trillion`; an unknown power stays explicit."""
    unit = unit.strip()
    try:
        power = int(multiplier.strip() or 0)
    except ValueError:
        return unit
    scale = SCALES.get(power, f"x10^{power}")
    return f"{unit} {scale}".strip()


def _text(value: Any) -> str:
    """A codelist field, which SDMX-JSON gives as a string or per language."""
    if isinstance(value, dict):
        return str(value.get("en") or next(iter(value.values()), "") or "").strip()
    return str(value or "").strip()


class KidbDataExtractor(Extractor):
    """One Bronze record per indicator and year."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE and landed.path.suffix.lower() == ".csv"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        text = decode(landed.path.read_bytes())
        if not text.strip():
            return

        reader = csv.DictReader(io.StringIO(text))
        missing = {"INDICATOR", "TIME_PERIOD", "OBS_VALUE"} - set(reader.fieldnames or ())
        if missing:
            raise ExtractionError(str(landed.path), f"not KIDB SDMX-CSV: no {sorted(missing)}")

        seen: set[tuple[str, str, str, str]] = set()
        for number, row in enumerate(reader, start=1):
            code = (row.get("INDICATOR") or "").strip()
            period = (row.get("TIME_PERIOD") or "").strip()
            if not code or not period:
                continue
            economy = (row.get("ECONOMY_CODE") or "").strip()
            value = (row.get("OBS_VALUE") or "").strip()

            key = (code, economy, period, value)
            if key in seen:
                continue
            seen.add(key)

            unit = row.get("UNIT") or ""
            multiplier = row.get("UNIT_MULT") or ""
            yield {
                "dataset": landed.dataset or "adb-key-indicators",
                "row_number": number,
                "columns": {
                    "indicator": indicator_id(code),
                    "code": code,
                    "dataflow": (row.get("DATAFLOW") or "").strip(),
                    "frequency": (row.get("FREQ") or "").strip(),
                    "economy_code": economy,
                    "country": ECONOMIES.get(economy, economy),
                    "period": period,
                    "value": value,
                    "unit": unit_label(unit, multiplier),
                    "unit_code": unit.strip(),
                    "unit_multiplier": multiplier.strip(),
                    "decimals": (row.get("DECIMALS") or "").strip(),
                    "status": (row.get("OBS_STATUS") or "").strip(),
                    "footnote": (row.get("FOOTNOTE") or "").strip(),
                    "reference_year": (row.get("REF_YEAR") or "").strip(),
                    "base_year": (row.get("BASE_YEAR") or "").strip(),
                    "publisher": (row.get("DATA_SOURCE") or "").strip(),
                    "methodology": (row.get("METHODOLOGY") or "").strip(),
                },
            }


class KidbCodelistExtractor(Extractor):
    """One Bronze record per indicator code: its name and definition."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE and landed.path.suffix.lower() == ".json"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            body = json.loads(landed.path.read_bytes())
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc

        codelists = ((body or {}).get("data") or {}).get("codelists") or []
        number = 0
        for codelist in codelists:
            for code in codelist.get("codes") or []:
                identifier = str(code.get("id") or "").strip()
                if not identifier:
                    continue
                number += 1
                yield {
                    "dataset": landed.dataset or "adb-key-indicators-codelist",
                    "row_number": number,
                    "columns": {
                        "indicator": indicator_id(identifier),
                        "code": identifier,
                        "codelist": str(codelist.get("id") or ""),
                        "title": _text(code.get("names") or code.get("name")),
                        "notes": _text(code.get("descriptions") or code.get("description")),
                    },
                }
