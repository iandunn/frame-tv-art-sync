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

from dataclasses import dataclass
from typing import Any

from . import mattes
from .pipeline import PIPELINE_VERSION

_FIELDS = ("pipeline_version", "matte_id", "highlight_rolloff", "jpeg_quality")

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
    highlight_rolloff: float
    jpeg_quality: int

    def __post_init__(self) -> None:
        # Lower case for the same reason `tv.normalize_matte_id` exists: the TV reports a matte
        # in one case and takes it in another, so nothing anywhere should compare them raw.
        object.__setattr__(self, "matte_id", self.matte_id.strip().lower())
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

    landscape_matte: str
    portrait_matte: str
    highlight_rolloff: float
    jpeg_quality: int
    pipeline_version: int = PIPELINE_VERSION

    def for_shape(self, width: int, height: int) -> RenderRecord:
        """The record a photo of this shape should carry, the matte being the half shape decides."""
        return RenderRecord(
            pipeline_version=self.pipeline_version,
            matte_id=mattes.matte_for(width, height, self.landscape_matte, self.portrait_matte),
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

    if not _is_whole(version) or not _is_whole(quality):
        raise RenderError("`pipeline_version` and `jpeg_quality` have to be whole numbers")
    if not isinstance(matte_id, str) or not matte_id.strip():
        raise RenderError("`matte_id` has to be a non-empty string")
    if isinstance(rolloff, bool) or not isinstance(rolloff, (int, float)):
        raise RenderError("`highlight_rolloff` has to be a number")

    return RenderRecord(
        pipeline_version=int(version),
        matte_id=matte_id,
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
