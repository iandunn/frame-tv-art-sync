"""What an image on the TV was rendered with, and what config says it should be rendered with.

The TV can be asked what it holds but not why it looks the way it does: `available()` reports a
`matte_id` and the stored dimensions, and nothing at all about the tone curve or the JPEG
quality behind the pixels. So the only account of how a copy was produced is the one kept here,
written into its inventory entry at upload time.

That is what lets `frame sync` notice that an image on the wall is stale. A matte can only be
set at upload time, since `change_matte()` does nothing on this firmware, so changing one means
uploading again; recording what each copy was made with turns that into a diff the sync already
knows how to carry out, rather than a separate command somebody has to remember to run. Every
field is compared, because a setting that changes nothing until the next photo happens to be
uploaded is a setting that silently doesn't apply.

An image can hold several photos, so a record describes a group rather than a photo. The crop
is the one thing that is still per photo, because `[pipeline.crop_overrides]` can give one
member of a group a different anchor from its neighbour.

Everything here is pure, and a record is a value: two are equal when they describe the same
rendering, which is the whole question this module exists to answer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

from . import composite as composites
from . import crop as crop_rules
from . import mattes
from .pipeline import PIPELINE_VERSION
from .sources import SourceItem

_FIELDS = (
    "pipeline_version",
    "matte_id",
    "layout",
    "edge",
    "mat",
    "gap_across",
    "gap_down",
    "crops",
    "labelled",
    "highlight_rolloff",
    "jpeg_quality",
    "image_date",
    "play_order",
)

# What a field reads as in a record written before it existed. A record missing one of these is
# an older upload rather than a malformed file, so the stand-in stands in for what that upload
# was really made with. `image_date` has no such answer and reads as a value that matches
# nothing, so the image is made again the way `from_stored(None)` would have it. `play_order`
# does: every upload before the key existed went up in album order, which is what
# `newest_first` names, so reading it that way is a fact rather than a guess and it keeps a
# config that never asked for anything from rebuilding the album. Every other field stays
# required, because guessing at one of those decides whether an image is destroyed.
_DEFAULTS = {"image_date": "unknown", "play_order": composites.PLAY_NEWEST_FIRST}

# The format the firmware parses `image_date` as, and the one `samsungtvws` fills in with the
# clock when nothing is passed.
DATE_FORMAT = "%Y:%m:%d %H:%M:%S"

# The rolloff is the one field that isn't an integer or a name, and equality on it decides
# whether the whole album is re-uploaded. Rounding on the way in means a record written by hand
# as `0.10`, or arrived at by arithmetic somewhere, still compares equal to the `0.1` in config.
ROLLOFF_PLACES = 4


class RenderError(Exception):
    """A stored render record that can't be read as one."""


@dataclass(frozen=True)
class RenderRecord:
    """The settings one upload was produced with, as stored beside its inventory entry.

    Normalizing in `__post_init__` rather than at each comparison is deliberate: a record built
    from config and one read back off disk go through the same door, so equality can be plain
    dataclass equality and no caller has to remember which form it is holding.

    `crops` runs in step with the entry's `source_ids`, one `(crop, anchor)` pair per photo. It
    is positional rather than keyed by source id because the ids are the group's identity
    already, so a record that lined up with a different group would be a record for a different
    image.

    `image_date` is the one field that isn't about the pixels. It is the only text an upload
    carries, it can't be changed afterwards any more than a matte can, and `available()` reports
    it empty, so it is recorded here for the same reason everything else is.

    `play_order` isn't about the pixels either, and it is here because the TV orders `play all`
    on insertion: where a copy sits on the wall is decided by when it was uploaded relative to
    the others, which is as unchangeable after the fact as a matte and as invisible from the
    TV. Recording it is what turns "this copy is in the wrong place" into the diff the sync
    already knows how to carry out, and it is what lets a run that died halfway resume rather
    than start over.
    """

    pipeline_version: int
    matte_id: str
    layout: str
    edge: str
    mat: str
    gap_across: int
    gap_down: int
    crops: tuple[tuple[str, str], ...]
    labelled: bool
    highlight_rolloff: float
    jpeg_quality: int
    image_date: str
    play_order: str

    def __post_init__(self) -> None:
        # Lower case for the same reason `tv.normalize_matte_id` exists: the TV reports a matte
        # in one case and takes it in another, so nothing anywhere should compare them raw.
        for field in ("matte_id", "layout", "edge", "mat"):
            object.__setattr__(self, field, getattr(self, field).strip().lower())

        object.__setattr__(
            self,
            "crops",
            tuple((crop.strip().lower(), anchor.strip().lower()) for crop, anchor in self.crops),
        )
        object.__setattr__(
            self, "highlight_rolloff", round(float(self.highlight_rolloff), ROLLOFF_PLACES)
        )

    def as_stored(self) -> dict[str, Any]:
        values = {field: getattr(self, field) for field in _FIELDS}
        values["crops"] = [{"crop": crop, "anchor": anchor} for crop, anchor in self.crops]
        return values


