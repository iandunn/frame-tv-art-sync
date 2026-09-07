"""Covers the three-way diff, which is the one place a mistake deletes photos."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from frame_tv_art_sync.crop import CropRule
from frame_tv_art_sync.inventory import Inventory
from frame_tv_art_sync.render import RenderSettings
from frame_tv_art_sync.sources import SourceItem
from frame_tv_art_sync.sync import newest_per_orientation, plan_sync

ALBUM = "google_album"

# What config would say, and what an entry has to be carrying to be left alone.
RENDER = RenderSettings(
    matte_by_ratio={
        Fraction(4, 3): "flexible_black",
        Fraction(3, 4): "shadowbox_black",
        Fraction(16, 9): "modern_black",
    },
    # Deliberately none of the three above, so a photo that lands on it can't pass by accident.
    fallback_matte="shadowbox_polar",
    highlight_rolloff=0.1,
    jpeg_quality=95,
)

CURRENT = RENDER.for_item(
    SourceItem(source_id="AF1QipA", url="https://example.test/x", width=4032, height=3024)
)


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


def inventory_of(*entries, render=CURRENT, uploaded_at=None):
    """Entries carrying the current rendering, so only what a test changes reads as stale."""
    inventory = Inventory()
    for content_id, source, source_id in entries:
        inventory.record(content_id, source, source_id, uploaded_at, render=render)
    return inventory


def test_an_album_item_with_no_entry_is_an_upload():
    plan = plan_sync(ALBUM, [album_item("AF1QipA")], Inventory(), [], render=RENDER)

    assert [item.source_id for item in plan.upload] == ["AF1QipA"]
    assert plan.delete == []


def test_an_item_already_on_the_tv_is_left_alone():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")], render=RENDER)

    assert plan.upload == []
    assert plan.delete == []
    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]


def test_an_entry_whose_item_left_the_album_is_a_delete():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [], inventory, [tv_row("MY_F0001")], render=RENDER)

    assert [entry.content_id for entry in plan.delete] == ["MY_F0001"]
    assert plan.orphaned == []


def test_an_entry_gone_from_the_tv_is_orphaned_rather_than_deleted():
    """Somebody deleted it through the TV's own UI, so there is nothing left to delete."""
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [], inventory, [], render=RENDER)

    assert [entry.content_id for entry in plan.orphaned] == ["MY_F0001"]
    assert plan.delete == []


def test_an_orphaned_entry_still_in_the_album_is_uploaded_again():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [], render=RENDER)

    assert [item.source_id for item in plan.upload] == ["AF1QipA"]
    assert [entry.content_id for entry in plan.orphaned] == ["MY_F0001"]
    assert plan.delete == []


def test_art_added_by_hand_is_reported_but_never_touched():
    plan = plan_sync(ALBUM, [], Inventory(), [tv_row("MY_F0009")], render=RENDER)

    assert plan.delete == []
    assert plan.unmanaged == ["MY_F0009"]


def test_an_entry_from_another_source_is_never_deleted():
    inventory = inventory_of(("MY_F0002", "local_folder", "beach.jpg"))

    plan = plan_sync(ALBUM, [], inventory, [tv_row("MY_F0002")], render=RENDER)

    assert plan.delete == []
    assert plan.orphaned == []
    assert plan.keep == []


def test_another_sources_entry_does_not_stand_in_for_this_album():
    """The source ids happen to collide, and the entry still belongs to the other source."""
    inventory = inventory_of(("MY_F0002", "local_folder", "AF1QipA"))

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0002")], render=RENDER)

    assert [item.source_id for item in plan.upload] == ["AF1QipA"]


def test_repeated_rows_for_one_image_are_deduped():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))
    available = [
        tv_row("MY_F0001", category_id="MY-C0002"),
        tv_row("MY_F0001", category_id="MY-C0004"),
    ]

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, available, render=RENDER)

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]
    assert plan.unmanaged == []


def test_the_art_store_stream_is_ignored():
    """Its id changes as the stream rotates, so counting it would show a phantom add every run."""
    available = [tv_row("SAM-S10003488", category_id="MY-C0008", content_type="server")]

    plan = plan_sync(ALBUM, [], Inventory(), available, render=RENDER)

    assert plan.unmanaged == []
    assert plan.delete == []


def test_an_empty_inventory_produces_no_deletes():
    available = [tv_row("MY_F0001"), tv_row("MY_F0002")]

    plan = plan_sync(ALBUM, [], Inventory(), available, render=RENDER)

    assert plan.delete == []


