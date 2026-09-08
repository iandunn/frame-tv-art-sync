"""Covers the record that decides whether a photo on the TV is stale.

Equality is the whole point of this module, and getting it wrong is expensive in one direction:
a record that compares unequal to an identical one re-uploads the album. So the tests are mostly
about the forms the same rendering can arrive in -- off disk, out of config, hand-edited -- and
that they all come out equal. The rest is about which matte a photo is looked up under, since
the crop decides the shape that lookup happens on.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from frame_tv_art_sync import mattes
from frame_tv_art_sync.composite import NO_RULE, Group
from frame_tv_art_sync.crop import CropRule
from frame_tv_art_sync.render import (
    RenderError,
    RenderRecord,
    RenderSettings,
    describe_change,
    from_stored,
    stamp,
)
from frame_tv_art_sync.sources import SourceItem

SETTINGS = RenderSettings(
    matte_by_ratio={
        Fraction(4, 3): "flexible_black",
        Fraction(3, 4): "shadowbox_polar",
        Fraction(16, 9): "modernwide_black",
    },
    # Deliberately none of the three above, so a photo that takes it can't pass by accident.
    fallback_matte="shadowbox_sand",
    highlight_rolloff=0.1,
    jpeg_quality=95,
)


def item(width: int, height: int, source_id: str = "AF1QipA") -> SourceItem:
    return SourceItem(
        source_id=source_id, url="https://example.test/x", width=width, height=height
    )


def record(**overrides) -> RenderRecord:
    fields = {
        "pipeline_version": 1,
        "matte_id": "flexible_black",
        "layout": "full:1",
        "edge": "shadowbox",
        "mat": "antique",
        "gap_across": 98,
        "gap_down": 98,
        "crops": (("none", "center"),),
        "labelled": False,
        "highlight_rolloff": 0.1,
        "jpeg_quality": 95,
        "image_date": "2023:04:02 16:15:05",
        "play_order": "newest_first",
    }
    return RenderRecord(**{**fields, **overrides})


def cropping(*rules: CropRule) -> RenderSettings:
    """`SETTINGS` with crop rules, so a test says only what it changes."""
    return replace(SETTINGS, crop=rules)


# What config asks for, per photo


def test_a_photo_whose_ratio_the_config_names_gets_that_ratios_matte():
    assert SETTINGS.for_item(item(1440, 1080)).matte_id == "flexible_black"


def test_a_photo_of_another_named_ratio_gets_its_own_matte():
    assert SETTINGS.for_item(item(810, 1080)).matte_id == "shadowbox_polar"


def test_a_photo_of_a_ratio_the_config_is_silent_about_takes_the_fallback():
    """An album holds shapes nobody wants to configure, and one of them can't stop a run."""
    assert SETTINGS.for_item(item(1080, 1080)).matte_id == "shadowbox_sand"


def test_a_ratio_a_few_pixels_off_a_key_is_still_that_key():
    """A Pixel writes 4080x3072, and nobody would write `85:64` in a config file."""
    assert SETTINGS.for_item(item(4080, 3072)).matte_id == "flexible_black"


def test_the_rest_of_the_record_is_the_same_whatever_the_shape():
    landscape, portrait = SETTINGS.for_item(item(1440, 1080)), SETTINGS.for_item(item(810, 1080))

    assert landscape.jpeg_quality == portrait.jpeg_quality
    assert landscape.highlight_rolloff == portrait.highlight_rolloff
    assert landscape.pipeline_version == portrait.pipeline_version


def test_restyling_one_ratio_leaves_the_other_shapes_where_they_were():
    restyled = replace(
        SETTINGS, matte_by_ratio={**SETTINGS.matte_by_ratio, Fraction(4, 3): "shadowbox_black"}
    )

    assert restyled.for_item(item(1440, 1080)).matte_id == "shadowbox_black"
    assert restyled.for_item(item(810, 1080)) == SETTINGS.for_item(item(810, 1080))


def test_two_configs_reaching_one_matte_by_different_routes_produce_one_record():
    """The record holds the resolved id and not the ratio, so a shape named outright and a
    shape that fell back onto the same matte are the same pixels and replace nothing."""
    fallen_back = replace(SETTINGS, matte_by_ratio={}, fallback_matte="flexible_black")

    assert fallen_back.for_item(item(1440, 1080)) == SETTINGS.for_item(item(1440, 1080))


# The crop, which decides which shape's matte a photo is looked up under


def test_a_cropped_photo_is_matted_for_the_shape_the_crop_leaves_it():
    """The TV is handed the crop's output, so the source's own shape names the wrong matte."""
    cropped = cropping(CropRule(when="4:3", to="16:9"))

    assert cropped.for_item(item(1440, 1080)).matte_id == "modernwide_black"


def test_a_crop_off_16_9_takes_the_fixed_aperture_matte_away():
    """Choosing the matte before the crop would send `modernwide` to a 4:3, and a fixed
    aperture on anything but a 16:9 crashes Art Mode into a power cycle."""
    cropped = cropping(CropRule(when="16:9", to="4:3"))

    matte_id = cropped.for_item(item(1920, 1080)).matte_id

    assert mattes.split_matte_id(matte_id)[0] not in mattes.FIXED_APERTURE_TYPES
    assert matte_id == "flexible_black"


def test_matte_choice_and_the_record_name_one_matte_for_a_cropped_photo():
    """A run reports the choice and stores the record, and the two resolve the crop apart."""
    cropped = cropping(CropRule(when="4:3", to="16:9"))
    photo = item(1440, 1080)

    assert cropped.matte_choice(photo).matte_id == cropped.for_item(photo).matte_id


def test_matte_choice_reports_the_ratio_the_crop_left_rather_than_the_sources():
    cropped = cropping(CropRule(when="4:3", to="16:9"))

    assert cropped.matte_choice(item(1440, 1080)).ratio == Fraction(16, 9)


def test_matte_choice_says_when_the_fallback_is_what_answered():
    assert SETTINGS.matte_choice(item(1080, 1080)).fell_back is True


def test_matte_choice_says_when_the_config_named_the_matte():
    assert SETTINGS.matte_choice(item(1440, 1080)).fell_back is False


# Equality, which is what decides whether 174 photos are uploaded again


def test_two_records_of_the_same_rendering_are_equal():
    assert record() == record()


def test_a_rolloff_written_with_a_trailing_zero_is_the_same_rolloff():
    """`0.10` in a hand-edited file is not a reason to re-upload the album."""
    assert record(highlight_rolloff=0.10) == record(highlight_rolloff=0.1)


def test_a_rolloff_off_by_less_than_the_recorded_precision_is_the_same_rolloff():
    assert record(highlight_rolloff=0.100004) == record(highlight_rolloff=0.1)


def test_a_matte_in_the_tvs_upper_case_is_the_same_matte():
    """`available()` reports `FLEXIBLE_BLACK` where config says `flexible_black`."""
    assert record(matte_id="FLEXIBLE_BLACK") == record(matte_id="flexible_black")


def test_a_different_matte_is_a_different_rendering():
    assert record(matte_id="modern_black") != record()


def test_a_different_pipeline_version_is_a_different_rendering():
    """Nothing else notices a change to the pipeline's code, since no config value moves."""
    assert record(pipeline_version=2) != record()


def test_a_different_quality_is_a_different_rendering():
    assert record(jpeg_quality=90) != record()


# Reading one back off disk


def test_a_record_survives_the_round_trip():
    assert from_stored(record().as_stored()) == record()


def test_an_entry_without_one_reads_as_unknown():
    assert from_stored(None) is None


def test_a_record_that_is_not_a_table_is_an_error():
    with pytest.raises(RenderError):
        from_stored("flexible_black")


def test_a_record_missing_a_field_is_an_error():
    stored = record().as_stored()
    del stored["jpeg_quality"]

    with pytest.raises(RenderError, match="jpeg_quality"):
        from_stored(stored)


def test_a_record_with_a_field_of_the_wrong_type_is_an_error():
    """Guessing at a value that decides whether an image is destroyed is not on offer."""
    with pytest.raises(RenderError):
        from_stored({**record().as_stored(), "jpeg_quality": "95"})


def test_a_record_written_before_the_date_existed_reads_as_unknown():
    """Every entry in the inventory today is one of these, and each has to be made again."""
    stored = record().as_stored()
    del stored["image_date"]

    assert from_stored(stored) == record(image_date="unknown")


def test_a_record_written_before_the_play_order_reads_as_the_order_it_was_made_with():
    """Every upload before the key existed went up in album order, so this is fact not guess.

    Reading it as unknown instead would rebuild the album for a config that never asked for
    anything, which is the one thing the record exists to avoid.
    """
    stored = record().as_stored()
    del stored["play_order"]

    assert from_stored(stored) == record(play_order="newest_first")


def test_reading_a_record_older_than_the_date_leaves_the_entry_alone():
    """The stand-in is what this run compares against, not something to write back to disk."""
    stored = record().as_stored()
    del stored["image_date"]
    from_stored(stored)

    assert "image_date" not in stored


def test_a_whole_number_rolloff_is_read_as_a_number():
    """TOML and JSON both write `0` for a rolloff that is off, and it is still a rolloff."""
    assert from_stored({**record().as_stored(), "highlight_rolloff": 0}) is not None


# The date an upload carries


def test_a_photo_is_dated_by_when_it_was_taken():
    taken = SourceItem(
        source_id="AF1QipA",
        url="https://example.test/x",
        width=1440,
        height=1080,
        taken_at_ms=1680452105564,
    )

    assert SETTINGS.for_item(taken).image_date == "2023:04:02 16:15:05"


def test_a_photo_with_no_shot_time_is_dated_the_epoch():
    """Which is what `taken_at_ms` of 0 already means everywhere else: as old as it gets."""
    assert stamp(0) == "1970:01:01 00:00:00"


def test_a_group_is_dated_by_its_oldest_photo():
    older = replace(item(1440, 1080, "AF1QipA"), taken_at_ms=1680452105564)
    newer = replace(item(1440, 1080, "AF1QipB"), taken_at_ms=1780452105564)
    group = Group(rule=NO_RULE, items=(older, newer))

    assert SETTINGS.for_group(group).image_date == stamp(older.taken_at_ms)


# Saying why a photo is being replaced


def test_an_entry_with_no_record_says_so():
    assert describe_change(None, record()) == "no record"


def test_one_changed_setting_is_named_with_both_values():
    change = describe_change(record(matte_id="modern_black"), record())

    assert change == "matte id modern_black to flexible_black"


def test_every_changed_setting_is_named():
    change = describe_change(record(matte_id="modern_black", jpeg_quality=90), record())

    assert "matte id" in change and "jpeg quality" in change
