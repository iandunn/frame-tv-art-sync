"""Covers the half of sync that writes, which is the one place a mistake destroys photos.

The TV is a fake here rather than a mock of `samsungtvws`, because what these tests are about
is the order the runner does things in: that an entry is dropped only once the TV has stopped
listing its image, that an orphan's old entry and its new one land in a single save, and that
one bad photo doesn't cost the rest of the run.
"""

from __future__ import annotations

import io
import urllib.error
from dataclasses import replace

import pytest
from PIL import Image

from frame_tv_art_sync import syncer
from frame_tv_art_sync.crop import CropRule

from frame_tv_art_sync.config import (
    ArtConfig,
    Config,
    GoogleAlbumConfig,
    PipelineConfig,
    TvConfig,
)
from frame_tv_art_sync.inventory import Inventory, load_inventory
from frame_tv_art_sync.render import RenderSettings
from frame_tv_art_sync.sources import SourceItem
from frame_tv_art_sync.sync import plan_sync
from frame_tv_art_sync.syncer import (
    ImageUnusable,
    SyncAborted,
    lost_inventory_ids,
    prefetch,
    provisional_uploads,
    run,
)
from frame_tv_art_sync.tv import TvRefused, TvTimeout

ALBUM = "google_album"

# What config says every photo should look like, and what `config_for` is built out of, so the
# two can't drift apart and have every test replacing every photo.
RENDER = RenderSettings(
    landscape_matte="flexible_black",
    portrait_matte="shadowbox_black",
    # No rolloff, so the tests spend no time on a tone curve they aren't about.
    highlight_rolloff=0.0,
    jpeg_quality=80,
)

# The record a photo of `album_item`'s default shape should be carrying. An entry holding this
# is one a run has nothing to do about, which is what most of these tests want to start from.
CURRENT = RENDER.for_shape(4032, 3024)


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
        art=ArtConfig(
            landscape_matte=RENDER.landscape_matte, portrait_matte=RENDER.portrait_matte
        ),
        pipeline=PipelineConfig(
            highlight_rolloff=RENDER.highlight_rolloff, jpeg_quality=RENDER.jpeg_quality
        ),
    )


def inventory_of(*entries, render=CURRENT) -> Inventory:
    """Entries carrying the current rendering, since a stale one is its own kind of test."""
    inventory = Inventory(existed=True)
    for content_id, source_id in entries:
        inventory.record(content_id, ALBUM, source_id, render=render)
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

    pending = provisional_uploads(
        ALBUM, items, inventory_of(("MY_F0001", "AF1QipA")), RENDER
    )

    assert [item.source_id for item in pending] == ["AF1QipB"]


def test_an_entry_from_another_source_does_not_hide_an_item():
    inventory = Inventory(existed=True)
    inventory.record("MY_F0001", "local_folder", "AF1QipA")

    pending = provisional_uploads(ALBUM, [album_item("AF1QipA")], inventory, RENDER)

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
    # 4032x3024 is 4:3, and nothing is cropped, so it keeps that shape bounded to the panel.
    assert (spooled["AF1QipA"].width, spooled["AF1QipA"].height) == (1440, 1080)


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


def sync_once(
    tmp_path,
    *,
    items,
    inventory,
    tv,
    fetch=None,
    spooled=None,
    delete_removed_from_album=True,
    delete_added_by_hand=False,
):
    """A whole run, spooling first the way the command does, unless `spooled` is given."""
    config = config_for(tmp_path)
    fetch = fetch or fetcher(**{item.source_id: jpeg() for item in items})
    failed: dict[str, str] = {}

    if spooled is None:
        pending = provisional_uploads(ALBUM, items, inventory, RENDER)
        spooled, failed = spool_all(pending, tmp_path, config, fetch)

    plan = plan_sync(
        ALBUM,
        items,
        inventory,
        tv.available(),
        render=RENDER,
        delete_removed_from_album=delete_removed_from_album,
        delete_added_by_hand=delete_added_by_hand,
    )
    report = run(
        plan,
        source=ALBUM,
        tv=tv,
        inventory=inventory,
        config=config,
        render=RENDER,
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
        plan_sync(ALBUM, [], Inventory(existed=False), [], render=RENDER),
        source=ALBUM,
        tv=FakeTv(),
        inventory=Inventory(existed=False),
        config=config,
        render=RENDER,
        spooled={},
    )

    assert config.inventory_file.exists()


# Retrying a download


