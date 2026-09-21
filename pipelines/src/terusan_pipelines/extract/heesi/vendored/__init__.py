"""Vendored HEESI table extraction, as it arrived.

Ported from the standalone `heesi-dfd` project with two edits and nothing else:
its intra-package imports are relative, and `sheets.yaml` resolves beside this
module rather than against the working directory. Everything that knows where
a table sits in the handbook — the title patterns, the chapter hints, the
per-orientation parsers, and the comments recording why a page needs each
trick — is kept verbatim. That knowledge is the expensive part, and it does not
survive a rewrite.

What was left behind: the xlsx builder, the combined-CSV writer, the drift
report and the Google Sheet tracker. Those wrote into another project's
database export, which Silver replaces here.
"""

from .config import SheetConfig, load_sheets
from .extract import TIDY_COLUMNS, extract_edition, extract_sheet
from .locate import SectionNotFound

__all__ = [
    "TIDY_COLUMNS",
    "SectionNotFound",
    "SheetConfig",
    "extract_edition",
    "extract_sheet",
    "load_sheets",
]
