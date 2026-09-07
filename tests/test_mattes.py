"""Covers the rules that keep a matte the TV can't draw off the panel.

Worth testing rather than trying against the hardware, because the failure being prevented is
an Art Mode crash that needs a physical power cycle.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from frame_tv_art_sync import mattes
from frame_tv_art_sync.pipeline import fit_size


# One size per shape the album actually holds, plus the 16:9 a fixed aperture is cut for.
SIXTEEN_BY_NINE = (1920, 1080)
FOUR_BY_THREE = (1434, 1080)
THREE_BY_FOUR = (810, 1080)
SQUARE = (1080, 1080)

EVERY_SHAPE = [SIXTEEN_BY_NINE, FOUR_BY_THREE, THREE_BY_FOUR, SQUARE]
SHAPES_WITHOUT_A_FIXED_APERTURE = [FOUR_BY_THREE, THREE_BY_FOUR, SQUARE]

ALBUM_SIZES = [
    (4080, 3072, "4:3"),
    (4032, 3024, "4:3"),
    (4000, 3000, "4:3"),
    (3264, 2448, "4:3"),
    (1599, 1204, "4:3"),
    (3072, 4080, "3:4"),
    (3024, 4032, "3:4"),
    (3000, 4000, "3:4"),
    (2448, 3264, "3:4"),
    (1536, 2048, "3:4"),
    (768, 1024, "3:4"),
    (2464, 3280, "3:4"),
    (3840, 2160, "16:9"),
    (1080, 1080, "1:1"),
]


@pytest.mark.parametrize("width,height,shape", ALBUM_SIZES)
def test_a_real_photo_size_lands_on_the_shape_a_person_would_name(width, height, shape):
    """A phone's pixels are never an exact small fraction, and the snapping is what hides that.

    A 4080x3072 is 85/64 and a 1599x1204 is neither, yet no eye can tell either from a 4:3, so
    all three have to look up the same config key.
    """
    assert mattes.shape_of(width, height) == shape


@pytest.mark.parametrize("width,height", [(0, 1080), (1920, 0), (-1920, 1080), (1920, -1080)])
def test_a_size_that_is_not_a_size_is_refused(width, height):
    with pytest.raises(mattes.MatteError):
        mattes.ratio_of(width, height)


def test_a_taller_image_is_a_portrait():
    assert mattes.orientation_of(810, 1080) == mattes.PORTRAIT
    assert mattes.orientation_of(1920, 1080) == mattes.LANDSCAPE


def test_a_square_counts_as_landscape():
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


@pytest.mark.parametrize("matte_type", sorted(mattes.FIXED_APERTURE_TYPES))
def test_a_fixed_aperture_type_is_accepted_on_a_sixteen_by_nine(matte_type):
    mattes.validate(f"{matte_type}_black", *SIXTEEN_BY_NINE)


def test_a_fixed_aperture_type_on_a_four_by_three_is_refused():
    """This is the observation the whole module exists to act on.

    A 1434x1080 uploaded with `modern` put the 40000 dialog on the panel and needed a power
    cycle. Orientation looked like the axis only because every landscape tested before T22
    was 16:9.
    """
    for matte_type in sorted(mattes.FIXED_APERTURE_TYPES):
        with pytest.raises(mattes.MatteError, match="fixed 16:9"):
            mattes.validate(f"{matte_type}_black", *FOUR_BY_THREE)


@pytest.mark.parametrize("width,height", SHAPES_WITHOUT_A_FIXED_APERTURE)
@pytest.mark.parametrize("matte_type", sorted(mattes.FIXED_APERTURE_TYPES))
def test_a_fixed_aperture_type_is_refused_on_every_other_shape(matte_type, width, height):
    with pytest.raises(mattes.MatteError, match="fixed 16:9"):
        mattes.validate(f"{matte_type}_black", width, height)


@pytest.mark.parametrize("width,height", EVERY_SHAPE)
@pytest.mark.parametrize("matte_type", sorted(mattes.ACCEPTED_ON_ANY_SHAPE))
def test_a_type_with_no_fixed_aperture_is_accepted_on_every_shape(matte_type, width, height):
    matte_id = matte_type if matte_type == mattes.BARE_TYPE else f"{matte_type}_black"

    mattes.validate(matte_id, width, height)


@pytest.mark.parametrize("width,height", EVERY_SHAPE)
@pytest.mark.parametrize("matte_type", sorted(mattes.UNOFFERED_TYPES))
def test_a_type_the_picker_offers_for_nothing_is_refused_on_every_shape(
    matte_type, width, height
):
    with pytest.raises(mattes.MatteError, match="for no image at all"):
        mattes.validate(f"{matte_type}_black", width, height)


def test_an_unknown_color_is_refused_and_lists_the_known_ones():
    with pytest.raises(mattes.MatteError, match="blak"):
        mattes.validate("flexible_blak", *THREE_BY_FOUR)


def test_the_error_says_where_to_update_the_color_list():
    """So a firmware that gains a color is a one-line fix rather than a mystery."""
    with pytest.raises(mattes.MatteError, match=r"mattes\.py"):
        mattes.validate("flexible_chartreuse", *THREE_BY_FOUR)


def test_an_unknown_type_and_an_unknown_color_are_told_apart():
    """Both are typos, and the message has to say which half was wrong."""
    with pytest.raises(mattes.MatteError, match="no such type"):
        mattes.validate("driftwood_black", *SIXTEEN_BY_NINE)

    with pytest.raises(mattes.MatteError, match="not one of the colors"):
        mattes.validate("flexible_driftwood", *SIXTEEN_BY_NINE)


def test_the_burgandy_misspelling_is_the_accepted_one():
    mattes.validate("flexible_burgandy", *FOUR_BY_THREE)

    with pytest.raises(mattes.MatteError):
        mattes.validate("flexible_burgundy", *FOUR_BY_THREE)


@pytest.mark.parametrize("width,height,shape", ALBUM_SIZES)
def test_a_shape_name_round_trips_through_a_fraction(width, height, shape):
    ratio = mattes.ratio_of(width, height)

    assert mattes.parse_ratio_name(mattes.ratio_name(ratio)) == ratio
    assert mattes.parse_ratio_name(shape) == ratio


def test_two_names_for_one_shape_are_one_key():
    """A config saying `16:10` has to find the same matte as one saying `8:5`."""
    assert mattes.parse_ratio_name("16:10") == mattes.parse_ratio_name("8:5")
    assert mattes.parse_ratio_name("16:10") == mattes.ratio_of(1920, 1200)


@pytest.mark.parametrize("text", ["16", "16:0", "a:b", "", "   ", "16:9:3", "-16:9"])
def test_something_that_is_not_a_shape_name_is_refused(text):
    with pytest.raises(mattes.MatteError):
        mattes.parse_ratio_name(text)


def test_a_configured_shape_gets_the_matte_it_names():
    choice = mattes.choose(
        *FOUR_BY_THREE,
        by_ratio={Fraction(4, 3): "shadowbox_polar", Fraction(3, 4): "flexible_black"},
        fallback="flexible_polar",
    )

    assert choice.matte_id == "shadowbox_polar"
    assert choice.ratio == Fraction(4, 3)
    assert choice.fell_back is False


def test_a_shape_the_config_is_silent_about_takes_the_fallback():
    choice = mattes.choose(
        *SQUARE,
        by_ratio={Fraction(4, 3): "shadowbox_polar"},
        fallback="flexible_polar",
    )

    assert choice.matte_id == "flexible_polar"
    assert choice.ratio == Fraction(1, 1)
    assert choice.fell_back is True


def test_a_key_written_unreduced_still_matches_the_image():
    """`parse_ratio_name` is what a config goes through, so its output has to meet `ratio_of`."""
    choice = mattes.choose(
        1920,
        1200,
        by_ratio={mattes.parse_ratio_name("16:10"): "shadowbox_sand"},
        fallback="flexible_polar",
    )

    assert choice.matte_id == "shadowbox_sand"
    assert choice.fell_back is False


def test_a_sixteen_by_nine_is_offered_more_than_any_other_shape():
    assert set(mattes.offered_for(Fraction(16, 9))) > set(mattes.offered_for(Fraction(4, 3)))
    assert mattes.offered_for(Fraction(4, 3)) == sorted(mattes.ACCEPTED_ON_ANY_SHAPE)


def test_the_offered_types_are_reported_sorted():
    assert mattes.offered_for(Fraction(16, 9)) == sorted(mattes.types_for(Fraction(16, 9)))


def test_the_three_type_sets_never_overlap():
    """So no type can be drawable and unoffered at once, whichever set gains a name."""
    assert not mattes.FIXED_APERTURE_TYPES & mattes.ACCEPTED_ON_ANY_SHAPE
    assert not mattes.FIXED_APERTURE_TYPES & mattes.UNOFFERED_TYPES
    assert not mattes.ACCEPTED_ON_ANY_SHAPE & mattes.UNOFFERED_TYPES


def test_every_known_type_is_the_three_sets_together():
    assert mattes.every_known_type() == (
        mattes.FIXED_APERTURE_TYPES | mattes.ACCEPTED_ON_ANY_SHAPE | mattes.UNOFFERED_TYPES
    )


# The boundary between a source's shape and the shape of the bytes made from it


@pytest.mark.parametrize(
    "width,height",
    [(2013, 1135), (2930, 1652), (3203, 1806), (4120, 2323)],
)
def test_a_source_that_bounds_to_a_rounded_16_9_still_takes_a_fixed_aperture(width, height):
    """Each of these is 16:9, and each becomes 1915x1080 once bounded to the panel.

    `sync.py` picks the matte off the source and `tv.upload()` checks it against the bytes, so
    without the tolerance the second would refuse what the first had just chosen.
    """
    assert mattes.ratio_of(width, height) == mattes.FIXED_APERTURE_RATIO
    assert mattes.ratio_of(1915, 1080) != mattes.FIXED_APERTURE_RATIO

    for matte_type in mattes.FIXED_APERTURE_TYPES:
        mattes.validate(f"{matte_type}_polar", width, height)
        mattes.validate(f"{matte_type}_polar", 1915, 1080)


def test_the_tolerance_is_nowhere_near_the_shapes_it_has_to_keep_apart():
    """A percent covers rounding and comes nowhere near a shape anybody photographs."""
    for ratio in [Fraction(4, 3), Fraction(3, 2), Fraction(3, 4), Fraction(1, 1), Fraction(8, 5)]:
        assert not mattes.is_widescreen(ratio)

    assert mattes.is_widescreen(mattes.FIXED_APERTURE_RATIO)


# The near-square shape, where orientation stops being a stable answer


@pytest.mark.parametrize(
    "width,height,prepared_width,prepared_height",
    [
        (2999, 3000, 1080, 1080),
        (3000, 2999, 1080, 1080),
        (1080, 1081, 1079, 1080),
        (2900, 3000, 1044, 1080),
    ],
)
def test_a_near_square_reads_the_same_before_and_after_the_panel_bound(
    width, height, prepared_width, prepared_height
):
    """Bounding a 2999x3000 gives a square, which counts as a landscape where the source is a
    portrait. Nothing here reads that, because the key is the ratio and the ratio is snapped, so
    a photo that changes orientation on the way to the panel still looks up one matte.
    """
    assert fit_size(width, height) == (prepared_width, prepared_height)
    assert mattes.ratio_of(width, height) == mattes.ratio_of(prepared_width, prepared_height)


def test_a_near_square_changes_orientation_even_though_its_ratio_holds():
    """The trap this design steps over, kept as a test so nobody keys on orientation again."""
    assert mattes.orientation_of(2999, 3000) == mattes.PORTRAIT
    assert mattes.orientation_of(*fit_size(2999, 3000)) == mattes.LANDSCAPE
    assert mattes.shape_of(2999, 3000) == mattes.shape_of(*fit_size(2999, 3000)) == "1:1"
