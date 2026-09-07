"""Which photos share one image on the TV, and where each one sits inside it.

A 3:4 photo alone on a 16:9 panel leaves most of the wall empty, and no matte fixes that,
because the mat is not what looks wrong. Several photos under one mat painted into the JPEG is
what does, and `docs/composites.md` has the rounds on the panel that settled the treatments.

Everything here is pure arithmetic over the source's own numbers, so the grouping and the
layout can be tested without a TV or a photo. `pipeline.compose()` is what turns a layout into
pixels, and it is the only thing here that touches an image.

**The spacing rule:** on each axis every gap is equal, the outer margins and the gaps between
prints alike, so an axis holding `n` prints is divided into `n + 1` equal spaces. The prints are
then as large as they can be with no gap under the floor its rule asks for.

Equal gaps on both axes at once is not reachable, and no floor gets there. Two 3:4 prints
filling the panel's height come to 1620px wide including three equal gaps, whatever height is
chosen, against a panel 1920 wide. So one axis is always looser than the other, and which one
depends on the shape.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from . import crop as crop_rules
from .pipeline import TARGET_HEIGHT, TARGET_WIDTH
from .sources import SourceItem

LAYOUT_ROW = "row"
LAYOUT_GRID = "grid"

# One photo filling the panel with no painted mat at all, which is what every photo does today.
# It stays reachable so a shape can opt out of compositing, and it is the only layout whose
# matte comes from `[art.matte_by_ratio]` rather than being `none`.
LAYOUT_FULL = "full"

LAYOUTS = (LAYOUT_ROW, LAYOUT_GRID, LAYOUT_FULL)

# The floor the two-up approved on the panel sits at. A print grows until one of its gaps
# reaches this, so lowering it on an axis is what buys a bigger print.
DEFAULT_GAP = 98

# The mat is painted rather than drawn by the TV, so it needs a real color. These are the
# triples `get_matte_list()` reports for the TV's own mats, which is what lets a composite hang
# in the same rotation as a TV-matted photo without a seam. `frame mattes` prints the live list,
# so a color the TV has and this doesn't can be added from there.
MAT_COLORS = {
    "antique": (224, 219, 210),
    "polar": (232, 230, 231),
    "black": (34, 34, 33),
}

# What sits between a print and the mat. Each one is drawn by `pipeline.compose()`, and the
# number is how far it reaches past the print, which the layout has to account for so that a
# thick treatment and a thin one still leave the same gap between prints.
EDGE_THICKNESS = {
    "none": 0,
    "keyline": 2,
    "bevel": 2,
    "shadowbox": 2,
}


class CompositeError(ValueError):
    """A rule or a style naming something that can't be drawn."""


@dataclass(frozen=True)
class CompositeStyle:
    """How every composite is finished, which is deliberately not per rule.

    A wall showing two mat colors at once is not a wall anybody wants, so the mat and the edge
    are settled once. The gaps are the exception: they sit here as the defaults and a rule may
    lower one, which is the only way a grid with bigger prints is expressible.
    """

    mat: str = "antique"
    edge: str = "shadowbox"
    tooth: bool = True
    gap_across: int = DEFAULT_GAP
    gap_down: int = DEFAULT_GAP

    def __post_init__(self) -> None:
        if self.mat not in MAT_COLORS:
            raise CompositeError(
                f"`{self.mat}` is not a mat color this can paint. They are: "
                f"{', '.join(sorted(MAT_COLORS))}."
            )
        if self.edge not in EDGE_THICKNESS:
            raise CompositeError(
                f"`{self.edge}` is not an edge treatment. They are: "
                f"{', '.join(sorted(EDGE_THICKNESS))}."
            )
        _check_gap(self.gap_across, "gap_across")
        _check_gap(self.gap_down, "gap_down")

    @property
    def rgb(self) -> tuple[int, int, int]:
        return MAT_COLORS[self.mat]

    @property
    def thickness(self) -> int:
        return EDGE_THICKNESS[self.edge]


