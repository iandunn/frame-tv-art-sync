"""Turns source bytes into a JPEG the TV can display.

Cropping is a choice the config makes per photo shape, and `crop.py` owns the rules. With no
rule matching, a photo keeps its own aspect ratio and is only bounded to fit inside the panel,
because a matte whose aperture flexes to the image makes the TV frame it whole, uncropped and
unstretched. `docs/spikes.md` T12 and T16 have the panel observations behind that, and
CLAUDE.md's matte notes have which types flex, since a fixed aperture crops at display time
and no crop the TV performs is one this tool can see.

A crop taken here is permanent, since it is baked into the bytes the TV stores, which is why
it happens before the frame is bounded to the panel rather than after: cropping first keeps
every pixel the crop leaves, where cropping a panel-bounded image would throw away resolution
it had already given up.

Nothing is upscaled. The TV scales an undersized image up at display time and does it no
worse than this would.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

from .crop import Crop, crop_box, parse_ratio

TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080

# Where the highlight shoulder starts. Below this the tone curve is untouched, so the
# rolloff only ever moves the bloom-prone end of the range.
_SHOULDER_KNEE = 191

# Past this the new ceiling would fall below the knee and the curve would stop being a
# shoulder at all.
MAX_HIGHLIGHT_ROLLOFF = 0.25

DEFAULT_HIGHLIGHT_ROLLOFF = 0.1

# The source images have already been through one JPEG encode, so the job here is to add no
# visible second generation of loss rather than to hit a size target. 95 with full chroma
# does that; higher buys nothing the panel can show, and storage is not the constraint.
DEFAULT_JPEG_QUALITY = 95

# How tall a bakeoff's index number is drawn. A fixed size rather than a fraction of the image,
# because every image is bounded to the panel, so this is a fixed size on the wall too.
LABEL_FONT_SIZE = 40

_SRGB = ImageCms.createProfile("sRGB")


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    width: int
    height: int


def prepare(
    data: bytes,
    *,
    crop: Crop | None = None,
    highlight_rolloff: float = DEFAULT_HIGHLIGHT_ROLLOFF,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> PreparedImage:
    """Correct, crop, bound, and re-encode one photo."""
    with Image.open(io.BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened)
        image = _to_srgb(image)
        image = _crop(image, crop)
        image = _fit_to_panel(image)
        image = _roll_off_highlights(image, highlight_rolloff)

        return _encode(image, quality)


def label_center(
    data: bytes, text: str, *, quality: int = DEFAULT_JPEG_QUALITY
) -> PreparedImage:
    """Draw `text` across the middle of an already-prepared JPEG and re-encode it.

    The TV's picker shows thumbnails and no names, so when the same photo is on the wall
    several times over with only its mat differing, looking at it is the only way to tell the
    copies apart. A number drawn into the image also survives into a photograph of the panel,
    which is what `docs/spikes.md` T13 lacked when two shots of the same mat measured
    differently and nothing said which was which.

    It goes in the middle rather than a corner so it stays away from the mat it exists to help
    judge, and it is white over a dark stroke so it reads on a bright photo and a dark one
    alike. `text` may hold newlines, which is what lets `frame sync --label` put the crop that
    fired above the id that names the photo.
    """
    with Image.open(io.BytesIO(data)) as opened:
        image = opened.convert("RGB")

        ImageDraw.Draw(image).multiline_text(
            (image.width // 2, image.height // 2),
            text,
            font=ImageFont.load_default(size=LABEL_FONT_SIZE),
            anchor="mm",
            align="center",
            fill=(255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )
        return _encode(image, quality)


def fit_size(width: int, height: int) -> tuple[int, int]:
    """The largest size inside the panel that keeps the frame's shape, never upscaling."""
    scale = min(TARGET_WIDTH / width, TARGET_HEIGHT / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


def highlight_lut(rolloff: float) -> list[int]:
    """A 256-entry tone curve that pulls white down by `rolloff` without touching midtones.

    The shoulder is a quadratic Bezier. Its control point sits on the 45 degree line, so
    the curve leaves the knee at the slope it arrived with and there's no visible crease
    there. The control point sits halfway up to the new ceiling rather than at the corner,
    which is what keeps the curve from overshooting and inverting near white while still
    leaving some slope at the top for the brightest detail to survive in.
    """
    if rolloff <= 0:
        return list(range(256))
    if rolloff > MAX_HIGHLIGHT_ROLLOFF:
        raise ValueError(
            f"A rolloff over {MAX_HIGHLIGHT_ROLLOFF} would pull white below the shoulder's "
            "knee, which darkens the whole image rather than rolling off its highlights. "
            "Use `frame brightness` for that."
        )

    ceiling = 255 * (1 - rolloff)
    control = _SHOULDER_KNEE + (ceiling - _SHOULDER_KNEE) / 2

    # x(t) = a*t^2 + b*t + knee, solved for the curve parameter at each input value.
    a = 255 - 2 * control + _SHOULDER_KNEE
    b = 2 * (control - _SHOULDER_KNEE)

    lut = list(range(_SHOULDER_KNEE))
    for value in range(_SHOULDER_KNEE, 256):
        offset = _SHOULDER_KNEE - value
        if abs(a) < 1e-9:
            t = -offset / b
        else:
            t = (-b + math.sqrt(max(0.0, b * b - 4 * a * offset))) / (2 * a)

        y = _SHOULDER_KNEE * (1 - t) ** 2 + 2 * control * t * (1 - t) + ceiling * t * t
        lut.append(min(255, max(0, round(y))))

    return lut


def _encode(image: Image.Image, quality: int) -> PreparedImage:
    """Write the JPEG every caller here wants, so the encoding terms are stated once."""
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        # Full chroma resolution. JPEG's default stores color at half resolution in each
        # direction, which is invisible on a photograph but smears the hard color edges that
        # graphic and museum art are full of.
        subsampling=0,
        icc_profile=ImageCms.ImageCmsProfile(_SRGB).tobytes(),
    )
    return PreparedImage(data=buffer.getvalue(), width=image.width, height=image.height)


def _to_srgb(image: Image.Image) -> Image.Image:
    """Convert into sRGB, which means honoring an embedded profile rather than retagging.

    `convert("RGB")` only changes the mode, so a Display P3 photo would keep its own
    primaries and come out oversaturated on the panel.
    """
    profile = image.info.get("icc_profile")
    if profile:
        try:
            image = ImageCms.profileToProfile(
                image, ImageCms.ImageCmsProfile(io.BytesIO(profile)), _SRGB, outputMode="RGB"
            )
        except ImageCms.PyCMSError:
            # A malformed profile is not a reason to drop the photo; sRGB is the safe guess.
            pass

    return image.convert("RGB") if image.mode != "RGB" else image


def _crop(image: Image.Image, crop: Crop | None) -> Image.Image:
    if crop is None or not crop.crops:
        return image

    box = crop_box(image.width, image.height, parse_ratio(crop.rule.to), crop.rule.anchor)
    return image if box == (0, 0, image.width, image.height) else image.crop(box)


def _fit_to_panel(image: Image.Image) -> Image.Image:
    """Bound the frame to the panel, keeping its own shape."""
    size = fit_size(image.width, image.height)
    return image if size == image.size else image.resize(size, Image.LANCZOS)


def _roll_off_highlights(image: Image.Image, rolloff: float) -> Image.Image:
    if rolloff <= 0:
        return image

    # `point()` on an RGB image wants one entry per channel. Applying the same curve to
    # each is deliberate: the bloom this compensates for is a property of the panel's matte
    # film rather than of any one color.
    return image.point(highlight_lut(rolloff) * 3)
