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
from typing import Any, Protocol

from . import mattes
from .config import BakeoffConfig, Config
from .inventory import Inventory
from .sources import SourceItem
from .sync import tv_content_ids, unmanaged_uploads
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
    """One upload of the photo, and the name burned into it.

    `label` is the half of the matte id that varies, so a color round burns `polar` and a type
    round burns `modernwide`. The whole id would be mostly the half that is held constant, and
    the name has to be read off the wall.
    """

    number: int
    matte_id: str
    label: str


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


def newest_of(items: list[SourceItem], orientation: str) -> SourceItem | None:
    """The most recently taken photo of one orientation, or nothing if the album holds none.

    Newest is `taken_at_ms` with `source_id` breaking a tie, which is the rule `short_run`
    already sorts by, so two rounds over an unchanged album compare the same photo.
    """
    want_portrait = orientation == mattes.PORTRAIT
    candidates = [item for item in items if item.is_portrait == want_portrait]
    if not candidates:
        return None

    return max(candidates, key=lambda item: (item.taken_at_ms, item.source_id))


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


def variants(
    compare: str,
    orientation: str,
    *,
    color: str | None,
    color_order: list[str],
    allowed: BakeoffConfig,
) -> list[Variant]:
    """The uploads a round is made of, numbered from one in the order they go up.

    Every matte named here is checked against what the TV's picker offers for the image's
    shape, because the API accepts combinations the picker withholds and one of them crashes
    Art Mode. That check is the reason a round can name a type at all.
    """
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
        names = offered_types(orientation, allowed)
        matte_ids = [
            name if name == mattes.BARE_TYPE else f"{name}_{color}" for name in names
        ]
    else:
        raise ValueError(f"`{compare}` is not something a bakeoff compares.")

    if not names:
        raise mattes.MatteError(_why_the_round_is_empty(compare, orientation, allowed))

    for matte_id in matte_ids:
        mattes.validate(matte_id, orientation)

    return [
        Variant(number=number, matte_id=matte_id, label=name)
        for number, (matte_id, name) in enumerate(zip(matte_ids, names), start=1)
    ]


def offered_types(orientation: str, allowed: BakeoffConfig) -> list[str]:
    """The types a round of this orientation covers, in the order they go up.

    What the TV's picker offers narrows the config's list rather than the other way round, so
    `modernwide` can sit in `bakeoff.types` for the sake of the landscape rounds and simply not
    appear in a portrait one.
    """
    return [name for name in mattes.offered_for(orientation) if _permitted(name, allowed.types)]


def candidate_labels(compare: str, orientation: str, allowed: BakeoffConfig) -> list[str]:
    """Every name a round of this shape could burn into an image.

    It is an upper bound for a color round, since the TV's own list narrows it further, and
    exact for a type round. What it is for is rendering: a label carries the name of the thing
    that varies and nothing about the matte id, so the images can all be drawn before the TV is
    asked anything, and rendering sixteen of them inside an open channel would spend seconds of
    a window that closes after twenty five.
    """
    if compare == COMPARE_COLORS:
        return [name for name in sorted(mattes.COLORS) if _permitted(name, allowed.colors)]
    if compare == COMPARE_TYPES:
        return offered_types(orientation, allowed)

    raise ValueError(f"`{compare}` is not something a bakeoff compares.")


def _permitted(name: str, allowed: tuple[str, ...] | None) -> bool:
    """`None` is every name, which is what a missing config key means."""
    return allowed is None or name in allowed


def _why_the_round_is_empty(compare: str, orientation: str, allowed: BakeoffConfig) -> str:
    if compare == COMPARE_COLORS:
        named = ", ".join(allowed.colors or ())
        return (
            f"`bakeoff.colors` names {named}, and this TV reported none of them, so the round "
            "has nothing to put on the wall. `frame mattes` prints what it does report."
        )

    named = ", ".join(allowed.types or ())
    return (
        f"`bakeoff.types` names {named}, and the TV's picker offers none of those for a "
        f"{orientation}. It offers {', '.join(mattes.offered_for(orientation))}."
    )


def plan_clear(available: list[dict[str, Any]], inventory: Inventory) -> ClearPlan:
    """Everything on the TV that somebody uploaded, whether or not the inventory claims it.

    This is wider than any sync, and deliberately so: a round has to start from an empty picker
    or there is nothing to compare left and right against. What it will not reach is Samsung's
    own art, in any of the forms that arrives in.
    """
    on_tv = tv_content_ids(available)
    known = {entry.content_id for entry in inventory}

    return ClearPlan(
        mine=sorted(known & on_tv),
        unmanaged=sorted(unmanaged_uploads(available, known) & on_tv),
        stale=sorted(known - on_tv),
    )


def carry_out(
    clear: ClearPlan,
    uploads: list[tuple[Variant, bytes]],
    *,
    tv: ArtTv,
    inventory: Inventory,
    config: Config,
    source_id: str,
    width: int,
    height: int,
    announce: Announce = lambda message: None,
) -> BakeoffReport:
    """Empty the TV, then put the variants up, saving the inventory as it goes.

    The images arrive already prepared and labelled, because the art channel closes itself
    after about 25 seconds of silence and rendering one between two uploads would eventually
    outlast that.
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
            source_id=source_id,
            width=width,
            height=height,
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
    uploads: list[tuple[Variant, bytes]],
    report: BakeoffReport,
    *,
    tv: ArtTv,
    inventory: Inventory,
    config: Config,
    source_id: str,
    width: int,
    height: int,
    announce: Announce,
) -> None:
    for index, (variant, data) in enumerate(uploads, start=1):
        announce(
            f"Uploading {index}/{len(uploads)}  {variant.matte_id}  {width}x{height}  "
            f"{len(data) / 1024:.0f} KB"
        )

        started = time.monotonic()
        report.in_flight = variant.matte_id
        try:
            # `date` is deliberately left alone. It is the only text an upload carries, so it
            # looked like a way to have the TV's own screens name a variant, and it isn't: the
            # firmware parses it, and a string that isn't a date becomes the epoch, which the
            # picker then shows as 1970 in place of a real date. The name goes in the pixels.
            content_id = tv.upload(data, matte_id=variant.matte_id, width=width, height=height)
        except TvRefused as error:
            report.in_flight = None
            report.failures.append(f"{variant.matte_id}: the TV refused it: {error}")
            announce(f"  refused after {time.monotonic() - started:.1f}s")
            continue

        report.in_flight = None
        report.upload_seconds.append(time.monotonic() - started)
        announce(f"  {content_id} in {report.upload_seconds[-1]:.1f}s")

        # The matte is part of the source id rather than a field of its own, because a round is
        # the same photo many times over and the matte is what tells two entries apart.
        inventory.record(content_id, SOURCE_NAME, f"{source_id}#{variant.matte_id}")
        inventory.save(config.inventory_file)
        report.uploaded.append((variant, content_id))

        if config.tv.upload_pause:
            time.sleep(config.tv.upload_pause)


def _luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = rgb
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue
