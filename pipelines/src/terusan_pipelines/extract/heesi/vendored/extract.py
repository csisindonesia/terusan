"""Turn configured HEESI PDF tables into tidy long-format rows."""
from __future__ import annotations

import re
import warnings
from collections import Counter
from pathlib import Path

import pandas as pd
import pdfplumber

from .config import SheetConfig, load_sheets
from .locate import SectionNotFound, _page_texts, find_table_page
from .normalize import _BLANKS, clean_label, parse_number, parse_year

_YAC_STOP_RE = re.compile(r"(?i)^\s*(sources?|notes?|catatan|sumber)\b")

TIDY_COLUMNS = ["data_id", "key", "xlsx_col", "variable_id", "year", "value", "combined"]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", clean_label(s).lower())


def _column_matcher(cfg_labels: list[str], pdf_labels: list[str]) -> dict[str, str]:
    """Map each pdf label -> the config label it represents (or '' if none)."""
    out: dict[str, str] = {}
    norm_cfg = {lbl: _norm(lbl) for lbl in cfg_labels}
    for pl in pdf_labels:
        npl = _norm(pl)
        best = ""
        for cl, ncl in norm_cfg.items():
            if npl == ncl or (npl and (npl in ncl or ncl in npl)):
                best = cl
                break
        out[pl] = best
    return out


def _canonical_width(lines: list[list[str]], target: int) -> int:
    """The value-column count shared by *most* data lines.

    Using the mode (not the max) means a single line with a stray leading token
    - a footnote split off as ``"2)"`` or a prose sentence that happens to start
    with a year - cannot inflate the width and silently shift every later
    position. Ties break toward the width closest to the configured column
    count."""
    widths = [len(p) - 1 for p in lines if len(p) >= 2]
    if not widths:
        return 0
    counts = Counter(widths)
    top = max(counts.values())
    best = [w for w, c in counts.items() if c == top]
    return min(best, key=lambda w: (abs(w - target), w))


def _data_lines(text: str) -> list[list[str]]:
    """The first contiguous grid of year-leading rows on the page.

    A single page can stack two year-leading grids - sheet 16's PLN production
    grid followed by a "PLN Purchase from IPP & PPU / Off Grid" continuation,
    sheet 18's "On Grid" block followed by an "Off Grid" block. Both grids start
    every row with a year, so a naive sweep concatenates them and emits
    duplicate ``(year, column)`` rows. Instead, split the year-leading lines
    into runs - a run ends at a non-year line, or at a year that does not
    exceed the previous one (the sequence restarts) - and return the first run
    that forms a plausible grid (>= 3 rows). A single-grid page yields exactly
    one run and its output is unchanged.
    """
    groups: list[list[list[str]]] = []
    cur: list[list[str]] = []
    prev_year: int | None = None
    for line in text.splitlines():
        parts = line.split()
        year = parse_year(parts[0]) if parts else None
        if year is not None and len(parts) >= 2:
            if cur and prev_year is not None and year <= prev_year:
                groups.append(cur)
                cur = []
            cur.append(parts)
            prev_year = year
        elif cur:
            groups.append(cur)
            cur = []
            prev_year = None
    if cur:
        groups.append(cur)
    if not groups:
        return []
    return next((g for g in groups if len(g) >= 3), groups[0])


def _table_data_rows(page) -> list[list[str]]:
    """Fallback for pages where the data grid is not recoverable from the raw
    text (e.g. two tables printed side by side). Collect every extracted-table
    row whose first cell parses as a year."""
    rows: list[list[str]] = []
    for tbl in page.extract_tables() or []:
        for raw in tbl:
            if not raw:
                continue
            year = parse_year(raw[0])
            if year is None:
                continue
            cells = [str(year)] + [
                (c if c not in (None, "") else "") for c in raw[1:]
            ]
            # drop trailing empty cells
            while len(cells) > 1 and cells[-1] == "":
                cells.pop()
            if len(cells) >= 2:
                rows.append(cells)
    return rows


def _header_labels_from_tables(page, cfg: SheetConfig) -> list[str] | None:
    tables = page.extract_tables()
    for tbl in tables or []:
        for row in tbl[: max(cfg.header_rows, 3) or 3]:
            cells = [clean_label(c or "") for c in row]
            if sum(bool(c) for c in cells) >= 2 and not any(
                parse_year(c) for c in cells[:2]
            ):
                return cells
    return None


