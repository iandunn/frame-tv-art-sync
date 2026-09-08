"""Puts one photo on the wall once per matte, so a mat can be chosen by looking at it.

A matte is set at upload time and nowhere else -- `change_matte()` is refused on every image --
so comparing sixteen mat colors means sixteen uploads of the same photo. That is what this is:
hold the photo still, vary one thing, and burn that thing's name into the middle of the image,
because the TV's picker shows thumbnails and no names and nothing else on the panel says which
copy is which.

A round starts by emptying the TV, which is the part that makes the comparison worth anything:
whatever is left over from a sync would sit in the picker between the variants and turn a
left-and-right comparison into a hunt. That delete is wider than a sync's, since it reaches
uploads no inventory claims, and it is the only thing here that touches an image this tool
didn't put up. Samsung's own art is never a candidate, which `sync.unmanaged_uploads()`
enforces by admitting an id only when every row `available()` returns for it says it was
uploaded from a phone.

The planning half is pure and the carrying-out half is not, the same split `sync.py` and
`syncer.py` have and for the same reason: the rules that decide what gets deleted are the ones
worth testing without hardware attached.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Protocol

from . import mattes
from .config import BakeoffConfig, Config
from .inventory import Inventory
from .pipeline import PreparedImage
from .sources import SourceItem
from .delete import plan_all_uploads
from .sync import tv_content_ids
from .tv import MatteColor, TvError, TvRefused

# What the inventory attributes a bakeoff's uploads to. It is deliberately not the photo's own
# source: a sync scopes its deletes to the entries its source owns, so naming these separately
# is what stops a sync from reading sixteen deliberately-mismatched mattes as sixteen photos to
# put right.
SOURCE_NAME = "bakeoff"

# What `--compare` takes. A matte id is a type and a color joined, so each of these names the
# half of one that varies while the other is held still.
COMPARE_COLORS = "colors"
COMPARE_TYPES = "types"

# The type held still while the colors vary. A color can't be judged without some type drawing
# it, and this is the one to hold: it is offered for both orientations, and its aperture takes
# the image's own shape, so nothing is cropped underneath the color being looked at.
COLOR_ROUND_TYPE = "flexible"


@dataclass(frozen=True)
class Variant:
    """One upload, the photo it is of, and the text burned into it.

    `label` is the half of the matte id that varies plus the shape it is being drawn around, so
    a color round burns `polar   4:3` and a type round burns `modernwide   16:9`. The constant
    half of the id is left out because it would be most of the text; the shape is in because a
    round covers every shape the album holds and two copies of the same matte on two different
    shapes are otherwise indistinguishable on the wall.

    `source_id` travels with the variant rather than beside it for the same reason: one round
    puts several different photos up, one per shape. The image's size does not, because it
    belongs to the bytes rather than to the plan, and the bytes are prepared after this.
    """

    number: int
    matte_id: str
    label: str
    shape: str
    source_id: str


@dataclass(frozen=True)
class ClearPlan:
    """What emptying the TV would take down, split by what each id is.

    `mine` and `unmanaged` are separated because they are different promises. One is this tool
    taking down what it put up, and the other is it taking down an upload it can't account for,
    which is the only reason this needs confirming at all. `stale` is an inventory entry whose
    image the TV has already stopped listing, so it is dropped rather than deleted.
    """

    mine: list[str]
    unmanaged: list[str]
    stale: list[str]

    @property
    def delete(self) -> list[str]:
        return [*self.mine, *self.unmanaged]

    @property
    def is_empty(self) -> bool:
        return not (self.delete or self.stale)


@dataclass
class BakeoffReport:
    """What a round did. `uploaded` is the roster, and it is the output that matters."""

    deleted: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    uploaded: list[tuple[Variant, str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    upload_seconds: list[float] = field(default_factory=list)
    in_flight: str | None = None


class BakeoffAborted(Exception):
    """The channel failed partway through, and `report` is what the round got done first.

    Deliberately not a `TvError`, so it travels past the handler that turns one of those into a
    bare error message. The roster is the whole output of a round, and a round that died on
    variant twelve has still put eleven on the wall with no other record of which is which.
    """

    def __init__(self, report: BakeoffReport, cause: Exception) -> None:
        super().__init__(str(cause))
        self.report = report
        self.cause = cause


class ArtTv(Protocol):
    """The three things a round asks of the TV, so the tests can hand it a fake."""

    def available(self) -> list[dict[str, Any]]: ...

    def upload(
        self,
        data: bytes,
        *,
        matte_id: str,
        width: int,
        height: int,
        file_type: str = ...,
        date: str | None = ...,
    ) -> str: ...

    def delete(self, content_id: str) -> None: ...


Announce = Callable[[str], None]


def newest_per_shape(
    items: list[SourceItem], *, orientation: str | None = None
) -> list[SourceItem]:
    """The most recently taken photo of each distinct shape, widest first.

    A round covers every shape rather than one photo, because the shape is what decides which
    types the TV will draw: three of the six are refused on anything but a 16:9, so a round run
    on a 4:3 says nothing about the 16:9 in the same album, and a mat chosen on one may be
    unavailable on the other.

    Newest is `taken_at_ms` with `source_id` breaking a tie, which is the rule `short_run`
    already sorts by, so two rounds over an unchanged album compare the same photos. Shapes come
    back widest first so the landscapes sit together and the portraits after them.
    """
    newest: dict[Fraction, SourceItem] = {}
    for item in items:
        if orientation and mattes.orientation_of(item.width, item.height) != orientation:
            continue

        ratio = mattes.ratio_of(item.width, item.height)
        standing = newest.get(ratio)
        if standing is None or _recency(item) > _recency(standing):
            newest[ratio] = item

    return [newest[ratio] for ratio in sorted(newest, reverse=True)]


def _recency(item: SourceItem) -> tuple[int, str]:
    return (item.taken_at_ms, item.source_id)


def by_luminance(colors: list[MatteColor]) -> list[str]:
    """The mat colors this tool knows, lightest first, out of what the TV reported.

    Ordering them by how light they are rather than alphabetically puts near neighbours next to
    each other on the wall, which is the comparison that is actually hard: `polar` against
    `antique` says something, and `polar` against `navy` says nothing anybody needed a test for.

    A color the TV reports that `mattes.py` has no record of is left out rather than sent, since
    nothing here can say it is safe. `frame mattes` is what reports one of those.
    """
    known = [color for color in colors if color.name in mattes.COLORS]
    return [
        color.name
        for color in sorted(known, key=lambda color: (-_luminance(color.rgb), color.name))
    ]


def plan_round(
    compare: str,
    photos: list[SourceItem],
    *,
    color: str | None,
    color_order: list[str],
    allowed: BakeoffConfig,
) -> list[Variant]:
    """Every upload a round is made of, over every shape, numbered from one in upload order.

    A shape whose types the config has narrowed to nothing is refused rather than skipped, so a
    `bakeoff.types` naming only fixed-aperture types can't quietly turn a whole-album round into
    a round of the one 16:9 photo.
    """
    numbered: list[Variant] = []
    for photo in photos:
        for matte_id, name in _round_for(compare, photo, color=color, color_order=color_order,
                                         allowed=allowed):
            shape = mattes.shape_of(photo.width, photo.height)
            numbered.append(
                Variant(
                    number=len(numbered) + 1,
                    matte_id=matte_id,
                    # Three spaces rather than one, because this is read off a photograph of a
                    # 32" panel and the two halves have to stay apart at that distance.
                    label=f"{name}   {shape}",
                    shape=shape,
                    source_id=photo.source_id,
                )
            )

    return numbered


def _round_for(
    compare: str,
    photo: SourceItem,
    *,
    color: str | None,
    color_order: list[str],
    allowed: BakeoffConfig,
) -> list[tuple[str, str]]:
    """The matte ids and varying names one photo contributes, checked before any of them go out.

    Every matte named here is checked against what the TV will draw around this photo's shape,
    because the API accepts a matte it can't draw and then crashes Art Mode. That check is the
    reason a round can name a type at all.
    """
    ratio = mattes.ratio_of(photo.width, photo.height)

    if compare == COMPARE_COLORS:
        names = [name for name in color_order if _permitted(name, allowed.colors)]
        matte_ids = [f"{COLOR_ROUND_TYPE}_{name}" for name in names]
    elif compare == COMPARE_TYPES:
        if not color:
            raise mattes.MatteError(
                "Comparing types needs a color to draw them in, since a type can only be "
                "judged with the mat colored some particular way. Pass the one the color "
                "round settled on."
            )
        names = offered_types(ratio, allowed)
        matte_ids = [
            name if name == mattes.BARE_TYPE else f"{name}_{color}" for name in names
        ]
    else:
        raise ValueError(f"`{compare}` is not something a bakeoff compares.")

    if not names:
        raise mattes.MatteError(_why_the_round_is_empty(compare, ratio, allowed))

    for matte_id in matte_ids:
        mattes.validate_for_ratio(matte_id, ratio)

    return list(zip(matte_ids, names))


def offered_types(ratio: Fraction, allowed: BakeoffConfig) -> list[str]:
    """The types a round over this shape covers, in the order they go up.

    What the TV will draw narrows the config's list rather than the other way round, so
    `modernwide` can sit in `bakeoff.types` for the sake of a 16:9 and simply not appear in a
    4:3 round.
    """
    return [name for name in mattes.offered_for(ratio) if _permitted(name, allowed.types)]


def candidate_labels(compare: str, photo: SourceItem, allowed: BakeoffConfig) -> list[str]:
    """Every text a round could burn into this photo.

    What it is for is rendering. A label carries the varying name and the photo's shape and
    nothing about the matte id, so every image can be drawn before the TV is asked anything: the
    art channel closes itself after about 25 seconds of silence, and rendering two dozen of them
    inside it would spend most of that window.

    It is an upper bound for a color round, since the TV's own reported color list narrows it
    further, and exact for a type round.
    """
    shape = mattes.shape_of(photo.width, photo.height)

    if compare == COMPARE_COLORS:
        names = [name for name in sorted(mattes.COLORS) if _permitted(name, allowed.colors)]
    elif compare == COMPARE_TYPES:
        names = offered_types(mattes.ratio_of(photo.width, photo.height), allowed)
    else:
        raise ValueError(f"`{compare}` is not something a bakeoff compares.")

    return [f"{name}   {shape}" for name in names]


def _permitted(name: str, allowed: tuple[str, ...] | None) -> bool:
    """`None` is every name, which is what a missing config key means."""
    return allowed is None or name in allowed


def _why_the_round_is_empty(compare: str, ratio: Fraction, allowed: BakeoffConfig) -> str:
    if compare == COMPARE_COLORS:
        named = ", ".join(allowed.colors or ())
        return (
            f"`bakeoff.colors` names {named}, and this TV reported none of them, so the round "
            "has nothing to put on the wall. `frame mattes` prints what it does report."
        )

    shape = mattes.ratio_name(ratio)
    named = ", ".join(allowed.types or ())
    return (
        f"`bakeoff.types` names {named}, and the TV draws none of those around a {shape} "
        f"image, which is the shape of one of the album's photos. It draws "
        f"{', '.join(mattes.offered_for(ratio))}. Narrow the round to one orientation, or widen "
        "`bakeoff.types`."
    )


def plan_clear(available: list[dict[str, Any]], inventory: Inventory) -> ClearPlan:
    """Everything on the TV that somebody uploaded, whether or not the inventory claims it.

    This is wider than any sync, and deliberately so: a round has to start from an empty picker
    or there is nothing to compare left and right against. What it will not reach is Samsung's
    own art, in any of the forms that arrives in.
    """
    uploads = plan_all_uploads(available, inventory)
    known = {entry.content_id for entry in inventory}

    return ClearPlan(
        mine=uploads.mine,
        unmanaged=uploads.unmanaged,
        stale=sorted(known - tv_content_ids(available)),
    )


def carry_out(
    clear: ClearPlan,
    uploads: list[tuple[Variant, PreparedImage]],
    *,
    tv: ArtTv,
    inventory: Inventory,
    config: Config,
    announce: Announce = lambda message: None,
) -> BakeoffReport:
    """Empty the TV, then put the variants up, saving the inventory as it goes.

    The images arrive already prepared and labelled, because the art channel closes itself
    after about 25 seconds of silence and rendering one between two uploads would eventually
    outlast that. Each upload carries its own prepared image, since a round covers every shape
    the album holds rather than one photo many times over.
    """
    report = BakeoffReport()

    try:
        _clear(clear, report, tv=tv, inventory=inventory, announce=announce)
        inventory.save(config.inventory_file)

        _upload(
            uploads,
            report,
            tv=tv,
            inventory=inventory,
            config=config,
            announce=announce,
        )
    except TvError as error:
        inventory.save(config.inventory_file)
        raise BakeoffAborted(report, error) from None

    inventory.save(config.inventory_file)
    return report


def _clear(
    clear: ClearPlan,
    report: BakeoffReport,
    *,
    tv: ArtTv,
    inventory: Inventory,
    announce: Announce,
) -> None:
    """Delete every image, then confirm the lot with one re-read of `available()`.

    `delete()` returns a bool that proves nothing, and this is the one place a mistake destroys
    photos, so an entry is dropped only once the TV has stopped listing its image.
    """
    for content_id in clear.stale:
        inventory.drop(content_id)
        report.dropped.append(content_id)

    if not clear.delete:
        return

    refused: set[str] = set()
    for index, content_id in enumerate(clear.delete, start=1):
        announce(f"Deleting {index}/{len(clear.delete)}  {content_id}")
        try:
            tv.delete(content_id)
        except TvRefused as error:
            refused.add(content_id)
            report.failures.append(f"{content_id}: the TV refused the delete: {error}")

    still_there = tv_content_ids(tv.available())
    for content_id in clear.delete:
        if content_id in refused:
            continue

        if content_id in still_there:
            report.unconfirmed.append(content_id)
            continue

        inventory.drop(content_id)
        report.deleted.append(content_id)


def _upload(
    uploads: list[tuple[Variant, PreparedImage]],
    report: BakeoffReport,
    *,
    tv: ArtTv,
    inventory: Inventory,
    config: Config,
    announce: Announce,
) -> None:
    for index, (variant, image) in enumerate(uploads, start=1):
        announce(
            f"Uploading {index}/{len(uploads)}  {variant.matte_id}  {variant.shape}  "
            f"{image.width}x{image.height}  {len(image.data) / 1024:.0f} KB"
        )

        started = time.monotonic()
        report.in_flight = variant.matte_id
        try:
            # `date` is deliberately left alone. It is the only text an upload carries, so it
            # looked like a way to have the TV's own screens name a variant, and it isn't: the
            # firmware parses it, and a string that isn't a date becomes the epoch, which the
            # picker then shows as 1970 in place of a real date. The name goes in the pixels.
            content_id = tv.upload(
                image.data, matte_id=variant.matte_id, width=image.width, height=image.height
            )
        except TvRefused as error:
            report.in_flight = None
            report.failures.append(f"{variant.matte_id}: the TV refused it: {error}")
            announce(f"  refused after {time.monotonic() - started:.1f}s")
            continue

        report.in_flight = None
        report.upload_seconds.append(time.monotonic() - started)
        announce(f"  {content_id} in {report.upload_seconds[-1]:.1f}s")

        # The matte is part of the source id rather than a field of its own, because a round
        # puts one photo up several times over and the matte is what tells two entries apart.
        inventory.record(content_id, SOURCE_NAME, f"{variant.source_id}#{variant.matte_id}")
        inventory.save(config.inventory_file)
        report.uploaded.append((variant, content_id))

        if config.tv.upload_pause:
            time.sleep(config.tv.upload_pause)


def _luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = rgb
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue
