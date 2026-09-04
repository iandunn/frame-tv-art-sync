"""Covers the half of sync that writes, which is the one place a mistake destroys photos.

The TV is a fake here rather than a mock of `samsungtvws`, because what these tests are about
is the order the runner does things in: that an entry is dropped only once the TV has stopped
listing its image, that an orphan's old entry and its new one land in a single save, and that
one bad photo doesn't cost the rest of the run.
"""

from __future__ import annotations

import io

from PIL import Image

from frame_tv_art_sync.config import (
    ArtConfig,
    Config,
    GoogleAlbumConfig,
    PipelineConfig,
    TvConfig,
)
from frame_tv_art_sync.inventory import Inventory, load_inventory
from frame_tv_art_sync.sources import SourceItem
from frame_tv_art_sync.sync import plan_sync
from frame_tv_art_sync.syncer import (
    ImageUnusable,
    lost_inventory_ids,
    prefetch,
    provisional_uploads,
    run,
)
from frame_tv_art_sync.tv import TvRefused

ALBUM = "google_album"


def jpeg(width: int = 4032, height: int = 3024) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def album_item(source_id: str, width: int = 4032, height: int = 3024) -> SourceItem:
    return SourceItem(
        source_id=source_id,
        url=f"https://lh3.googleusercontent.com/{source_id}=w1920-h1080-n",
        width=width,
        height=height,
    )


def tv_row(content_id: str, content_type: str = "mobile") -> dict:
    return {
        "content_id": content_id,
        "category_id": "MY-C0002",
        "content_type": content_type,
        "width": 1920,
        "height": 1080,
    }


def config_for(tmp_path) -> Config:
    return Config(
        path=tmp_path / "config.toml",
        inventory_file=tmp_path / "inventory.json",
        tv=TvConfig(host="10.0.0.5", name="frame", token_file=tmp_path / "token.txt"),
        google_album=GoogleAlbumConfig(url="https://photos.google.com/share/a?key=b"),
        art=ArtConfig(landscape_matte="flexible_black", portrait_matte="shadowbox_black"),
        # No rolloff, so the tests spend no time on a tone curve they aren't about.
        pipeline=PipelineConfig(highlight_rolloff=0.0, jpeg_quality=80),
    )


def inventory_of(*entries) -> Inventory:
    inventory = Inventory(existed=True)
    for content_id, source_id in entries:
        inventory.record(content_id, ALBUM, source_id)
    return inventory


class FakeTv:
    """Records what it was asked to do and answers `available()` consistently with it.

    `undeletable` is the hazard the confirming re-read exists for: a delete the TV accepts and
    doesn't carry out. `delete()` gives no sign of it either way.
    """

    def __init__(
        self, rows=(), *, refuse_upload_at=(), undeletable=(), refuse_delete=()
    ) -> None:
        self.rows = [dict(row) for row in rows]
        self.refuse_upload_at = set(refuse_upload_at)
        self.undeletable = set(undeletable)
        self.refuse_delete = set(refuse_delete)
        self.uploads: list[dict] = []
        self.deletes: list[str] = []
        self.reads = 0
        self.attempts = 0

    def available(self) -> list[dict]:
        self.reads += 1
        return [dict(row) for row in self.rows]

    def upload(self, data, *, matte_id, width, height, file_type="jpg") -> str:
        self.attempts += 1
        if self.attempts in self.refuse_upload_at:
            raise TvRefused("The TV refused `upload`: error -7")

        content_id = f"MY_F{len(self.uploads) + 1:04d}"
        self.uploads.append(
            {"content_id": content_id, "matte_id": matte_id, "size": (width, height)}
        )
        self.rows.append(tv_row(content_id))
        return content_id

    def delete(self, content_id: str) -> None:
        self.deletes.append(content_id)
        if content_id in self.refuse_delete:
            raise TvRefused("The TV refused `delete`: error -7")
        if content_id in self.undeletable:
            return
        self.rows = [row for row in self.rows if row["content_id"] != content_id]


def fetcher(**responses):
    """A fetch function keyed by source id, where an exception value is raised instead."""

    def fetch(url: str, timeout: float) -> bytes:
        source_id = url.split("/")[-1].split("=")[0]
        answer = responses[source_id]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fetch


def spool_all(items, tmp_path, config, fetch):
    spool = tmp_path / "spool"
    spool.mkdir()
    return prefetch(items, spool, config=config, fetch=fetch)


# Working out what can be prepared before the channel opens


def test_an_item_already_in_the_inventory_is_not_prefetched():
    items = [album_item("AF1QipA"), album_item("AF1QipB")]

    pending = provisional_uploads(ALBUM, items, inventory_of(("MY_F0001", "AF1QipA")))

    assert [item.source_id for item in pending] == ["AF1QipB"]


def test_an_entry_from_another_source_does_not_hide_an_item():
    inventory = Inventory(existed=True)
    inventory.record("MY_F0001", "local_folder", "AF1QipA")

    pending = provisional_uploads(ALBUM, [album_item("AF1QipA")], inventory)

    assert [item.source_id for item in pending] == ["AF1QipA"]


# The lost inventory guard


def test_a_present_inventory_strands_nothing():
    assert lost_inventory_ids(inventory_of(), [tv_row("MY_F0001")]) == []


def test_a_missing_inventory_strands_what_the_tv_already_holds():
    stranded = lost_inventory_ids(Inventory(existed=False), [tv_row("MY_F0001")])

    assert stranded == ["MY_F0001"]


def test_the_art_stores_own_images_are_not_treated_as_something_to_lose():
    """Otherwise a genuine first run against a TV showing the Store would refuse to start."""
    rows = [tv_row("SAM-S0001", content_type="preinstall"), tv_row("SAM-F0002", "server")]

    assert lost_inventory_ids(Inventory(existed=False), rows) == []


# Preparing photos


def test_prefetch_spools_a_prepared_jpeg_per_item(tmp_path):
    config = config_for(tmp_path)
    items = [album_item("AF1QipA")]

    spooled, failures = spool_all(items, tmp_path, config, fetcher(AF1QipA=jpeg()))

    assert failures == {}
    assert spooled["AF1QipA"].path.exists()
    # 4032x3024 is 4:3, so the landscape crop takes it to the panel's own shape.
    assert (spooled["AF1QipA"].width, spooled["AF1QipA"].height) == (1920, 1080)


def test_a_portrait_keeps_its_shape_for_the_tv_to_mat(tmp_path):
    config = config_for(tmp_path)
    items = [album_item("AF1QipA", width=810, height=1080)]

    spooled, _ = spool_all(items, tmp_path, config, fetcher(AF1QipA=jpeg(810, 1080)))

    assert (spooled["AF1QipA"].width, spooled["AF1QipA"].height) == (810, 1080)


def test_a_photo_that_cannot_be_fetched_is_named_and_the_rest_are_prepared(tmp_path):
    config = config_for(tmp_path)
    items = [album_item("AF1QipA"), album_item("AF1QipB")]
    fetch = fetcher(AF1QipA=ImageUnusable("it could not be downloaded"), AF1QipB=jpeg())

    spooled, failures = spool_all(items, tmp_path, config, fetch)

    assert list(spooled) == ["AF1QipB"]
    assert failures == {"AF1QipA": "it could not be downloaded"}


def test_bytes_that_are_not_an_image_are_a_failure_rather_than_a_crash(tmp_path):
    config = config_for(tmp_path)

    _, failures = spool_all([album_item("AF1QipA")], tmp_path, config, fetcher(AF1QipA=b"nope"))

    assert list(failures) == ["AF1QipA"]
    assert failures["AF1QipA"].startswith("it is not an image this tool can prepare")


# Running the plan