def test_a_duplicate_entry_for_one_item_is_cleaned_up():
    """Two uploads of one photo can only come from an interrupted run, and the older one wins."""
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"), ("MY_F0002", ALBUM, "AF1QipA"))
    available = [tv_row("MY_F0001"), tv_row("MY_F0002")]

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, available, render=RENDER)

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]
    assert [entry.content_id for entry in plan.delete] == ["MY_F0002"]
    assert plan.upload == []


def test_the_plan_reports_whether_it_would_change_anything():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")], render=RENDER)

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
        ALBUM,
        [],
        inventory,
        [tv_row("MY_F0001")],
        render=RENDER,
        delete_removed_from_album=False,
    )

    assert plan.delete == []
    assert [entry.content_id for entry in plan.left_in_place] == ["MY_F0001"]
    assert plan.is_empty is True


def test_a_departed_photo_already_gone_from_the_tv_is_still_orphaned_with_the_mirror_off():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [], inventory, [], delete_removed_from_album=False, render=RENDER)

    assert plan.left_in_place == []
    assert [entry.content_id for entry in plan.orphaned] == ["MY_F0001"]


def test_a_duplicate_is_cleaned_up_even_with_the_mirror_off():
    """A duplicate is one photo twice over, so no flag may keep it and strand a copy forever."""
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"), ("MY_F0002", ALBUM, "AF1QipA"))
    available = [tv_row("MY_F0001"), tv_row("MY_F0002")]

    plan = plan_sync(
        ALBUM,
        [album_item("AF1QipA")],
        inventory,
        available,
        render=RENDER,
        delete_removed_from_album=False,
    )

    assert [entry.content_id for entry in plan.delete] == ["MY_F0002"]
    assert plan.left_in_place == []


def test_art_added_by_hand_is_deleted_only_when_the_flag_says_so():
    available = [tv_row("MY_F0001"), tv_row("MY_F0009")]
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(
        ALBUM,
        [album_item("AF1QipA")],
        inventory,
        available,
        render=RENDER,
        delete_added_by_hand=True,
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

    plan = plan_sync(ALBUM, [], Inventory(), available, delete_added_by_hand=True, render=RENDER)

    assert plan.delete_unmanaged == ["MY_F0009"]
    assert plan.unmanaged == ["SAM-F0222"]


def test_an_image_reporting_two_content_types_is_protected():
    """`available()` repeats an image per category, and disagreeing rows are not worth guessing."""
    available = [tv_row("MY_F0009"), tv_row("MY_F0009", "MY-C0009", content_type="preinstall")]

    plan = plan_sync(ALBUM, [], Inventory(), available, delete_added_by_hand=True, render=RENDER)

    assert plan.delete_unmanaged == []
    assert plan.unmanaged == ["MY_F0009"]


def test_an_image_reporting_no_content_type_is_protected():
    row = tv_row("MY_F0009")
    del row["content_type"]

    plan = plan_sync(ALBUM, [], Inventory(), [row], delete_added_by_hand=True, render=RENDER)

    assert plan.delete_unmanaged == []
    assert plan.unmanaged == ["MY_F0009"]


def test_another_sources_upload_is_not_art_added_by_hand():
    """The inventory claims it, so it belongs to that source's own sync rather than to this one."""
    inventory = inventory_of(("MY_F0009", "local_folder", "beach.jpg"))

    plan = plan_sync(
        ALBUM, [], inventory, [tv_row("MY_F0009")], delete_added_by_hand=True, render=RENDER
    )

    assert plan.delete_unmanaged == []
    assert plan.delete == []
    assert plan.unmanaged == []


# Replacing a photo whose rendering has changed


def test_a_photo_rendered_the_way_config_asks_for_is_left_alone():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")], render=RENDER)

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]
    assert plan.superseded == []
    assert plan.upload == []


def test_a_photo_rendered_some_other_way_is_uploaded_again_and_its_copy_superseded():
    stale = RENDER.for_item(album_item("AF1QipA"))
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipA"),
        render=replace(stale, matte_id="modern_black"),
    )

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")], render=RENDER)

    assert [item.source_id for item in plan.upload] == ["AF1QipA"]
    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]
    assert plan.keep == []
    # The old copy comes down as a replacement rather than as a photo leaving the album.
    assert plan.delete == []


def test_an_entry_with_no_record_is_replaced():
    """Every entry on disk today, which is what makes the first run re-upload the album."""
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"), render=None)

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")], render=RENDER)

    assert [item.source_id for item in plan.upload] == ["AF1QipA"]
    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]


def test_a_matte_recorded_for_another_shape_is_a_mismatch():
    """A 4:3 and a 3:4 are looked up under different keys, so one's matte is stale on the other."""
    portrait = album_item("AF1QipA", width=3024, height=4032)
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipA"), render=RENDER.for_item(album_item("AF1QipA"))
    )

    plan = plan_sync(ALBUM, [portrait], inventory, [tv_row("MY_F0001")], render=RENDER)

    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]


