"""Covers the three-way diff, which is the one place a mistake deletes photos."""

from __future__ import annotations

from frame_tv_art_sync.inventory import Inventory
from frame_tv_art_sync.sources import SourceItem
from frame_tv_art_sync.sync import newest_per_orientation, plan_sync

ALBUM = "google_album"


def album_item(source_id, width=4032, height=3024, taken_at_ms=1680452105564):
    return SourceItem(
        source_id=source_id,
        url=f"https://lh3.googleusercontent.com/{source_id}=w1920-h1080",
        width=width,
        height=height,
        taken_at_ms=taken_at_ms,
    )


def tv_row(content_id, category_id="MY-C0002", content_type="mobile", slideshow="false"):
    """One row as `art.available()` reports it, which is per category rather than per image."""
    return {
        "content_id": content_id,
        "category_id": category_id,
        "slideshow": slideshow,
        "matte_id": "NONE",
        "portrait_matte_id": "NONE",
        "width": 1920,
        "height": 1080,
        "image_date": "",
        "content_type": content_type,
    }


def inventory_of(*entries):
    inventory = Inventory()
    for content_id, source, source_id in entries:
        inventory.record(content_id, source, source_id)
    return inventory


def test_an_album_item_with_no_entry_is_an_upload():
    plan = plan_sync(ALBUM, [album_item("AF1QipA")], Inventory(), [])

    assert [item.source_id for item in plan.upload] == ["AF1QipA"]
    assert plan.delete == []


def test_an_item_already_on_the_tv_is_left_alone():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")])

    assert plan.upload == []
    assert plan.delete == []
    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]


def test_an_entry_whose_item_left_the_album_is_a_delete():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [], inventory, [tv_row("MY_F0001")])

    assert [entry.content_id for entry in plan.delete] == ["MY_F0001"]
    assert plan.orphaned == []


def test_an_entry_gone_from_the_tv_is_orphaned_rather_than_deleted():
    """Somebody deleted it through the TV's own UI, so there is nothing left to delete."""
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [], inventory, [])

    assert [entry.content_id for entry in plan.orphaned] == ["MY_F0001"]
    assert plan.delete == []


def test_an_orphaned_entry_still_in_the_album_is_uploaded_again():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [])

    assert [item.source_id for item in plan.upload] == ["AF1QipA"]
    assert [entry.content_id for entry in plan.orphaned] == ["MY_F0001"]
    assert plan.delete == []


def test_art_added_by_hand_is_reported_but_never_touched():
    plan = plan_sync(ALBUM, [], Inventory(), [tv_row("MY_F0009")])

    assert plan.delete == []
    assert plan.unmanaged == ["MY_F0009"]


def test_an_entry_from_another_source_is_never_deleted():
    inventory = inventory_of(("MY_F0002", "local_folder", "beach.jpg"))

    plan = plan_sync(ALBUM, [], inventory, [tv_row("MY_F0002")])

    assert plan.delete == []
    assert plan.orphaned == []
    assert plan.keep == []


def test_another_sources_entry_does_not_stand_in_for_this_album():
    """The source ids happen to collide, and the entry still belongs to the other source."""
    inventory = inventory_of(("MY_F0002", "local_folder", "AF1QipA"))

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0002")])

    assert [item.source_id for item in plan.upload] == ["AF1QipA"]


def test_repeated_rows_for_one_image_are_deduped():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))
    available = [
        tv_row("MY_F0001", category_id="MY-C0002"),
        tv_row("MY_F0001", category_id="MY-C0004"),
    ]

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, available)

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]
    assert plan.unmanaged == []


def test_the_art_store_stream_is_ignored():
    """Its id changes as the stream rotates, so counting it would show a phantom add every run."""
    available = [tv_row("SAM-S10003488", category_id="MY-C0008", content_type="server")]

    plan = plan_sync(ALBUM, [], Inventory(), available)

    assert plan.unmanaged == []
    assert plan.delete == []


def test_an_empty_inventory_produces_no_deletes():
    available = [tv_row("MY_F0001"), tv_row("MY_F0002")]

    plan = plan_sync(ALBUM, [], Inventory(), available)

    assert plan.delete == []


def test_a_duplicate_entry_for_one_item_is_cleaned_up():
    """Two uploads of one photo can only come from an interrupted run, and the older one wins."""
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"), ("MY_F0002", ALBUM, "AF1QipA"))
    available = [tv_row("MY_F0001"), tv_row("MY_F0002")]

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, available)

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]
    assert [entry.content_id for entry in plan.delete] == ["MY_F0002"]
    assert plan.upload == []


def test_the_plan_reports_whether_it_would_change_anything():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")])

    assert plan.is_empty is True


