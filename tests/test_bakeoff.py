"""Covers what a round is made of and what emptying the TV would reach.

Two of these matter more than the rest. A round has to keep the fixed-aperture types off any
shape but a 16:9, because the TV accepts one of those and then crashes Art Mode. And emptying
the TV is the only delete in this tool that reaches an image no inventory claims, so the thing
it must never reach is Samsung's own art.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from frame_tv_art_sync.bakeoff import (
    COMPARE_COLORS,
    COMPARE_TYPES,
    by_luminance,
    candidate_labels,
    newest_per_shape,
    offered_types,
    plan_clear,
    plan_round,
)
from frame_tv_art_sync.config import BakeoffConfig
from frame_tv_art_sync.inventory import Inventory
from frame_tv_art_sync.mattes import (
    FIXED_APERTURE_TYPES,
    LANDSCAPE,
    PORTRAIT,
    MatteError,
    split_matte_id,
)
from frame_tv_art_sync.sources import SourceItem
from frame_tv_art_sync.tv import MatteColor

BAKEOFF = "bakeoff"

# What a config with no `[bakeoff]` table gives, which is every matte the TV offers.
EVERYTHING = BakeoffConfig()


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


def types_by_shape(chosen):
    """The matte types a round puts on each shape, which is the rule that keeps a crash off."""
    grouped: dict[str, set[str]] = {}
    for variant in chosen:
        grouped.setdefault(variant.shape, set()).add(split_matte_id(variant.matte_id)[0])
    return grouped


# Which photos a round compares


def test_a_round_covers_every_shape_the_album_holds():
    items = [
        album_item("wide", width=1920, height=1080),
        album_item("four-three"),
        album_item("tall", width=3024, height=4032),
    ]

    assert [photo.source_id for photo in newest_per_shape(items)] == [
        "wide",
        "four-three",
        "tall",
    ]


def test_shapes_come_back_widest_first_whatever_order_the_album_listed_them():
    items = [
        album_item("tall", width=3024, height=4032),
        album_item("four-three"),
        album_item("wide", width=1920, height=1080),
    ]

    shapes = [f"{photo.width}x{photo.height}" for photo in newest_per_shape(items)]
    assert shapes == ["1920x1080", "4032x3024", "3024x4032"]


def test_two_sizes_a_fraction_of_a_percent_apart_are_one_shape():
    """4080x3072 is 85/64 and 4032x3024 is 4/3, which no eye can tell apart on a 32" panel."""
    items = [
        album_item("older-pixel", width=4080, height=3072, taken_at_ms=1),
        album_item("newer-iphone", width=4032, height=3024, taken_at_ms=9),
    ]

    chosen = newest_per_shape(items)

    assert len(chosen) == 1
    assert chosen[0].source_id == "newer-iphone"


def test_the_newest_photo_of_each_shape_is_the_one_tested():
    items = [
        album_item("older", taken_at_ms=1),
        album_item("newest", taken_at_ms=3),
        album_item("portrait", width=3024, height=4032, taken_at_ms=9),
    ]

    assert [photo.source_id for photo in newest_per_shape(items)] == ["newest", "portrait"]


def test_two_rounds_over_an_unchanged_album_compare_the_same_photos():
    tied = [album_item("bbb", taken_at_ms=5), album_item("aaa", taken_at_ms=5)]

    forwards = newest_per_shape(tied)
    backwards = newest_per_shape(tied[::-1])

    assert [photo.source_id for photo in forwards] == ["bbb"]
    assert [photo.source_id for photo in backwards] == ["bbb"]


def test_an_orientation_narrows_a_round_to_the_shapes_that_are_that_way_round():
    items = [
        album_item("wide", width=1920, height=1080),
        album_item("four-three"),
        album_item("tall", width=3024, height=4032),
    ]

    landscapes = newest_per_shape(items, orientation=LANDSCAPE)
    portraits = newest_per_shape(items, orientation=PORTRAIT)

    assert [photo.source_id for photo in landscapes] == ["wide", "four-three"]
    assert [photo.source_id for photo in portraits] == ["tall"]


