"""What a photo on the TV was rendered with, and what config says it should be rendered with.

The TV can be asked what it holds but not why it looks the way it does: `available()` reports a
`matte_id` and the stored dimensions, and nothing at all about the tone curve or the JPEG
quality behind the pixels. So the only account of how a copy was produced is the one kept here,
written into its inventory entry at upload time.

That is what lets `frame sync` notice that a photo on the wall is stale. A matte can only be set
at upload time, since `change_matte()` does nothing on this firmware, so changing one means
uploading the photo again; recording what each copy was made with turns that into a diff the
sync already knows how to carry out, rather than a separate command somebody has to remember to
run. Every field is compared, because a setting that changes nothing until the next photo
happens to be uploaded is a setting that silently doesn't apply.

Everything here is pure, and a record is a value: two are equal when they describe the same
rendering, which is the whole question this module exists to answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from . import crop as crop_rules
from . import mattes
from .pipeline import PIPELINE_VERSION
from .sources import SourceItem

_FIELDS = (
    "pipeline_version",
    "matte_id",
    "crop",
    "crop_anchor",
    "labelled",
    "highlight_rolloff",
    "jpeg_quality",
)

# The rolloff is the one field that isn't an integer or a name, and equality on it decides
# whether 174 photos are re-uploaded. Rounding on the way in means a record written by hand as
# `0.10`, or arrived at by arithmetic somewhere, still compares equal to the `0.1` in config.
ROLLOFF_PLACES = 4


class RenderError(Exception):
    """A stored render record that can't be read as one."""


@dataclass(frozen=True)
class RenderRecord:
    """The settings one upload was produced with, as stored beside its inventory entry.

    Normalizing in `__post_init__` rather than at each comparison is deliberate: a record built
    from config and one read back off disk go through the same door, so equality can be plain
    dataclass equality and no caller has to remember which form it is holding.
    """

    pipeline_version: int
    matte_id: str
    crop: str
    crop_anchor: str
    labelled: bool
    highlight_rolloff: float
    jpeg_quality: int

    def __post_init__(self) -> None:
        # Lower case for the same reason `tv.normalize_matte_id` exists: the TV reports a matte
        # in one case and takes it in another, so nothing anywhere should compare them raw.
        object.__setattr__(self, "matte_id", self.matte_id.strip().lower())
        object.__setattr__(self, "crop", self.crop.strip().lower())
        object.__setattr__(self, "crop_anchor", self.crop_anchor.strip().lower())
        object.__setattr__(
            self, "highlight_rolloff", round(float(self.highlight_rolloff), ROLLOFF_PLACES)
        )

    def as_stored(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in _FIELDS}