@dataclass(frozen=True)
class CompositeRule:
    """One row of the table: photos of `when` shape go up `count` at a time, arranged `layout`.

    `when` is read the same way `[[pipeline.crop]]` reads its own, since two tables keyed on the
    shape coming in should agree about what a key means. The first rule a photo matches wins.
    """

    when: str
    count: int = 1
    layout: str = LAYOUT_FULL
    gap_across: int | None = None
    gap_down: int | None = None

    def __post_init__(self) -> None:
        # Re-raised so that everything wrong with this table reads as one kind of error, even
        # though the shape is parsed by the module `[[pipeline.crop]]` shares with it.
        try:
            crop_rules.parse_shape(self.when)
        except crop_rules.CropError as error:
            raise CompositeError(str(error)) from None

        if self.layout not in LAYOUTS:
            raise CompositeError(
                f"`{self.layout}` is not a layout. They are: {', '.join(LAYOUTS)}."
            )
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count < 1:
            raise CompositeError("`count` has to be a whole number of at least 1.")
        if self.layout == LAYOUT_FULL and self.count != 1:
            raise CompositeError(
                "`full` fills the panel with one photo, so it can only take `count = 1`. Use "
                "`row` or `grid` for more than one."
            )
        for gap, name in ((self.gap_across, "gap_across"), (self.gap_down, "gap_down")):
            if gap is not None:
                _check_gap(gap, name)

    def matches(self, width: int, height: int) -> bool:
        return crop_rules.matches_shape(self.when, width, height)

    def shadows(self, later: CompositeRule) -> bool:
        return crop_rules.shadows_shape(self.when, later.when)


# What a photo matching no rule gets, which is the behavior every photo has today.
NO_RULE = CompositeRule(when=crop_rules.ANY, count=1, layout=LAYOUT_FULL)


@dataclass(frozen=True)
class Group:
    """The photos of one composite, in the order they are drawn.

    Order is part of the identity because it is part of the image: the same two photos left to
    right and right to left are different pixels. It is the order the chunking used, so the
    oldest hangs on the left and a grid fills left to right and then down.
    """

    rule: CompositeRule
    items: tuple[SourceItem, ...]

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(item.source_id for item in self.items)

    @property
    def is_partial(self) -> bool:
        return len(self.items) < self.rule.count

    @property
    def layout(self) -> str:
        """A partial group falls back to a row rather than breaking into singles.

        Four to a grid over an odd album leaves two, and those two go up as one pair. Only a
        group that falls all the way to one hangs alone, and it hangs under the same mat.
        """
        return LAYOUT_ROW if self.is_partial else self.rule.layout

    @property
    def name(self) -> str:
        """What the render record stores, so a layout change is a rendering change."""
        return f"{self.layout}:{len(self.items)}"

    @property
    def is_full(self) -> bool:
        return self.layout == LAYOUT_FULL


@dataclass(frozen=True)
class Cell:
    """Where one print goes, in panel pixels, not counting whatever is drawn around it."""

    left: int
    top: int
    width: int
    height: int


def plan_groups(
    items: Sequence[SourceItem],
    rules: Sequence[CompositeRule],
    *,
    crop: Sequence[crop_rules.CropRule] = (),
    crop_overrides: Sequence[tuple[str, crop_rules.CropRule]] = (),
) -> list[Group]:
    """Which photos share a composite, from the album and the config and nothing else.

    Reading the existing groups out of the inventory instead would keep a photo added mid-album
    from re-pairing everything after it, and it was in an earlier design. It buys less than it
    costs: it makes a `count` edit inert on the groups already up, it lets singles accumulate,
    and it pairs a stranded photo with whatever arrives next whatever their dates.

    **Oldest first is what makes the ordinary case cheap.** Chunking from the newest end would
    re-pair everything below an insertion and park the leftover single at the oldest end. From
    the oldest end a photo added at the newest end costs one composite, and the leftover is the
    newest photo, so the next one added pairs with the one taken just before it.

    The shape a photo is grouped on is its shape **after** its crop rule, since that is the
    shape the panel is handed and a rule can turn a portrait into a landscape.
    """
    buckets: dict[int, list[SourceItem]] = {}
    for item in items:
        width, height = crop_rules.resolved_size(
            item.width, item.height, item.source_id, tuple(crop), tuple(crop_overrides)
        )
        buckets.setdefault(_rule_index(rules, width, height), []).append(item)

    groups: list[Group] = []
    for index in sorted(buckets):
        rule = rules[index] if index < len(rules) else NO_RULE
        oldest_first = sorted(buckets[index], key=lambda item: (item.taken_at_ms, item.source_id))

        for start in range(0, len(oldest_first), rule.count):
            groups.append(Group(rule=rule, items=tuple(oldest_first[start : start + rule.count])))

    return groups


