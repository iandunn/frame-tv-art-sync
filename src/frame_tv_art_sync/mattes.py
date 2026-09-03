"""Which mattes the TV will accept, and which one an image of a given shape should get.

The TV renders the mat itself, so all this ever sends is a name, and the names are the
firmware's. What makes that worth its own module is that **the API accepts combinations the
TV's own picker refuses, and one of them crashes Art Mode** -- `modernwide` on a portrait
puts an error dialog on the panel and needs a power cycle. `get_matte_list()` reports all ten
types whatever the image, so it can't be used to tell a safe one from a dangerous one; the
sets below came off the picker instead, and `docs/spikes.md` T15 records how.

Everything here is pure, so the rules that keep a crash off the panel are covered by tests
rather than discovered against the hardware.
"""

from __future__ import annotations

LANDSCAPE = "landscape"
PORTRAIT = "portrait"

# What the TV's own matte picker offers, per orientation of the image. A portrait gets two.
TYPES_BY_ORIENTATION: dict[str, frozenset[str]] = {
    LANDSCAPE: frozenset({"none", "modernthin", "modern", "modernwide", "flexible", "shadowbox"}),
    PORTRAIT: frozenset({"flexible", "shadowbox"}),
}

# `get_matte_list()` also returns `panoramic`, `triptych`, `mix` and `squares`. The picker
# offers those for neither orientation, so nothing here will send one.
UNOFFERED_TYPES = frozenset({"panoramic", "triptych", "mix", "squares"})

# `burgandy` is the TV's own spelling and has to be sent that way.
COLORS = frozenset({
    "black", "neutral", "antique", "warm", "polar", "sand", "seafoam", "sage",
    "burgandy", "navy", "apricot", "byzantine", "lavender", "redorange", "skyblue",
    "turquoise",
})

# The one type with no color half. The TV reports it as a bare `none`, not `none_black`.
BARE_TYPE = "none"

# Allowed on either orientation, unlike everything else, and `none` is the only member. The
# picker offers it for a landscape alone, but T9 uploaded a portrait with `matte="none"` and
# got a center-crop rather than a crash, so it is the one type with a direct observation
# behind it. Refusing it would also block the composited-mat fallback, which needs the TV to
# draw nothing at all. It costs a portrait 58% of its height, which is a framing choice
# rather than a hazard.
ACCEPTED_ANYWHERE = frozenset({BARE_TYPE})

# `portrait_matte_id` is keyed to the panel's orientation rather than the image's, for the
# models on an auto-rotating mount. A fixed-landscape panel never reads it, so it is sent as
# this and the image's own matte always goes through `matte_id`.
INERT_PORTRAIT_MATTE_ID = "none"


class MatteError(Exception):
    """A matte id the TV would refuse, or would accept and then crash on."""


def orientation_of(width: int, height: int) -> str:
    """A square counts as landscape, matching the pipeline's own crop decision."""
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

    return matte_type, color


def validate(matte_id: str, orientation: str) -> None:
    """Raise unless the TV offers this matte for an image of this shape."""
    matte_type, color = split_matte_id(matte_id)

    offered = TYPES_BY_ORIENTATION[orientation]
    if matte_type not in offered and matte_type not in ACCEPTED_ANYWHERE:
        raise MatteError(_why_the_type_is_wrong(matte_type, orientation, offered))

    if color is not None and color not in COLORS:
        raise MatteError(
            f"`{color}` is not one of the colors this firmware reports. They are: "
            f"{', '.join(sorted(COLORS))}. `frame mattes` reads the live list, so update "
            "`COLORS` in `mattes.py` if the TV has gained one."
        )


def matte_for(width: int, height: int, landscape_matte: str, portrait_matte: str) -> str:
    """The matte id to send for an image of this shape.

    Which key applies is decided by the image, not by the panel, because the two orientations
    accept different types. The caller has the dimensions from `available()`; the inventory
    doesn't record them.
    """
    if orientation_of(width, height) == PORTRAIT:
        return portrait_matte
    return landscape_matte


def offered_for(orientation: str) -> list[str]:
    return sorted(TYPES_BY_ORIENTATION[orientation])


def every_known_type() -> frozenset[str]:
    """Every type this module has a record of, offered or not.

    What the TV reports outside this set is a firmware that has gained one, which is worth
    saying out loud rather than treating as an unknown type somebody mistyped.
    """
    return frozenset().union(*TYPES_BY_ORIENTATION.values()) | UNOFFERED_TYPES


def _why_the_type_is_wrong(matte_type: str, orientation: str, offered: frozenset[str]) -> str:
    known = every_known_type()

    if matte_type in UNOFFERED_TYPES:
        reason = "the TV offers it for neither orientation"
    elif matte_type not in known:
        reason = "no such type exists on this firmware"
    else:
        reason = f"the TV offers it for a landscape only, and this image is a {orientation}"

    allowed = sorted(offered | ACCEPTED_ANYWHERE)
    return (
        f"`{matte_type}` can't be used on a {orientation}: {reason}. The types accepted on a "
        f"{orientation} are {', '.join(allowed)}. This is refused here rather than sent "
        "because the TV accepts a matte it can't draw and then crashes Art Mode, which needs "
        "a power cycle to clear."
    )