def _locate_via_tables(pdf, cfg: SheetConfig) -> int:
    """Last resort when `find_table_page` finds no text data-grid: scan the
    pages whose text matches the title for one whose *extracted tables* hold a
    year-leading data grid."""
    pat = re.compile(cfg.title_regex, re.IGNORECASE)
    for idx, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if not pat.search(text):
            continue
        if len(_table_data_rows(page)) >= 3:
            return idx
    raise SectionNotFound(cfg.title_regex)


def _ordered_cfg_cols(cfg: SheetConfig) -> list[str]:
    seen, out = set(), []
    for lbl in cfg.columns:
        if lbl not in seen:
            out.append(lbl)
            seen.add(lbl)
    return out


def extract_sheet(pdf, cfg: SheetConfig) -> list[dict]:
    if cfg.orientation == "matrix":
        return _extract_matrix(pdf, cfg)
    if cfg.orientation == "derived_from_matrix":
        return _extract_derived(pdf, cfg)
    if cfg.orientation == "years_as_columns":
        try:
            idx = _locate_years_as_columns(pdf, cfg)
        except SectionNotFound:
            idx = _locate_via_tables(pdf, cfg)
    else:
        try:
            idx = find_table_page(
                pdf, cfg.title_regex, chapter_hint=cfg.chapter_hint
            )
        except SectionNotFound:
            idx = _locate_via_tables(pdf, cfg)
    page = pdf.pages[idx]
    text = page.extract_text() or ""

    if cfg.orientation == "years_as_rows":
        return _extract_years_as_rows(page, text, cfg)
    if cfg.orientation == "years_as_columns":
        return _extract_years_as_columns(page, text, cfg)
    raise ValueError(f"unknown orientation {cfg.orientation!r}")


def _row(cfg: SheetConfig, col: str, year: int, value):
    vid = cfg.columns.get(col)
    return {
        "data_id": cfg.data_id,
        "key": cfg.key,
        "xlsx_col": col,
        "variable_id": vid,
        "year": year,
        "value": value,
        "combined": cfg.combined and vid is not None,
    }


def _anomaly_row(cfg: SheetConfig) -> dict:
    return {
        "data_id": cfg.data_id,
        "key": cfg.key,
        "xlsx_col": "__ANOMALY__",
        "variable_id": None,
        "year": None,
        "value": None,
        "combined": False,
    }


def _extract_years_as_rows(page, text, cfg: SheetConfig) -> list[dict]:
    cfg_cols = _ordered_cfg_cols(cfg)
    # When the PDF prints column-group sub-headers (sheets 20, 21) or an
    # off-by-position value order, `value_columns` gives the left-to-right PDF
    # value-column order verbatim - more robust than matching a split header.
    order_cols = cfg.value_columns or cfg_cols
    lines = _data_lines(text)

    if len(lines) < 3:
        # text layout unusable - rebuild the grid from extracted tables and
        # align strictly by config order.
        lines = _table_data_rows(page)
        if len(lines) < 3:
            raise ValueError(f"{cfg.key}: no year rows found on located page")
        width = _canonical_width(lines, len(order_cols))
        ordered = order_cols if cfg.value_columns else order_cols[:width]
        return _rows_from_lines(lines, ordered, cfg, width)

    width = _canonical_width(lines, len(order_cols))

    if cfg.value_columns:
        # Use the configured PDF value-column order verbatim; skip header
        # matching entirely.
        return _rows_from_lines(lines, cfg.value_columns, cfg, width)

    # Try to align PDF header labels; fall back to config order.
    ordered = cfg_cols[:width]
    pdf_header = _header_labels_from_tables(page, cfg)
    if pdf_header:
        pdf_value_labels = [c for c in pdf_header if c and not parse_year(c)]
        match = _column_matcher(cfg_cols, pdf_value_labels)
        aligned = [match.get(pl, "") for pl in pdf_value_labels]
        aligned = [c for c in aligned if c]
        if len(aligned) == width:
            ordered = aligned

    return _rows_from_lines(lines, ordered, cfg, width)


_FOOTNOTE_TOKEN_RE = re.compile(r"^(?:\d{1,2}\)|\*+)$")


def _fit_to_width(values: list[str], width: int) -> list[str] | None:
    """Coerce a data line's value tokens to the canonical width, or give up.

    - ``== width``: unchanged.
    - ``< width``: a legitimate short row - the rightmost PDF column(s) are
      blank for this year - right-padded with ``None``.
    - ``> width``: salvaged when the surplus is leading non-numeric junk after
      the year (e.g. a footnote token ``"2)"``) and/or a single trailing
      footnote marker on the last value. Anything still too wide returns None so
      the caller records it as an anomaly instead of emitting shifted values.
    """
    if len(values) == width:
        return list(values)
    if len(values) < width:
        return list(values) + [None] * (width - len(values))
    trimmed = list(values)
    while len(trimmed) > width and parse_number(trimmed[0]) is None:
        trimmed.pop(0)
    if len(trimmed) > width and _FOOTNOTE_TOKEN_RE.match(str(trimmed[-1])):
        trimmed.pop()
    if len(trimmed) == width:
        return trimmed
    return None


def _rows_from_lines(
    lines, ordered, cfg: SheetConfig, width: int | None = None
) -> list[dict]:
    rows: list[dict] = []
    anomalies: list[str] = []
    for parts in lines:
        year = parse_year(parts[0])
        values = parts[1:]
        if width is not None:
            fitted = _fit_to_width(values, width)
            if fitted is None:
                anomalies.append(" ".join(str(p) for p in parts))
                continue
            values = fitted
        for pos, col in enumerate(ordered):
            if pos >= len(values):
                continue
            rows.append(_row(cfg, col, year, parse_number(values[pos])))
    for detail in anomalies:
        warnings.warn(
            f"{cfg.key}: skipped unparseable data line (width mismatch): {detail}",
            stacklevel=2,
        )
        rows.append(_anomaly_row(cfg))
    return rows


def _year_run(parts: list[str]) -> list[int] | None:
    """The years in a *header* line: a strictly increasing, gap-free run of at
    least four. Rejects prose sentences and broken chart axes that merely happen
    to contain several year-like tokens."""
    years = [y for y in (parse_year(p) for p in parts) if y is not None]
    if len(years) < 4:
        return None
    if years == sorted(years) and max(years) - min(years) + 1 == len(years):
        return years
    return None


def _yac_value_like(tok: str) -> bool:
    return parse_number(tok) is not None or str(tok).strip().lower() in _BLANKS


def _yac_tokens(label: str) -> set[str]:
    return {t for t in (_norm(w) for w in clean_label(label).split()) if t}


def _locate_years_as_columns(pdf, cfg: SheetConfig) -> int:
    """First page whose text matches the title *and* carries a real year-header
    row (see ``_year_run``). ``extract_tables`` on these wide statistical pages
    returns ragged, column-truncated grids, so location and extraction both work
    from the page text."""
    pat = re.compile(cfg.title_regex, re.IGNORECASE)
    for idx, text in enumerate(_page_texts(pdf)):
        if not pat.search(text):
            continue
        if any(_year_run(line.split()) for line in text.splitlines()):
            return idx
    raise SectionNotFound(cfg.title_regex)


def _extract_years_as_columns(page, text, cfg: SheetConfig) -> list[dict]:
    lines = text.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if _year_run(line.split())), None
    )
    if header_idx is None:
        raise ValueError(f"{cfg.key}: no year header row on located page")
    years = _year_run(lines[header_idx].split())
    n = len(years)

    fuzzy = cfg.fuzzy_labels
    norm_cols = {k: _norm(k) for k in cfg.columns}
    rows: list[dict] = []
    used: set[str] = set()
    pending = ""
    i = header_idx + 1
    while i < len(lines):
        parts = lines[i].split()
        if not parts:
            pending = ""
            i += 1
            continue
        if _year_run(parts) or _YAC_STOP_RE.match(lines[i]):
            break

        trailing = parts[-n:]
        is_data = (
            (len(parts) > n or (len(parts) == n and pending))
            and all(_yac_value_like(t) for t in trailing)
            and any(parse_number(t) is not None for t in trailing)
        )
        if not is_data:
            # a label line that wrapped off its value row - remember it as a
            # prefix for the next data row.
            if not any(_yac_value_like(t) for t in parts):
                pending = f"{pending} {lines[i].strip()}".strip()
            i += 1
            continue

        label_prefix = " ".join(parts[: len(parts) - n])
        # Labels can also wrap *after* the value row (badly ordered text layout);
        # in fuzzy mode pull the following non-value lines into the label too.
        j = i + 1
        tail: list[str] = []
        if fuzzy:
            while j < len(lines):
                pj = lines[j].split()
                if (
                    not pj
                    or _year_run(pj)
                    or _YAC_STOP_RE.match(lines[j])
                    or any(_yac_value_like(t) for t in pj)
                ):
                    break
                # If the *next* line is a bare value row (exactly `n` value
                # tokens, no inline label), this line is that row's own label -
                # a new record - not a trailing fragment of the current one.
                nxt = lines[j + 1].split() if j + 1 < len(lines) else []
                if (
                    len(nxt) == n
                    and all(_yac_value_like(t) for t in nxt)
                    and any(parse_number(t) is not None for t in nxt)
                ):
                    break
                tail.append(lines[j].strip())
                j += 1

        label = clean_label(
            " ".join(x for x in (pending, label_prefix, " ".join(tail)) if x)
        )
        pending = ""
        i = j

        norm_label = _norm(label)
        vid: int | None = None
        key = label
        for col, ncol in norm_cols.items():
            if ncol == norm_label:
                vid, key = cfg.columns[col], col
                break
        if vid is None and fuzzy:
            for col in cfg.columns:
                if col not in used and _yac_tokens(col) <= _yac_tokens(label):
                    vid, key = cfg.columns[col], col
                    break

        if key in used:
            continue
        used.add(key)
        if vid is None and fuzzy:
            # targeted extraction: don't emit the sheet's other rows
            continue
        for pos, year in enumerate(years):
            rows.append(
                {
                    "data_id": cfg.data_id,
                    "key": cfg.key,
                    "xlsx_col": key,
                    "variable_id": vid,
                    "year": year,
                    "value": parse_number(trailing[pos]),
                    "combined": cfg.combined and vid is not None,
                }
            )
    return rows


_MATRIX_SECTION_RE = re.compile(r"^([1-8])\s+([A-Za-z].*)$")
_MATRIX_LETTER_RE = re.compile(r"^([a-z])\.\s+(.*)$")
_MATRIX_DASH_RE = re.compile(r"^-\s+(.*)$")
_MATRIX_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
# The energy-type grid is stable from "Industrial Biomass" through "Total"; the
# only header that varies between editions is the "Solar Powered Public Street
# Lighting ..." (LTSHE) column, present in the 2024 edition and dropped in 2025.
_MATRIX_STABLE_TAIL = 15


def _matrix_trailing_numbers(tokens: list[str]) -> list[float]:
    """The maximal run of numeric tokens at the end of a line (the value row)."""
    out: list[float] = []
    for tok in reversed(tokens):
        num = parse_number(tok) if _MATRIX_NUM_RE.match(tok) else None
        if num is None:
            break
        out.append(num)
    out.reverse()
    return out


def _matrix_year(text: str, pat: re.Pattern) -> int | None:
    for line in text.splitlines():
        if pat.search(line):
            year = parse_year(line)
            if year is not None:
                return year
    return parse_year(text)