def urlopen_raising(*errors):
    """A stand-in for `urlopen` that raises each error in turn, then serves an image."""
    remaining = list(errors)

    def urlopen(url, timeout=None):
        if remaining:
            raise remaining.pop(0)
        return io.BytesIO(jpeg(16, 16))

    return urlopen


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://lh3", code, "", headers, None)


def test_a_rate_limited_download_is_retried(monkeypatch):
    monkeypatch.setattr(syncer.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        syncer.urllib.request, "urlopen", urlopen_raising(http_error(429), http_error(503))
    )

    assert syncer.fetch_image("https://lh3/x=w1920-h1080-n")


def test_a_download_refused_for_good_gives_up_without_waiting(monkeypatch):
    """A 404 means the URL, not the moment, so retrying it only slows the run down."""
    slept: list[float] = []
    monkeypatch.setattr(syncer.time, "sleep", slept.append)
    monkeypatch.setattr(syncer.urllib.request, "urlopen", urlopen_raising(http_error(404)))

    with pytest.raises(ImageUnusable, match="404"):
        syncer.fetch_image("https://lh3/x=w1920-h1080-n")

    assert slept == []


def test_retries_run_out_rather_than_going_forever(monkeypatch):
    monkeypatch.setattr(syncer.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        syncer.urllib.request,
        "urlopen",
        urlopen_raising(*[http_error(429)] * syncer.FETCH_ATTEMPTS),
    )

    with pytest.raises(ImageUnusable, match="429"):
        syncer.fetch_image("https://lh3/x=w1920-h1080-n")