@dataclass(frozen=True)
class RenderSettings:
    """What config says a photo should be rendered with now, whatever shape it turns out to be.

    It is one object rather than four arguments so that the diff and the upload can be handed
    the same thing. They have to agree: the diff deciding a photo is stale and the upload then
    producing something different would leave a photo that is replaced on every run forever.
    """

    matte_by_ratio: Mapping[Fraction, str]
    fallback_matte: str
    highlight_rolloff: float
    jpeg_quality: int
    crop: tuple[crop_rules.CropRule, ...] = ()
    crop_overrides: tuple[tuple[str, crop_rules.CropRule], ...] = ()

    # `frame sync --label` burns the crop that fired into the middle of the image, so a
    # labelled copy is different pixels from an unlabelled one and has to say so. Without it
    # the record would describe a photo that isn't on the wall, and a plain sync afterwards
    # would take the labels off by accident rather than on purpose.
    labelled: bool = False
    pipeline_version: int = PIPELINE_VERSION

    def matte_choice(self, item: SourceItem) -> mattes.MatteChoice:
        """The matte one photo gets, and whether `[art.matte_by_ratio]` is what named it.

        Split out of `for_item` because a run reports how many photos took the fallback, and
        that count has to describe the mattes actually sent rather than a second answer worked
        out alongside them. The record keeps only the resolved id, since whether a ratio was
        named is how the id was arrived at rather than anything about the photo.
        """
        width, height = crop_rules.resolved_size(
            item.width, item.height, item.source_id, self.crop, self.crop_overrides
        )
        return mattes.choose(
            width, height, by_ratio=self.matte_by_ratio, fallback=self.fallback_matte
        )

    def for_item(self, item: SourceItem) -> RenderRecord:
        """The record one photo should be carrying, given what config asks for now.

        The crop is resolved first and the matte chosen from the shape it leaves, because that
        is the shape the panel is handed: a rule can reshape a photo, and the TV draws different
        matte types around different shapes. Three of them need a 16:9 and crash Art Mode on
        anything else, so getting this order wrong is a power cycle rather than a wrong-looking
        mat.

        Everything is derived from the source item rather than from the prepared image. The
        diff has only the item, so anything read off the prepared bytes would be a second
        answer to the same question, and a photo the two disagreed about would be replaced on
        every run forever.
        """
        crop = crop_rules.resolve(
            item.width, item.height, item.source_id, self.crop, self.crop_overrides
        )

        return RenderRecord(
            pipeline_version=self.pipeline_version,
            # The resolved id rather than the ratio it was found under, so two configs that
            # arrive at one matte by different routes replace nothing.
            matte_id=self.matte_choice(item).matte_id,
            # The rule's effect rather than the rule, so that rewriting a `when` clause to
            # catch the same photo by a different name replaces nothing. An uncropped photo
            # records a fixed pair, because an anchor decides nothing when nothing is cut.
            crop=crop.rule.to if crop.crops else crop_rules.NO_CROP,
            crop_anchor=crop.rule.anchor if crop.crops else crop_rules.CENTER,
            labelled=self.labelled,
            highlight_rolloff=self.highlight_rolloff,
            jpeg_quality=self.jpeg_quality,
        )


def from_stored(value: Any) -> RenderRecord | None:
    """Read a record out of an inventory entry, `None` meaning the entry predates them.

    A missing record and a malformed one are different: the first is every entry written before
    this existed and reads as "unknown", while the second is a file that says something its
    author didn't mean. The first is a photo to re-upload and the second is an error, because
    the alternative is guessing at a value that decides whether an image is destroyed.
    """
    if value is None:
        return None

    if not isinstance(value, dict):
        raise RenderError("a render record has to be a table")

    missing = [field for field in _FIELDS if field not in value]
    if missing:
        raise RenderError(f"a render record is missing {', '.join(missing)}")

    version, matte_id = value["pipeline_version"], value["matte_id"]
    rolloff, quality = value["highlight_rolloff"], value["jpeg_quality"]
    crop, anchor, labelled = value["crop"], value["crop_anchor"], value["labelled"]

    if not isinstance(labelled, bool):
        raise RenderError("`labelled` has to be `true` or `false`")
    if not _is_whole(version) or not _is_whole(quality):
        raise RenderError("`pipeline_version` and `jpeg_quality` have to be whole numbers")
    for name, text in (("matte_id", matte_id), ("crop", crop), ("crop_anchor", anchor)):
        if not isinstance(text, str) or not text.strip():
            raise RenderError(f"`{name}` has to be a non-empty string")
    if isinstance(rolloff, bool) or not isinstance(rolloff, (int, float)):
        raise RenderError("`highlight_rolloff` has to be a number")

    return RenderRecord(
        pipeline_version=int(version),
        matte_id=matte_id,
        crop=crop,
        crop_anchor=anchor,
        labelled=labelled,
        highlight_rolloff=float(rolloff),
        jpeg_quality=int(quality),
    )


def describe_change(stored: RenderRecord | None, wanted: RenderRecord) -> str:
    """Why a photo is being re-uploaded, short enough to sit at the end of a dry run's line.

    This is read once, before a run that can re-upload the whole album, so it says which
    settings moved rather than only that something did.
    """
    if stored is None:
        return "no record"

    changes = [
        f"{field.replace('_', ' ')} {getattr(stored, field)} to {getattr(wanted, field)}"
        for field in _FIELDS
        if getattr(stored, field) != getattr(wanted, field)
    ]

    return ", ".join(changes) if changes else "no change"


def _is_whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
