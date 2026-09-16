"""Reading Bank Indonesia's SEKI index page.

Pure functions: HTML in, a table catalogue out. No network, no disk — which is
what lets the whole parser be tested against a captured fixture in a
millisecond, and is how you notice the day the page is restructured.

The page's rows look like:

    <th colspan="4"><b>I. UANG DAN BANK</b></th>
    <td width="30">I.1.</td>
    <td style='text-align:left;'>Uang Beredar dan Faktor-Faktor ...</td>
    <td><a href=".../TABEL1_1.pdf"><img ...></a></td>
    <td><a href=".../TABEL1_1.xls"><img ...></a></td>

The anchors carry an icon rather than text, so a table's name comes from the
sibling cell, not the link. A `<th colspan="4">` opens a section that applies to
every row beneath it until the next one.

Ported from an earlier warehouse, verified against the real page.
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

#: Only ever follow links back to Bank Indonesia. The index is the one input we
#: do not control and it decides what gets fetched next, so a rewritten page
#: must not be able to point the scraper at another host.
ALLOWED_HOSTS = frozenset({"www.bi.go.id", "bi.go.id"})

_SECTION = re.compile(r"<th[^>]*colspan=[\"']?4[\"']?[^>]*>\s*<b>(?P<section>.*?)</b>", re.I | re.S)
_ROW = re.compile(
    r"<td[^>]*width=[\"']?30[\"']?[^>]*>(?P<number>.*?)</td>\s*"
    r"<td[^>]*text-align:\s*left[^>]*>(?P<title>.*?)</td>"
    r"(?P<rest>.*?)(?=<td[^>]*width=[\"']?30|<th[^>]*colspan=|</table>)",
    re.I | re.S,
)
_XLS_HREF = re.compile(r"href=[\"']([^\"']*\.xls[xm]?)[\"']", re.I)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class SekiTable:
    """One downloadable table listed on the SEKI index."""

    #: "TABEL1_1" — stable across months, so it is the filename stem too.
    table_id: str
    #: "I.1." — Bank Indonesia's own numbering.
    number: str
    title: str
    section: str
    url: str


def _text(fragment: str) -> str:
    return _WHITESPACE.sub(" ", html_module.unescape(_TAG.sub(" ", fragment))).strip()


def _table_id(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def extract_tables(content: bytes, base_url: str, *, limit: int | None = None) -> list[SekiTable]:
    """Every Excel table on the index, in page order.

    A row with no .xls link — some tables are PDF-only — is skipped rather than
    guessed at. The PDFs hold the same numbers rendered for print, so nothing is
    lost by ignoring them here.
    """
    markup = content.decode("utf-8", errors="replace")

    # Sections and rows are matched in one pass so each row inherits the heading
    # above it. Two separate scans would lose that relationship entirely.
    combined = re.compile(f"{_SECTION.pattern}|{_ROW.pattern}", re.I | re.S)

    tables: list[SekiTable] = []
    section = ""
    seen: set[str] = set()

    for match in combined.finditer(markup):
        if match.group("section"):
            section = _text(match.group("section"))
            continue

        href = _XLS_HREF.search(match.group("rest") or "")
        if not href:
            continue

        url = urljoin(base_url, html_module.unescape(href.group(1)))
        if urlparse(url).hostname not in ALLOWED_HOSTS:
            continue

        table_id = _table_id(url)
        if not table_id or table_id in seen:
            continue
        seen.add(table_id)

        tables.append(
            SekiTable(
                table_id=table_id,
                number=_text(match.group("number")),
                title=_text(match.group("title")),
                section=section,
                url=url,
            )
        )
        if limit is not None and len(tables) >= limit:
            break

    return tables
