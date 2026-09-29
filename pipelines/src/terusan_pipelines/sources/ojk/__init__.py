"""Otoritas Jasa Keuangan (OJK) sources."""

from .banking import BankingStatistics
from .fintech import PeerToPeerLending
from .sjk import SjkBankingStatistics

__all__ = ["BankingStatistics", "PeerToPeerLending", "SjkBankingStatistics"]
