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
# changing what an existing one means does. Version 2 turned `source_id` into `source_ids`,
# because one image on the TV can hold several photos, and gave the render record the fields a
# composite needs.
FORMAT_VERSION = 2

_REQUIRED_FIELDS = ("source", "uploaded_at")

# What version 1 called the single photo an image held. A file written then still reads, as a
# group of one, so an existing inventory needs no migration and no rewrite.
LEGACY_SOURCE_FIELD = "source_id"
SOURCE_IDS_FIELD = "source_ids"

# Optional, because every entry written before render records existed is missing it, and a
# reader has to be able to say so rather than refuse the file.
RENDER_FIELD = "render"


class InventoryError(Exception):
    """The inventory file is there but can't be read as one."""


@dataclass(frozen=True)
class InventoryEntry:
    """One image on the TV, and the source items it was uploaded from.

    `source` and `source_ids` together are the identity. An image can hold several photos, so
    the second is a list even where it holds one, and its order is part of the identity because
    it is part of the image. `uploaded_at` is bookkeeping, so that an entry matching nothing on
    either side is still explicable a year later.

    `render` is what that copy was made with, and `None` means it is unknown -- an entry written
    before records existed, or one written under a superseded format version whose record no
    longer describes anything comparable. Since nothing else says how a copy was produced,
    unknown has to read as stale: an entry with no record is one whose photos get uploaded again.
    """

    content_id: str
    source: str
    source_ids: tuple[str, ...]
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
        source_ids: str | Iterable[str],
        uploaded_at: str | None = None,
        render: RenderRecord | None = None,
    ) -> InventoryEntry:
        entry = InventoryEntry(
            content_id=content_id,
            source=source,
            # A bare id is taken as a group of one rather than iterated, since `tuple("AF1Qip")`
            # is six single-character ids and nothing downstream would notice.
            source_ids=(source_ids,) if isinstance(source_ids, str) else tuple(source_ids),
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

    # A version 1 render record describes one photo cropped and matted on its own, and there is
    # no shape it could take that a composite would compare against, so the whole file's records
    # read as unknown. That costs one run of re-uploads, which is what the first run after
    # composites shipped was always going to be.
    version = raw.get("version")
    superseded = not isinstance(version, int) or version < FORMAT_VERSION

    entries = [
        _entry(content_id, stored, path, superseded_render=superseded)
        for content_id, stored in raw["items"].items()
    ]
    return Inventory(entries, existed=True)


def _stored(entry: InventoryEntry) -> dict[str, Any]:
    """One entry as it goes to disk. The render record is omitted where there isn't one.

    Writing it as `null` instead would be the same thing to a reader, but every entry written
    from now on has a record, so an entry without the key is one this tool has never re-uploaded
    since records existed -- which is worth being able to see in the file.
    """
    values: dict[str, Any] = {field: getattr(entry, field) for field in _REQUIRED_FIELDS}
    values[SOURCE_IDS_FIELD] = list(entry.source_ids)
    if entry.render is not None:
        values[RENDER_FIELD] = entry.render.as_stored()

    return values


def _entry(
    content_id: str, stored: Any, path: Path, *, superseded_render: bool = False
) -> InventoryEntry:
    if not isinstance(stored, dict):
        raise InventoryError(f"The entry for `{content_id}` in {path} is not a table.")

    values: dict[str, str] = {}
    for field in _REQUIRED_FIELDS:
        value = stored.get(field)
        if not isinstance(value, str) or not value:
            raise InventoryError(f"The entry for `{content_id}` in {path} has no `{field}`.")
        values[field] = value

    source_ids = _source_ids(content_id, stored, path)

    try:
        render = None if superseded_render else from_stored(stored.get(RENDER_FIELD))
    except RenderError as error:
        raise InventoryError(
            f"The entry for `{content_id}` in {path} has a `{RENDER_FIELD}` that can't be read: "
            f"{error}. It says what that image was uploaded with, and a run reads it to decide "
            "whether to replace the image, so repair it rather than deleting it."
        ) from None

    return InventoryEntry(content_id=content_id, source_ids=source_ids, render=render, **values)


def _source_ids(content_id: str, stored: dict[str, Any], path: Path) -> tuple[str, ...]:
    """The photos one image holds, reading a version 1 entry's single id as a group of one."""
    raw = stored.get(SOURCE_IDS_FIELD)
    if raw is None:
        legacy = stored.get(LEGACY_SOURCE_FIELD)
        if isinstance(legacy, str) and legacy:
            return (legacy,)

        raise InventoryError(
            f"The entry for `{content_id}` in {path} has no `{SOURCE_IDS_FIELD}`."
        )

    if not isinstance(raw, list) or not raw or not all(isinstance(one, str) and one for one in raw):
        raise InventoryError(
            f"`{SOURCE_IDS_FIELD}` for `{content_id}` in {path} has to be a non-empty list of "
            "source ids. It says which photos that image holds, and a run reads it to decide "
            "whether to replace the image, so repair it rather than deleting it."
        )

    return tuple(raw)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