def test_a_portrait_rendered_with_the_matte_for_its_shape_is_left_alone():
    portrait = album_item("AF1QipA", width=3024, height=4032)
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipA"), render=RENDER.for_item(portrait)
    )

    plan = plan_sync(ALBUM, [portrait], inventory, [tv_row("MY_F0001")], render=RENDER)

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]


def test_changing_the_matte_for_a_ratio_replaces_the_photos_of_that_shape():
    restyled = replace(
        RENDER, matte_by_ratio={**RENDER.matte_by_ratio, Fraction(4, 3): "shadowbox_sand"}
    )
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(
        ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")], render=restyled
    )

    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]


def test_changing_the_matte_for_a_ratio_nothing_in_the_album_has_replaces_nothing():
    """Naming a shape no photo is costs nothing, the way an unmatched crop rule costs nothing."""
    restyled = replace(
        RENDER, matte_by_ratio={**RENDER.matte_by_ratio, Fraction(1, 1): "shadowbox_sand"}
    )
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_sync(
        ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")], render=restyled
    )

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]


def test_a_shape_the_config_does_not_name_is_compared_against_the_fallback():
    square = album_item("AF1QipA", width=3024, height=3024)
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"), render=RENDER.for_item(square))

    plan = plan_sync(ALBUM, [square], inventory, [tv_row("MY_F0001")], render=RENDER)

    assert plan.keep and plan.keep[0].render.matte_id == RENDER.fallback_matte


def test_changing_the_fallback_replaces_the_photos_that_took_it():
    square = album_item("AF1QipA", width=3024, height=3024)
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"), render=RENDER.for_item(square))

    plan = plan_sync(
        ALBUM,
        [square],
        inventory,
        [tv_row("MY_F0001")],
        render=replace(RENDER, fallback_matte="none"),
    )

    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]


def test_a_replacement_happens_with_the_mirror_off_too():
    """The flags answer whether to stop mirroring a photo, and this photo is still in the album."""
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"), render=None)

    plan = plan_sync(
        ALBUM,
        [album_item("AF1QipA")],
        inventory,
        [tv_row("MY_F0001")],
        render=RENDER,
        delete_removed_from_album=False,
    )

    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]
    assert plan.left_in_place == []


def test_a_plan_holding_only_a_replacement_is_not_empty():
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"), render=None)

    plan = plan_sync(ALBUM, [album_item("AF1QipA")], inventory, [tv_row("MY_F0001")], render=RENDER)

    assert not plan.is_empty


# Which of two entries for one photo stands, which a replacement makes routine


def test_the_entry_matching_config_stands_and_the_stale_one_is_deleted():
    """A run interrupted mid-replace: the new copy is up and the old one is still there."""
    inventory = Inventory()
    inventory.record("MY_F0001", ALBUM, "AF1QipA", "2026-09-01T00:00:00Z", render=None)
    inventory.record("MY_F0002", ALBUM, "AF1QipA", "2026-09-02T00:00:00Z", render=CURRENT)

    plan = plan_sync(
        ALBUM,
        [album_item("AF1QipA")],
        inventory,
        [tv_row("MY_F0001"), tv_row("MY_F0002")],
        render=RENDER,
    )

    assert [entry.content_id for entry in plan.keep] == ["MY_F0002"]
    assert [entry.content_id for entry in plan.delete] == ["MY_F0001"]
    assert plan.upload == []


def test_the_matching_entry_stands_even_when_it_is_the_older_one():
    """Order of recording says nothing about which copy config asks for."""
    inventory = Inventory()
    inventory.record("MY_F0001", ALBUM, "AF1QipA", "2026-09-01T00:00:00Z", render=CURRENT)
    inventory.record("MY_F0002", ALBUM, "AF1QipA", "2026-09-02T00:00:00Z", render=None)

    plan = plan_sync(
        ALBUM,
        [album_item("AF1QipA")],
        inventory,
        [tv_row("MY_F0001"), tv_row("MY_F0002")],
        render=RENDER,
    )

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]
    assert [entry.content_id for entry in plan.delete] == ["MY_F0002"]


def test_with_neither_entry_matching_the_newest_upload_stands():
    inventory = Inventory()
    inventory.record("MY_F0001", ALBUM, "AF1QipA", "2026-09-01T00:00:00Z", render=None)
    inventory.record("MY_F0002", ALBUM, "AF1QipA", "2026-09-02T00:00:00Z", render=None)

    plan = plan_sync(
        ALBUM,
        [album_item("AF1QipA")],
        inventory,
        [tv_row("MY_F0001"), tv_row("MY_F0002")],
        render=RENDER,
    )

    assert [entry.content_id for entry in plan.superseded] == ["MY_F0002"]
    assert [entry.content_id for entry in plan.delete] == ["MY_F0001"]
    assert [item.source_id for item in plan.upload] == ["AF1QipA"]