def test_an_album_with_no_photo_of_that_orientation_has_nothing_to_test():
    assert newest_per_shape([album_item("only-landscape")], orientation=PORTRAIT) == []


# What varies across a round


def test_a_type_round_keeps_the_fixed_aperture_types_off_every_shape_but_16_9():
    """The crash this whole thing exists to prevent: a fixed aperture on a 4:3 needs a reboot."""
    chosen = plan_round(
        COMPARE_TYPES,
        [album_item("wide", width=1920, height=1080), album_item("four-three")],
        color="polar",
        color_order=[],
        allowed=EVERYTHING,
    )

    drawn = types_by_shape(chosen)
    assert FIXED_APERTURE_TYPES <= drawn["16:9"]
    assert not FIXED_APERTURE_TYPES & drawn["4:3"]


def test_a_type_round_over_a_16_9_covers_all_six_the_tv_draws():
    chosen = plan_round(
        COMPARE_TYPES,
        [album_item("wide", width=1920, height=1080)],
        color="polar",
        color_order=[],
        allowed=EVERYTHING,
    )

    assert len(chosen) == 6


def test_a_type_round_over_a_portrait_covers_only_the_three_that_fit_any_shape():
    chosen = plan_round(
        COMPARE_TYPES,
        [album_item("tall", width=3024, height=4032)],
        color="polar",
        color_order=[],
        allowed=EVERYTHING,
    )

    assert [variant.matte_id for variant in chosen] == [
        "flexible_polar",
        "none",
        "shadowbox_polar",
    ]


def test_a_color_round_uses_the_one_type_that_flexes_so_it_works_on_every_shape():
    chosen = plan_round(
        COMPARE_COLORS,
        [album_item("wide", width=1920, height=1080), album_item("tall", 3024, 4032)],
        color=None,
        color_order=["polar", "black"],
        allowed=EVERYTHING,
    )

    assert [variant.matte_id for variant in chosen] == [
        "flexible_polar",
        "flexible_black",
        "flexible_polar",
        "flexible_black",
    ]


def test_variants_are_numbered_continuously_across_the_shapes():
    chosen = plan_round(
        COMPARE_COLORS,
        [album_item("wide", width=1920, height=1080), album_item("four-three")],
        color=None,
        color_order=["polar", "black"],
        allowed=EVERYTHING,
    )

    assert [variant.number for variant in chosen] == [1, 2, 3, 4]


def test_each_variant_carries_the_photo_it_is_of():
    """One round puts several photos up now, so a variant read against the wrong one is a crash."""
    chosen = plan_round(
        COMPARE_COLORS,
        [album_item("wide", width=1920, height=1080), album_item("tall", 3024, 4032)],
        color=None,
        color_order=["polar"],
        allowed=EVERYTHING,
    )

    assert [(v.source_id, v.shape) for v in chosen] == [("wide", "16:9"), ("tall", "3:4")]


def test_the_bare_type_keeps_its_bare_name():
    chosen = plan_round(
        COMPARE_TYPES,
        [album_item("four-three")],
        color="polar",
        color_order=[],
        allowed=EVERYTHING,
    )

    assert "none" in [variant.matte_id for variant in chosen]
    assert "none_polar" not in [variant.matte_id for variant in chosen]


def test_a_type_round_needs_a_color_to_draw_them_in():
    with pytest.raises(MatteError):
        plan_round(
            COMPARE_TYPES, [album_item("four-three")], color=None, color_order=[],
            allowed=EVERYTHING,
        )


def test_a_color_the_tv_never_offered_is_refused_before_anything_is_sent():
    with pytest.raises(MatteError):
        plan_round(
            COMPARE_TYPES, [album_item("four-three")], color="chartreuse", color_order=[],
            allowed=EVERYTHING,
        )


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


# What gets burned into the image, and what config narrows a round to


