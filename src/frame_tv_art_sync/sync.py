"""The three-way diff between a source's items, the inventory, and what the TV holds.

Pure by design. It takes the three lists and returns a plan, so the rules that decide what gets
deleted can be covered by tests with no hardware attached, which matters because this is the one
place a mistake destroys photos. `docs/inventory-and-sync.md` states the rules and the hazards
the TV's own reporting imposes.

Nothing here talks to the TV, so a plan is a proposal: acting on it, and confirming a delete by
re-reading `available()` rather than trusting what `delete()` returns, belongs to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .inventory import Inventory, InventoryEntry
from .render import RenderRecord, RenderSettings
from .sources import SourceItem

# The Art Store's live stream image is reported this way, under an id that changes as the stream
# rotates, so leaving it in would show a phantom add and a phantom delete on every run.
ART_STORE_CONTENT_TYPE = "server"

# Art Store and bundled images carry this prefix whatever their `content_type`, and an upload
# never does: the TV names its own `MY_F0001`. It never decides whether an image belongs to
# this tool, which only the inventory answers. It answers the narrower question of whether an
# image is Samsung's own, which is what a lost inventory strands and what no flag may delete.
ART_STORE_ID_PREFIX = "SAM-"

# What `available()` calls an image that was uploaded to the TV, as against `preinstall` for
# bundled art and `server` for the Store's rotating stream. It is the same for an upload from
# here and one from a phone, so it says an image is somebody's photo rather than whose.
UPLOAD_CONTENT_TYPE = "mobile"


@dataclass(frozen=True)
class SyncPlan:
    """What a sync would do, in album order.

    `orphaned` is the case that is easy to forget: an entry whose image is no longer on the TV
    was deleted through the TV's own UI, so the entry is dropped rather than acted on. If its
    photo is still in the album it also turns up in `upload`, which is how a hand-deleted photo
    comes back.

    `superseded` is the copy a re-render replaces, and its photo is in `upload` too, because a
    matte can only be set at upload time. The two halves are one operation: the caller uploads
    first and takes the old copy down afterward, so an interrupted run leaves a duplicate that
    the next run heals rather than a wall with nothing on it.

    Two lists hold what a delete flag decided against. `left_in_place` is an entry whose photo
    has left the album that `delete_removed_from_album` is keeping, and `unmanaged` is what the
    inventory doesn't claim that `delete_added_by_hand` is keeping, Samsung's own art included.
    Both are populated whether or not their flag is on, because a dry run has to say out loud
    what it is leaving alone.

    **A list a flag does not name is acted on unconditionally.** `delete` and `delete_unmanaged`
    are the only two anything gates, and a list added here later inherits that default rather
    than a gate nobody remembered to write. That is deliberate: the flags answer whether this
    tool may stop mirroring a photo, and a list that exists for another reason -- a duplicate
    entry, a copy superseded by a re-render -- is garbage by construction rather than a
    mirroring decision.
    """

    upload: list[SourceItem]
    delete: list[InventoryEntry]
    delete_unmanaged: list[str]
    superseded: list[InventoryEntry]
    keep: list[InventoryEntry]
    orphaned: list[InventoryEntry]
    left_in_place: list[InventoryEntry]
    unmanaged: list[str]

    @property
    def is_empty(self) -> bool:
        return not (
            self.upload
            or self.delete
            or self.delete_unmanaged
            or self.superseded
            or self.orphaned
        )


def plan_sync(
    source: str,
    items: list[SourceItem],
    inventory: Inventory,
    available: list[dict[str, Any]],
    *,
    render: RenderSettings,
    delete_removed_from_album: bool = True,
    delete_added_by_hand: bool = False,
) -> SyncPlan:
    """Work out what to upload and what to delete for one source.

    Entries the inventory attributes to another source are never candidates however familiar
    their ids look, and Samsung's own art is never a candidate at all.

    `render` is what config says every photo should look like now. A photo already on the TV
    whose entry records something else is uploaded again and its old copy superseded, because
    a matte can only be set at upload time. An entry with no record at all is treated the same
    way, since nothing else says how that copy was made.

    The two flags are the config keys of the same names, and they are independent. Turning the
    first off makes a sync append rather than mirror, so a photo that leaves the album keeps
    both its entry and its image. Turning the second on lets a sync delete images the inventory
    doesn't claim, which is the only way to reach a photo added from a phone or one stranded by
    an upload that timed out after the bytes had landed.
    """
    on_tv = tv_content_ids(available)
    wanted = {item.source_id: render.for_item(item) for item in items}

    mine, duplicates = _one_entry_each(inventory.for_source(source), wanted)

    upload: list[SourceItem] = []
    keep: list[InventoryEntry] = []
    orphaned: list[InventoryEntry] = []
    delete: list[InventoryEntry] = []
    superseded: list[InventoryEntry] = []
    left_in_place: list[InventoryEntry] = []

    for item in items:
        entry = mine.pop(item.source_id, None)
        if entry is None:
            upload.append(item)
        elif entry.content_id not in on_tv:
            orphaned.append(entry)
            upload.append(item)
        elif entry.render == wanted[item.source_id]:
            keep.append(entry)
        else:
            superseded.append(entry)
            upload.append(item)

    # Whatever is left in `mine` is an entry whose photo has left the album, which is the one
    # thing the first flag decides.
    for entry in mine.values():
        if entry.content_id not in on_tv:
            orphaned.append(entry)
        elif delete_removed_from_album:
            delete.append(entry)
        else:
            left_in_place.append(entry)

    # A duplicate is the same photo twice over rather than a photo that left the album, so no
    # flag gates it. Keeping one would turn a run interrupted between an upload and the write
    # into a duplicate on the wall that heals on no later run. That holds with the mirror off
    # as much as on: a leftover is not a photo anybody chose to put there, so appending rather
    # than mirroring says nothing about it.
    for entry in duplicates:
        if entry.content_id in on_tv:
            delete.append(entry)
        else:
            orphaned.append(entry)

    known = {entry.content_id for entry in inventory}
    doomed = unmanaged_uploads(available, known) & on_tv if delete_added_by_hand else set()

    return SyncPlan(
        upload=upload,
        delete=delete,
        delete_unmanaged=sorted(doomed),
        superseded=superseded,
        keep=keep,
        orphaned=orphaned,
        left_in_place=left_in_place,
        unmanaged=sorted(on_tv - known - doomed),
    )


def _one_entry_each(
    entries: list[InventoryEntry], wanted: dict[str, RenderRecord]
) -> tuple[dict[str, InventoryEntry], list[InventoryEntry]]:
    """Split the entries into one per photo plus the leftovers, deciding which one stands.

    A photo can only have one entry. A second one means a run was interrupted between an upload
    and the write, which a re-render makes routine rather than rare: replacing a photo mints a
    new `content_id` and the old entry only goes once its image is confirmed gone, so there are
    two for as long as that takes.

    Which one stands matters, because the loser's image is deleted. The entry whose record
    matches config wins, since that is the copy a run just made and the other is the stale one
    it was making it to replace. Preferring the older entry, which is what this did before
    records existed, would have an interrupted re-render delete the new copy and keep the old,
    and the next run would make the same replacement again forever. With nothing to choose
    between them -- neither matching, or the photo no longer in the album -- the newest upload
    stands, on the grounds that it is the one somebody most recently meant to have.
    """
    mine: dict[str, InventoryEntry] = {}
    duplicates: list[InventoryEntry] = []

    for entry in entries:
        standing = mine.get(entry.source_id)
        if standing is None:
            mine[entry.source_id] = entry
            continue

        winner, loser = _preferred(standing, entry, wanted.get(entry.source_id))
        mine[entry.source_id] = winner
        duplicates.append(loser)

    return mine, duplicates


def _preferred(
    standing: InventoryEntry, rival: InventoryEntry, wanted: RenderRecord | None
) -> tuple[InventoryEntry, InventoryEntry]:
    """The entry that stands and the one that goes, out of two for the same photo."""
    if wanted is not None and (standing.render == wanted) != (rival.render == wanted):
        return (standing, rival) if standing.render == wanted else (rival, standing)

    return (rival, standing) if rival.uploaded_at > standing.uploaded_at else (standing, rival)


def unmanaged_uploads(available: list[dict[str, Any]], known: set[str]) -> set[str]:
    """The images on the TV that no inventory entry claims and that somebody uploaded.

    This is what `delete_added_by_hand` reaches, and everything it leaves out is the reason it
    exists: Samsung's own art is never a candidate, whether it arrives as the Store's rotating
    stream, as the bundled art the TV falls back on when My Pictures empties, or under any
    `content_type` nobody here has seen.

    So an image qualifies only by saying so on every row it appears on. `available()` repeats
    an image once per category, and an id whose rows disagree, or one carrying no type at all,
    is left alone rather than guessed at -- the cost of protecting something deletable is one
    line in a dry run, and the cost of the reverse is somebody's photo.
    """
    types: dict[str, set[Any]] = {}
    for row in available:
        if not isinstance(row, dict):
            continue

        content_id = row.get("content_id")
        if not isinstance(content_id, str):
            continue

        types.setdefault(content_id, set()).add(row.get("content_type"))

    return {
        content_id
        for content_id, reported in types.items()
        if content_id not in known
        and not content_id.startswith(ART_STORE_ID_PREFIX)
        and reported == {UPLOAD_CONTENT_TYPE}
    }


def newest_per_orientation(items: list[SourceItem], count: int) -> list[SourceItem]:
    """The newest `count` landscapes and the newest `count` portraits, newest first.

    This is what `sync.short_run` narrows a sync down to. Both orientations are represented on
    purpose, because the two take different matte types and a run that happened to draw only
    landscapes would say nothing about the other half.

    Newest is decided by `taken_at_ms` rather than by the order a source listed its items in,
    with `source_id` breaking a tie so that two runs over an unchanged album pick the same
    photos. `count` of 0 means no narrowing at all.
    """
    if count <= 0:
        return list(items)

    newest = sorted(items, key=lambda item: (item.taken_at_ms, item.source_id), reverse=True)
    landscapes = [item for item in newest if not item.is_portrait][:count]
    portraits = [item for item in newest if item.is_portrait][:count]

    chosen = {item.source_id for item in landscapes + portraits}
    return [item for item in newest if item.source_id in chosen]


def tv_content_ids(available: list[dict[str, Any]]) -> set[str]:
    """The set of images the TV holds, out of rows that repeat once per category.

    `category_id` is deliberately not read. It says where the TV files an image, never who
    uploaded it, and the inventory is the only thing that answers that.
    """
    return {
        row["content_id"]
        for row in available
        if isinstance(row, dict)
        and isinstance(row.get("content_id"), str)
        and row.get("content_type") != ART_STORE_CONTENT_TYPE
    }