def sync_once(tmp_path, *, items, inventory, tv, fetch=None, spooled=None):
    """A whole run, spooling first the way the command does, unless `spooled` is given."""
    config = config_for(tmp_path)
    fetch = fetch or fetcher(**{item.source_id: jpeg() for item in items})
    failed: dict[str, str] = {}

    if spooled is None:
        pending = provisional_uploads(ALBUM, items, inventory)
        spooled, failed = spool_all(pending, tmp_path, config, fetch)

    plan = plan_sync(ALBUM, items, inventory, tv.available())
    report = run(
        plan,
        source=ALBUM,
        tv=tv,
        inventory=inventory,
        config=config,
        spooled=spooled,
        failed=failed,
        fetch=fetch,
    )
    return report, load_inventory(config.inventory_file)


def test_a_new_photo_is_uploaded_and_recorded(tmp_path):
    tv = FakeTv()

    report, saved = sync_once(
        tmp_path, items=[album_item("AF1QipA")], inventory=Inventory(existed=True), tv=tv
    )

    assert report.uploaded == ["MY_F0001"]
    assert saved.entry("MY_F0001").source_id == "AF1QipA"
    assert [upload["content_id"] for upload in tv.uploads] == ["MY_F0001"]


def test_the_matte_is_chosen_by_the_shape_of_each_photo(tmp_path):
    items = [album_item("AF1QipA"), album_item("AF1QipB", width=810, height=1080)]
    fetch = fetcher(AF1QipA=jpeg(), AF1QipB=jpeg(810, 1080))
    tv = FakeTv()

    sync_once(tmp_path, items=items, inventory=Inventory(existed=True), tv=tv, fetch=fetch)

    assert [upload["matte_id"] for upload in tv.uploads] == [
        "flexible_black",
        "shadowbox_black",
    ]


def test_a_photo_the_tv_refuses_is_skipped_and_the_run_carries_on(tmp_path):
    items = [album_item("AF1QipA"), album_item("AF1QipB")]
    tv = FakeTv(refuse_upload_at=[1])

    report, saved = sync_once(
        tmp_path, items=items, inventory=Inventory(existed=True), tv=tv
    )

    assert len(report.uploaded) == 1
    assert len(report.failures) == 1
    assert report.failures[0].startswith("AF1QipA: the TV refused it")
    assert [entry.source_id for entry in saved] == ["AF1QipB"]


def test_a_photo_that_failed_to_prepare_is_not_fetched_again_with_the_channel_open(tmp_path):
    """That second attempt would be the download inside the connection the spool exists to avoid."""
    attempts: list[str] = []
    items = [album_item("AF1QipA"), album_item("AF1QipB")]

    def fetch(url: str, timeout: float) -> bytes:
        source_id = url.split("/")[-1].split("=")[0]
        attempts.append(source_id)
        if source_id == "AF1QipA":
            raise ImageUnusable("it could not be downloaded")
        return jpeg()

    report, _ = sync_once(
        tmp_path, items=items, inventory=Inventory(existed=True), tv=FakeTv(), fetch=fetch
    )

    assert attempts == ["AF1QipA", "AF1QipB"]
    assert report.failures == ["AF1QipA: it could not be downloaded"]


def test_a_delete_the_tv_refused_is_reported_once_rather_than_twice(tmp_path):
    tv = FakeTv([tv_row("MY_F0001")], refuse_delete=["MY_F0001"])

    report, saved = sync_once(
        tmp_path, items=[], inventory=inventory_of(("MY_F0001", "AF1QipA")), tv=tv
    )

    assert len(report.failures) == 1
    assert report.unconfirmed == []
    assert saved.entry("MY_F0001") is not None


def test_a_photo_that_left_the_album_is_deleted_and_its_entry_dropped(tmp_path):
    tv = FakeTv([tv_row("MY_F0001")])

    report, saved = sync_once(
        tmp_path, items=[], inventory=inventory_of(("MY_F0001", "AF1QipA")), tv=tv
    )

    assert report.deleted == ["MY_F0001"]
    assert tv.deletes == ["MY_F0001"]
    assert len(saved) == 0