def test_a_color_round_burns_the_color_name_and_the_shape():
    chosen = plan_round(
        COMPARE_COLORS, [album_item("four-three")], color=None, color_order=["polar"],
        allowed=EVERYTHING,
    )

    assert [variant.label for variant in chosen] == ["polar   4:3"]


def test_a_type_round_burns_the_type_name_and_the_shape():
    chosen = plan_round(
        COMPARE_TYPES, [album_item("tall", width=3024, height=4032)], color="polar",
        color_order=[], allowed=EVERYTHING,
    )

    assert [variant.label for variant in chosen] == [
        "flexible   3:4",
        "none   3:4",
        "shadowbox   3:4",
    ]


def test_the_shape_is_in_the_label_so_two_shapes_under_one_matte_are_told_apart():
    chosen = plan_round(
        COMPARE_COLORS,
        [album_item("wide", width=1920, height=1080), album_item("four-three")],
        color=None,
        color_order=["polar"],
        allowed=EVERYTHING,
    )

    assert [variant.label for variant in chosen] == ["polar   16:9", "polar   4:3"]


def test_config_narrows_a_color_round_to_the_colors_it_names():
    allowed = BakeoffConfig(colors=("polar", "black"))

    chosen = plan_round(
        COMPARE_COLORS, [album_item("four-three")], color=None,
        color_order=["polar", "antique", "black"], allowed=allowed,
    )

    assert [variant.matte_id for variant in chosen] == ["flexible_polar", "flexible_black"]


def test_config_narrows_a_type_round_to_the_types_it_names():
    allowed = BakeoffConfig(types=("flexible", "modernwide"))

    chosen = plan_round(
        COMPARE_TYPES, [album_item("wide", width=1920, height=1080)], color="polar",
        color_order=[], allowed=allowed,
    )

    assert [variant.matte_id for variant in chosen] == ["flexible_polar", "modernwide_polar"]


def test_a_type_the_shape_cant_take_drops_out_rather_than_failing_the_round():
    """One list serves every shape, so a 16:9-only type is fine to leave in it."""
    allowed = BakeoffConfig(types=("flexible", "modernwide"))

    assert offered_types(Fraction(4, 3), allowed) == ["flexible"]


def test_a_round_config_narrows_to_nothing_on_one_shape_is_refused():
    """Naming only fixed-aperture types would otherwise quietly shrink a round to the 16:9."""
    allowed = BakeoffConfig(types=("modernwide",))

    with pytest.raises(MatteError) as raised:
        plan_round(
            COMPARE_TYPES,
            [album_item("wide", width=1920, height=1080), album_item("four-three")],
            color="polar",
            color_order=[],
            allowed=allowed,
        )

    assert "4:3" in str(raised.value)


def test_every_name_a_round_could_burn_is_known_before_the_tv_is_asked():
    """The images are rendered before the channel opens, so this says how many to draw."""
    assert len(candidate_labels(COMPARE_COLORS, album_item("four-three"), EVERYTHING)) == 16
    assert candidate_labels(
        COMPARE_TYPES, album_item("tall", width=3024, height=4032), EVERYTHING
    ) == ["flexible   3:4", "none   3:4", "shadowbox   3:4"]


@pytest.mark.parametrize("compare", [COMPARE_COLORS, COMPARE_TYPES])
def test_every_label_a_round_settles_on_was_already_drawn(compare):
    """The CLI looks a rendered image up by `(source_id, label)`, so a miss is a `KeyError`."""
    allowed = BakeoffConfig(colors=("polar", "sand"), types=("flexible", "modernwide"))
    photos = [
        album_item("wide", width=1920, height=1080),
        album_item("four-three"),
        album_item("tall", width=3024, height=4032),
    ]
    drawn = {
        (photo.source_id, text)
        for photo in photos
        for text in candidate_labels(compare, photo, allowed)
    }

    chosen = plan_round(
        compare, photos, color="polar", color_order=["sand", "polar", "black"], allowed=allowed
    )

    assert {(variant.source_id, variant.label) for variant in chosen} <= drawn