def buried_rule(rules: Sequence[CompositeRule]) -> tuple[CompositeRule, CompositeRule] | None:
    """The first rule that can never fire, with the rule burying it, or `None`.

    The first match wins, so a rule under a wider one is dead. A table read top to bottom makes
    that easy to write and nothing else would ever say so.
    """
    for index, rule in enumerate(rules):
        for later in rules[index + 1 :]:
            if rule.shadows(later):
                return rule, later

    return None


def arrange(ratio: float, group: Group, style: CompositeStyle) -> tuple[Cell, ...]:
    """Where each print of one composite sits on the panel.

    `ratio` is the shape every print in the group shares, which grouping guarantees, so the
    cells come out uniform. A `full` group is the whole panel and has no mat to space.
    """
    count = len(group.items)
    if group.is_full:
        return (Cell(left=0, top=0, width=TARGET_WIDTH, height=TARGET_HEIGHT),)

    columns, rows = _shape_of(group.layout, count)
    gap_across = group.rule.gap_across or style.gap_across
    gap_down = group.rule.gap_down or style.gap_down
    edge = style.thickness

    height = _print_height(ratio, columns, rows, edge, gap_across, gap_down)
    width = max(1, round(height * ratio))
    block_width, block_height = width + edge * 2, height + edge * 2

    # Every gap on an axis is equal, so an axis holding `n` prints is `n + 1` of them.
    across = (TARGET_WIDTH - columns * block_width) // (columns + 1)
    down = (TARGET_HEIGHT - rows * block_height) // (rows + 1)

    return tuple(
        Cell(
            left=across + edge + (index % columns) * (block_width + across),
            top=down + edge + (index // columns) * (block_height + down),
            width=width,
            height=height,
        )
        for index in range(count)
    )


def _print_height(
    ratio: float, columns: int, rows: int, edge: int, gap_across: int, gap_down: int
) -> int:
    """The largest print leaving every gap on each axis at or above that axis's floor."""
    across = ((TARGET_WIDTH - (columns + 1) * gap_across) / columns - edge * 2) / ratio
    down = (TARGET_HEIGHT - (rows + 1) * gap_down) / rows - edge * 2
    height = int(min(across, down))

    if height < 1:
        raise CompositeError(
            f"{columns * rows} prints of {ratio:.2f}:1 don't fit on the panel with gaps of "
            f"{gap_across} across and {gap_down} down. Lower a gap or lower the count."
        )

    return height


def _shape_of(layout: str, count: int) -> tuple[int, int]:
    """How many columns and rows a layout puts `count` prints into."""
    if layout == LAYOUT_ROW:
        return count, 1

    columns = math.ceil(math.sqrt(count))
    return columns, math.ceil(count / columns)


def _rule_index(rules: Sequence[CompositeRule], width: int, height: int) -> int:
    """The first rule a shape matches, or one past the end for a shape no rule names."""
    for index, rule in enumerate(rules):
        if rule.matches(width, height):
            return index

    return len(rules)


def _check_gap(gap: int, name: str) -> None:
    if not isinstance(gap, int) or isinstance(gap, bool) or gap < 1:
        raise CompositeError(f"`{name}` has to be a whole number of panel pixels, at least 1.")