@dataclass(frozen=True)
class RenderSettings:
    """What config says an image should be rendered with now, whatever shape it turns out to be.

    It is one object rather than a pile of arguments so that the diff and the upload can be
    handed the same thing. They have to agree: the diff deciding an image is stale and the
    upload then producing something different would leave an image that is replaced on every
    run forever.
    """

    matte_by_ratio: Mapping[Fraction, str]
    fallback_matte: str
    highlight_rolloff: float
    jpeg_quality: int
    crop: tuple[crop_rules.CropRule, ...] = ()
    crop_overrides: tuple[tuple[str, crop_rules.CropRule], ...] = ()
    composite_style: composites.CompositeStyle = composites.CompositeStyle()

    # `frame sync --label` burns the crop that fired into the image, so a labelled copy is
    # different pixels from an unlabelled one and has to say so. Without it the record would
    # describe an image that isn't on the wall, and a plain sync afterwards would take the
    # labels off by accident rather than on purpose.
    labelled: bool = False
    pipeline_version: int = PIPELINE_VERSION

    # Which end of the album the panel is meant to start at. It says nothing about the pixels,
    # and it is here because a copy uploaded under the other one is in the wrong place on the
    # wall, which is a thing only a re-upload can put right.
    play_order: str = composites.PLAY_NEWEST_FIRST

    def matte_choice(self, item: SourceItem) -> mattes.MatteChoice:
        """The matte one photo gets, and whether `[art.matte_by_ratio]` is what named it.

        Only a `full` group reads this, since a composite carries its mat in its own pixels and
        goes up under `none`. A run reports how many photos took the fallback, and that count
        has to describe the mattes actually sent rather than a second answer worked out
        alongside them.
        """
        width, height = self.resolved_size(item)
        return mattes.choose(
            width, height, by_ratio=self.matte_by_ratio, fallback=self.fallback_matte
        )

    def resolved_size(self, item: SourceItem) -> tuple[int, int]:
        """The shape the panel is handed, which is the photo's own shape after its crop."""
        return crop_rules.resolved_size(
            item.width, item.height, item.source_id, self.crop, self.crop_overrides
        )

    def for_item(self, item: SourceItem) -> RenderRecord:
        """The record one photo gets going up whole and on its own, which is `full` of one.

        Every photo did this before composites existed, and a shape no `[[pipeline.composite]]`
        rule names still does, so it is worth being able to ask for directly.
        """
        return self.for_group(composites.Group(rule=composites.NO_RULE, items=(item,)))

    def for_group(self, group: composites.Group) -> RenderRecord:
        """The record one image should be carrying, given what config asks for now.

        The crop is resolved first and the matte chosen from the shape it leaves, because that
        is the shape the panel is handed: a rule can reshape a photo, and the TV draws different
        matte types around different shapes. Three of them need a 16:9 and crash Art Mode on
        anything else, so getting this order wrong is a power cycle rather than a wrong-looking
        mat.

        Everything is derived from the source items rather than from the prepared image. The
        diff has only the items, so anything read off the prepared bytes would be a second
        answer to the same question, and an image the two disagreed about would be replaced on
        every run forever.
        """
        style = self.composite_style

        return RenderRecord(
            pipeline_version=self.pipeline_version,
            # A composite carries its mat in its own pixels, so the TV draws none. Only a group
            # of one going up whole reads the table, and then by the resolved id rather than the
            # ratio it was found under, so two configs arriving at one matte by different routes
            # replace nothing.
            matte_id=(
                self.matte_choice(group.items[0]).matte_id
                if group.is_full
                else mattes.BARE_TYPE
            ),
            layout=group.name,
            edge=style.edge,
            mat=style.mat,
            gap_across=group.rule.gap_across or style.gap_across,
            gap_down=group.rule.gap_down or style.gap_down,
            # The rule's effect rather than the rule, so that rewriting a `when` clause to catch
            # the same photo the same way replaces nothing. An uncropped photo records a fixed
            # pair, because an anchor decides nothing when nothing is cut.
            crops=tuple(self._crop_of(item) for item in group.items),
            labelled=self.labelled,
            highlight_rolloff=self.highlight_rolloff,
            jpeg_quality=self.jpeg_quality,
            # The oldest photo is what places the group in the album, so it is what dates the
            # image too. A group of one is its own oldest.
            image_date=stamp(group.items[0].taken_at_ms),
            play_order=self.play_order,
        )

    def _crop_of(self, item: SourceItem) -> tuple[str, str]:
        crop = crop_rules.resolve(
            item.width, item.height, item.source_id, self.crop, self.crop_overrides
        )
        return (
            (crop.rule.to, crop.rule.anchor)
            if crop.crops
            else (crop_rules.NO_CROP, crop_rules.CENTER)
        )


