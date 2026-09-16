"""Otoritas Jasa Keuangan (OJK) sources."""

from .banking import BankingStatistics
from .fintech import PeerToPeerLending

__all__ = ["BankingStatistics", "PeerToPeerLending"]
