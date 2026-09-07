"""Covers reading and writing the inventory, which is the only record of what this tool uploaded."""

from __future__ import annotations

import json

import pytest

from frame_tv_art_sync.inventory import (
    Inventory,
    InventoryError,
    load_inventory,
)
from frame_tv_art_sync.render import RenderRecord

RECORD = RenderRecord(
    pipeline_version=1,
    matte_id="flexible_black",
    crop="none",
    crop_anchor="center",
    labelled=False,
    highlight_rolloff=0.1,
    jpeg_quality=95,
)


def test_round_trips_an_entry(tmp_path):
    path = tmp_path / "inventory.json"
    inventory = Inventory()
    inventory.record("MY_F0001", "google_album", "AF1QipA", uploaded_at="2026-09-02T18:00:00Z")
    inventory.save(path)

    entry = load_inventory(path).entry("MY_F0001")

    assert entry is not None
    assert (entry.source, entry.source_id) == ("google_album", "AF1QipA")
    assert entry.uploaded_at == "2026-09-02T18:00:00Z"


def test_an_upload_is_stamped_with_the_time_by_default(tmp_path):
    inventory = Inventory()

    entry = inventory.record("MY_F0001", "google_album", "AF1QipA")

    assert entry.uploaded_at.startswith("20")


def test_a_missing_file_reads_as_an_inventory_that_never_existed(tmp_path):
    inventory = load_inventory(tmp_path / "inventory.json")

    assert inventory.existed is False
    assert len(inventory) == 0


def test_an_empty_saved_file_is_not_a_missing_one(tmp_path):
    path = tmp_path / "inventory.json"
    Inventory().save(path)

    inventory = load_inventory(path)

    assert inventory.existed is True
    assert len(inventory) == 0


def test_unparseable_json_names_the_file(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(InventoryError, match="inventory.json"):
        load_inventory(path)


def test_a_file_that_is_not_the_expected_shape_names_the_file(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text('["MY_F0001"]', encoding="utf-8")

    with pytest.raises(InventoryError, match="inventory.json"):
        load_inventory(path)


def test_an_entry_missing_a_required_field_names_the_content_id(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps({"version": 1, "items": {"MY_F0001": {"source": "google_album"}}}),
        encoding="utf-8",
    )

    with pytest.raises(InventoryError, match="MY_F0001"):
        load_inventory(path)


def test_an_unknown_field_on_an_entry_is_ignored(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "MY_F0001": {
                        "source": "google_album",
                        "source_id": "AF1QipA",
                        "uploaded_at": "2026-09-02T18:00:00Z",
                        "fingerprint": "written by a later version",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert load_inventory(path).entry("MY_F0001").source_id == "AF1QipA"


def test_for_source_ignores_other_sources():
    inventory = Inventory()
    inventory.record("MY_F0001", "google_album", "AF1QipA")
    inventory.record("MY_F0002", "local_folder", "beach.jpg")

    assert [entry.content_id for entry in inventory.for_source("google_album")] == ["MY_F0001"]


def test_entries_keep_the_order_they_were_recorded_in(tmp_path):
    path = tmp_path / "inventory.json"
    inventory = Inventory()
    inventory.record("MY_F0002", "google_album", "AF1QipB")
    inventory.record("MY_F0001", "google_album", "AF1QipA")
    inventory.save(path)

    reloaded = load_inventory(path)

    assert [entry.content_id for entry in reloaded] == ["MY_F0002", "MY_F0001"]


def test_dropping_an_entry_removes_it(tmp_path):
    path = tmp_path / "inventory.json"
    inventory = Inventory()
    inventory.record("MY_F0001", "google_album", "AF1QipA")
    inventory.drop("MY_F0001")
    inventory.save(path)

    assert len(load_inventory(path)) == 0


def test_dropping_an_unknown_entry_is_not_an_error():
    Inventory().drop("MY_F0001")


# The render record, which is what says whether the copy on the TV is still current


def test_an_entry_round_trips_its_render_record(tmp_path):
    path = tmp_path / "inventory.json"
    inventory = Inventory()
    inventory.record("MY_F0001", "google_album", "AF1QipA", render=RECORD)
    inventory.save(path)

    assert load_inventory(path).entry("MY_F0001").render == RECORD


def test_an_entry_written_before_records_existed_reads_as_unknown(tmp_path):
    """Which is every entry on disk today, and what makes the first run replace all of them."""
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "MY_F0001": {
                        "source": "google_album",
                        "source_id": "AF1QipA",
                        "uploaded_at": "2026-09-02T18:00:00Z",
                    }
                },
            }
        )
    )

    assert load_inventory(path).entry("MY_F0001").render is None


def test_an_entry_without_a_record_writes_no_render_key(tmp_path):
    path = tmp_path / "inventory.json"
    inventory = Inventory()
    inventory.record("MY_F0001", "google_album", "AF1QipA")
    inventory.save(path)

    assert "render" not in json.loads(path.read_text())["items"]["MY_F0001"]


def test_a_render_record_that_cannot_be_read_is_an_error(tmp_path):
    """Loud rather than treated as unknown, because unknown re-uploads the photo."""
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "MY_F0001": {
                        "source": "google_album",
                        "source_id": "AF1QipA",
                        "uploaded_at": "2026-09-02T18:00:00Z",
                        "render": {"matte_id": "flexible_black"},
                    }
                },
            }
        )
    )

    with pytest.raises(InventoryError, match="MY_F0001"):
        load_inventory(path)
