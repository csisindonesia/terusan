"""The history behind a Trading Economics indicator page's chart.

A detail page carries two readings; the chart on it carries the whole series.
The chart script (`ec.min.js`) fetches it from a CloudFront endpoint named in
the page's own globals:

    GET {TEChartsDatasource}/economics/{symbol}?span=max&v={TELastUpdate}00
    x-api-key: {TEChartsToken}

and the body is a JSON string that is base64, then XORed with a fixed key,
then gzip. Decoded it is `[{"series": [{"serie": {...}}]}]`, where each point
is `[value, unix_ts, null, "YYYY-MM-DD"]` and the serie carries its unit, its
frequency and the publisher Trading Economics took it from.

The body is landed as it arrives — still encoded — because that is what was
published; decoding is extraction's job, and a change to the scheme then costs
a new decoder rather than the landed history.

Market pages (the rupiah, the index, commodities) are served by a different
endpoint and answer this one with no series. They keep their daily reading.
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from dataclasses import dataclass
from typing import Any

#: What the chart script XORs the payload with.
OBFUSCATION_KEY = b"tradingeconomics-charts-core-api-key"

DEFAULT_DATASOURCE = "https://d3ii0wo49og5mi.cloudfront.net"


@dataclass(frozen=True, slots=True)
class ChartRequest:
    symbol: str
    token: str
    last_update: str
    datasource: str
    frequency: str

    @property
    def url(self) -> str:
        url = f"{self.datasource.rstrip('/')}/economics/{self.symbol.lower()}?span=max"
        if self.last_update:
            # The cache key the page itself sends: a new release changes it,
            # so an unchanged series is answered from CloudFront's cache.
            url += f"&v={self.last_update}00"
        return url

    @property
    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.token} if self.token else {}


def _global(html: str, name: str) -> str:
    """A page global's effective value.

    Each is assigned more than once — blank first, filled in later — so the
    last non-empty assignment is the one the chart script sees.
    """
    for value in reversed(re.findall(rf"(?:var\s+)?{name}\s*=\s*'([^']*)'", html)):
        if value:
            return value
    return ""


def chart_request(html: str) -> ChartRequest | None:
    """What the page's chart would ask for, or None for a page with no chart."""
    symbol = _global(html, "TESymbol")
    if not symbol:
        return None
    datasource = _global(html, "TEChartsDatasource") or DEFAULT_DATASOURCE
    if not datasource.startswith("http"):
        datasource = f"https://{datasource}"
    return ChartRequest(
        symbol=symbol,
        token=_global(html, "TEChartsToken"),
        last_update=_global(html, "TELastUpdate"),
        datasource=datasource,
        frequency=_global(html, "TEFrequency"),
    )


def decode_payload(body: bytes) -> list[dict[str, Any]]:
    """The chart payload, decoded: base64, then the XOR key, then gzip."""
    raw = body.decode("utf-8").strip().strip('"')
    buffer = bytearray(base64.b64decode(raw))
    for index in range(len(buffer)):
        buffer[index] ^= OBFUSCATION_KEY[index % len(OBFUSCATION_KEY)]
    decoded = json.loads(zlib.decompress(bytes(buffer), 31))
    return decoded if isinstance(decoded, list) else [decoded]


def encode_payload(payload: Any) -> bytes:
    """The inverse of `decode_payload`, for tests."""
    compressor = zlib.compressobj(wbits=31)
    buffer = bytearray(compressor.compress(json.dumps(payload).encode()) + compressor.flush())
    for index in range(len(buffer)):
        buffer[index] ^= OBFUSCATION_KEY[index % len(OBFUSCATION_KEY)]
    return json.dumps(base64.b64encode(bytes(buffer)).decode()).encode()
