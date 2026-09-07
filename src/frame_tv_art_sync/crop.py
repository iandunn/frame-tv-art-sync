"""Decides how much of a photo to throw away before it reaches the TV.

The rules are keyed on the shape of the photo coming in rather than on one target shape for
everything, because a 4:3 and a 3:4 want different answers and the panel is 16:9. A rule says
"a photo of about this shape becomes about that shape", and the first one that matches wins.

Matching is a tolerance rather than an equality, and that is not a nicety. A Pixel writes
4080x3072, which is 0.4% off exact 4:3, so a rule that demanded equality would match nothing
in a real album. The tolerance is far tighter than the gap to the next shape anybody uses:
3:2 sits 12.5% away from 4:3 and 16:9 sits 33% away.

A crop here is permanent, unlike a matte, since it is baked into the JPEG the TV stores.
Changing one costs the same full re-upload that changing a matte does.
"""

from __future__ import annotations

from dataclasses import dataclass

# How far a photo's aspect ratio may sit from a rule's before the rule stops matching, as a
# fraction of the rule's own ratio.
RATIO_TOLERANCE = 0.02

# What a rule says to leave a shape alone. It is a value rather than the absence of a rule so
# that "don't crop a portrait" can be written down next to the rule that does crop a landscape.
NO_CROP = "none"

# Shorthands a rule can match on instead of a ratio.
LANDSCAPE = "landscape"
PORTRAIT = "portrait"
ANY = "*"
_SHAPE_WORDS = frozenset({LANDSCAPE, PORTRAIT, ANY})

CENTER = "center"
_VERTICAL_ANCHORS = ("top", CENTER, "bottom")
_HORIZONTAL_ANCHORS = ("left", CENTER, "right")
ANCHORS = tuple(dict.fromkeys(_VERTICAL_ANCHORS + _HORIZONTAL_ANCHORS))

# How much of a source id the label draws. Ten characters is already unique across a 169 photo
# album -- every Google id opens with the same `AF1Qip` -- and twelve leaves room to grow.
LABEL_ID_CHARS = 12

# The shortest an override key may be. An override names one photo, and a key short enough to
# match every id in an album would silently crop the whole collection.
MIN_OVERRIDE_KEY = 8


class CropError(ValueError):
    """A rule names a shape, a target, or an anchor that can't be read."""


@dataclass(frozen=True)
class CropRule:
    """One row of the table: photos of `when` shape become `to` shape, anchored at `anchor`.

    `when` is a ratio like `4:3`, or `landscape`, `portrait`, or `*`. `to` is a ratio or
    `none`. `anchor` names the part of the frame to keep, and only the axis actually being
    trimmed reads it, so `top` on a rule that trims width is inert rather than wrong.
    """

    when: str
    to: str
    anchor: str = CENTER

    def __post_init__(self) -> None:
        if self.when not in _SHAPE_WORDS:
            parse_ratio(self.when)
        if self.to != NO_CROP:
            parse_ratio(self.to)
        if self.anchor not in ANCHORS:
            raise CropError(
                f"`{self.anchor}` is not an anchor. Use one of: {', '.join(ANCHORS)}."
            )

    @property
    def crops(self) -> bool:
        return self.to != NO_CROP

    def matches(self, width: int, height: int) -> bool:
        if self.when == ANY:
            return True
        if self.when == PORTRAIT:
            return height > width
        if self.when == LANDSCAPE:
            return width >= height

        return ratios_match(width / height, parse_ratio(self.when))

    def shadows(self, later: CropRule) -> bool:
        """Whether every shape `later` would match, this rule matches first.

        The first match wins, so a rule under one of these can never fire. `landscape` over a
        landscape ratio is the case worth naming: it reads as narrowing and is really burial.
        """
        if self.when == ANY:
            return True
        if self.when in (LANDSCAPE, PORTRAIT):
            return later.when == self.when or (
                later.when not in _SHAPE_WORDS
                and (parse_ratio(later.when) < 1) == (self.when == PORTRAIT)
            )
        if later.when in _SHAPE_WORDS:
            return False

        return ratios_match(parse_ratio(later.when), parse_ratio(self.when))


@dataclass(frozen=True)
class Crop:
    """What a photo actually gets, once a rule has been picked for it.

    `label` is the human sentence for it, and it is what `frame sync --label` burns into the
    image so the decision can be read off the panel rather than inferred from it.
    """

    rule: CropRule
    label: str

    @property
    def crops(self) -> bool:
        return self.rule.crops


