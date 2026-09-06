"""Covers what `frame sync` does before it commits to anything.

`--dry-run` is the gate before the first real run and the lost-inventory refusal is the gate
before every one after it, so both are worth proving from the command rather than from the
functions underneath. The TV is replaced wholesale, because what these check is that the
command reaches for it exactly as far as it should and no further.
"""

from __future__ import annotations

import io

import pytest
from click.testing import CliRunner
from PIL import Image

from frame_tv_art_sync import cli
from frame_tv_art_sync.sources import SourceItem
from frame_tv_art_sync.tv import TvTimeout

CONFIG = """
[tv]
host = "10.0.0.5"
name = "frame"
token_file = "token"

[source.google_album]
url = "https://photos.app.goo.gl/EXAMPLE"

[art]
landscape_matte = "flexible_black"
portrait_matte = "flexible_black"

[pipeline]
highlight_rolloff = 0.0
jpeg_quality = 80
"""


class FakeFrameTv:
    """Stands in for the whole wrapper, recording every write so a test can assert there were none.

    The state is on the class rather than the instance because the command constructs its own.
    """

    rows: list[dict] = []
    uploads: list[dict] = []
    deletes: list[str] = []

    def __init__(self, config, **kwargs) -> None:
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *exception) -> None:
        return None

    def available(self) -> list[dict]:
        return [dict(row) for row in FakeFrameTv.rows]

    def upload(self, data, *, matte_id, width, height, file_type="jpg") -> str:
        content_id = f"MY_F{len(FakeFrameTv.uploads) + 1:04d}"
        FakeFrameTv.uploads.append({"content_id": content_id, "matte_id": matte_id})
        FakeFrameTv.rows = [*FakeFrameTv.rows, tv_row(content_id)]
        return content_id

    def delete(self, content_id: str) -> None:
        FakeFrameTv.deletes.append(content_id)
        FakeFrameTv.rows = [
            row for row in FakeFrameTv.rows if row["content_id"] != content_id
        ]


