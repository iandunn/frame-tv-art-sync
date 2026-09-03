"""Covers the rules that keep a matte the TV can't draw off the panel.

Worth testing rather than trying against the hardware, because the failure being prevented is
an Art Mode crash that needs a physical power cycle.
"""

from __future__ import annotations

import pytest

from frame_tv_art_sync import mattes


def test_a_taller_image_is_a_portrait():
    assert mattes.orientation_of(810, 1080) == mattes.PORTRAIT
    assert mattes.orientation_of(1920, 1080) == mattes.LANDSCAPE


def test_a_square_counts_as_landscape():
    """Matching the pipeline, which crops a square to 16:9 rather than leaving it alone."""
    assert mattes.orientation_of(1080, 1080) == mattes.LANDSCAPE


def test_an_id_splits_into_type_and_color():
    assert mattes.split_matte_id("flexible_black") == ("flexible", "black")


def test_the_bare_none_has_no_color():
    """The TV reports it as `none`, never as `none_black`."""
    assert mattes.split_matte_id("none") == ("none", None)


@pytest.mark.parametrize("matte_id", ["", "   ", "flexible", "flexible_", "_black"])
def test_something_that_is_not_an_id_is_refused(matte_id):
    with pytest.raises(mattes.MatteError):
        mattes.split_matte_id(matte_id)


def test_a_second_underscore_is_refused():
    """No type or color name contains one, so this is a typo rather than a new name."""
    with pytest.raises(mattes.MatteError, match="more than one underscore"):
        mattes.split_matte_id("flexible_black_polar")


@pytest.mark.parametrize("matte_id", ["none", "modernthin_polar", "modern_black",
                                      "modernwide_warm", "flexible_black", "shadowbox_navy"])
def test_every_type_the_picker_offers_for_a_landscape_is_accepted(matte_id):
    mattes.validate(matte_id, mattes.LANDSCAPE)


@pytest.mark.parametrize("matte_id", ["flexible_black", "shadowbox_polar"])
def test_both_types_the_picker_offers_for_a_portrait_are_accepted(matte_id):
    mattes.validate(matte_id, mattes.PORTRAIT)


def test_the_type_that_crashed_art_mode_is_refused_on_a_portrait():
    """`modernwide_polar` on a portrait is the observed crash, so this is the case that matters."""
    with pytest.raises(mattes.MatteError, match="landscape only"):
        mattes.validate("modernwide_polar", mattes.PORTRAIT)


def test_a_landscape_only_type_is_still_fine_on_a_landscape():
    mattes.validate("modernwide_polar", mattes.LANDSCAPE)


def test_no_mat_is_allowed_on_a_portrait_even_though_the_picker_hides_it():
    """T9 uploaded a portrait with `matte="none"` and got a center-crop, not a crash.

    It costs 58% of the height, so it is a framing choice rather than a hazard, and the
    composited-mat fallback needs the TV to draw nothing at all.
    """
    mattes.validate("none", mattes.PORTRAIT)

    assert "none" not in mattes.offered_for(mattes.PORTRAIT)


@pytest.mark.parametrize("matte_type", sorted(mattes.UNOFFERED_TYPES))
def test_a_type_the_picker_offers_for_neither_orientation_is_refused(matte_type):
    for orientation in (mattes.LANDSCAPE, mattes.PORTRAIT):
        with pytest.raises(mattes.MatteError, match="neither orientation"):
            mattes.validate(f"{matte_type}_black", orientation)


def test_a_type_that_does_not_exist_says_so():
    with pytest.raises(mattes.MatteError, match="no such type"):
        mattes.validate("driftwood_black", mattes.LANDSCAPE)


def test_an_unknown_color_is_refused_and_lists_the_known_ones():
    with pytest.raises(mattes.MatteError, match="blak"):
        mattes.validate("flexible_blak", mattes.PORTRAIT)


def test_the_error_says_where_to_update_the_color_list():
    """So a firmware that gains a color is a one-line fix rather than a mystery."""
    with pytest.raises(mattes.MatteError, match=r"mattes\.py"):
        mattes.validate("flexible_chartreuse", mattes.PORTRAIT)


def test_the_burgandy_misspelling_is_the_accepted_one():
    mattes.validate("modern_burgandy", mattes.LANDSCAPE)

    with pytest.raises(mattes.MatteError):
        mattes.validate("modern_burgundy", mattes.LANDSCAPE)


def test_an_images_shape_chooses_the_key():
    assert mattes.matte_for(1920, 1080, "modern_black", "flexible_black") == "modern_black"
    assert mattes.matte_for(810, 1080, "modern_black", "flexible_black") == "flexible_black"


def test_the_offered_types_are_reported_sorted():
    assert mattes.offered_for(mattes.PORTRAIT) == ["flexible", "shadowbox"]


def test_the_unoffered_types_overlap_neither_set():
    """So the two constants can't drift into disagreeing about a type."""
    for offered in mattes.TYPES_BY_ORIENTATION.values():
        assert not offered & mattes.UNOFFERED_TYPES


def test_a_portrait_accepts_only_what_a_landscape_does():
    """The portrait set has to be a subset, or `matte_for` could hand back an unusable id."""
    assert (mattes.TYPES_BY_ORIENTATION[mattes.PORTRAIT]
            <= mattes.TYPES_BY_ORIENTATION[mattes.LANDSCAPE])


def test_what_is_accepted_anywhere_is_never_reported_as_offered():
    """`frame mattes` should say what the TV's picker says, not what this tool tolerates."""
    for orientation, offered in mattes.TYPES_BY_ORIENTATION.items():
        for matte_type in mattes.ACCEPTED_ANYWHERE - offered:
            mattes.validate(matte_type, orientation)
            assert matte_type not in mattes.offered_for(orientation)