def parse_ratio(text: str) -> float:
    """Read `16:9` as a number, refusing anything that isn't two positive numbers."""
    parts = text.split(":")
    if len(parts) != 2:
        raise CropError(
            f"`{text}` is not an aspect ratio. Write one as `width:height`, like `16:9`."
        )

    try:
        width, height = float(parts[0]), float(parts[1])
    except ValueError:
        raise CropError(f"`{text}` is not an aspect ratio. Both halves have to be numbers.")

    if width <= 0 or height <= 0:
        raise CropError(f"`{text}` is not an aspect ratio. Both halves have to be positive.")

    return width / height


def ratios_match(actual: float, wanted: float) -> bool:
    return abs(actual - wanted) <= wanted * RATIO_TOLERANCE


def crop_box(
    width: int, height: int, target: float, anchor: str = CENTER
) -> tuple[int, int, int, int]:
    """The largest window of `target` shape inside the frame, placed by `anchor`.

    A frame already at the target shape comes back whole, so a rule that fires on a photo it
    would not change costs nothing.
    """
    actual = width / height
    if ratios_match(actual, target):
        return 0, 0, width, height

    if target > actual:
        # The target is wider, so height is what goes.
        crop_width, crop_height = width, max(1, round(width / target))
    else:
        crop_width, crop_height = max(1, round(height * target)), height

    left = _offset(width - crop_width, anchor, _HORIZONTAL_ANCHORS)
    top = _offset(height - crop_height, anchor, _VERTICAL_ANCHORS)
    return left, top, left + crop_width, top + crop_height


def cropped_size(width: int, height: int, crop: Crop) -> tuple[int, int]:
    """The frame's shape after `crop`, which is what decides its matte.

    A crop can change a photo's orientation -- `3:4 -> 16:9` turns a portrait into a landscape
    -- and the matte is picked from the shape that reaches the TV. So a dry run has to ask this
    rather than the source's own dimensions, or it names one matte and the run applies another.
    """
    if not crop.crops:
        return width, height

    left, top, right, bottom = crop_box(width, height, parse_ratio(crop.rule.to), crop.rule.anchor)
    return right - left, bottom - top


def resolve(
    width: int,
    height: int,
    source_id: str,
    rules: tuple[CropRule, ...],
    overrides: tuple[tuple[str, CropRule], ...] = (),
) -> Crop:
    """The crop one photo gets: its own override if it has one, else the first matching rule.

    An override wins outright rather than being merged into a rule, because the reason to
    write one is that the rule got this photo wrong.
    """
    for key, rule in overrides:
        if source_id.startswith(key):
            return Crop(rule=rule, label=_label(rule, overridden=True))

    for rule in rules:
        if rule.matches(width, height):
            return Crop(rule=rule, label=_label(rule, overridden=False))

    return Crop(rule=CropRule(when=ANY, to=NO_CROP), label="no crop")


def unmatched_overrides(
    source_ids: list[str], overrides: tuple[tuple[str, CropRule], ...]
) -> list[str]:
    """Override keys that name no photo in this run, which is what a typo looks like."""
    return [
        key
        for key, _ in overrides
        if not any(source_id.startswith(key) for source_id in source_ids)
    ]


def ambiguous_overrides(
    source_ids: list[str], overrides: tuple[tuple[str, CropRule], ...]
) -> list[str]:
    """Override keys short enough to name more than one photo in this run.

    This can only be answered once the source has listed its items, so it is checked per run
    rather than when the config loads.
    """
    return [
        key
        for key, _ in overrides
        if sum(source_id.startswith(key) for source_id in source_ids) > 1
    ]


def _offset(slack: int, anchor: str, axis: tuple[str, str, str]) -> int:
    """Where the window starts on one axis, given how much of that axis is being given up."""
    if slack <= 0 or anchor not in axis:
        return max(0, slack // 2)
    if anchor == axis[0]:
        return 0
    if anchor == axis[2]:
        return slack

    return slack // 2


def _label(rule: CropRule, *, overridden: bool) -> str:
    """The sentence drawn on the image, naming the rule rather than the pixel numbers.

    It says what fired, because reading a crop off the photograph is exactly what is hard: a
    photo that lost a quarter of its height still looks like a photo. Naming the rule is also
    what makes the label actionable, since it points at the line of config to edit.
    """
    if overridden:
        body = "kept whole" if not rule.crops else f"-> {rule.to} {rule.anchor}"
        return f"override {body}"

    return (
        f"{rule.when} kept whole"
        if not rule.crops
        else f"{rule.when} -> {rule.to} {rule.anchor}"
    )