def test_a_short_run_takes_the_newest_of_each_orientation():
    landscapes = [album_item(f"L{index}", taken_at_ms=index) for index in range(5)]
    portraits = [album_item(f"P{index}", 3024, 4032, taken_at_ms=index) for index in range(5)]

    chosen = newest_per_orientation([*landscapes, *portraits], 2)

    assert [item.source_id for item in chosen] == ["P4", "L4", "P3", "L3"]


def test_a_short_run_takes_what_it_can_when_one_orientation_is_short():
    items = [album_item("L1", taken_at_ms=1), album_item("P1", 3024, 4032, taken_at_ms=2)]

    assert {item.source_id for item in newest_per_orientation(items, 3)} == {"L1", "P1"}


def test_a_short_run_of_zero_narrows_nothing():
    items = [album_item("L1"), album_item("P1", 3024, 4032)]

    assert newest_per_orientation(items, 0) == items


def test_a_short_run_breaks_a_tie_the_same_way_every_time():
    """Two photos taken in the same millisecond can't reorder between runs and re-upload."""
    items = [album_item("Lb", taken_at_ms=7), album_item("La", taken_at_ms=7)]

    assert [item.source_id for item in newest_per_orientation(items, 1)] == ["Lb"]


def test_a_departed_photo_is_kept_when_the_mirror_is_off():
    """Appending rather than mirroring, which is `sync.delete_removed_from_album` turned off."""
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(
        ALBUM, [], inventory, [tv_row("MY_F0001")], delete_removed_from_album=False
    )

    assert plan.delete == []
    assert [entry.content_id for entry in plan.left_in_place] == ["MY_F0001"]
    assert plan.is_empty is True


def test_a_departed_photo_already_gone_from_the_tv_is_still_orphaned_with_the_mirror_off():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [], inventory, [], delete_removed_from_album=False)

    assert plan.left_in_place == []
    assert [entry.content_id for entry in plan.orphaned] == ["MY_F0001"]


def test_a_duplicate_is_cleaned_up_even_with_the_mirror_off():
    """A duplicate is one photo twice over, so no flag may keep it and strand a copy forever."""
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"), ("MY_F0002", ALBUM, "AF1QipA"))
    available = [tv_row("MY_F0001"), tv_row("MY_F0002")]

    plan = plan_sync(
        ALBUM, [album_item("AF1QipA")], inventory, available, delete_removed_from_album=False
    )

    assert [entry.content_id for entry in plan.delete] == ["MY_F0002"]
    assert plan.left_in_place == []


def test_art_added_by_hand_is_deleted_only_when_the_flag_says_so():
    available = [tv_row("MY_F0001"), tv_row("MY_F0009")]
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(
        ALBUM, [album_item("AF1QipA")], inventory, available, delete_added_by_hand=True
    )

    assert plan.delete_unmanaged == ["MY_F0009"]
    assert plan.unmanaged == []
    assert plan.is_empty is False


def test_samsungs_own_art_is_never_deleted_however_the_flags_are_set():
    """The bundled art the TV falls back on, and the Store stream, are not anybody's to delete."""
    available = [
        tv_row("SAM-F0222", content_type="preinstall"),
        tv_row("SAM-S10004103", category_id="MY-C0008", content_type="server"),
        tv_row("MY_F0009"),
    ]

    plan = plan_sync(ALBUM, [], Inventory(), available, delete_added_by_hand=True)

    assert plan.delete_unmanaged == ["MY_F0009"]
    assert plan.unmanaged == ["SAM-F0222"]


def test_an_image_reporting_two_content_types_is_protected():
    """`available()` repeats an image per category, and disagreeing rows are not worth guessing."""
    available = [tv_row("MY_F0009"), tv_row("MY_F0009", "MY-C0009", content_type="preinstall")]

    plan = plan_sync(ALBUM, [], Inventory(), available, delete_added_by_hand=True)

    assert plan.delete_unmanaged == []
    assert plan.unmanaged == ["MY_F0009"]


def test_an_image_reporting_no_content_type_is_protected():
    row = tv_row("MY_F0009")
    del row["content_type"]

    plan = plan_sync(ALBUM, [], Inventory(), [row], delete_added_by_hand=True)

    assert plan.delete_unmanaged == []
    assert plan.unmanaged == ["MY_F0009"]


def test_another_sources_upload_is_not_art_added_by_hand():
    """The inventory claims it, so it belongs to that source's own sync rather than to this one."""
    inventory = inventory_of(("MY_F0009", "local_folder", "beach.jpg"))

    plan = plan_sync(ALBUM, [], inventory, [tv_row("MY_F0009")], delete_added_by_hand=True)

    assert plan.delete_unmanaged == []
    assert plan.delete == []
    assert plan.unmanaged == []
