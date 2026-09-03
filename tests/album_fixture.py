"""Builds a synthetic album page in the shape `docs/spikes.md` G1 documents.

Synthetic on purpose. A real capture carries the owner id and the album `key`, and this
repo is meant to be published. Only the fields the source reads are filled in; everything
else is padding at the right index.
"""

from __future__ import annotations

import json
from typing import Any

BASE_URL = "https://lh3.googleusercontent.com/pw/EXAMPLE"

_ALBUM_LENGTH = 43
_ITEM_COUNT_INDEX = 21


def item(source_id: str, width: int, height: int, *, hash_field: str = "Ga9a7emx") -> list[Any]:
    return [
        source_id,
        [f"{BASE_URL}/{source_id}", width, height, None, None, None, None, None, [1, 2], [40000]],
        1680452105564,
        hash_field,
        -25200000,
        1788277106538,
        ["owner"],
        [],
        2,
        {},
    ]


def album_page(
    items: list[list[Any]],
    *,
    token: str = "",
    reported_count: int | None = None,
    album_length: int = _ALBUM_LENGTH,
) -> str:
    fields = {
        0: "ALBUMID",
        1: "Frame TV",
        19: "ALBUMKEY",
        _ITEM_COUNT_INDEX: len(items) if reported_count is None else reported_count,
        32: "https://photos.app.goo.gl/EXAMPLE",
    }
    album: list[Any] = [fields.get(index) for index in range(album_length)]

    payload = [None, items, token, album, None, 0]
    return (
        "<html><body><script nonce='x'>"
        "AF_initDataCallback({key: 'ds:0', hash: '1', data:[], sideChannel: {}});"
        f"AF_initDataCallback({{key: 'ds:1', hash: '2', data:{json.dumps(payload)},"
        " sideChannel: {}});"
        "</script></body></html>"
    )