def test_the_wait_grows_and_is_capped(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(syncer.time, "sleep", slept.append)

    syncer._wait(None, 1)
    syncer._wait(None, 2)
    syncer._wait(None, 40)

    assert slept[0] < slept[1]
    assert slept[2] == syncer.MAX_BACKOFF_SECONDS


def test_a_retry_after_header_is_honored_when_it_asks_for_longer(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(syncer.time, "sleep", slept.append)

    syncer._wait("30", 1)

    assert slept == [30.0]


def test_an_unparseable_retry_after_falls_back_to_the_backoff(monkeypatch):
    """It may be an HTTP date, which isn't worth parsing to decide how long to nap."""
    slept: list[float] = []
    monkeypatch.setattr(syncer.time, "sleep", slept.append)

    syncer._wait("Wed, 21 Oct 2015 07:28:00 GMT", 1)

    assert slept == [syncer.FETCH_BACKOFF_SECONDS]


# Reporting a run that died partway


def test_an_aborted_run_carries_what_it_managed_first(tmp_path):
    """Losing the report is how a partial run becomes a mystery."""
    items = [album_item("AF1QipA"), album_item("AF1QipB")]
    tv = FakeTv()
    original = tv.upload

    def upload(*args, **kwargs):
        if tv.uploads:
            raise TvTimeout("No answer to `upload` in 120s, so the connection was cut.")
        return original(*args, **kwargs)

    tv.upload = upload

    with pytest.raises(SyncAborted) as aborted:
        sync_once(tmp_path, items=items, inventory=Inventory(existed=True), tv=tv)

    report = aborted.value.report
    assert report.uploaded == ["MY_F0001"]
    assert report.planned_uploads == 2
    assert len(report.upload_seconds) == 1
    # The bytes may have landed even though the confirmation never came, so the photo that was
    # in flight has to be named or an unmanaged image is left on the TV with nobody knowing.
    assert report.in_flight == "AF1QipB"


def test_a_run_that_finishes_has_nothing_in_flight(tmp_path):
    report, _ = sync_once(
        tmp_path,
        items=[album_item("AF1QipA")],
        inventory=Inventory(existed=True),
        tv=FakeTv(),
    )

    assert report.in_flight is None


def test_an_aborted_run_leaves_the_inventory_holding_what_did_upload(tmp_path):
    """That is what lets a re-run pick up where it stopped rather than start over."""
    config = config_for(tmp_path)
    items = [album_item("AF1QipA"), album_item("AF1QipB")]
    tv = FakeTv()
    original = tv.upload

    def upload(*args, **kwargs):
        if tv.uploads:
            raise TvTimeout("cut")
        return original(*args, **kwargs)

    tv.upload = upload

    with pytest.raises(SyncAborted):
        sync_once(tmp_path, items=items, inventory=Inventory(existed=True), tv=tv)

    assert [entry.source_id for entry in load_inventory(config.inventory_file)] == ["AF1QipA"]


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


# The two delete flags


def test_a_photo_that_left_the_album_survives_with_the_mirror_off(tmp_path):
    tv = FakeTv([tv_row("MY_F0001")])

    report, saved = sync_once(
        tmp_path,
        items=[],
        inventory=inventory_of(("MY_F0001", "AF1QipA")),
        tv=tv,
        delete_removed_from_album=False,
    )

    assert tv.deletes == []
    assert report.deleted == []
    assert saved.entry("MY_F0001") is not None

    # Nothing was deleted, so nothing needed confirming and the channel saw one read.
    assert tv.reads == 1


def test_art_added_by_hand_is_deleted_when_the_flag_is_on(tmp_path):
    tv = FakeTv([tv_row("MY_F0009")])

    report, saved = sync_once(
        tmp_path, items=[], inventory=inventory_of(), tv=tv, delete_added_by_hand=True
    )

    assert tv.deletes == ["MY_F0009"]
    assert report.deleted_unmanaged == ["MY_F0009"]
    assert report.deleted == []
    assert len(saved) == 0


def test_an_unmanaged_delete_the_tv_did_not_carry_out_is_reported(tmp_path):
    tv = FakeTv([tv_row("MY_F0009")], undeletable=["MY_F0009"])

    report, _ = sync_once(
        tmp_path, items=[], inventory=inventory_of(), tv=tv, delete_added_by_hand=True
    )

    assert report.deleted_unmanaged == []
    assert report.unconfirmed == ["MY_F0009"]


def test_both_delete_classes_are_confirmed_by_one_re_read(tmp_path):
    """Two passes would double the requests on the channel a long run is already straining."""
    tv = FakeTv([tv_row("MY_F0001"), tv_row("MY_F0009")])

    report, saved = sync_once(
        tmp_path,
        items=[],
        inventory=inventory_of(("MY_F0001", "AF1QipA")),
        tv=tv,
        delete_added_by_hand=True,
    )

    assert tv.reads == 2
    assert report.deleted == ["MY_F0001"]
    assert report.deleted_unmanaged == ["MY_F0009"]
    assert len(saved) == 0


def test_samsungs_own_art_survives_a_run_with_both_flags_on(tmp_path):
    tv = FakeTv([tv_row("SAM-F0222", content_type="preinstall")])

    report, _ = sync_once(
        tmp_path, items=[], inventory=inventory_of(), tv=tv, delete_added_by_hand=True
    )

    assert tv.deletes == []
    assert report.deleted_unmanaged == []


def test_prefetch_crops_each_photo_by_the_rule_its_shape_matches(tmp_path):
    config = replace(
        config_for(tmp_path),
        pipeline=PipelineConfig(
            highlight_rolloff=0.0,
            jpeg_quality=80,
            crop=(
                CropRule(when="4:3", to="16:9"),
                CropRule(when="portrait", to="none"),
            ),
        ),
    )
    items = [album_item("a", 4032, 3024), album_item("b", 3024, 4032)]
    served = {"a": jpeg(1920, 1440), "b": jpeg(1440, 1920)}

    spooled, failures = prefetch(
        items,
        tmp_path,
        config=config,
        fetch=lambda url, timeout: served["a" if "/a=" in url else "b"],
    )

    assert failures == {}
    assert (spooled["a"].width, spooled["a"].height) == (1920, 1080)
    assert (spooled["b"].width, spooled["b"].height) == (810, 1080)


def test_the_crop_is_chosen_from_what_the_source_reported(tmp_path):
    """A dry run has to predict this without downloading, so the rule can't read the pixels."""
    config = replace(
        config_for(tmp_path),
        pipeline=PipelineConfig(
            highlight_rolloff=0.0, jpeg_quality=80, crop=(CropRule(when="3:4", to="1:1"),)
        ),
    )

    spooled, _ = prefetch(
        [album_item("a", 3024, 4032)],
        tmp_path,
        config=config,
        fetch=lambda url, timeout: jpeg(1440, 1920),
    )

    assert (spooled["a"].width, spooled["a"].height) == (1080, 1080)


def test_label_burns_the_crop_and_the_id_into_the_image(tmp_path):
    flat = (120, 90, 60)
    config = replace(
        config_for(tmp_path),
        pipeline=PipelineConfig(highlight_rolloff=0.0, jpeg_quality=95),
    )
    fetch = lambda url, timeout: jpeg(1440, 1080)

    plain_spool, marked_spool = tmp_path / "plain", tmp_path / "marked"
    plain_spool.mkdir()
    marked_spool.mkdir()

    plain, _ = prefetch([album_item("a", 1440, 1080)], plain_spool, config=config, fetch=fetch)
    marked, _ = prefetch(
        [album_item("a", 1440, 1080)], marked_spool, config=config, fetch=fetch, label_crop=True
    )

    with Image.open(marked["a"].path) as art:
        assert art.getpixel((20, 20)) == pytest.approx(flat, abs=6)
        assert len(art.crop((520, 440, 920, 640)).getcolors(maxcolors=1 << 20)) > 1

    assert (marked["a"].width, marked["a"].height) == (plain["a"].width, plain["a"].height)
# Replacing a photo whose rendering has changed


def test_a_stale_photo_is_uploaded_again_and_its_old_copy_taken_down(tmp_path):
    tv = FakeTv([tv_row("MY_F0900")])

    report, saved = sync_once(
        tmp_path,
        items=[album_item("AF1QipA")],
        inventory=inventory_of(("MY_F0900", "AF1QipA"), render=None),
        tv=tv,
    )

    assert report.uploaded == ["MY_F0001"]
    assert report.superseded == ["MY_F0900"]
    # Not counted as a delete, because the wall holds the same photo it did before.
    assert report.deleted == []
    assert [entry.content_id for entry in saved] == ["MY_F0001"]


def test_the_replacement_carries_the_rendering_it_was_made_with(tmp_path):
    """Or the next run reads it as stale again and replaces it forever."""
    _, saved = sync_once(
        tmp_path,
        items=[album_item("AF1QipA")],
        inventory=inventory_of(("MY_F0900", "AF1QipA"), render=None),
        tv=FakeTv([tv_row("MY_F0900")]),
    )

    assert saved.entry("MY_F0001").render == CURRENT


def test_the_replacement_goes_up_before_the_copy_it_replaces_comes_down(tmp_path):
    """So the photo is never off the wall, and a run that dies leaves a duplicate not a hole."""
    order: list[str] = []
    tv = FakeTv([tv_row("MY_F0900")])
    original_upload, original_delete = tv.upload, tv.delete

    def upload(*args, **kwargs):
        order.append("upload")
        return original_upload(*args, **kwargs)

    def delete(content_id):
        order.append(f"delete {content_id}")
        return original_delete(content_id)

    tv.upload, tv.delete = upload, delete

    sync_once(
        tmp_path,
        items=[album_item("AF1QipA")],
        inventory=inventory_of(("MY_F0900", "AF1QipA"), render=None),
        tv=tv,
    )

    assert order == ["upload", "delete MY_F0900"]


def test_a_stale_photo_whose_replacement_failed_keeps_the_copy_it_has(tmp_path):
    """Deleting it would take the photo off the wall to make room for one that doesn't exist."""
    tv = FakeTv([tv_row("MY_F0900")], refuse_upload_at=[1])

    report, saved = sync_once(
        tmp_path,
        items=[album_item("AF1QipA")],
        inventory=inventory_of(("MY_F0900", "AF1QipA"), render=None),
        tv=tv,
    )

    assert tv.deletes == []
    assert report.superseded == []
    assert saved.entry("MY_F0900") is not None
    assert len(report.failures) == 1


def test_a_replacement_and_a_delete_are_confirmed_by_one_re_read(tmp_path):
    """A third class of delete must not cost a third read of `available()`."""
    tv = FakeTv([tv_row("MY_F0900"), tv_row("MY_F0901")])

    report, _ = sync_once(
        tmp_path,
        items=[album_item("AF1QipA")],
        inventory=inventory_of(
            ("MY_F0900", "AF1QipA"), ("MY_F0901", "AF1QipB"), render=None
        ),
        tv=tv,
    )

    assert tv.reads == 2
    assert report.superseded == ["MY_F0900"]
    assert report.deleted == ["MY_F0901"]


def test_a_stale_photo_is_spooled_before_the_channel_opens():
    """It is an upload like any other, and a live fetch mid-run is what the spool prevents."""
    inventory = inventory_of(("MY_F0900", "AF1QipA"), render=None)

    pending = provisional_uploads(ALBUM, [album_item("AF1QipA")], inventory, RENDER)

    assert [item.source_id for item in pending] == ["AF1QipA"]
