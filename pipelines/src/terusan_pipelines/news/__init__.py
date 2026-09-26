"""News monitoring: what the provincial press reports, coded.

The warehouse holds figures somebody else published. This is the one part that
makes its own: fifty-odd newspapers are read every day, what they report is
coded against a controlled vocabulary, and the codings are counted into series.

The shape is deliberately in three layers, because a monitor that produces only
one of them is not much use:

- **The corpus.** Every article the crawl found, with its outlet, its date and
  its text. Issue-agnostic — an article is not violence or economics, it is an
  article, and it may be tagged as both.
- **The events.** What the coder read out of those articles. One row per
  incident, in the columns the human coders of VEWS already use, so the machine
  record and the human record can be laid side by side.
- **The counts.** Events per province per month, which is the only part of this
  that is an observation in the warehouse's sense.

`profiles` is what makes the first layer worth having. An issue — violence
today, economics and politics later — declares its own search terms, its own
questions and its own event columns, and the crawl itself knows nothing about
any of them.
"""

from .outlets import Outlet, active_outlets, load_outlets

__all__ = ["Outlet", "active_outlets", "load_outlets"]
