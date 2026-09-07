"""Covers the record that decides whether a photo on the TV is stale.

Equality is the whole point of this module, and getting it wrong is expensive in one direction:
a record that compares unequal to an identical one re-uploads the album. So the tests are mostly
about the forms the same rendering can arrive in -- off disk, out of config, hand-edited -- and
that they all come out equal.
"""

from __future__ import annotations

import pytest

from frame_tv_art_sync.crop import CropRule
from frame_tv_art_sync.render import (
    RenderError,
    RenderRecord,
    RenderSettings,
    describe_change,
    from_stored,
)
from frame_tv_art_sync.sources import SourceItem

SETTINGS = RenderSettings(
    landscape_matte="flexible_black",
    portrait_matte="shadowbox_polar",
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
        "crop": "none",
        "crop_anchor": "center",
        "labelled": False,
        "highlight_rolloff": 0.1,
        "jpeg_quality": 95,
    }
    return RenderRecord(**{**fields, **overrides})


# What config asks for, per photo


def test_a_landscape_gets_the_landscape_matte():
    assert SETTINGS.for_item(item(1440, 1080)).matte_id == "flexible_black"


def test_a_portrait_gets_the_portrait_matte():
    assert SETTINGS.for_item(item(810, 1080)).matte_id == "shadowbox_polar"


def test_the_rest_of_the_record_is_the_same_whatever_the_shape():
    landscape, portrait = SETTINGS.for_item(item(1440, 1080)), SETTINGS.for_item(item(810, 1080))

    assert landscape.jpeg_quality == portrait.jpeg_quality
    assert landscape.highlight_rolloff == portrait.highlight_rolloff
    assert landscape.pipeline_version == portrait.pipeline_version


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


def test_a_whole_number_rolloff_is_read_as_a_number():
    """TOML and JSON both write `0` for a rolloff that is off, and it is still a rolloff."""
    assert from_stored({**record().as_stored(), "highlight_rolloff": 0}) is not None


# Saying why a photo is being replaced


def test_an_entry_with_no_record_says_so():
    assert describe_change(None, record()) == "no record"


def test_one_changed_setting_is_named_with_both_values():
    change = describe_change(record(matte_id="modern_black"), record())

    assert change == "matte id modern_black to flexible_black"


def test_every_changed_setting_is_named():
    change = describe_change(record(matte_id="modern_black", jpeg_quality=90), record())

    assert "matte id" in change and "jpeg quality" in change