# The crop, which is recorded by its effect rather than by the rule that produced it


def cropping(*rules, **overrides):
    """`RENDER` with crop rules, so a test says only what it changes."""
    return replace(RENDER, crop=rules, crop_overrides=tuple(overrides.items()))


def plan_with(render, inventory, item=None):
    item = item or album_item("AF1QipA")
    return plan_sync(ALBUM, [item], inventory, [tv_row("MY_F0001")], render=render)


def test_a_photo_a_new_crop_rule_reaches_is_replaced():
    cropped = cropping(CropRule(when="4:3", to="16:9"))
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_with(cropped, inventory)

    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]


def test_a_photo_the_rule_does_not_reach_is_left_alone():
    """A rule for a shape nothing in the album has costs nothing, which is the point of
    recording the crop rather than the rule table."""
    cropped = cropping(CropRule(when="3:4", to="16:9"))
    inventory = inventory_of(("MY_F0001", ALBUM, "AF1QipA"))

    plan = plan_with(cropped, inventory)

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]


def test_rewriting_a_rule_to_catch_the_same_photo_replaces_nothing():
    """`landscape` and `4:3` name the same crop for this photo, so the pixels don't move."""
    by_ratio = cropping(CropRule(when="4:3", to="16:9"))
    by_shape = cropping(CropRule(when="landscape", to="16:9"))
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipA"), render=by_ratio.for_item(album_item("AF1QipA"))
    )

    plan = plan_with(by_shape, inventory)

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]


def test_changing_where_a_crop_is_anchored_replaces_the_photo():
    centered = cropping(CropRule(when="4:3", to="16:9"))
    from_the_top = cropping(CropRule(when="4:3", to="16:9", anchor="top"))
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipA"), render=centered.for_item(album_item("AF1QipA"))
    )

    plan = plan_with(from_the_top, inventory)

    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]


def test_an_override_replaces_only_the_photo_it_names():
    overridden = cropping(AF1QipAAAA=CropRule(when="*", to="16:9"))
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipAAAA"), ("MY_F0002", ALBUM, "AF1QipBBBB")
    )
    items = [album_item("AF1QipAAAA"), album_item("AF1QipBBBB")]

    plan = plan_sync(
        ALBUM, items, inventory, [tv_row("MY_F0001"), tv_row("MY_F0002")], render=overridden
    )

    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]
    assert [entry.content_id for entry in plan.keep] == ["MY_F0002"]


def test_a_crop_to_16_9_is_matted_under_the_16_9_key_rather_than_the_sources():
    """The crop is resolved first and the matte chosen from the shape it leaves. Getting that
    order the other way round sends a fixed aperture to a 4:3, which crashes Art Mode."""
    cropped = cropping(CropRule(when="4:3", to="16:9"))
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipA"), render=cropped.for_item(album_item("AF1QipA"))
    )

    plan = plan_with(cropped, inventory)

    assert plan.keep and plan.keep[0].render.matte_id == RENDER.matte_by_ratio[Fraction(16, 9)]


def test_a_cropped_portrait_is_compared_against_the_matte_for_its_new_shape():
    """A crop can turn a 3:4 into a 16:9, and the config names the two separately."""
    portrait = album_item("AF1QipA", width=3024, height=4032)
    cropped = cropping(CropRule(when="3:4", to="16:9"))
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipA"), render=cropped.for_item(portrait)
    )

    plan = plan_with(cropped, inventory, portrait)

    assert plan.keep and plan.keep[0].render.matte_id == RENDER.matte_by_ratio[Fraction(16, 9)]


def test_a_labelled_copy_is_replaced_by_a_plain_run():
    """`frame sync --label` burns the crop into the pixels, so the copy really is different."""
    labelled = replace(RENDER, labelled=True)
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipA"), render=labelled.for_item(album_item("AF1QipA"))
    )

    plan = plan_with(RENDER, inventory)

    assert [entry.content_id for entry in plan.superseded] == ["MY_F0001"]


def test_a_labelled_copy_is_left_alone_by_another_labelled_run():
    labelled = replace(RENDER, labelled=True)
    inventory = inventory_of(
        ("MY_F0001", ALBUM, "AF1QipA"), render=labelled.for_item(album_item("AF1QipA"))
    )

    plan = plan_with(labelled, inventory)

    assert [entry.content_id for entry in plan.keep] == ["MY_F0001"]
