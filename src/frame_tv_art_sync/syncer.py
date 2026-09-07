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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import mattes
from .config import Config
from .inventory import Inventory, InventoryEntry
from .crop import LABEL_ID_CHARS
from .crop import resolve as resolve_crop
from .pipeline import PreparedImage, label_center, prepare
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

    The dimensions travel with the path so that reading one back costs no second decode, and
    so the matte is chosen from the shape that will actually be uploaded.
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
    """

    uploaded: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    deleted_unmanaged: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    kept: int = 0

    # How long each upload took, in the order they happened. It is the one measurement that
    # says whether the TV degrades under a long run: a series that climbs toward the deadline
    # is the Art app wearing down, while a flat series ending in one hang is a single event.
    upload_seconds: list[float] = field(default_factory=list)
    planned_uploads: int = 0
    planned_deletes: int = 0
    planned_unmanaged_deletes: int = 0

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
    source: str, items: list[SourceItem], inventory: Inventory
) -> list[SourceItem]:
    """The album items with no inventory entry yet, which is what can be spooled up front.

    It is one short of the real upload list, which isn't known until `available()` has been
    read: a photo still in the album whose image was deleted from the TV by hand has an entry
    and so isn't here, but has to be uploaded again. Those are fetched live instead.
    """
    known = {entry.source_id for entry in inventory.for_source(source)}
    return [item for item in items if item.source_id not in known]


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
    """
    fetch = fetch or fetch_image
    report = SyncReport(
        kept=len(plan.keep),
        planned_uploads=len(plan.upload),
        planned_deletes=len(plan.delete),
        planned_unmanaged_deletes=len(plan.delete_unmanaged),
    )

    # Keyed by `content_id` rather than by source id, because a run interrupted between an
    # upload and the write can leave two entries for one photo and both may be orphaned.
    stale = {entry.content_id: entry for entry in plan.orphaned}

    try:
        _upload_all(
            plan,
            report,
            stale,
            source=source,
            tv=tv,
            inventory=inventory,
            config=config,
            spooled=spooled,
            failed=failed or {},
            fetch=fetch,
            announce=announce,
            label_crop=label_crop,
        )
        _delete_all(plan, report, tv=tv, inventory=inventory, announce=announce)

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
    *,
    source: str,
    tv: ArtTv,
    inventory: Inventory,
    config: Config,
    spooled: dict[str, SpooledImage],
    failed: dict[str, str],
    fetch: Fetcher,
    announce: Announce,
    label_crop: bool = False,
) -> None:
    for index, item in enumerate(plan.upload, start=1):
        label = f"{index}/{len(plan.upload)}  {item.source_id[:20]}"

        # Trying again here would put the download it just failed inside the open channel,
        # which is the one thing filling the spool first exists to prevent.
        if item.source_id in failed:
            announce(f"Skipping  {label}  {failed[item.source_id]}")
            report.failures.append(f"{item.source_id}: {failed[item.source_id]}")
            continue

        try:
            prepared = _load(item, spooled, config, fetch, label_crop)
        except ImageUnusable as error:
            announce(f"Skipping  {label}  {error}")
            report.failures.append(f"{item.source_id}: {error}")
            continue

        matte_id = mattes.matte_for(
            prepared.width,
            prepared.height,
            config.art.landscape_matte,
            config.art.portrait_matte,
        )

        # Everything about the image goes out before the call rather than after it, because a
        # request that never answers is exactly the one whose details are wanted.
        announce(
            f"Uploading {label}  {prepared.width}x{prepared.height}  {matte_id}  "
            f"{len(prepared.data) / 1024:.0f} KB"
        )

        started = time.monotonic()
        report.in_flight = item.source_id
        try:
            content_id = tv.upload(
                prepared.data,
                matte_id=matte_id,
                width=prepared.width,
                height=prepared.height,
            )
        except TvRefused as error:
            report.in_flight = None
            report.failures.append(f"{item.source_id}: the TV refused it: {error}")
            announce(f"  refused after {time.monotonic() - started:.1f}s")
            continue

        report.in_flight = None
        elapsed = time.monotonic() - started
        report.upload_seconds.append(elapsed)
        announce(f"  {content_id} in {elapsed:.1f}s")

        # The drop and the record land in one save. Written separately, a run interrupted
        # between them leaves the two entries for one photo that the diff then has to heal.
        for dead in [entry for entry in stale.values() if entry.source_id == item.source_id]:
            del stale[dead.content_id]
            inventory.drop(dead.content_id)
            report.dropped.append(dead.content_id)

        inventory.record(content_id, source, item.source_id)
        inventory.save(config.inventory_file)
        report.uploaded.append(content_id)

        # Off by default. It exists because a run of 93 uploads back to back left the Art app
        # unable to answer and needing a reboot, and giving the TV a moment between them is
        # the cheapest thing that might prevent it. Whether it does is unmeasured.
        if config.tv.upload_pause:
            time.sleep(config.tv.upload_pause)


def _delete_all(
    plan: SyncPlan,
    report: SyncReport,
    *,
    tv: ArtTv,
    inventory: Inventory,
    announce: Announce,
) -> None:
    """Delete every planned image, then confirm the lot with one re-read of `available()`.

    `delete()` returns a bool that proves nothing, and this is the one place a mistake destroys
    photos, so nothing is recorded as gone until the TV has stopped listing it.

    The two classes differ in one respect and are otherwise identical. An entry deleted from
    `plan.delete` is dropped from the inventory, while an image from `plan.delete_unmanaged` has
    no entry to drop -- being unclaimed is what put it there. Both are confirmed by the same
    single re-read, which is why they are deleted together rather than in two passes.
    """
    entries = {entry.content_id: entry for entry in plan.delete}
    doomed = [*entries, *plan.delete_unmanaged]
    if not doomed:
        return

    refused: set[str] = set()
    for index, content_id in enumerate(doomed, start=1):
        whose = "" if content_id in entries else "  not this tool's"
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

        if content_id in entries:
            inventory.drop(content_id)
            report.deleted.append(content_id)
        else:
            report.deleted_unmanaged.append(content_id)


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
