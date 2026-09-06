"""Covers the size math and the tone curve, which are the pure parts of the pipeline."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from frame_tv_art_sync.pipeline import (
    TARGET_HEIGHT,
    TARGET_WIDTH,
    PreparedImage,
    fit_size,
    highlight_lut,
    label_center,
    prepare,
)


def encode(width, height, color=(120, 130, 140), **save_options):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="JPEG", **save_options)
    return buffer.getvalue()


def test_fit_never_upscales():
    assert fit_size(810, 1080) == (810, 1080)
    assert fit_size(640, 480) == (640, 480)


def test_fit_scales_a_large_portrait_down_to_the_panel_height():
    assert fit_size(3024, 4032) == (810, 1080)


def test_fit_scales_a_four_by_three_down_to_the_panel_height():
    assert fit_size(4032, 3024) == (1440, 1080)


def test_fit_scales_a_frame_wider_than_the_panel_down_to_the_panel_width():
    assert fit_size(5000, 1000) == (1920, 384)


def test_a_zero_rolloff_leaves_the_curve_alone():
    assert highlight_lut(0) == list(range(256))


@pytest.mark.parametrize("rolloff", [0.05, 0.1, 0.25])
def test_the_curve_pulls_white_down_by_the_rolloff(rolloff):
    assert highlight_lut(rolloff)[255] == round(255 * (1 - rolloff))


def test_the_curve_leaves_everything_below_the_knee_untouched():
    lut = highlight_lut(0.1)

    assert lut[:191] == list(range(191))


@pytest.mark.parametrize("rolloff", [0.01, 0.05, 0.1, 0.2, 0.25])
def test_the_curve_never_goes_backwards(rolloff):
    """A curve that dips would invert the brightest highlights, which is worse than bloom."""
    lut = highlight_lut(rolloff)

    assert all(later >= earlier for earlier, later in zip(lut, lut[1:]))


def test_the_curve_has_no_crease_at_the_knee():
    """It leaves the knee at the slope it arrived with, so the shoulder doesn't show as a band."""
    lut = highlight_lut(0.1)

    assert lut[191] - lut[190] == 1
    assert lut[192] - lut[191] == 1


def test_a_four_by_three_landscape_keeps_its_shape():
    """Nothing is cropped, so a 4:3 stays 4:3 and the TV frames it inside a flexible mat."""
    prepared = prepare(encode(4032, 3024))

    assert isinstance(prepared, PreparedImage)
    assert (prepared.width, prepared.height) == (1440, TARGET_HEIGHT)


def test_a_portrait_photo_keeps_its_shape():
    """The TV frames a portrait whole inside its own mat, so nothing is added here."""
    prepared = prepare(encode(810, 1080))

    assert (prepared.width, prepared.height) == (810, 1080)


def test_an_oversized_portrait_is_bounded_to_the_panel_height():
    prepared = prepare(encode(3024, 4032))

    assert (prepared.width, prepared.height) == (810, 1080)


def test_a_small_portrait_photo_is_not_upscaled():
    prepared = prepare(encode(400, 600))

    assert (prepared.width, prepared.height) == (400, 600)


def test_an_oversized_16_by_9_landscape_is_only_shrunk():
    prepared = prepare(encode(3840, 2160))

    assert (prepared.width, prepared.height) == (TARGET_WIDTH, TARGET_HEIGHT)


def test_a_small_landscape_photo_is_not_upscaled():
    prepared = prepare(encode(1280, 720))

    assert (prepared.width, prepared.height) == (1280, 720)


def test_the_output_is_a_readable_jpeg():
    with Image.open(io.BytesIO(prepare(encode(4032, 3024)).data)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"


def test_an_exif_rotation_is_applied_before_the_bound():
    """A photo tagged as rotated is 3024x4032 on screen even though the file says otherwise.

    An unrotated read would bound it by width instead of height and upload a 1440x1080.
    """
    buffer = io.BytesIO()
    image = Image.new("RGB", (4032, 3024), (120, 130, 140))
    exif = image.getexif()
    exif[274] = 6
    image.save(buffer, format="JPEG", exif=exif)

    prepared = prepare(buffer.getvalue())

    assert (prepared.width, prepared.height) == (810, TARGET_HEIGHT)


def test_white_comes_out_below_white():
    prepared = prepare(encode(1920, 1080, color=(255, 255, 255)), highlight_rolloff=0.1)

    with Image.open(io.BytesIO(prepared.data)) as image:
        assert max(image.getpixel((960, 540))) < 240


def test_a_zero_rolloff_leaves_white_alone():
    prepared = prepare(encode(1920, 1080, color=(255, 255, 255)), highlight_rolloff=0)

    with Image.open(io.BytesIO(prepared.data)) as image:
        assert min(image.getpixel((960, 540))) > 250


def test_a_label_keeps_the_image_the_size_it_already_was():
    labelled = label_center(encode(1440, 1080), "7")

    assert (labelled.width, labelled.height) == (1440, 1080)


def test_a_label_marks_the_middle_and_leaves_the_edges_alone():
    """The number goes in the middle so it stays away from the mat it exists to help judge."""
    flat = (120, 130, 140)
    labelled = label_center(encode(1440, 1080, color=flat), "7")

    with Image.open(io.BytesIO(labelled.data)) as image:
        middle = image.crop((620, 440, 820, 640)).getcolors(maxcolors=1 << 20)
        assert image.getpixel((20, 20)) == pytest.approx(flat, abs=4)
        assert len(middle) > 1


def test_a_label_reads_over_a_dark_photo_and_a_bright_one_alike():
    """White with a dark stroke, so neither ground swallows it."""
    for ground in ((0, 0, 0), (255, 255, 255)):
        with Image.open(io.BytesIO(label_center(encode(600, 600, color=ground), "8").data)) as art:
            middle = art.crop((150, 150, 450, 450)).convert("L")

        assert middle.getextrema()[1] - middle.getextrema()[0] > 100
