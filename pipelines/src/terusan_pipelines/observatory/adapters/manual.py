"""Meta and X: reports landed by hand, not yet read.

Their source reports are listed — the evidence is in RAW, with provenance — but
no figure is read from them until the format a researcher saves has been seen
and a reader written against it. Any Bronze record that arrives meanwhile, from
the generic CSV reader, is quarantined as unread rather than guessed at.
"""

from __future__ import annotations

from .. import taxonomy as tx
from ..model import Observation, Quarantined
from .base import Adapter, BronzeRow


class _UnreadAdapter(Adapter):
    def normalize(self, row: BronzeRow) -> Observation | Quarantined:
        return self.quarantine(
            row, f"no {self.provider.value} reader yet; the saved report's format is unverified"
        )


class MetaAdapter(_UnreadAdapter):
    provider = tx.Provider.META
    sources = ("meta-content-restrictions",)
    license_notes = "Meta Transparency Center; robots.txt prohibits automated collection."
    methodology_notes = (
        "Content restricted under local law, per country and half-year, for Facebook "
        "and Instagram. Collected by hand only."
    )


class XAdapter(_UnreadAdapter):
    provider = tx.Provider.X
    sources = ("x-removal-requests",)
    license_notes = "X Transparency Center."
    methodology_notes = (
        "Legal demands, accounts specified, accounts and posts withheld. The page "
        "draws on an undocumented endpoint, so it is collected by hand only."
    )
