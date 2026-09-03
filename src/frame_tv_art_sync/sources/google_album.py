"""Reads a link-shared Google Photos album by scraping the JSON its public page embeds.

Nothing here is documented or sanctioned by Google, so treat a parse failure as expected
maintenance. `docs/spikes.md` G1 records the structure this expects, and G4 records why the
size suffixes below are the ones to ask for.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from . import SourceError, SourceItem

SOURCE_NAME = "google_album"

# The bare `lh3` URL serves a 512x384 thumbnail, so a suffix is not optional. `-n` is an
# exact center crop to the panel's 1920x1080; the plain form fits inside that box instead,
# returning the whole frame at native resolution, which is what a portrait goes up as for the
# TV to mat. `-c` looks like the center crop and isn't, so it is deliberately absent here.
CROP_SUFFIX = "=w1920-h1080-n"
FIT_SUFFIX = "=w1920-h1080"

# The album payload lives in a script tag as a JS call whose `data:` argument is real JSON.
# Two blocks are present and `ds:0` is empty.
_BLOCK_MARKER = "AF_initDataCallback({key: 'ds:1'"
_DATA_KEY = "data:"

# Positions in the `ds:1` payload and in each item, per G1.
_ITEMS = 1
_CONTINUATION_TOKEN = 2
_ALBUM = 3
_ALBUM_ITEM_COUNT = 21

_DEFAULT_TIMEOUT = 30.0


class AlbumReadError(SourceError):
    """The album page could not be fetched or its shape no longer matches G1."""


class AlbumTruncatedError(AlbumReadError):
    """The page holds only part of the album.

    Raised rather than returned, because `frame sync` mirrors: a short list would read as
    photos having been removed from the album and delete them off the TV.
    """


class GoogleAlbumSource:
    """A link-shared Google Photos album, read from its public page."""

    name = SOURCE_NAME

    def __init__(self, url: str, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.url = url
        self.timeout = timeout

    def items(self) -> list[SourceItem]:
        return parse_album_page(self._fetch())

    def _fetch(self) -> str:
        """Fetch the album page, following the `photos.app.goo.gl` redirect to the real URL.

        The share link's `key` parameter is what authorizes this, so no cookies, no
        `User-Agent`, and no JavaScript are needed.
        """
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            hint = " The share link's `key` parameter is required." if error.code == 404 else ""
            raise AlbumReadError(f"The album page returned HTTP {error.code}.{hint}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise AlbumReadError(f"Could not reach the album page: {error}") from None


def parse_album_page(html: str) -> list[SourceItem]:
    """Turn an album page into items, refusing anything that might be a partial list."""
    payload = _extract_payload(html)
    items = _payload_field(payload, _ITEMS)
    token = _payload_field(payload, _CONTINUATION_TOKEN)

    if not isinstance(items, list):
        raise AlbumReadError("The `ds:1` payload holds no item list. The page shape changed.")

    if token:
        raise AlbumTruncatedError(
            f"The album paginates: it handed back the continuation token {token!r} and this "
            "tool cannot redeem one yet. See `docs/spikes.md` G3."
        )

    reported = _reported_item_count(payload)
    if reported is not None and reported != len(items):
        raise AlbumTruncatedError(
            f"The album says it holds {reported} photos but the page listed {len(items)}."
        )

    return [_item(entry, index) for index, entry in enumerate(items)]


def _extract_payload(html: str) -> list[Any]:
    """Pull the `ds:1` payload out of the page.

    The JSON is decoded straight off its offset rather than matched with a closing
    delimiter, so a photo caption containing `);` can't truncate it.
    """
    block = html.find(_BLOCK_MARKER)
    if block == -1:
        raise AlbumReadError(
            "The album page has no `ds:1` data block, which is the assumption the whole sync "
            "feature rests on. Re-run `.claude/tmp/g_probe.py` and see `docs/spikes.md` G1."
        )

    key = html.find(_DATA_KEY, block)
    if key == -1:
        raise AlbumReadError("The `ds:1` block has no `data:` field. The page shape changed.")

    start = key + len(_DATA_KEY)
    while start < len(html) and html[start].isspace():
        start += 1

    try:
        payload, _ = json.JSONDecoder().raw_decode(html, start)
    except json.JSONDecodeError as error:
        raise AlbumReadError(f"The `ds:1` payload is not valid JSON: {error}") from None

    if not isinstance(payload, list):
        raise AlbumReadError("The `ds:1` payload is not the array G1 describes.")

    return payload


def _payload_field(payload: list[Any], index: int) -> Any:
    return payload[index] if index < len(payload) else None


def _reported_item_count(payload: list[Any]) -> int | None:
    """The album's own count, which is only worth reading as a cross-check on the item list."""
    album = _payload_field(payload, _ALBUM)
    if not isinstance(album, list) or _ALBUM_ITEM_COUNT >= len(album):
        return None

    count = album[_ALBUM_ITEM_COUNT]
    return count if isinstance(count, int) and not isinstance(count, bool) else None


def _item(entry: Any, index: int) -> SourceItem:
    if not isinstance(entry, list) or len(entry) < 2:
        raise AlbumReadError(f"Item {index} is not the array G1 describes.")

    source_id = entry[0]
    media = entry[1]
    if not isinstance(source_id, str) or not source_id:
        raise AlbumReadError(f"Item {index} has no media id.")
    if not isinstance(media, list) or len(media) < 3:
        raise AlbumReadError(f"Item {index} ({source_id}) has no url and dimensions.")

    base_url, width, height = media[0], media[1], media[2]
    if not isinstance(base_url, str) or not base_url:
        raise AlbumReadError(f"Item {index} ({source_id}) has no image url.")
    if not _is_positive_int(width) or not _is_positive_int(height):
        raise AlbumReadError(
            f"Item {index} ({source_id}) reports dimensions {width!r}x{height!r}, which the "
            "framing decision needs as positive integers."
        )

    return SourceItem(
        source_id=source_id,
        url=base_url + size_suffix(width, height),
        width=width,
        height=height,
    )


def size_suffix(width: int, height: int) -> str:
    """Pick the suffix to request, which orientation decides.

    A portrait frame has no good 16:9 crop -- `-n` on a 3:4 photo keeps 42% of the frame
    height -- so ask for the whole frame instead and let the TV mat it.
    """
    return FIT_SUFFIX if height > width else CROP_SUFFIX


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
