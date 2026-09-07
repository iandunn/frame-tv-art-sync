"""Covers the rule table and the crop window, which are the whole of `crop.py`."""

from __future__ import annotations

import pytest

from frame_tv_art_sync.crop import (
    CropError,
    CropRule,
    ambiguous_overrides,
    crop_box,
    cropped_size,
    parse_ratio,
    ratios_match,
    resolve,
    unmatched_overrides,
)

FOUR_THREE = CropRule(when="4:3", to="16:9")
PORTRAIT_KEPT = CropRule(when="portrait", to="none")


def test_a_ratio_reads_as_a_number():
    assert parse_ratio("16:9") == pytest.approx(16 / 9)
    assert parse_ratio("1:1") == 1.0


@pytest.mark.parametrize("text", ["16", "16:9:3", "16:0", "-4:3", "wide", "16:x"])
def test_a_ratio_that_is_not_one_is_refused(text):
    with pytest.raises(CropError):
        parse_ratio(text)


def test_a_pixel_four_by_three_matches_a_four_by_three_rule():
    """4080x3072 is 0.4% off exact 4:3, so equality would match nothing in a real album."""
    assert FOUR_THREE.matches(4080, 3072)
    assert FOUR_THREE.matches(4032, 3024)
    assert FOUR_THREE.matches(3264, 2448)


def test_the_tolerance_does_not_reach_the_next_shape_anybody_uses():
    assert not FOUR_THREE.matches(1920, 1080)
    assert not FOUR_THREE.matches(3000, 2000)


def test_the_shape_words_match_on_orientation():
    assert CropRule(when="portrait", to="none").matches(3024, 4032)
    assert not CropRule(when="portrait", to="none").matches(4032, 3024)
    assert CropRule(when="landscape", to="none").matches(4032, 3024)
    assert CropRule(when="*", to="none").matches(3024, 4032)


def test_a_square_counts_as_a_landscape():
    """It has to fall on one side, and a square already fills more of the panel than a 3:4."""
    assert CropRule(when="landscape", to="none").matches(1000, 1000)
    assert not CropRule(when="portrait", to="none").matches(1000, 1000)


def test_an_unknown_anchor_is_refused():
    with pytest.raises(CropError):
        CropRule(when="4:3", to="16:9", anchor="middle")


def test_a_frame_already_at_the_target_shape_is_kept_whole():
    assert crop_box(1920, 1080, 16 / 9) == (0, 0, 1920, 1080)


def test_cropping_a_four_by_three_to_the_panel_takes_height_only():
    assert crop_box(1920, 1440, 16 / 9) == (0, 180, 1920, 1260)


def test_cropping_toward_a_taller_shape_takes_width_only():
    assert crop_box(1920, 1440, 1.0) == (240, 0, 1680, 1440)


def test_the_anchor_places_the_window_on_the_axis_being_trimmed():
    assert crop_box(1920, 1440, 16 / 9, "top") == (0, 0, 1920, 1080)
    assert crop_box(1920, 1440, 16 / 9, "bottom") == (0, 360, 1920, 1440)
    assert crop_box(1440, 1920, 3 / 4, "left") == (0, 0, 1440, 1920)


def test_an_anchor_on_the_other_axis_is_inert_rather_than_wrong():
    """A rule that trims height can carry `left`, and it centers rather than misplacing."""
    assert crop_box(1920, 1440, 16 / 9, "left") == crop_box(1920, 1440, 16 / 9, "center")


def test_a_portrait_cropped_to_the_panel_keeps_a_band():
    left, top, right, bottom = crop_box(1440, 1920, 16 / 9)

    assert (right - left, bottom - top) == (1440, 810)
    assert top == 555


def test_the_first_matching_rule_wins():
    rules = (CropRule(when="4:3", to="16:9"), CropRule(when="*", to="3:2"))

    assert resolve(4032, 3024, "x", rules).rule.to == "16:9"
    assert resolve(3000, 2000, "x", rules).rule.to == "3:2"


def test_a_photo_no_rule_matches_is_kept_whole():
    assert not resolve(3024, 4032, "x", (FOUR_THREE,)).crops


def test_an_override_beats_every_rule():
    overrides = (("AF1QipNt2uKI", CropRule(when="*", to="none")),)
    crop = resolve(4032, 3024, "AF1QipNt2uKIcvII", (FOUR_THREE,), overrides)

    assert not crop.crops
    assert crop.label == "override kept whole"


def test_an_override_matches_on_a_prefix_and_not_on_a_coincidence():
    overrides = (("AF1QipNt2uKI", CropRule(when="*", to="1:1")),)

    assert resolve(4032, 3024, "AF1QipNt2uKIcvII", (), overrides).rule.to == "1:1"
    assert not resolve(4032, 3024, "AF1QipObuXbB", (), overrides).crops


def test_a_label_names_the_rule_rather_than_the_pixels():
    assert resolve(4032, 3024, "x", (CropRule(when="4:3", to="16:9", anchor="top"),)).label == (
        "4:3 -> 16:9 top"
    )
    assert resolve(3024, 4032, "x", (PORTRAIT_KEPT,)).label == "portrait kept whole"


def test_an_override_key_naming_nothing_is_reported():
    overrides = (("AF1QipZZZZZZ", CropRule(when="*", to="none")),)

    assert unmatched_overrides(["AF1QipNt2uKIcvII"], overrides) == ["AF1QipZZZZZZ"]
    assert unmatched_overrides(["AF1QipZZZZZZaaaa"], overrides) == []


def test_an_override_key_naming_several_photos_is_reported():
    overrides = (("AF1QipNt", CropRule(when="*", to="none")),)
    ids = ["AF1QipNtaaaa", "AF1QipNtbbbb", "AF1QipOgcccc"]

    assert ambiguous_overrides(ids, overrides) == ["AF1QipNt"]
    assert ambiguous_overrides(ids[1:], overrides) == []


def test_the_cropped_size_is_the_frame_the_tv_is_handed():
    crop = resolve(1920, 1440, "x", (FOUR_THREE,))

    assert cropped_size(1920, 1440, crop) == (1920, 1080)


def test_a_photo_kept_whole_reports_the_size_it_arrived_at():
    assert cropped_size(1440, 1920, resolve(1440, 1920, "x", (PORTRAIT_KEPT,))) == (1440, 1920)


def test_a_crop_can_turn_a_portrait_into_a_landscape():
    """Which is what decides its matte, so the two calls that pick one have to agree."""
    crop = resolve(1440, 1920, "x", (CropRule(when="3:4", to="16:9"),))
    width, height = cropped_size(1440, 1920, crop)

    assert width > height


def test_ratios_match_is_relative_to_the_ratio_being_asked_for():
    assert ratios_match(0.7529, 0.75)
    assert not ratios_match(0.80, 0.75)