def stamp(taken_at_ms: int) -> str:
    """When a photo was taken, in the form the firmware parses `image_date` as.

    UTC rather than local time, because the source reports an instant and not the offset it was
    captured at, and a stamp that moved with the running machine's timezone would replace the
    whole album the first time a scheduled job ran from somewhere else. A source with nothing to
    report says 0, which stamps the epoch and sorts oldest, matching what `taken_at_ms` already
    means everywhere else.
    """
    return datetime.fromtimestamp(taken_at_ms / 1000, UTC).strftime(DATE_FORMAT)


def from_stored(value: Any) -> RenderRecord | None:
    """Read a record out of an inventory entry, `None` meaning the entry predates them.

    A missing record and a malformed one are different: the first is every entry written before
    this existed and reads as "unknown", while the second is a file that says something its
    author didn't mean. The first is an image to upload again and the second is an error,
    because the alternative is guessing at a value that decides whether an image is destroyed.
    """
    if value is None:
        return None

    if not isinstance(value, dict):
        raise RenderError("a render record has to be a table")

    missing = [field for field in _FIELDS if field not in value and field not in _DEFAULTS]
    if missing:
        raise RenderError(f"a render record is missing {', '.join(missing)}")

    # A copy, so that reading a record older than one of its fields doesn't write the stand-in
    # value back into the inventory the next time it is saved.
    value = {**_DEFAULTS, **value}

    for name in ("pipeline_version", "jpeg_quality", "gap_across", "gap_down"):
        if not _is_whole(value[name]):
            raise RenderError(f"`{name}` has to be a whole number")
    for name in ("matte_id", "layout", "edge", "mat", "image_date", "play_order"):
        if not isinstance(value[name], str) or not value[name].strip():
            raise RenderError(f"`{name}` has to be a non-empty string")
    if not isinstance(value["labelled"], bool):
        raise RenderError("`labelled` has to be `true` or `false`")

    rolloff = value["highlight_rolloff"]
    if isinstance(rolloff, bool) or not isinstance(rolloff, (int, float)):
        raise RenderError("`highlight_rolloff` has to be a number")

    return RenderRecord(
        pipeline_version=int(value["pipeline_version"]),
        matte_id=value["matte_id"],
        layout=value["layout"],
        edge=value["edge"],
        mat=value["mat"],
        gap_across=int(value["gap_across"]),
        gap_down=int(value["gap_down"]),
        crops=_stored_crops(value["crops"]),
        labelled=value["labelled"],
        highlight_rolloff=float(rolloff),
        jpeg_quality=int(value["jpeg_quality"]),
        image_date=value["image_date"],
        play_order=value["play_order"],
    )


def describe_change(stored: RenderRecord | None, wanted: RenderRecord) -> str:
    """Why an image is being uploaded again, short enough to sit at the end of a dry run's line.

    This is read once, before a run that can re-upload the whole album, so it says which
    settings moved rather than only that something did.
    """
    if stored is None:
        return "no record"

    changes = [
        f"{field.replace('_', ' ')} {_shown(getattr(stored, field))} to "
        f"{_shown(getattr(wanted, field))}"
        for field in _FIELDS
        if getattr(stored, field) != getattr(wanted, field)
    ]

    return ", ".join(changes) if changes else "no change"


def _shown(value: Any) -> str:
    """A field as a dry run prints it, which for the crops is one phrase rather than a tuple."""
    if isinstance(value, tuple):
        return "/".join(f"{crop}@{anchor}" for crop, anchor in value)

    return str(value)


def _stored_crops(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or not value:
        raise RenderError("`crops` has to be a non-empty list, one entry per photo")

    crops = []
    for row in value:
        if not isinstance(row, dict):
            raise RenderError("each entry in `crops` has to be a table")

        crop, anchor = row.get("crop"), row.get("anchor")
        for name, text in (("crop", crop), ("anchor", anchor)):
            if not isinstance(text, str) or not text.strip():
                raise RenderError(f"`crops.{name}` has to be a non-empty string")

        crops.append((crop, anchor))

    return tuple(crops)


def _is_whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def resolved_sizes(
    items: Sequence[SourceItem],
    crop: Sequence[crop_rules.CropRule],
    overrides: Sequence[tuple[str, crop_rules.CropRule]] = (),
) -> list[tuple[int, int]]:
    """Every photo's post-crop shape, which is what grouping and the layout both key on."""
    return [
        crop_rules.resolved_size(
            item.width, item.height, item.source_id, tuple(crop), tuple(overrides)
        )
        for item in items
    ]
