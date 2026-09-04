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

import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import mattes
from .config import Config
from .inventory import Inventory, InventoryEntry
from .pipeline import PreparedImage, prepare
from .sources import SourceItem
from .sync import ART_STORE_ID_PREFIX, SyncPlan, tv_content_ids
from .tv import TvRefused

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

Fetcher = Callable[[str, float], bytes]
Announce = Callable[[str], None]


class ImageUnusable(Exception):
    """One photo could not be fetched or turned into a JPEG. The rest of the run is unaffected."""


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
    on purpose, so the next run tries again; dropping one would leave its image on the TV with
    nothing attributing it to this tool, and nothing outside the inventory is ever a delete
    candidate.
    """

    uploaded: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    kept: int = 0


def fetch_image(url: str, timeout: float = FETCH_TIMEOUT_SECONDS) -> bytes:
    """Download one photo, as the source's URL already asks for it at the size wanted."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise ImageUnusable(f"the server returned HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise ImageUnusable(f"it could not be downloaded: {error}") from None


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
    original copies become unmanaged forever, since nothing outside the inventory is ever a
    delete candidate. The Art Store's own images are excluded because they are never uploads,
    which is what lets a genuine first run against a TV showing the Store proceed untroubled.
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
            prepared = _prepare_one(item, config, fetch, FETCH_TIMEOUT_SECONDS)
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
) -> SyncReport:
    """Upload, then delete, saving the inventory as it goes.

    `failed` is what `prefetch` couldn't prepare, so those photos are reported without being
    tried again. Uploads come before deletes because the album is a couple of hundred megabytes
    against the six the TV has, so there is no reason to empty the wall before filling it.
    """
    fetch = fetch or fetch_image
    report = SyncReport(kept=len(plan.keep))

    # Keyed by `content_id` rather than by source id, because a run interrupted between an
    # upload and the write can leave two entries for one photo and both may be orphaned.
    stale = {entry.content_id: entry for entry in plan.orphaned}

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
    )
    _delete_all(plan, report, tv=tv, inventory=inventory, announce=announce)

    # Every entry still here points at an image the TV was already not listing, so it describes
    # nothing whether or not a replacement went up.
    for entry in stale.values():
        inventory.drop(entry.content_id)
        report.dropped.append(entry.content_id)

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
) -> None:
    for index, item in enumerate(plan.upload, start=1):
        announce(f"Uploading {index}/{len(plan.upload)}  {item.source_id}")

        # Trying again here would put the download it just failed inside the open channel,
        # which is the one thing filling the spool first exists to prevent.
        if item.source_id in failed:
            report.failures.append(f"{item.source_id}: {failed[item.source_id]}")
            continue

        try:
            prepared = _load(item, spooled, config, fetch)
        except ImageUnusable as error:
            report.failures.append(f"{item.source_id}: {error}")
            continue

        matte_id = mattes.matte_for(
            prepared.width,
            prepared.height,
            config.art.landscape_matte,
            config.art.portrait_matte,
        )

        try:
            content_id = tv.upload(
                prepared.data,
                matte_id=matte_id,
                width=prepared.width,
                height=prepared.height,
            )
        except TvRefused as error:
            report.failures.append(f"{item.source_id}: the TV refused it: {error}")
            continue

        # The drop and the record land in one save. Written separately, a run interrupted
        # between them leaves the two entries for one photo that the diff then has to heal.
        for dead in [entry for entry in stale.values() if entry.source_id == item.source_id]:
            del stale[dead.content_id]
            inventory.drop(dead.content_id)
            report.dropped.append(dead.content_id)

        inventory.record(content_id, source, item.source_id)
        inventory.save(config.inventory_file)
        report.uploaded.append(content_id)


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
    photos, so an entry is dropped only once the TV has stopped listing its image.
    """
    if not plan.delete:
        return

    refused: set[str] = set()
    for index, entry in enumerate(plan.delete, start=1):
        announce(f"Deleting {index}/{len(plan.delete)}  {entry.content_id}")
        try:
            tv.delete(entry.content_id)
        except TvRefused as error:
            refused.add(entry.content_id)
            report.failures.append(f"{entry.content_id}: the TV refused the delete: {error}")

    still_there = tv_content_ids(tv.available())
    for entry in plan.delete:
        # A refusal has already been reported, and the image being listed is what it means
        # rather than a second thing that went wrong.
        if entry.content_id in refused:
            continue

        if entry.content_id in still_there:
            report.unconfirmed.append(entry.content_id)
            continue

        inventory.drop(entry.content_id)
        report.deleted.append(entry.content_id)


def _load(
    item: SourceItem, spooled: dict[str, SpooledImage], config: Config, fetch: Fetcher
) -> PreparedImage:
    """The prepared image, off the spool if it's there and off the network if it isn't."""
    ready = spooled.get(item.source_id)
    if ready is None:
        return _prepare_one(item, config, fetch, LIVE_FETCH_TIMEOUT_SECONDS)

    try:
        data = ready.path.read_bytes()
    except OSError as error:
        raise ImageUnusable(f"its spooled copy could not be read: {error}") from None

    return PreparedImage(data=data, width=ready.width, height=ready.height)


def _prepare_one(
    item: SourceItem, config: Config, fetch: Fetcher, timeout: float
) -> PreparedImage:
    data = fetch(item.url, timeout)

    try:
        return prepare(
            data,
            highlight_rolloff=config.pipeline.highlight_rolloff,
            quality=config.pipeline.jpeg_quality,
        )
    except (OSError, ValueError) as error:
        raise ImageUnusable(f"it is not an image this tool can prepare: {error}") from None
