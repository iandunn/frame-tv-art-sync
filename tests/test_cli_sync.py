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
    """Stands in for the whole wrapper, and fails loudly if a write is attempted."""

    rows: list[dict] = []

    def __init__(self, config, **kwargs) -> None:
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *exception) -> None:
        return None

    def available(self) -> list[dict]:
        return [dict(row) for row in self.rows]

    def upload(self, *args, **kwargs):
        raise AssertionError("A dry run uploaded something.")

    def delete(self, *args, **kwargs):
        raise AssertionError("A dry run deleted something.")


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
    FakeAlbum.items_to_return = []
    return tmp_path


def item(source_id: str, width: int = 4032, height: int = 3024) -> SourceItem:
    return SourceItem(
        source_id=source_id,
        url=f"https://lh3.googleusercontent.com/{source_id}=w1920-h1080-n",
        width=width,
        height=height,
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
    assert not (project / "inventory.json").exists()


def test_a_dry_run_names_the_matte_each_photo_would_get(project):
    FakeAlbum.items_to_return = [item("AF1QipA")]

    result = invoke(project, "--dry-run")

    assert "flexible_black" in result.output


def test_a_dry_run_says_a_real_run_would_refuse_when_the_inventory_is_gone(project):
    FakeFrameTv.rows = [tv_row("MY_F0001")]

    result = invoke(project, "--dry-run")

    assert "would refuse" in result.output


def test_a_dry_run_told_it_is_a_first_run_does_not_warn_about_a_refusal(project):
    FakeFrameTv.rows = [tv_row("MY_F0001")]

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
    monkeypatch.setattr(cli.syncer, "fetch_image", lambda url, timeout: jpeg())

    result = invoke(project, "--first-run")

    assert result.exit_code == 0, result.output
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


def test_an_empty_album_on_a_first_run_writes_an_inventory_to_sync_against_later(project):
    result = invoke(project, "--first-run")

    assert result.exit_code == 0, result.output
    assert (project / "inventory.json").exists()
