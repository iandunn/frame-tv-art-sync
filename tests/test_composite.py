"""Covers the grouping and the spacing rule, which are the whole of `composite.py`."""

from __future__ import annotations

import pytest

from frame_tv_art_sync.composite import (
    LAYOUT_FULL,
    LAYOUT_GRID,
    LAYOUT_ROW,
    CompositeError,
    CompositeRule,
    CompositeStyle,
    Group,
    arrange,
    buried_rule,
    plan_groups,
)
from frame_tv_art_sync.crop import CropRule
from frame_tv_art_sync.sources import SourceItem

PORTRAIT_PAIR = CompositeRule(when="3:4", count=2, layout=LAYOUT_ROW)
LANDSCAPE_PAIR = CompositeRule(when="4:3", count=2, layout=LAYOUT_ROW)
LANDSCAPE_GRID = CompositeRule(when="4:3", count=4, layout=LAYOUT_GRID)
STYLE = CompositeStyle()


def portrait(name: str, taken: int) -> SourceItem:
    return SourceItem(source_id=name, url=f"https://x/{name}", width=3072, height=4080, taken_at_ms=taken)


def landscape(name: str, taken: int) -> SourceItem:
    return SourceItem(source_id=name, url=f"https://x/{name}", width=4080, height=3072, taken_at_ms=taken)


# Rules and style


def test_a_layout_that_is_not_one_is_refused():
    with pytest.raises(CompositeError):
        CompositeRule(when="3:4", count=2, layout="mosaic")


def test_full_takes_one_photo_only():
    with pytest.raises(CompositeError):
        CompositeRule(when="3:4", count=2, layout=LAYOUT_FULL)


@pytest.mark.parametrize("count", [0, -1])
def test_a_count_below_one_is_refused(count):
    with pytest.raises(CompositeError):
        CompositeRule(when="3:4", count=count, layout=LAYOUT_ROW)


def test_a_shape_that_is_not_one_is_refused():
    with pytest.raises(CompositeError):
        CompositeRule(when="wide", count=2, layout=LAYOUT_ROW)


def test_a_mat_the_tv_has_no_triple_for_is_refused():
    with pytest.raises(CompositeError):
        CompositeStyle(mat="chartreuse")


def test_an_edge_treatment_that_is_not_one_is_refused():
    with pytest.raises(CompositeError):
        CompositeStyle(edge="gilded")


def test_a_rule_buried_under_a_wider_one_is_found():
    wide = CompositeRule(when="landscape", count=2, layout=LAYOUT_ROW)
    assert buried_rule([wide, LANDSCAPE_PAIR]) == (wide, LANDSCAPE_PAIR)
    assert buried_rule([LANDSCAPE_PAIR, wide]) is None


# Grouping


def test_photos_are_chunked_oldest_first():
    items = [portrait("d", 400), portrait("a", 100), portrait("c", 300), portrait("b", 200)]

    groups = plan_groups(items, [PORTRAIT_PAIR])

    assert [group.source_ids for group in groups] == [("a", "b"), ("c", "d")]


def test_a_photo_added_at_the_newest_end_costs_one_composite():
    """The whole reason the chunking runs oldest first, and the cheapest case to protect."""
    items = [portrait(name, taken) for name, taken in (("a", 100), ("b", 200), ("c", 300))]
    before = {group.source_ids for group in plan_groups(items, [PORTRAIT_PAIR])}

    after = {group.source_ids for group in plan_groups(items + [portrait("n", 400)], [PORTRAIT_PAIR])}

    assert before == {("a", "b"), ("c",)}
    assert after == {("a", "b"), ("c", "n")}
    assert len(before & after) == 1


def test_the_leftover_is_the_newest_photo_and_hangs_alone():
    items = [portrait(name, taken) for name, taken in (("a", 100), ("b", 200), ("c", 300))]

    groups = plan_groups(items, [PORTRAIT_PAIR])

    assert groups[-1].source_ids == ("c",)
    assert groups[-1].layout == LAYOUT_ROW
    assert groups[-1].name == "row:1"


def test_a_partial_grid_falls_back_to_a_row():
    items = [landscape(name, taken) for name, taken in (("a", 1), ("b", 2), ("c", 3), ("d", 4), ("e", 5), ("f", 6))]

    groups = plan_groups(items, [LANDSCAPE_GRID])

    assert [group.name for group in groups] == ["grid:4", "row:2"]


def test_shapes_never_mix():
    items = [portrait("p1", 1), landscape("l1", 2), portrait("p2", 3), landscape("l2", 4)]

    groups = plan_groups(items, [PORTRAIT_PAIR, LANDSCAPE_PAIR])

    assert {group.source_ids for group in groups} == {("p1", "p2"), ("l1", "l2")}


