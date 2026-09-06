"""Covers what a round is made of and what emptying the TV would reach.

The second is the one that matters, because it is the only delete in this tool that reaches an
image no inventory claims, and the thing it must never reach is Samsung's own art.
"""

from __future__ import annotations

import pytest

from frame_tv_art_sync.bakeoff import (
    COMPARE_COLORS,
    COMPARE_TYPES,
    by_luminance,
    newest_of,
    plan_clear,
    variants,
)
from frame_tv_art_sync.inventory import Inventory
from frame_tv_art_sync.mattes import LANDSCAPE, PORTRAIT, MatteError
from frame_tv_art_sync.sources import SourceItem
from frame_tv_art_sync.tv import MatteColor

BAKEOFF = "bakeoff"


def album_item(source_id, width=4032, height=3024, taken_at_ms=1680452105564):
    return SourceItem(
        source_id=source_id,
        url=f"https://lh3.googleusercontent.com/{source_id}=w1920-h1080",
        width=width,
        height=height,
        taken_at_ms=taken_at_ms,
    )


def tv_row(content_id, category_id="MY-C0002", content_type="mobile"):
    return {
        "content_id": content_id,
        "category_id": category_id,
        "content_type": content_type,
        "matte_id": "NONE",
        "width": 1920,
        "height": 1080,
    }


def inventory_of(*entries):
    inventory = Inventory()
    for content_id, source, source_id in entries:
        inventory.record(content_id, source, source_id)
    return inventory


# Which photo a round compares


def test_the_newest_photo_of_that_orientation_is_the_one_tested():
    items = [
        album_item("older", taken_at_ms=1),
        album_item("newest", taken_at_ms=3),
        album_item("portrait", width=3024, height=4032, taken_at_ms=9),
    ]

    assert newest_of(items, LANDSCAPE).source_id == "newest"
    assert newest_of(items, PORTRAIT).source_id == "portrait"


def test_an_album_with_no_photo_of_that_orientation_has_nothing_to_test():
    assert newest_of([album_item("only-landscape")], PORTRAIT) is None


def test_two_rounds_over_an_unchanged_album_compare_the_same_photo():
    tied = [album_item("bbb", taken_at_ms=5), album_item("aaa", taken_at_ms=5)]

    assert newest_of(tied, LANDSCAPE).source_id == newest_of(tied[::-1], LANDSCAPE).source_id


# What varies across a round


def test_a_color_round_covers_every_color_in_the_one_type_that_flexes():
    chosen = variants(
        COMPARE_COLORS, PORTRAIT, color=None, color_order=["polar", "antique", "black"]
    )

    assert [variant.matte_id for variant in chosen] == [
        "flexible_polar",
        "flexible_antique",
        "flexible_black",
    ]
    assert [variant.number for variant in chosen] == [1, 2, 3]


def test_a_type_round_covers_what_the_picker_offers_that_orientation():
    landscape = variants(COMPARE_TYPES, LANDSCAPE, color="polar", color_order=[])
    portrait = variants(COMPARE_TYPES, PORTRAIT, color="polar", color_order=[])

    assert len(landscape) == 6
    assert [variant.matte_id for variant in portrait] == ["flexible_polar", "shadowbox_polar"]


def test_the_bare_type_keeps_its_bare_name():
    chosen = variants(COMPARE_TYPES, LANDSCAPE, color="polar", color_order=[])

    assert "none" in [variant.matte_id for variant in chosen]
    assert "none_polar" not in [variant.matte_id for variant in chosen]


def test_a_type_round_needs_a_color_to_draw_them_in():
    with pytest.raises(MatteError):
        variants(COMPARE_TYPES, LANDSCAPE, color=None, color_order=[])


def test_a_color_the_tv_never_offered_is_refused_before_anything_is_sent():
    with pytest.raises(MatteError):
        variants(COMPARE_TYPES, LANDSCAPE, color="chartreuse", color_order=[])


def test_colors_are_ordered_lightest_first_so_neighbours_are_comparable():
    reported = [
        MatteColor(name="black", rgb=(34, 34, 33)),
        MatteColor(name="polar", rgb=(232, 230, 231)),
        MatteColor(name="antique", rgb=(224, 219, 210)),
    ]

    assert by_luminance(reported) == ["polar", "antique", "black"]


def test_a_color_this_tool_has_no_record_of_is_left_out_rather_than_sent():
    reported = [
        MatteColor(name="polar", rgb=(232, 230, 231)),
        MatteColor(name="new", rgb=(1, 1, 1)),
    ]

    assert by_luminance(reported) == ["polar"]


# What emptying the TV reaches


def test_everything_the_inventory_claims_is_deleted_whatever_uploaded_it():
    inventory = inventory_of(
        ("MY_F0001", "google_album", "AF1QipA"), ("MY_F0002", BAKEOFF, "AF1QipA#flexible_polar")
    )

    clear = plan_clear([tv_row("MY_F0001"), tv_row("MY_F0002")], inventory)

    assert clear.mine == ["MY_F0001", "MY_F0002"]
    assert clear.unmanaged == []


def test_an_upload_no_inventory_claims_is_deleted_too():
    clear = plan_clear([tv_row("MY_F0009")], Inventory())

    assert clear.mine == []
    assert clear.unmanaged == ["MY_F0009"]
    assert clear.delete == ["MY_F0009"]


def test_samsungs_own_art_is_never_a_candidate():
    rows = [
        tv_row("SAM-S10000", category_id="MY-C0008", content_type="server"),
        tv_row("SAM-F0206", category_id="MY-C0008", content_type="preinstall"),
        tv_row("MY_F0009"),
    ]

    clear = plan_clear(rows, Inventory())

    assert clear.delete == ["MY_F0009"]


def test_an_image_whose_rows_disagree_about_what_it_is_gets_left_alone():
    rows = [tv_row("MY_F0009"), tv_row("MY_F0009", category_id="MY-C0009", content_type="myphoto")]

    assert plan_clear(rows, Inventory()).delete == []


def test_an_entry_whose_image_is_already_gone_is_dropped_rather_than_deleted():
    inventory = inventory_of(("MY_F0001", "google_album", "AF1QipA"))

    clear = plan_clear([], inventory)

    assert clear.delete == []
    assert clear.stale == ["MY_F0001"]


def test_an_empty_tv_and_an_empty_inventory_leave_nothing_to_do():
    assert plan_clear([], Inventory()).is_empty
