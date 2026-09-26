"""Issues, and what coding one means.

The crawl has no notion of violence, economics or politics. It finds articles
and stores them. A profile is what turns an article into a coded event: the
terms to search for, the questions to ask about what was found, and the shape
of the row that comes out.

Adding an issue is adding a profile. Nothing in discovery, fetching, landing,
extraction or serving needs to know it exists — which is the whole reason the
corpus is issue-agnostic, and the reason an article found by the violence terms
can be tagged as economic too without being fetched twice.
"""

from __future__ import annotations

from .base import Coding, Profile, profile, profiles, register
from .violence import VIOLENCE

__all__ = ["Coding", "Profile", "VIOLENCE", "profile", "profiles", "register"]