def test_a_delete_the_tv_did_not_carry_out_keeps_its_entry(tmp_path):
    """Dropping it would leave the image on the TV with nothing able to delete it ever again."""
    tv = FakeTv([tv_row("MY_F0001")], undeletable=["MY_F0001"])

    report, saved = sync_once(
        tmp_path, items=[], inventory=inventory_of(("MY_F0001", "AF1QipA")), tv=tv
    )

    assert report.deleted == []
    assert report.unconfirmed == ["MY_F0001"]
    assert saved.entry("MY_F0001") is not None


def test_a_delete_is_confirmed_by_re_reading_rather_than_by_the_call(tmp_path):
    tv = FakeTv([tv_row("MY_F0001"), tv_row("MY_F0002")])

    sync_once(
        tmp_path,
        items=[],
        inventory=inventory_of(("MY_F0001", "AF1QipA"), ("MY_F0002", "AF1QipB")),
        tv=tv,
    )

    # One read to plan against, then exactly one more to confirm both deletes.
    assert tv.reads == 2


def test_art_that_is_not_this_tools_is_never_deleted(tmp_path):
    tv = FakeTv([tv_row("MY_F0009")])

    report, _ = sync_once(tmp_path, items=[], inventory=inventory_of(), tv=tv)

    assert tv.deletes == []
    assert report.deleted == []


def test_an_image_deleted_from_the_tv_by_hand_is_uploaded_again_under_one_entry(tmp_path):
    """The old entry and the new one have to land in a single save, or the next run sees two."""
    tv = FakeTv()

    report, saved = sync_once(
        tmp_path,
        items=[album_item("AF1QipA")],
        inventory=inventory_of(("MY_F0001", "AF1QipA")),
        tv=tv,
    )

    assert report.uploaded == ["MY_F0001"]
    assert report.dropped == ["MY_F0001"]
    assert [entry.source_id for entry in saved] == ["AF1QipA"]


def test_an_entry_for_a_photo_gone_from_both_sides_is_dropped_without_a_delete(tmp_path):
    tv = FakeTv()

    report, saved = sync_once(
        tmp_path, items=[], inventory=inventory_of(("MY_F0001", "AF1QipA")), tv=tv
    )

    assert tv.deletes == []
    assert report.dropped == ["MY_F0001"]
    assert len(saved) == 0


def test_an_orphan_is_fetched_live_because_nothing_could_spool_it(tmp_path):
    """It has an entry, so `provisional_uploads` passes over it and the spool has no copy."""
    tv = FakeTv()
    fetch = fetcher(AF1QipA=jpeg())

    report, _ = sync_once(
        tmp_path,
        items=[album_item("AF1QipA")],
        inventory=inventory_of(("MY_F0001", "AF1QipA")),
        tv=tv,
        fetch=fetch,
        spooled={},
    )

    assert report.uploaded == ["MY_F0001"]


def test_a_run_with_nothing_to_do_still_writes_the_inventory(tmp_path):
    """A first run against an empty album has to leave a file, or the next one refuses."""
    config = config_for(tmp_path)

    run(
        plan_sync(ALBUM, [], Inventory(existed=False), []),
        source=ALBUM,
        tv=FakeTv(),
        inventory=Inventory(existed=False),
        config=config,
        spooled={},
    )

    assert config.inventory_file.exists()


def test_uploads_happen_before_deletes(tmp_path):
    """So the wall is never emptier than it started, storage being no constraint here."""
    order: list[str] = []
    tv = FakeTv([tv_row("MY_F0001")])
    original_upload, original_delete = tv.upload, tv.delete

    def upload(*args, **kwargs):
        order.append("upload")
        return original_upload(*args, **kwargs)

    def delete(content_id):
        order.append("delete")
        return original_delete(content_id)

    tv.upload, tv.delete = upload, delete

    sync_once(
        tmp_path,
        items=[album_item("AF1QipB")],
        inventory=inventory_of(("MY_F0001", "AF1QipA")),
        tv=tv,
    )

    assert order == ["upload", "delete"]
