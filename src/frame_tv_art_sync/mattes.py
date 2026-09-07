"""Which matte the TV will draw around an image of a given shape.

The TV renders the mat itself, so all this ever sends is a name, and the names are the
firmware's. What makes that worth its own module is that **the API accepts combinations the TV
refuses to draw, and one of them crashes Art Mode** -- a fixed-aperture type on anything but a
16:9 image puts an error dialog on the panel and needs a power cycle.

**The axis is the image's aspect ratio, not its orientation.** Three types draw a mat whose
aperture is fixed, cut for a 16:9, and the TV will only put a 16:9 image inside one; the other
three it displays whatever shape they are given. Orientation looked like the axis for as long
as every landscape anyone had tested was 16:9, and `docs/spikes.md` T22 is the round that put a
4:3 landscape up and got the same crash a portrait gets.

Everything here is pure, so the rules that keep a crash off the panel are covered by tests
rather than discovered against the hardware.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction

LANDSCAPE = "landscape"
PORTRAIT = "portrait"

# Types whose mat has a fixed aperture, cut for a 16:9 image. The TV draws one around a 16:9 and
# crashes Art Mode on any other shape, so nothing else may ever be sent one. Fixed does not mean
# the aperture is itself 16:9: T9 measured `modernwide` as wider than the panel, trimming a
# 1920x1080 top and bottom to fit.
FIXED_APERTURE_TYPES = frozenset({"modernthin", "modern", "modernwide"})

# Types the TV displays whatever shape it is given. What they share is that none of them
# crashes, not that they all fit: `flexible` and `shadowbox` cut an aperture to the image's own
# shape and crop nothing, while `none` draws no mat at all and lets the panel center-crop to
# fill, which costs a 3:4 portrait 58% of its height. That is a framing choice rather than a
# hazard, which is why it belongs here.
ACCEPTED_ON_ANY_SHAPE = frozenset({"flexible", "shadowbox", "none"})

# The one shape a fixed aperture is cut for.
FIXED_APERTURE_RATIO = Fraction(16, 9)

# How far from that a shape may be and still count as it. A photo's shape is rounded to whole
# pixels twice on its way to the panel, once by Google's resizer answering `=w1920-h1080` and
# once by `pipeline.fit_size()`, so a 2013x1135 source that is 16:9 arrives as 1915x1080, which
# is 23:13. Two names for one picture, three hundredths of a percent apart. The config key is
# looked up on the source's own shape, because that is the one a person would write down, while
# `tv.upload()` checks the bytes actually being sent -- so without a tolerance the second would
# refuse a matte the first had just chosen. Predicting the arriving size instead would not fix
# it, since Google's rounding happens outside this code and cannot be reproduced from here.
# One percent is roughly thirty times the largest rounding error and roughly a thirtieth of the
# distance to 3:2, the nearest shape anyone photographs, so it separates what has to be
# separated and nothing else.
FIXED_APERTURE_TOLERANCE = 0.01

# `get_matte_list()` also returns these four. The TV's picker offers them for no image at all,
# so nothing here will ever send one.
UNOFFERED_TYPES = frozenset({"panoramic", "triptych", "mix", "squares"})

# `burgandy` is the TV's own spelling and has to be sent that way.
COLORS = frozenset({
    "black", "neutral", "antique", "warm", "polar", "sand", "seafoam", "sage",
    "burgandy", "navy", "apricot", "byzantine", "lavender", "redorange", "skyblue",
    "turquoise",
})

# The one type with no color half. The TV reports it as a bare `none`, not `none_black`.
BARE_TYPE = "none"

# `portrait_matte_id` is keyed to the panel's orientation rather than the image's, for the
# models on an auto-rotating mount. A fixed-landscape panel never reads it, so it is sent as
# this and the image's own matte always goes through `matte_id`.
INERT_PORTRAIT_MATTE_ID = "none"

# A photo's pixel ratio is almost never exactly a small fraction: a 4080x3072 phone shot is
# 85/64, which is four tenths of a percent off 4:3 and indistinguishable from it on a 32" panel.
# So a ratio is snapped to the nearest fraction this simple before anything is looked up.
# Sixteen keeps 16:9 exact and is low enough that every ordinary photo shape lands on the name a
# person would actually write in a config file.
RATIO_DENOMINATOR_LIMIT = 16


class MatteError(Exception):
    """A matte id the TV would refuse, or would accept and then crash on."""


@dataclass(frozen=True)
class MatteChoice:
    """Which matte an image gets, and whether the config is what named it.

    `fell_back` is carried rather than inferred because it is the whole point of the fallback:
    a run that quietly mats a hundred photos in something nobody chose is worse than one that
    says so, and the caller is what turns this into the line at the end of a run.
    """

    matte_id: str
    ratio: Fraction
    fell_back: bool


def ratio_of(width: int, height: int) -> Fraction:
    """The image's aspect ratio, snapped to the nearest simple fraction.

    Snapping is what makes a ratio usable as a config key. Without it the album's landscapes
    alone come in as `85/64`, `4/3` and `1599/1204`, three names for one shape that no eye can
    tell apart, and nobody would write any of them down.
    """
    if width <= 0 or height <= 0:
        raise MatteError(f"{width}x{height} is not an image size.")

    return Fraction(width, height).limit_denominator(RATIO_DENOMINATOR_LIMIT)


def ratio_name(ratio: Fraction) -> str:
    """`Fraction(16, 9)` as `16:9`, which is how a config names it."""
    return f"{ratio.numerator}:{ratio.denominator}"


def parse_ratio_name(text: str) -> Fraction:
    """`16:9` back to a `Fraction`, reduced, so `16:10` and `8:5` are the same key."""
    width, separator, height = text.strip().partition(":")
    if not separator:
        raise MatteError(
            f"`{text}` is not an aspect ratio. One is two whole numbers joined by a colon, "
            "such as `16:9` or `3:4`."
        )

    try:
        ratio = Fraction(int(width), int(height))
    except (ValueError, ZeroDivisionError):
        raise MatteError(
            f"`{text}` is not an aspect ratio. Both halves have to be whole numbers, and the "
            "second can't be zero."
        ) from None

    if ratio <= 0:
        raise MatteError(f"`{text}` is not an aspect ratio. Both halves have to be positive.")

    return ratio


def shape_of(width: int, height: int) -> str:
    """The ratio name an image of this size is looked up under."""
    return ratio_name(ratio_of(width, height))


def orientation_of(width: int, height: int) -> str:
    """Which way round the image is. A square counts as landscape.

    This decides nothing about mattes any more -- `ratio_of` is what does -- and survives
    because a person still picks a photo by saying `landscape` or `portrait`, and because a
    round of the bakeoff is still worth narrowing that way.
    """
    return PORTRAIT if height > width else LANDSCAPE


def split_matte_id(matte_id: str) -> tuple[str, str | None]:
    """Split `flexible_black` into its type and color. A bare `none` has no color."""
    text = matte_id.strip()
    if not text:
        raise MatteError("A matte id can't be empty.")

    if text == BARE_TYPE:
        return BARE_TYPE, None

    matte_type, separator, color = text.partition("_")
    if not separator or not matte_type or not color:
        raise MatteError(
            f"`{matte_id}` is not a matte id. One is a type and a color joined by an "
            f"underscore, such as `flexible_black`, or the bare word `{BARE_TYPE}`."
        )
    if "_" in color:
        raise MatteError(
            f"`{matte_id}` has more than one underscore. No type or color name contains one."
        )
    if matte_type == BARE_TYPE:
        raise MatteError(
            f"`{matte_id}` is not a matte id. `{BARE_TYPE}` draws no mat, so it has no color "
            f"half and the TV reports it as the bare word `{BARE_TYPE}`."
        )

    return matte_type, color


def validate(matte_id: str, width: int, height: int) -> None:
    """Raise unless the TV will draw this matte around an image of this size."""
    validate_for_ratio(matte_id, ratio_of(width, height))


def validate_for_ratio(matte_id: str, ratio: Fraction) -> None:
    """Raise unless the TV will draw this matte around an image of this shape.

    Taking a ratio rather than a size is what lets a config be checked before any photo is
    known, since a `[art.matte_by_ratio]` key names the shape its matte is for.
    """
    matte_type, color = split_matte_id(matte_id)

    if matte_type not in types_for(ratio):
        raise MatteError(_why_the_type_is_wrong(matte_type, ratio))

    if color is not None and color not in COLORS:
        raise MatteError(
            f"`{color}` is not one of the colors this firmware reports. They are: "
            f"{', '.join(sorted(COLORS))}. `frame mattes` reads the live list, so update "
            "`COLORS` in `mattes.py` if the TV has gained one."
        )


def types_for(ratio: Fraction) -> frozenset[str]:
    """The types the TV will draw around an image of this shape."""
    if is_widescreen(ratio):
        return ACCEPTED_ON_ANY_SHAPE | FIXED_APERTURE_TYPES

    return ACCEPTED_ON_ANY_SHAPE


def is_widescreen(ratio: Fraction) -> bool:
    """Whether a fixed aperture will take this shape, within `FIXED_APERTURE_TOLERANCE`.

    Near-equality rather than equality because the two ratios one photo has are not the same
    number: `plan_sync` only ever sees the source's dimensions, while the bytes uploaded are
    that image bounded to the panel and rounded to whole pixels. A source that is 16:9 can
    become 23:13, and refusing the second after choosing for the first would fail an upload
    over a third of a hundredth of a percent.
    """
    return abs(ratio - FIXED_APERTURE_RATIO) <= FIXED_APERTURE_RATIO * FIXED_APERTURE_TOLERANCE


def offered_for(ratio: Fraction) -> list[str]:
    """What the TV will display around this shape, which is not quite what its picker offers.

    The one difference is `none` on a portrait: the picker withholds it, so a portrait can't be
    un-matted from the TV itself, but T9 uploaded one that way and got a center-crop rather than
    a crash. It is listed here because this is the set a bakeoff enumerates and an error message
    suggests, and both want what the TV accepts.
    """
    return sorted(types_for(ratio))


def every_known_type() -> frozenset[str]:
    """Every type this module has a record of, drawable or not.

    What the TV reports outside this set is a firmware that has gained one, which is worth
    saying out loud rather than treating as an unknown type somebody mistyped.
    """
    return ACCEPTED_ON_ANY_SHAPE | FIXED_APERTURE_TYPES | UNOFFERED_TYPES


def choose(
    width: int, height: int, *, by_ratio: Mapping[Fraction, str], fallback: str
) -> MatteChoice:
    """The matte an image of this size gets, and whether the config named it.

    An album holds more shapes than anyone wants to configure -- a stray screenshot, one photo
    off an older phone -- so a shape the config is silent about takes the fallback rather than
    stopping the run. The caller reports how often that happened, which is the only way anybody
    finds out a shape is worth naming.
    """
    ratio = ratio_of(width, height)
    configured = by_ratio.get(ratio)
    if configured is not None:
        return MatteChoice(matte_id=configured, ratio=ratio, fell_back=False)

    return MatteChoice(matte_id=fallback, ratio=ratio, fell_back=True)


def _why_the_type_is_wrong(matte_type: str, ratio: Fraction) -> str:
    if matte_type in UNOFFERED_TYPES:
        reason = "the TV's picker offers it for no image at all"
    elif matte_type not in every_known_type():
        reason = "no such type exists on this firmware"
    else:
        reason = (
            f"its aperture is a fixed {ratio_name(FIXED_APERTURE_RATIO)}, and this image is "
            f"{ratio_name(ratio)}"
        )

    return (
        f"`{matte_type}` can't be used on a {ratio_name(ratio)} image: {reason}. The types that "
        f"fit one are {', '.join(offered_for(ratio))}. This is refused here rather than sent "
        "because the TV accepts a matte it can't draw and then crashes Art Mode, which needs a "
        "power cycle to clear."
    )
