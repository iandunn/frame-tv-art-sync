"""Carries out a `SyncPlan` against the TV, which is the half `sync.py` deliberately leaves out.

Photos are downloaded and put through the pipeline *before* the art channel is opened, into a
spool directory the caller owns. That ordering is the whole point of this module. The channel
closes itself after about 25 seconds of silence and nothing here can reopen it, so an
interleaved loop would put a live HTTP fetch in every gap between two uploads and one slow
response would kill the run partway through. Prefetching also means every download and decode
failure surfaces while the TV is still untouched.

Failures are per photo where they can be: an image that can't be fetched, decoded, or that the
TV refuses is named and skipped, and the run carries on. Anything that reaches the channel
itself -- a timeout, an unreachable TV -- is left to propagate, because after one of those
nothing else would succeed either.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import mattes
from .composite import Group, arrange
from .config import Config
from .inventory import Inventory, InventoryEntry
from .crop import LABEL_ID_CHARS, resolved_size
from .crop import resolve as resolve_crop
from .pipeline import PreparedImage, compose, label_center, prepare
from .render import RenderRecord, RenderSettings
from .sources import SourceItem
from .sync import ART_STORE_ID_PREFIX, SyncPlan, tv_content_ids
from .tv import TvError, TvRefused

# Nothing holds the art channel while the spool is being filled, so this only has to be long
# enough for a slow response.
FETCH_TIMEOUT_SECONDS = 30.0

# The channel is open for this one, and it closes itself after about 25 seconds of silence, so
# the fetch and the decode after it want to stay well inside that. It applies only to the rare
# re-upload of a photo deleted from the TV by hand, which can't be spooled up front because
# nothing knows it is missing until `available()` has been read. It bounds each read rather
# than the whole response, so a slow enough drip can still outlast the channel; that comes out
# as a loud failed run rather than as anything silent, which is why it's left as it is.
LIVE_FETCH_TIMEOUT_SECONDS = 15.0

# A whole album goes out as one burst of a couple of hundred requests on a first run, which is
# the only time this is likely to matter. These are the statuses worth waiting on: the rest
# describe the request rather than the moment, so a retry would fail the same way.
FETCH_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

Fetcher = Callable[[str, float], bytes]
Announce = Callable[[str], None]


class ImageUnusable(Exception):
    """One photo could not be fetched or turned into a JPEG. The rest of the run is unaffected."""


class SyncAborted(Exception):
    """The channel failed partway through, and `report` is what the run got done first.

    Deliberately not a `TvError`, so that it travels past the handler that turns one of those
    into a bare error message. What the run managed before it died is the more useful half of
    a failure, and losing it is how a partial run becomes a mystery.
    """

    def __init__(self, report: SyncReport, cause: Exception) -> None:
        super().__init__(str(cause))
        self.report = report
        self.cause = cause


@dataclass(frozen=True)
class SpooledImage:
    """A prepared JPEG waiting on disk, with the size `prepare()` settled on.

    The dimensions travel with the path so that reading one back costs no second decode. They
    are what `upload()` is told it is sending; the matte and the render record come from the
    source item's shape instead, because that is the one the diff compares against.
    """

    path: Path
    width: int
    height: int


class ArtTv(Protocol):
    """The three things a sync asks of the TV, so the tests can hand it a fake."""

    def available(self) -> list[dict[str, Any]]: ...

    def upload(
        self, data: bytes, *, matte_id: str, width: int, height: int, file_type: str = ...
    ) -> str: ...

    def delete(self, content_id: str) -> None: ...


@dataclass
class SyncReport:
    """What a run actually did, as opposed to what the plan proposed.

    `unconfirmed` is a delete the TV still lists afterward. Those entries stay in the inventory
    on purpose, so the next run tries again; dropping one would leave its image on the TV
    attributed to nobody, which `sync.delete_added_by_hand` is the only thing that can then
    reach and which is off unless it was asked for.

    `deleted_unmanaged` is kept apart from `deleted` because the two are different promises. One
    is this tool taking down what it put up, and the other is it taking down somebody else's
    upload because the config said it may, which is worth reading as its own number.

    `superseded` is separate for the same reason. Those images went down because a replacement
    for each one went up first, so counting them among the deletes would read as photos leaving
    the wall when the wall is unchanged.

    `fell_back` counts the uploads whose shape `[art.matte_by_ratio]` didn't name, keyed by the
    ratio, so a run can say which key is worth adding. Without it a hundred photos can go up in
    a mat nobody chose and nothing anywhere says so.
    """

    uploaded: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    deleted_unmanaged: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    kept: int = 0

    # Ratio name to how many uploads took the fallback matte because nothing named that shape.
    fell_back: Counter[str] = field(default_factory=Counter)

    # How long each upload took, in the order they happened. It is the one measurement that
    # says whether the TV degrades under a long run: a series that climbs toward the deadline
    # is the Art app wearing down, while a flat series ending in one hang is a single event.
    upload_seconds: list[float] = field(default_factory=list)
    planned_uploads: int = 0
    planned_deletes: int = 0
    planned_unmanaged_deletes: int = 0
    planned_supersedes: int = 0

    # The photo whose upload was in flight when the channel died, if one was. It matters
    # because the bytes go out over a socket of their own and only the confirmation comes back
    # on the channel, so an upload can land on the TV and still time out. The image is then on
    # the wall with no inventory entry, indistinguishable from art added by hand, so the only
    # thing that will ever delete it is a run with `sync.delete_added_by_hand` turned on.
    in_flight: str | None = None


def fetch_image(url: str, timeout: float = FETCH_TIMEOUT_SECONDS) -> bytes:
    """Download one photo, as the source's URL already asks for it at the size wanted.

    A whole album is fetched in one burst on a first run, so a transient refusal is retried
    with a growing wait rather than costing that photo the run. A refusal that isn't transient
    -- a 404, a 403 -- is raised on the first try, because waiting cannot help it.
    """
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE_STATUSES or attempt == FETCH_ATTEMPTS:
                raise ImageUnusable(f"the server returned HTTP {error.code}") from None
            _wait(error.headers.get("Retry-After"), attempt)
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            if attempt == FETCH_ATTEMPTS:
                raise ImageUnusable(f"it could not be downloaded: {error}") from None
            _wait(None, attempt)

    raise ImageUnusable("it could not be downloaded")


def _wait(retry_after: str | None, attempt: int) -> None:
    """Sleep before the next try, honoring `Retry-After` when the server sent a usable one."""
    delay = FETCH_BACKOFF_SECONDS * 2 ** (attempt - 1)

    if retry_after:
        try:
            delay = max(delay, min(float(retry_after), MAX_BACKOFF_SECONDS))
        except ValueError:
            # It may be an HTTP date rather than seconds, which isn't worth parsing for this.
            pass

    time.sleep(min(delay, MAX_BACKOFF_SECONDS))


def provisional_uploads(
    source: str, groups: list[Group], inventory: Inventory, render: RenderSettings
) -> list[SourceItem]:
    """The photos an upload is foreseeable for, which is what can be spooled up front.

    Two kinds of group qualify. One has no inventory entry, so it has plainly never been
    uploaded. The other has an entry recording a different rendering from the one config now
    asks for, because that image is going up again under the new settings -- and on the first
    run after a change to any recorded setting that is every image on the TV.

    It is still one short of the real upload list, which isn't known until `available()` has
    been read: a group still in the album whose image was deleted from the TV by hand has an
    entry that matches and so isn't here, but has to be uploaded again. Those are fetched live
    instead, which is affordable because there are rarely any.

    Photos come back rather than groups, because the spool holds one prepared JPEG per photo.
    Compositing happens later, with the channel already open, and costs no network.
    """
    recorded: dict[tuple[str, ...], list[RenderRecord | None]] = {}
    for entry in inventory.for_source(source):
        recorded.setdefault(entry.source_ids, []).append(entry.render)

    pending: list[SourceItem] = []
    for group in groups:
        records = recorded.get(group.source_ids)

        # Any mismatch is enough, rather than every one. Two entries for a group means a run was
        # interrupted mid-replace, and spooling a photo that turns out not to need it costs one
        # download while missing one puts that download inside the open channel.
        if records is None or any(record != render.for_group(group) for record in records):
            pending.extend(group.items)

    return pending


def lost_inventory_ids(inventory: Inventory, available: list[dict[str, Any]]) -> list[str]:
    """What a missing inventory would put beyond reach, which is everything already on the TV.

    An inventory that is merely absent looks exactly like one that owns nothing, so without
    this check a lost file reads as a first run: every photo uploads a second time and the
    original copies become unmanaged, reachable only by a run that has been told it may delete
    what it didn't upload. The Art Store's own images are excluded because they are never
    uploads, which is what lets a genuine first run against a TV showing the Store proceed
    untroubled.
    """
    if inventory.existed:
        return []

    return sorted(
        content_id
        for content_id in tv_content_ids(available)
        if not content_id.startswith(ART_STORE_ID_PREFIX)
    )


def prefetch(
    items: list[SourceItem],
    spool: Path,
    *,
    config: Config,
    fetch: Fetcher | None = None,
    announce: Announce = lambda message: None,
    label_crop: bool = False,
) -> tuple[dict[str, SpooledImage], dict[str, str]]:
    """Fill the spool with prepared JPEGs, returning them and the failures, both by source id.

    The failures are returned rather than raised so that `run()` can carry them, and they are
    keyed so it can tell a photo that failed here from one it has no copy of because nothing
    knew it needed re-uploading. The first must not be reached for again with the channel open,
    which is the whole reason the spool is filled before it opens.
    """
    fetch = fetch or fetch_image
    spooled: dict[str, SpooledImage] = {}
    failures: dict[str, str] = {}

    for index, item in enumerate(items, start=1):
        announce(f"Preparing {index}/{len(items)}  {item.source_id}")
        try:
            prepared = _prepare_one(item, config, fetch, FETCH_TIMEOUT_SECONDS, label_crop)
        except ImageUnusable as error:
            failures[item.source_id] = str(error)
            continue

        path = spool / f"{item.source_id}.jpg"
        try:
            path.write_bytes(prepared.data)
        except OSError as error:
            failures[item.source_id] = f"it could not be spooled: {error}"
            continue

        spooled[item.source_id] = SpooledImage(
            path=path, width=prepared.width, height=prepared.height
        )

    return spooled, failures


def run(
    plan: SyncPlan,
    *,
    source: str,
    tv: ArtTv,
    inventory: Inventory,
    config: Config,
    render: RenderSettings,
    spooled: dict[str, SpooledImage],
    failed: dict[str, str] | None = None,
    fetch: Fetcher | None = None,
    announce: Announce = lambda message: None,
    label_crop: bool = False,
) -> SyncReport:
    """Upload, then delete, saving the inventory as it goes.

    `failed` is what `prefetch` couldn't prepare, so those photos are reported without being
    tried again. Uploads come before deletes because the album is a couple of hundred megabytes
    against the six the TV has, so there is no reason to empty the wall before filling it.

    That ordering is what makes a re-render safe as well as economical. The replacement is on
    the TV before the copy it supersedes comes down, so the photo is never absent from the wall,
    and a run that dies in between leaves a duplicate rather than a hole.
    """
    fetch = fetch or fetch_image
    report = SyncReport(
        kept=len(plan.keep),
        planned_uploads=len(plan.upload),
        planned_deletes=len(plan.delete),
        planned_unmanaged_deletes=len(plan.delete_unmanaged),
        planned_supersedes=len(plan.superseded),
    )

    # Keyed by `content_id` rather than by source id, because a run interrupted between an
    # upload and the write can leave two entries for one photo and both may be orphaned.
    stale = {entry.content_id: entry for entry in plan.orphaned}

    # Which photos got a new copy onto the TV, so that a superseded one is only taken down once
    # its replacement is really up there.
    uploaded_source_ids: set[str] = set()

    try:
        _upload_all(
            plan,
            report,
            stale,
            uploaded_source_ids,
            source=source,
            tv=tv,
            inventory=inventory,
            config=config,
            render=render,
            spooled=spooled,
            failed=failed or {},
            fetch=fetch,
            announce=announce,
            label_crop=label_crop,
        )
        _delete_all(
            plan, report, uploaded_source_ids, tv=tv, inventory=inventory, announce=announce
        )

        # Every entry still here points at an image the TV was already not listing, so it
        # describes nothing whether or not a replacement went up.
        for entry in stale.values():
            inventory.drop(entry.content_id)
            report.dropped.append(entry.content_id)
    except TvError as error:
        inventory.save(config.inventory_file)
        raise SyncAborted(report, error) from None

    inventory.save(config.inventory_file)
    return report


def _upload_all(
    plan: SyncPlan,
    report: SyncReport,
    stale: dict[str, InventoryEntry],
    uploaded_source_ids: set[str],
    *,
    source: str,
    tv: ArtTv,
    inventory: Inventory,
    config: Config,
    render: RenderSettings,
    spooled: dict[str, SpooledImage],
    failed: dict[str, str],
    fetch: Fetcher,
    announce: Announce,
    label_crop: bool = False,
) -> None:
    for index, group in enumerate(plan.upload, start=1):
        name = "+".join(source_id[:12] for source_id in group.source_ids)
        label = f"{index}/{len(plan.upload)}  {name}"

        # Trying again here would put the download it just failed inside the open channel,
        # which is the one thing filling the spool first exists to prevent.
        broken = next((one for one in group.source_ids if one in failed), None)
        if broken is not None:
            announce(f"Skipping  {label}  {failed[broken]}")
            report.failures.append(f"{name}: {failed[broken]}")
            continue

        try:
            prepared = _load_group(group, spooled, config, render, fetch, label_crop)
        except ImageUnusable as error:
            announce(f"Skipping  {label}  {error}")
            report.failures.append(f"{name}: {error}")
            continue

        # The record and the matte come from one call, so what goes to the TV and what goes
        # into the inventory can't describe two different renderings.
        #
        # Everything is derived from the source items rather than from the prepared image,
        # because the diff has only the items and the two have to agree. They can differ:
        # bounding a 2999x3000 portrait to the panel gives a square 1080x1080. That flips the
        # orientation, which is why nothing here reads one; both still snap to the same ratio,
        # so the matte is unaffected either way.
        record = render.for_group(group)

        # The same pure call `for_group` made, asked again for the half the record deliberately
        # doesn't keep: whether the config named this shape or the fallback caught it. Only a
        # group going up whole reads the table at all, since a composite carries its own mat.
        choice = render.matte_choice(group.items[0]) if group.is_full else None
        if choice is not None and choice.fell_back:
            report.fell_back[mattes.ratio_name(choice.ratio)] += 1

        # Everything about the image goes out before the call rather than after it, because a
        # request that never answers is exactly the one whose details are wanted.
        shape = mattes.ratio_name(choice.ratio) if choice is not None else record.layout
        announce(
            f"Uploading {label}  {prepared.width}x{prepared.height}  "
            f"{shape}  {record.matte_id}"
            f"{'  (fallback)' if choice is not None and choice.fell_back else ''}  "
            f"{len(prepared.data) / 1024:.0f} KB"
        )

        started = time.monotonic()
        report.in_flight = name
        try:
            content_id = tv.upload(
                prepared.data,
                matte_id=record.matte_id,
                width=prepared.width,
                height=prepared.height,
            )
        except TvRefused as error:
            report.in_flight = None
            report.failures.append(f"{name}: the TV refused it: {error}")
            announce(f"  refused after {time.monotonic() - started:.1f}s")
            continue
        except mattes.MatteError as error:
            # `upload()` checks the matte against the shape it is actually sending, which is
            # the prepared one rather than the one the record was built from. Config is
            # validated at load time, so reaching this means a source reported a shape its
            # bytes don't have, and that costs one photo rather than the run.
            report.in_flight = None
            report.failures.append(f"{name}: {error}")
            continue

        report.in_flight = None
        elapsed = time.monotonic() - started
        report.upload_seconds.append(elapsed)
        announce(f"  {content_id} in {elapsed:.1f}s")

        # The drop and the record land in one save. Written separately, a run interrupted
        # between them leaves the two entries for one group that the diff then has to heal.
        for dead in [entry for entry in stale.values() if entry.source_ids == group.source_ids]:
            del stale[dead.content_id]
            inventory.drop(dead.content_id)
            report.dropped.append(dead.content_id)

        inventory.record(content_id, source, group.source_ids, render=record)
        inventory.save(config.inventory_file)
        report.uploaded.append(content_id)
        uploaded_source_ids.update(group.source_ids)

        # Off by default. It exists because a run of 93 uploads back to back left the Art app
        # unable to answer and needing a reboot, and giving the TV a moment between them is
        # the cheapest thing that might prevent it. Whether it does is unmeasured.
        if config.tv.upload_pause:
            time.sleep(config.tv.upload_pause)


def _delete_all(
    plan: SyncPlan,
    report: SyncReport,
    uploaded_source_ids: set[str],
    *,
    tv: ArtTv,
    inventory: Inventory,
    announce: Announce,
) -> None:
    """Delete every planned image, then confirm the lot with one re-read of `available()`.

    `delete()` returns a bool that proves nothing, and this is the one place a mistake destroys
    photos, so nothing is recorded as gone until the TV has stopped listing it.

    The three classes differ in what they leave behind and are otherwise identical. An entry
    from `plan.delete` or `plan.superseded` is dropped from the inventory, while an image from
    `plan.delete_unmanaged` has no entry to drop -- being unclaimed is what put it there. All
    of them are confirmed by the same single re-read, which is why they are deleted together
    rather than in three passes.

    An entry holding a photo this run was uploading is only taken down once `uploaded_source_ids`
    says every one of its photos really landed. Deleting one whose upload failed would take a
    photo off the wall entirely to make room for a copy that doesn't exist, and the failure has
    already been reported by the half that failed. That covers a superseded copy and an image
    regrouped around a photo that arrived alike; an entry whose photos have simply left the album
    is not waiting on anything, so nothing holds it back.
    """
    pending = {source_id for group in plan.upload for source_id in group.source_ids}

    def replaced(entry: InventoryEntry) -> bool:
        at_stake = pending.intersection(entry.source_ids)
        return not at_stake or at_stake <= uploaded_source_ids

    superseded: list[InventoryEntry] = []
    for entry in plan.superseded:
        if replaced(entry):
            superseded.append(entry)
        else:
            announce(f"Keeping   {entry.content_id}, since its replacement did not upload")

    removed: list[InventoryEntry] = []
    for entry in plan.delete:
        if replaced(entry):
            removed.append(entry)
        else:
            announce(f"Keeping   {entry.content_id}, since its replacement did not upload")

    entries = {entry.content_id: entry for entry in [*removed, *superseded]}
    stood_in_for = {entry.content_id for entry in superseded}
    doomed = [*entries, *plan.delete_unmanaged]
    if not doomed:
        return

    refused: set[str] = set()
    for index, content_id in enumerate(doomed, start=1):
        if content_id in stood_in_for:
            whose = "  superseded"
        elif content_id in entries:
            whose = ""
        else:
            whose = "  not this tool's"
        announce(f"Deleting {index}/{len(doomed)}  {content_id}{whose}")
        try:
            tv.delete(content_id)
        except TvRefused as error:
            refused.add(content_id)
            report.failures.append(f"{content_id}: the TV refused the delete: {error}")

    still_there = tv_content_ids(tv.available())
    for content_id in doomed:
        # A refusal has already been reported, and the image being listed is what it means
        # rather than a second thing that went wrong.
        if content_id in refused:
            continue

        if content_id in still_there:
            report.unconfirmed.append(content_id)
            continue

        if content_id in stood_in_for:
            inventory.drop(content_id)
            report.superseded.append(content_id)
        elif content_id in entries:
            inventory.drop(content_id)
            report.deleted.append(content_id)
        else:
            report.deleted_unmanaged.append(content_id)


def planned_shape(item: SourceItem, config: Config) -> tuple[int, int]:
    """The dimensions the TV will be handed, predicted from what the source reported.

    Predicted rather than measured, because a dry run has to answer without downloading a
    photo and `sync.py` never has anything but the source's own numbers. It is the crop's
    output rather than the source's shape, since a crop is what decides what reaches the
    panel: a 4:3 cropped to 16:9 wants a 16:9's matte, and a 16:9 cropped to anything else
    must not be handed a fixed aperture, which is a dialog on the panel and a power cycle.

    Everything that needs this calls it here rather than working it out again. Two paths
    agreeing today is not the same as agreeing after the next edit, and a disagreement between
    the diff and the upload is silent.
    """
    return resolved_size(
        item.width,
        item.height,
        item.source_id,
        config.pipeline.crop,
        config.pipeline.crop_overrides,
    )


def _load_group(
    group: Group,
    spooled: dict[str, SpooledImage],
    config: Config,
    render: RenderSettings,
    fetch: Fetcher,
    label_crop: bool = False,
) -> PreparedImage:
    """The single image one group goes up as, composited if it holds more than one photo.

    A group of one under `full` is the photo itself, which is what every photo did before
    composites existed. Anything else is laid onto a painted mat here, with the channel already
    open, because compositing costs no network and the prints it needs are already spooled.
    """
    prepared = [_load(item, spooled, config, fetch, label_crop) for item in group.items]
    if group.is_full:
        return prepared[0]

    style = config.pipeline.composite_style
    width, height = render.resolved_size(group.items[0])
    cells = arrange(width / height, group, style)

    return compose(
        [one.data for one in prepared],
        [(cell.left, cell.top, cell.width, cell.height) for cell in cells],
        mat=style.rgb,
        edge=style.edge,
        tooth=style.tooth,
        quality=config.pipeline.jpeg_quality,
    )


def _load(
    item: SourceItem,
    spooled: dict[str, SpooledImage],
    config: Config,
    fetch: Fetcher,
    label_crop: bool = False,
) -> PreparedImage:
    """The prepared image, off the spool if it's there and off the network if it isn't."""
    ready = spooled.get(item.source_id)
    if ready is None:
        return _prepare_one(item, config, fetch, LIVE_FETCH_TIMEOUT_SECONDS, label_crop)

    try:
        data = ready.path.read_bytes()
    except OSError as error:
        raise ImageUnusable(f"its spooled copy could not be read: {error}") from None

    return PreparedImage(data=data, width=ready.width, height=ready.height)


def _prepare_one(
    item: SourceItem,
    config: Config,
    fetch: Fetcher,
    timeout: float,
    label_crop: bool = False,
) -> PreparedImage:
    """Fetch one photo and put it through the pipeline, with the crop its shape calls for.

    The crop is resolved from the source's own reported dimensions rather than the decoded
    ones, so a dry run and the run it predicts pick the same rule without downloading anything.
    """
    data = fetch(item.url, timeout)
    crop = resolve_crop(
        item.width,
        item.height,
        item.source_id,
        config.pipeline.crop,
        config.pipeline.crop_overrides,
    )

    try:
        prepared = prepare(
            data,
            crop=crop,
            highlight_rolloff=config.pipeline.highlight_rolloff,
            quality=config.pipeline.jpeg_quality,
        )
    except (OSError, ValueError) as error:
        raise ImageUnusable(f"it is not an image this tool can prepare: {error}") from None

    if not label_crop:
        return prepared

    # Drawn after the crop rather than before it, or the label would be the first thing the
    # crop threw away.
    return label_center(
        prepared.data,
        f"{crop.label}\n{item.source_id[:LABEL_ID_CHARS]}",
        quality=config.pipeline.jpeg_quality,
    )