def _extract_matrix(pdf, cfg: SheetConfig) -> list[dict]:
    """Energy-balance style: one edition-year per table, rows are balance lines
    keyed by a numeric Code (100 Primary Energy Supply, 110 a. Production, ...),
    columns are energy types plus a trailing Total. The table is not
    year-leading, so it is located and parsed here rather than via
    ``find_table_page`` / the year-leading grid parsers.

    The Code is reconstructed from the PDF's outline numbering: ``N Label`` ->
    ``N*100``; ``x. Label`` under section N -> ``N*100 + 10*rank(x)``; ``- Label``
    -> previous sub-code + running counter. This matches the xlsx Code scheme.
    """
    pat = re.compile(cfg.title_regex, re.IGNORECASE)
    labels_cfg = list(cfg.energy_type_columns or [])
    seen: dict[tuple, dict] = {}

    for text in _page_texts(pdf):
        if not pat.search(text):
            continue
        year = _matrix_year(text, pat)
        if year is None:
            continue
        major = minor = None
        subctr = 0
        pending_code: int | None = None
        for raw in text.splitlines():
            s = raw.strip()
            if not s:
                continue
            tokens = s.split()
            code: int | None = None
            m_sec = _MATRIX_SECTION_RE.match(s)
            m_let = _MATRIX_LETTER_RE.match(s)
            if m_sec:
                major = int(m_sec.group(1)) * 100
                minor, subctr, code = None, 0, major
            elif m_let and major is not None:
                rank = ord(m_let.group(1)) - ord("a") + 1
                minor = major + 10 * rank
                subctr, code = 0, minor
            elif _MATRIX_DASH_RE.match(s) and minor is not None:
                subctr += 1
                code = minor + subctr

            nums = _matrix_trailing_numbers(tokens)
            if code is not None:
                pending_code = code
                if not nums:
                    continue
            elif nums and pending_code is not None and len(nums) >= _MATRIX_STABLE_TAIL:
                code = pending_code
            else:
                continue

            labels = labels_cfg
            if len(nums) == len(labels_cfg) - 1:
                # 2025+ layout: the "Public Street Lighting ..." (LTSHE) column
                # present in the 2024 edition is absent.
                labels = [c for c in labels_cfg if "Street Lighting" not in c]
            if len(nums) != len(labels):
                if len(nums) >= 10:
                    warnings.warn(
                        f"{cfg.key}: skipped balance line "
                        f"(got {len(nums)} values, expected {len(labels)}): {s[:60]}",
                        stacklevel=2,
                    )
                continue

            for label, value in zip(labels, nums):
                key = (year, code, label)
                seen.setdefault(
                    key,
                    {
                        "data_id": None,
                        "key": cfg.key,
                        "xlsx_col": label,
                        "variable_id": None,
                        "year": year,
                        "value": value,
                        "combined": False,
                        "code": code,
                    },
                )
    return list(seen.values())


def _extract_derived(pdf, cfg: SheetConfig) -> list[dict]:
    """Re-emit one series lifted from a matrix sheet as ``years_as_rows`` tidy
    rows (sheet 12 = the code-110 'a. Production' Total of the energy balance)."""
    src = next(s for s in load_sheets() if s.key == cfg.source_key)
    matrix_rows = _extract_matrix(pdf, src)

    if any(r.get("xlsx_col") == "__NEEDS_MANUAL__" for r in matrix_rows):
        return [
            {
                "data_id": cfg.data_id,
                "key": cfg.key,
                "xlsx_col": "__NEEDS_MANUAL__",
                "variable_id": None,
                "year": None,
                "value": None,
                "combined": False,
            }
        ]

    sel = cfg.select or {}
    want_code, want_col = sel.get("code"), sel.get("xlsx_col")
    target_col, vid = (
        next(iter(cfg.columns.items())) if cfg.columns else (cfg.key, None)
    )
    rows: list[dict] = []
    for r in matrix_rows:
        if r.get("code") == want_code and r.get("xlsx_col") == want_col:
            rows.append(
                {
                    "data_id": cfg.data_id,
                    "key": cfg.key,
                    "xlsx_col": target_col,
                    "variable_id": vid,
                    "year": r["year"],
                    "value": r["value"],
                    "combined": cfg.combined and vid is not None,
                }
            )
    return rows


def extract_edition(
    pdf_path: Path, sheets: list[SheetConfig] | None = None
) -> pd.DataFrame:
    sheets = sheets or load_sheets()
    all_rows: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for cfg in sheets:
            try:
                all_rows.extend(extract_sheet(pdf, cfg))
            except Exception as exc:  # noqa: BLE001 - collected for the report
                all_rows.append(
                    {
                        "data_id": cfg.data_id,
                        "key": cfg.key,
                        "xlsx_col": "__ERROR__",
                        "variable_id": None,
                        "year": None,
                        "value": None,
                        "combined": False,
                        "error": repr(exc),
                    }
                )
    df = pd.DataFrame(all_rows)
    for col in TIDY_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df
