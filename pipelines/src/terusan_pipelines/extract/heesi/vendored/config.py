"""Load the per-sheet extraction config."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

#: Packaged beside this module rather than resolved from the working
#: directory: an extractor runs from wherever the CLI was invoked.
DEFAULT_PATH = Path(__file__).resolve().parent / "sheets.yaml"


@dataclass
class SheetConfig:
    key: str
    xlsx_sheet: str
    title_regex: str
    orientation: str
    data_id: int | None = None
    chapter_hint: int | None = None
    year_axis_col: int = 0
    header_rows: int = 0
    columns: dict[str, int | None] = field(default_factory=dict)
    unit: str = ""
    checks: list[dict] = field(default_factory=list)
    combined: bool = True
    tolerance: dict | None = None
    row_keys: list[str] | None = None          # matrix orientation
    subheader_rows: int = 0                     # sub-header orientations
    value_columns: list[str] | None = None      # explicit PDF value-column order (left-to-right)
    row_label_col: int = 0                      # years_as_columns: row-label column
    xlsx_orientation: str | None = None         # xlsx layout if it differs from `orientation`
    xlsx_value_columns: list[str] | None = None # xlsx column(s) the series sits in
    fuzzy_labels: bool = False                  # years_as_columns: token-subset row matching + skip unmapped
    energy_type_columns: list[str] | None = None  # matrix: PDF energy-type column order (left-to-right, ends "Total")
    source_key: str | None = None               # derived_from_matrix: the matrix sheet to derive from
    select: dict | None = None                  # derived_from_matrix: {code, xlsx_col} filter on the matrix rows


def load_sheets(path: Path = DEFAULT_PATH) -> list[SheetConfig]:
    raw = yaml.safe_load(Path(path).read_text())
    return [SheetConfig(**entry) for entry in raw]