class FakeAlbum:
    name = "google_album"
    items_to_return: list[SourceItem] = []

    def __init__(self, url: str) -> None:
        self.url = url

    def items(self) -> list[SourceItem]:
        return list(self.items_to_return)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A config on disk, a fake TV, and a fake album, with nothing else patched."""
    (tmp_path / "config.toml").write_text(CONFIG)
    monkeypatch.setattr(cli, "FrameTv", FakeFrameTv)
    monkeypatch.setattr(cli, "GoogleAlbumSource", FakeAlbum)
    FakeFrameTv.rows = []
    FakeFrameTv.uploads = []
    FakeFrameTv.deletes = []
    FakeAlbum.items_to_return = []
    return tmp_path


def item(
    source_id: str, width: int = 4032, height: int = 3024, taken_at_ms: int = 1680452105564
) -> SourceItem:
    return SourceItem(
        source_id=source_id,
        url=f"https://lh3.googleusercontent.com/{source_id}=w1920-h1080",
        width=width,
        height=height,
        taken_at_ms=taken_at_ms,
    )


def tv_row(content_id: str, content_type: str = "mobile") -> dict:
    return {"content_id": content_id, "category_id": "MY-C0002", "content_type": content_type}


def invoke(project, *arguments):
    return CliRunner().invoke(
        cli.main, ["--config", str(project / "config.toml"), "sync", *arguments]
    )


def test_a_dry_run_uploads_nothing_deletes_nothing_and_writes_no_inventory(project):
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Upload      1" in result.output
    assert FakeFrameTv.uploads == []
    assert FakeFrameTv.deletes == []
    assert not (project / "inventory.json").exists()


def test_a_dry_run_names_the_matte_each_photo_would_get(project):
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert "flexible_black" in result.output


def test_a_dry_run_says_a_real_run_would_refuse_when_the_inventory_is_gone(project):
    FakeFrameTv.rows = [tv_row("MY_F0001")]
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert "would refuse" in result.output


def test_a_dry_run_told_it_is_a_first_run_does_not_warn_about_a_refusal(project):
    FakeFrameTv.rows = [tv_row("MY_F0001")]
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run", "--first-run")

    assert "would refuse" not in result.output


def test_a_lost_inventory_stops_the_run_before_anything_is_written(project, monkeypatch):
    FakeFrameTv.rows = [tv_row("MY_F0001")]
    FakeAlbum.items_to_return = [item("AF1QipA")]
    monkeypatch.setattr(cli.syncer, "fetch_image", lambda url, timeout: jpeg())

    result = invoke(project)

    assert result.exit_code != 0
    assert "no inventory file" in result.output
    assert not (project / "inventory.json").exists()


def test_first_run_is_what_says_the_tvs_own_art_is_not_this_tools(project, monkeypatch):
    FakeFrameTv.rows = [tv_row("MY_F0001")]
    FakeAlbum.items_to_return = [item("AF1QipA")]
    monkeypatch.setattr(cli.syncer, "fetch_image", lambda url, timeout: jpeg())

    result = invoke(project, "--first-run")

    assert result.exit_code == 0, result.output
    assert (project / "inventory.json").exists()


def test_a_run_killed_by_the_channel_still_says_what_it_managed(project, monkeypatch):
    FakeAlbum.items_to_return = [item("AF1QipA"), item("AF1QipB")]
    monkeypatch.setattr(cli.syncer, "fetch_image", lambda url, timeout: jpeg())

    def upload(self, data, **kwargs):
        if FakeFrameTv.uploads:
            raise TvTimeout("No answer to `upload` in 120s, so the connection was cut.")
        FakeFrameTv.uploads.append({"content_id": "MY_F0001"})
        return "MY_F0001"

    monkeypatch.setattr(FakeFrameTv, "upload", upload)

    result = invoke(project, "--first-run")

    assert result.exit_code != 0
    assert "Uploaded 1 of 2" in result.output
    assert "re-running takes it from there" in result.output
    assert (project / "inventory.json").exists()


def test_an_unreadable_album_fails_before_the_tv_is_touched(project, monkeypatch):
    def refuse(self):
        raise cli.SourceError("The album page returned HTTP 404.")

    monkeypatch.setattr(FakeAlbum, "items", refuse)
    monkeypatch.setattr(
        cli, "FrameTv", lambda *args, **kwargs: pytest.fail("The TV was connected to.")
    )

    result = invoke(project, "--dry-run")

    assert result.exit_code != 0
    assert "404" in result.output


def jpeg(width: int = 4032, height: int = 3024) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_a_source_that_returns_nothing_is_refused_rather_than_mirrored(project):
    """Mirroring nothing means deleting everything, and no source can tell those two apart."""
    result = invoke(project)

    assert result.exit_code != 0
    assert "returned no photos at all" in result.output
    assert FakeFrameTv.deletes == []


def test_a_short_run_narrows_the_album_and_says_so(project):
    (project / "config.toml").write_text(CONFIG + "\n[sync]\nshort_run = 1\n")
    FakeAlbum.items_to_return = [
        item("AF1QipOldLandscape", taken_at_ms=1),
        item("AF1QipNewLandscape", taken_at_ms=9),
        item("AF1QipPortrait", 3024, 4032, taken_at_ms=5),
    ]

    result = invoke(project, "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Upload      2" in result.output
    assert "AF1QipNewLandscape" in result.output
    assert "AF1QipPortrait" in result.output
    assert "AF1QipOldLandscape" not in result.output
    assert "short_run" in result.output


def test_a_short_run_deletes_what_it_left_out(project):
    """It still mirrors, so a photo it excludes comes off the TV. That is the point of it."""
    (project / "config.toml").write_text(CONFIG + "\n[sync]\nshort_run = 1\n")
    (project / "inventory.json").write_text(
        '{"version": 1, "items": {"MY_F0001": {"source": "google_album", '
        '"source_id": "AF1QipOldLandscape", "uploaded_at": "2026-09-01T00:00:00Z"}}}'
    )
    FakeFrameTv.rows = [tv_row("MY_F0001")]
    FakeAlbum.items_to_return = [
        item("AF1QipOldLandscape", taken_at_ms=1),
        item("AF1QipNewLandscape", taken_at_ms=9),
    ]

    result = invoke(project, "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Delete      1" in result.output
    assert "MY_F0001" in result.output


def with_flags(project, body):
    """Rewrite the config with a `[sync]` table, since the flags are read from the file alone."""
    (project / "config.toml").write_text(CONFIG + body)
    return project


def test_a_dry_run_reports_the_unmanaged_delete_count_even_when_the_flag_is_off(project):
    """A missing line would read as the flag being safe rather than as it being off."""
    FakeFrameTv.rows = [tv_row("MY_F0009")]
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert "Purge       0, not this tool's" in result.output
    assert "Left alone  1, not this tool's" in result.output


def test_a_dry_run_names_the_images_the_hand_upload_flag_would_delete(project):
    with_flags(project, "\n[sync]\ndelete_added_by_hand = true\n")
    FakeFrameTv.rows = [tv_row("MY_F0009")]
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert "Purge       1, not this tool's" in result.output
    assert "MY_F0009" in result.output
    assert FakeFrameTv.deletes == []


def test_a_run_says_out_loud_that_it_may_delete_what_it_did_not_upload(project, monkeypatch):
    with_flags(project, "\n[sync]\ndelete_added_by_hand = true\n")
    FakeAlbum.items_to_return = [item("AF1QipA")]
    monkeypatch.setattr(cli.syncer, "fetch_image", lambda url, timeout: jpeg())

    result = invoke(project, "--first-run")

    assert result.exit_code == 0, result.output
    assert "may delete images this tool did not upload" in result.output


def test_a_run_says_out_loud_that_it_is_appending_rather_than_mirroring(project, monkeypatch):
    with_flags(project, "\n[sync]\ndelete_removed_from_album = false\n")
    FakeAlbum.items_to_return = [item("AF1QipA")]
    monkeypatch.setattr(cli.syncer, "fetch_image", lambda url, timeout: jpeg())

    result = invoke(project, "--first-run")

    assert result.exit_code == 0, result.output
    assert "keeps its place on the TV" in result.output


def test_the_lost_inventory_refusal_says_the_copies_would_be_deleted_under_the_flag(project):
    """With the flag on, the same run that duplicates the album destroys what it duplicated."""
    with_flags(project, "\n[sync]\ndelete_added_by_hand = true\n")
    FakeFrameTv.rows = [tv_row("MY_F0001")]
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert "then delete those copies" in result.output


def test_the_lost_inventory_refusal_says_the_copies_survive_by_default(project):
    FakeFrameTv.rows = [tv_row("MY_F0001")]
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert "leave those copies on the TV" in result.output


def test_a_config_pairing_a_short_run_with_no_mirror_is_refused_by_the_command(project):
    with_flags(project, "\n[sync]\nshort_run = 2\ndelete_removed_from_album = false\n")
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert result.exit_code != 0
    assert "short_run" in result.output


CROP_RULES = """
[[pipeline.crop]]
when = "4:3"
to = "16:9"
anchor = "top"

