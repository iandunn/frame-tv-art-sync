"""Covers the album parser, the suffix decision, and the refusal to return a partial list."""

from __future__ import annotations

import pytest
from album_fixture import BASE_URL, album_page, item

from frame_tv_art_sync.sources.google_album import (
    CROP_SUFFIX,
    FIT_SUFFIX,
    AlbumReadError,
    AlbumTruncatedError,
    parse_album_page,
    size_suffix,
)


def test_reads_id_dimensions_and_url():
    items = parse_album_page(album_page([item("AF1QipLandscape", 4032, 3024)]))

    assert len(items) == 1
    assert items[0].source_id == "AF1QipLandscape"
    assert (items[0].width, items[0].height) == (4032, 3024)
    assert items[0].url == f"{BASE_URL}/AF1QipLandscape{CROP_SUFFIX}"


def test_keeps_album_order():
    page = album_page([item("AF1QipA", 4032, 3024), item("AF1QipB", 3024, 4032)])

    assert [entry.source_id for entry in parse_album_page(page)] == ["AF1QipA", "AF1QipB"]


def test_empty_album_reads_as_no_items():
    assert parse_album_page(album_page([])) == []


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (4032, 3024, CROP_SUFFIX),
        (1920, 1080, CROP_SUFFIX),
        (5000, 1000, CROP_SUFFIX),
        (1080, 1080, CROP_SUFFIX),
        (3024, 4032, FIT_SUFFIX),
    ],
)
def test_orientation_decides_the_suffix(width, height, expected):
    assert size_suffix(width, height) == expected


def test_a_caption_holding_a_call_terminator_does_not_truncate_the_payload():
    page = album_page([item("AF1QipA", 4032, 3024, hash_field='a);b"c')])

    assert [entry.source_id for entry in parse_album_page(page)] == ["AF1QipA"]


def test_a_continuation_token_is_refused():
    page = album_page([item("AF1QipA", 4032, 3024)], token="CONTINUE")

    with pytest.raises(AlbumTruncatedError, match="CONTINUE"):
        parse_album_page(page)


def test_a_count_that_disagrees_with_the_item_list_is_refused():
    page = album_page([item("AF1QipA", 4032, 3024)], reported_count=60)

    with pytest.raises(AlbumTruncatedError, match="60"):
        parse_album_page(page)


def test_a_missing_count_field_is_not_treated_as_a_mismatch():
    page = album_page([item("AF1QipA", 4032, 3024)], album_length=4)

    assert len(parse_album_page(page)) == 1


def test_a_page_without_the_data_block_fails_loudly():
    with pytest.raises(AlbumReadError, match="ds:1"):
        parse_album_page("<html><body>Sign in to continue</body></html>")


def test_an_item_missing_its_dimensions_fails_loudly():
    broken = item("AF1QipA", 4032, 3024)
    broken[1][1] = None

    with pytest.raises(AlbumReadError, match="AF1QipA"):
        parse_album_page(album_page([broken]))


def test_an_item_missing_its_url_fails_loudly():
    broken = item("AF1QipA", 4032, 3024)
    broken[1][0] = ""

    with pytest.raises(AlbumReadError, match="AF1QipA"):
        parse_album_page(album_page([broken]))
