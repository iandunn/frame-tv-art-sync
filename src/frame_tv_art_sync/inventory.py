"""Remembers which images on the TV this tool uploaded, and which source each came from.

The TV records nothing about where an image came from, so without this file there is no way
to tell this tool's uploads apart from art added by hand, and a mirror would either delete
somebody else's art or accumulate duplicates forever. `docs/inventory-and-sync.md` has the
reasoning and the shape.

An entry carries no fingerprint of the photo's content, so an edit made in the source after
upload is invisible here. It does carry a render record, which is the settings that produced
the copy on the TV, so a photo whose *rendering* has gone stale is visible even though one
whose *pixels* changed at the source is not. `render.py` has why that distinction is where it
is, and the same doc records what closing the other half would take.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .render import RenderError, RenderRecord, from_stored

INVENTORY_FILENAME = "inventory.json"

# Readers ignore fields they don't recognize, so adding one needs no bump. Removing a field or
# changing what an existing one means does.
FORMAT_VERSION = 1

_REQUIRED_FIELDS = ("source", "source_id", "uploaded_at")

# Optional, because every entry written before render records existed is missing it, and a
# reader has to be able to say so rather than refuse the file.
RENDER_FIELD = "render"


class InventoryError(Exception):
    """The inventory file is there but can't be read as one."""


@dataclass(frozen=True)
class InventoryEntry:
    """One image on the TV, and the source item it was uploaded from.

    `source` and `source_id` together are the identity. `uploaded_at` is bookkeeping, so that
    an entry matching nothing on either side is still explicable a year later.

    `render` is what that copy was made with, and `None` means the entry was written before
    records existed. Since nothing else says how a copy was produced, unknown has to read as
    stale: an entry with no record is one whose photo gets uploaded again.
    """

    content_id: str
    source: str
    source_id: str
    uploaded_at: str
    render: RenderRecord | None = None


class Inventory:
    """The `content_id` to source mapping, in memory.

    `existed` says whether this came from a file that was already there. It matters because an
    inventory that is merely absent looks exactly like one that owns nothing, and the second is
    safe to sync against while the first means the attribution was lost and every photo would be
    uploaded a second time.
    """

    def __init__(self, entries: Iterable[InventoryEntry] = (), *, existed: bool = False) -> None:
        self._entries = {entry.content_id: entry for entry in entries}
        self.existed = existed

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[InventoryEntry]:
        return iter(self._entries.values())

    def __contains__(self, content_id: str) -> bool:
        return content_id in self._entries

    def entry(self, content_id: str) -> InventoryEntry | None:
        return self._entries.get(content_id)

    def for_source(self, source: str) -> list[InventoryEntry]:
        """Every entry this source uploaded, oldest first, which is what scopes a delete."""
        return [entry for entry in self if entry.source == source]

    def record(
        self,
        content_id: str,
        source: str,
        source_id: str,
        uploaded_at: str | None = None,
        render: RenderRecord | None = None,
    ) -> InventoryEntry:
        entry = InventoryEntry(
            content_id=content_id,
            source=source,
            source_id=source_id,
            uploaded_at=uploaded_at or _now(),
            render=render,
        )
        self._entries[content_id] = entry
        return entry

    def drop(self, content_id: str) -> None:
        self._entries.pop(content_id, None)

    def save(self, path: Path) -> None:
        """Write the file, replacing it in one step.

        A half-written inventory loses the attribution for everything below the truncation
        point, which is the one failure this file can't recover from, so the write lands on a
        temporary name and is renamed over the real one.
        """
        path = Path(path)
        payload = {
            "version": FORMAT_VERSION,
            "items": {entry.content_id: _stored(entry) for entry in self},
        }
        temporary = path.with_name(f"{path.name}.tmp")

        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
            os.replace(temporary, path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise InventoryError(f"Could not write {path}: {error}") from None

        self.existed = True


def load_inventory(path: Path) -> Inventory:
    """Read the inventory, treating a missing file as empty rather than as an error."""
    path = Path(path)

    try:
        with open(path, "rb") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return Inventory(existed=False)
    except json.JSONDecodeError as error:
        raise InventoryError(
            f"{path} is not valid JSON: {error}. It maps every image this tool uploaded back to "
            "its source, so repair it rather than deleting it."
        ) from None
    except OSError as error:
        raise InventoryError(f"Could not read {path}: {error}") from None

    if not isinstance(raw, dict) or not isinstance(raw.get("items"), dict):
        raise InventoryError(f"{path} has no `items` table, so it is not an inventory file.")

    entries = [_entry(content_id, stored, path) for content_id, stored in raw["items"].items()]
    return Inventory(entries, existed=True)


def _stored(entry: InventoryEntry) -> dict[str, Any]:
    """One entry as it goes to disk. The render record is omitted where there isn't one.

    Writing it as `null` instead would be the same thing to a reader, but every entry written
    from now on has a record, so an entry without the key is one this tool has never re-uploaded
    since records existed -- which is worth being able to see in the file.
    """
    values: dict[str, Any] = {field: getattr(entry, field) for field in _REQUIRED_FIELDS}
    if entry.render is not None:
        values[RENDER_FIELD] = entry.render.as_stored()

    return values


def _entry(content_id: str, stored: Any, path: Path) -> InventoryEntry:
    if not isinstance(stored, dict):
        raise InventoryError(f"The entry for `{content_id}` in {path} is not a table.")

    values: dict[str, str] = {}
    for field in _REQUIRED_FIELDS:
        value = stored.get(field)
        if not isinstance(value, str) or not value:
            raise InventoryError(f"The entry for `{content_id}` in {path} has no `{field}`.")
        values[field] = value

    try:
        render = from_stored(stored.get(RENDER_FIELD))
    except RenderError as error:
        raise InventoryError(
            f"The entry for `{content_id}` in {path} has a `{RENDER_FIELD}` that can't be read: "
            f"{error}. It says what that image was uploaded with, and a run reads it to decide "
            "whether to replace the image, so repair it rather than deleting it."
        ) from None

    return InventoryEntry(content_id=content_id, render=render, **values)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