[[pipeline.crop]]
when = "portrait"
to = "none"
"""


def with_crop(project, extra: str = CROP_RULES):
    (project / "config.toml").write_text(CONFIG + extra)
    return project


def test_a_run_says_what_each_crop_rule_will_cover(project):
    FakeAlbum.items_to_return = [item("AF1QipA"), item("AF1QipB"), item("AF1QipC", 3024, 4032)]

    result = invoke(with_crop(project), "--dry-run")

    assert "2  4:3 -> 16:9 top" in result.output
    assert "1  portrait kept whole" in result.output


def test_a_run_with_no_crop_rules_says_nothing_about_them(project):
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert "Crop rules" not in result.output


def test_an_override_naming_no_photo_in_the_run_is_called_out(project):
    """A short run or a photo taken out of the album leaves an override pointing at nothing."""
    FakeAlbum.items_to_return = [item("AF1QipA")]
    extra = CROP_RULES + '\n[pipeline.crop_overrides]\n"AF1QipZZZZ" = { to = "none" }\n'

    result = invoke(with_crop(project, extra), "--dry-run")

    assert "names no photo in this run" in result.output


def test_an_override_naming_several_photos_is_called_out(project):
    FakeAlbum.items_to_return = [item("AF1QipAlphaOne"), item("AF1QipAlphaTwo")]
    extra = CROP_RULES + '\n[pipeline.crop_overrides]\n"AF1QipAlpha" = { to = "none" }\n'

    result = invoke(with_crop(project, extra), "--dry-run")

    assert "names more than one photo" in result.output


def test_a_dry_run_names_the_matte_the_cropped_shape_will_get(project):
    """A `3:4 -> 16:9` rule hands the TV a landscape, so the portrait matte is the wrong answer."""
    FakeAlbum.items_to_return = [item("AF1QipC", 3024, 4032)]
    extra = '\n[[pipeline.crop]]\nwhen = "3:4"\nto = "16:9"\n'
    (project / "config.toml").write_text(
        CONFIG.replace('portrait_matte = "flexible_black"', 'portrait_matte = "shadowbox_black"')
        + extra
    )

    result = invoke(project, "--dry-run")

    assert "flexible_black" in result.output
    assert "shadowbox_black" not in result.output


def test_the_crop_report_covers_the_short_run_rather_than_the_album(project):
    """A short run is what these rules are usually tried out under, so it has to count those."""
    FakeAlbum.items_to_return = [item(f"AF1QipA{index}", taken_at_ms=index) for index in range(4)]

    result = invoke(with_crop(project, CROP_RULES + "\n[sync]\nshort_run = 1\n"), "--dry-run")

    assert "1  4:3 -> 16:9 top" in result.output


def test_label_is_off_unless_it_is_asked_for(project):
    FakeAlbum.items_to_return = [item("AF1QipA")]

    assert "`--label` is on" not in invoke(with_crop(project), "--dry-run").output
def test_a_dry_run_counts_a_replacement_apart_from_a_new_photo(project):
    """Both are uploads, and printing them together would say the album had doubled."""
    _inventory_holding(project, "MY_F0001", "AF1QipA")
    FakeFrameTv.rows = [tv_row("MY_F0001")]
    FakeAlbum.items_to_return = [item("AF1QipA"), item("AF1QipB")]

    result = invoke(project, "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Upload      1" in result.output
    assert "Replace     1" in result.output


def test_a_dry_run_says_why_each_photo_is_being_replaced(project):
    """It is read before a run that can re-upload the whole album, so it says what moved."""
    _inventory_holding(project, "MY_F0001", "AF1QipA")
    FakeFrameTv.rows = [tv_row("MY_F0001")]
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert "no record" in result.output


def _inventory_holding(project, content_id, source_id):
    """An inventory as it looks today: entries written before render records existed."""
    (project / "inventory.json").write_text(
        f'{{"version": 1, "items": {{"{content_id}": {{"source": "google_album", '
        f'"source_id": "{source_id}", "uploaded_at": "2026-09-01T00:00:00Z"}}}}}}'
    )
