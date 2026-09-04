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
from .sources import SourceItem

# The Art Store's live stream image is reported this way, under an id that changes as the stream
# rotates, so leaving it in would show a phantom add and a phantom delete on every run.
ART_STORE_CONTENT_TYPE = "server"

# Art Store images carry this prefix whatever their `content_type`, and an upload never does:
# the TV names its own `MY_F0001`. Deliberately not used to filter `available()`, because the
# inventory is the only thing that decides what belongs to this tool. It is for the narrower
# question of whether a TV holds anything a lost inventory would strand.
ART_STORE_ID_PREFIX = "SAM-"


@dataclass(frozen=True)
class SyncPlan:
    """What a sync would do, in album order.

    `orphaned` is the case that is easy to forget: an entry whose image is no longer on the TV
    was deleted through the TV's own UI, so the entry is dropped rather than acted on. If its
    photo is still in the album it also turns up in `upload`, which is how a hand-deleted photo
    comes back. `unmanaged` is everything on the TV the inventory doesn't claim, listed only so
    a dry run can say out loud what it is leaving alone.
    """

    upload: list[SourceItem]
    delete: list[InventoryEntry]
    keep: list[InventoryEntry]
    orphaned: list[InventoryEntry]
    unmanaged: list[str]

    @property
    def is_empty(self) -> bool:
        return not (self.upload or self.delete or self.orphaned)


def plan_sync(
    source: str,
    items: list[SourceItem],
    inventory: Inventory,
    available: list[dict[str, Any]],
) -> SyncPlan:
    """Work out what to upload and what to delete for one source.

    Deletes are scoped to entries the inventory attributes to `source`, so art added by hand or
    uploaded by a different source is never a candidate however familiar its id looks.
    """
    on_tv = tv_content_ids(available)

    # An item can only have one entry. A second one means an earlier run was interrupted between
    # the upload and the write, so the older entry stands and the extra image is cleaned up.
    mine: dict[str, InventoryEntry] = {}
    duplicates: list[InventoryEntry] = []
    for entry in inventory.for_source(source):
        if entry.source_id in mine:
            duplicates.append(entry)
        else:
            mine[entry.source_id] = entry

    upload: list[SourceItem] = []
    keep: list[InventoryEntry] = []
    orphaned: list[InventoryEntry] = []
    delete: list[InventoryEntry] = []

    for item in items:
        entry = mine.pop(item.source_id, None)
        if entry is None:
            upload.append(item)
        elif entry.content_id in on_tv:
            keep.append(entry)
        else:
            orphaned.append(entry)
            upload.append(item)

    # Whatever is left is an entry whose photo has left the album, plus any duplicate.
    for entry in [*mine.values(), *duplicates]:
        if entry.content_id in on_tv:
            delete.append(entry)
        else:
            orphaned.append(entry)

    known = {entry.content_id for entry in inventory}
    return SyncPlan(
        upload=upload,
        delete=delete,
        keep=keep,
        orphaned=orphaned,
        unmanaged=sorted(on_tv - known),
    )


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