def test_the_groups_come_back_in_the_albums_own_order():
    """Chunking gathers a shape together; the order they go up in stays the album's."""
    items = [
        landscape("l1", 1),
        landscape("l2", 2),
        portrait("p1", 3),
        landscape("l3", 4),
        portrait("p2", 5),
        portrait("p3", 6),
        landscape("l4", 7),
    ]

    groups = plan_groups(items, [PORTRAIT_PAIR, LANDSCAPE_PAIR])

    assert [group.source_ids for group in groups] == [
        ("l1", "l2"),
        ("p1", "p2"),
        ("l3", "l4"),
        ("p3",),
    ]


def test_a_shape_no_rule_names_goes_up_whole_on_its_own():
    wide = SourceItem(source_id="w", url="https://x/w", width=1920, height=1080, taken_at_ms=1)

    groups = plan_groups([wide], [PORTRAIT_PAIR])

    assert groups[0].is_full
    assert groups[0].name == "full:1"


def test_a_photo_is_grouped_on_the_shape_its_crop_leaves():
    """A rule can turn a portrait into a landscape, and the panel is handed the second one."""
    items = [portrait("p1", 1), portrait("p2", 2)]

    groups = plan_groups(
        items, [PORTRAIT_PAIR, LANDSCAPE_PAIR], crop=(CropRule(when="3:4", to="4:3"),)
    )

    assert groups[0].rule == LANDSCAPE_PAIR


def test_a_tie_on_the_shot_time_is_broken_by_the_source_id():
    items = [portrait("b", 100), portrait("a", 100)]

    assert plan_groups(items, [PORTRAIT_PAIR])[0].source_ids == ("a", "b")


# The spacing rule


def test_two_portraits_land_on_the_geometry_chosen_on_the_panel():
    cells = arrange(3 / 4, Group(rule=PORTRAIT_PAIR, items=(portrait("a", 1), portrait("b", 2))), STYLE)

    assert [(cell.width, cell.height) for cell in cells] == [(660, 880), (660, 880)]
    assert cells[0].left == 199 and cells[0].top == 100
    assert cells[1].left == 1060


def test_every_gap_on_an_axis_is_equal():
    cells = arrange(3 / 4, Group(rule=PORTRAIT_PAIR, items=(portrait("a", 1), portrait("b", 2))), STYLE)

    edge = STYLE.thickness
    left_margin = cells[0].left - edge
    between = (cells[1].left - edge) - (cells[0].left + cells[0].width + edge)
    right_margin = 1920 - (cells[1].left + cells[1].width + edge)

    # A panel that doesn't divide evenly leaves a pixel over, which lands in one gap.
    assert max(left_margin, between, right_margin) - min(left_margin, between, right_margin) <= 1


def test_lowering_the_vertical_floor_grows_the_prints():
    """`land-grid-tall`, which is the 2x2 with its top and bottom spaces halved."""
    items = tuple(landscape(name, index) for index, name in enumerate("abcd"))
    even = arrange(4 / 3, Group(rule=LANDSCAPE_GRID, items=items), STYLE)

    taller = CompositeRule(when="4:3", count=4, layout=LAYOUT_GRID, gap_down=49)
    grown = arrange(4 / 3, Group(rule=taller, items=items), STYLE)

    assert grown[0].height > even[0].height
    assert grown[0].width > even[0].width


def test_a_grid_fills_left_to_right_and_then_down():
    items = tuple(landscape(name, index) for index, name in enumerate("abcd"))

    cells = arrange(4 / 3, Group(rule=LANDSCAPE_GRID, items=items), STYLE)

    assert cells[0].top == cells[1].top and cells[2].top == cells[3].top
    assert cells[0].left == cells[2].left and cells[1].left == cells[3].left
    assert cells[2].top > cells[0].top


def test_a_full_group_is_the_whole_panel():
    wide = SourceItem(source_id="w", url="https://x/w", width=1920, height=1080, taken_at_ms=1)

    cells = arrange(16 / 9, Group(rule=CompositeRule(when="16:9"), items=(wide,)), STYLE)

    assert cells == (type(cells[0])(left=0, top=0, width=1920, height=1080),)


def test_prints_that_cannot_fit_are_refused_rather_than_drawn_at_a_pixel():
    items = tuple(portrait(str(index), index) for index in range(20))
    crowded = CompositeRule(when="3:4", count=20, layout=LAYOUT_ROW)

    with pytest.raises(CompositeError):
        arrange(3 / 4, Group(rule=crowded, items=items), STYLE)
