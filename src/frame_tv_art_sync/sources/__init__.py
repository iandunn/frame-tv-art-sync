"""The interface every photo source implements.

A source is a parser, not a downloader: it resolves each item to a URL the bytes can be
fetched from and stops there, so `frame sync --dry-run` can enumerate an album without
touching a single image.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SourceItem:
    """One photo, as its source describes it.

    `source_id` has to be stable across runs, since it is half of the inventory key that
    tells this tool's uploads apart from art added by hand. `width` and `height` are the
    source's own reported display dimensions with rotation already applied, which is what
    decides the framing; the image actually served can be smaller.

    `taken_at_ms` is when the photo was taken, in epoch milliseconds, and it exists so that a
    short run can pick the newest photos rather than whichever ones a source happened to list
    first. A source with nothing to report puts 0 there, which sorts oldest.
    """

    source_id: str
    url: str
    width: int
    height: int
    taken_at_ms: int = 0

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width


class Source(Protocol):
    """A named collection of items. The name is what the inventory attributes uploads to."""

    name: str

    def items(self) -> list[SourceItem]: ...


class SourceError(Exception):
    """A source could not produce a complete, trustworthy item list."""
