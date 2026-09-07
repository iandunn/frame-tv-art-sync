"""Covers what `frame bakeoff` does to the TV, which is more than any other command does.

A round is the only thing here that deletes an image no inventory claims, so what these prove
is that it reaches exactly that far: every upload goes, Samsung's art stays, and answering no
to the question leaves the TV as it was.

The other half is that a round covers every shape the album holds without ever putting a
fixed-aperture matte on anything but a 16:9, which is the combination that crashes Art Mode.
"""

from __future__ import annotations

import io
import json
import re

import pytest
from click.testing import CliRunner
from PIL import Image

from frame_tv_art_sync import cli
from frame_tv_art_sync.mattes import FIXED_APERTURE_TYPES, split_matte_id
from frame_tv_art_sync.sources import SourceItem
from frame_tv_art_sync.tv import MatteColor, TvTimeout

CONFIG = """
[tv]
host = "10.0.0.5"
name = "frame"
token_file = "token"

[source.google_album]
url = "https://photos.app.goo.gl/EXAMPLE"

[art]
fallback_matte = "shadowbox_black"

[art.matte_by_ratio]
"4:3" = "shadowbox_black"
"3:4" = "shadowbox_black"

[pipeline]
highlight_rolloff = 0.0
jpeg_quality = 80

[sync]
short_run = 5
"""

COLORS = [
    MatteColor(name="polar", rgb=(232, 230, 231)),
    MatteColor(name="antique", rgb=(224, 219, 210)),
    MatteColor(name="black", rgb=(34, 34, 33)),
]


class FakeFrameTv:
    """Stands in for the wrapper, recording every write so a test can assert there were none."""

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

    def matte_list(self) -> tuple[list[str], list[MatteColor]]:
        return [color.name for color in COLORS], list(COLORS)

    def upload(self, data, *, matte_id, width, height, file_type="jpg", date=None) -> str:
        content_id = f"MY_F{len(FakeFrameTv.uploads) + 1:04d}"
        FakeFrameTv.uploads.append(
            {
                "content_id": content_id,
                "matte_id": matte_id,
                "data": data,
                "date": date,
                "width": width,
                "height": height,
            }
        )
        FakeFrameTv.rows = [*FakeFrameTv.rows, tv_row(content_id, image_date=date or "")]
        return content_id

    def delete(self, content_id: str) -> None:
        FakeFrameTv.deletes.append(content_id)
        FakeFrameTv.rows = [row for row in FakeFrameTv.rows if row["content_id"] != content_id]


class FakeAlbum:
    name = "google_album"
    items_to_return: list[SourceItem] = []

    def __init__(self, url: str) -> None:
        self.url = url

    def items(self) -> list[SourceItem]:
        return list(self.items_to_return)


def item(source_id, width=4032, height=3024, taken_at_ms=1680452105564) -> SourceItem:
    return SourceItem(
        source_id=source_id,
        url=f"https://lh3.googleusercontent.com/{source_id}=w1920-h1080",
        width=width,
        height=height,
        taken_at_ms=taken_at_ms,
    )


def tv_row(content_id, content_type="mobile", category_id="MY-C0002", image_date="") -> dict:
    return {
        "content_id": content_id,
        "category_id": category_id,
        "content_type": content_type,
        "image_date": image_date,
    }


def jpeg(width=1200, height=900) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 130, 140)).save(buffer, format="JPEG")
    return buffer.getvalue()


def fetch_matching_item(url: str, timeout: float = 30.0) -> bytes:
    """Serve each photo at its own declared shape, since a round now turns on the shape.

    A fetcher that answered every URL with one size would let a variant be uploaded against the
    wrong photo's dimensions without any test noticing.
    """
    source_id = url.split("/")[-1].split("=")[0]
    for candidate in FakeAlbum.items_to_return:
        if candidate.source_id == source_id:
            return jpeg(candidate.width, candidate.height)

    raise AssertionError(f"nothing fetched {source_id}")


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(CONFIG)
    monkeypatch.setattr(cli, "FrameTv", FakeFrameTv)
    monkeypatch.setattr(cli, "GoogleAlbumSource", FakeAlbum)
    monkeypatch.setattr(cli.syncer, "fetch_image", fetch_matching_item)
    FakeFrameTv.rows = []
    FakeFrameTv.uploads = []
    FakeFrameTv.deletes = []
    FakeAlbum.items_to_return = [item("AF1QipA")]
    return tmp_path


def invoke(project, *arguments, answer=None):
    return CliRunner().invoke(
        cli.main, ["--config", str(project / "config.toml"), "bakeoff", *arguments], input=answer
    )


def inventory_of(project) -> dict:
    return json.loads((project / "inventory.json").read_text())["items"]


def roster_line(output, number, shape, matte_id) -> bool:
    """One line of the roster or the dry run's upload list, whatever the column padding is."""
    return re.search(rf"^\s+{number}\s+{re.escape(shape)}\s+{matte_id}\b", output, re.M) is not None


def test_a_dry_run_deletes_nothing_uploads_nothing_and_writes_no_inventory(project):
    FakeFrameTv.rows = [tv_row("MY_F0001")]

    result = invoke(project, "--compare=colors", "--orientation=landscape", "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Upload      3" in result.output
    assert FakeFrameTv.uploads == []
    assert FakeFrameTv.deletes == []
    assert not (project / "inventory.json").exists()


def test_a_color_round_uploads_the_photo_once_per_color_lightest_first(project):
    result = invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    assert result.exit_code == 0, result.output
    assert [upload["matte_id"] for upload in FakeFrameTv.uploads] == [
        "flexible_polar",
        "flexible_antique",
        "flexible_black",
    ]


def test_a_round_ignores_the_matte_the_config_asks_for(project):
    invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    assert "shadowbox_black" not in [upload["matte_id"] for upload in FakeFrameTv.uploads]


def test_a_type_round_over_a_portrait_covers_the_three_that_fit_any_shape(project):
    FakeAlbum.items_to_return = [item("AF1QipB", width=3024, height=4032)]

    result = invoke(
        project, "--compare=types", "--orientation=portrait", "--color=polar", "--yes"
    )

    assert result.exit_code == 0, result.output
    assert [upload["matte_id"] for upload in FakeFrameTv.uploads] == [
        "flexible_polar",
        "none",
        "shadowbox_polar",
    ]


# What a round covers when it isn't narrowed, and what it must never put on the wall


def test_a_type_round_puts_the_fixed_aperture_types_on_the_16_9_and_nothing_else(project):
    """A fixed aperture on a 4:3 crashes Art Mode and needs a power cycle to clear."""
    FakeAlbum.items_to_return = [item("AF1QipA"), item("AF1QipW", width=1920, height=1080)]

    result = invoke(project, "--compare=types", "--color=polar", "--yes")

    assert result.exit_code == 0, result.output
    fixed = [
        upload
        for upload in FakeFrameTv.uploads
        if split_matte_id(upload["matte_id"])[0] in FIXED_APERTURE_TYPES
    ]
    assert len(fixed) == len(FIXED_APERTURE_TYPES)
    # Prepared, not source: the long edge is bound to the panel and nothing is cropped.
    assert {(upload["width"], upload["height"]) for upload in fixed} == {(1920, 1080)}


def test_a_type_round_covers_every_shape_rather_than_only_the_one_that_takes_them_all(project):
    FakeAlbum.items_to_return = [item("AF1QipA"), item("AF1QipW", width=1920, height=1080)]

    invoke(project, "--compare=types", "--color=polar", "--yes")

    assert len(FakeFrameTv.uploads) == 9
    # Prepared, not source: the long edge is bound to the panel and nothing is cropped.
    assert {(upload["width"], upload["height"]) for upload in FakeFrameTv.uploads} == {
        (1920, 1080),
        (1440, 1080),
    }


def test_a_color_round_covers_every_shape_the_album_holds(project):
    FakeAlbum.items_to_return = [item("AF1QipA"), item("AF1QipT", width=3024, height=4032)]

    result = invoke(project, "--compare=colors", "--yes")

    assert result.exit_code == 0, result.output
    assert len(FakeFrameTv.uploads) == 6


def test_a_dry_run_names_the_photo_per_shape_and_the_shape_on_every_upload(project):
    FakeAlbum.items_to_return = [item("AF1QipA"), item("AF1QipW", width=1920, height=1080)]

    result = invoke(project, "--compare=colors", "--dry-run")

    assert "Photos      2, one per shape" in result.output
    assert "AF1QipW" in result.output
    assert "AF1QipA" in result.output
    assert roster_line(result.output, 1, "16:9", "flexible_polar"), result.output
    assert roster_line(result.output, 4, "4:3", "flexible_polar"), result.output


def test_an_orientation_narrows_a_round_to_the_shapes_that_are_that_way_round(project):
    FakeAlbum.items_to_return = [
        item("AF1QipA"),
        item("AF1QipW", width=1920, height=1080),
        item("AF1QipT", width=3024, height=4032),
    ]

    invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    # Prepared, not source: the long edge is bound to the panel and nothing is cropped.
    assert {(upload["width"], upload["height"]) for upload in FakeFrameTv.uploads} == {
        (1920, 1080),
        (1440, 1080),
    }


def test_every_variant_carries_its_own_number(project):
    """Two uploads of one photo differing only in the matte would be identical bytes otherwise."""
    invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    rendered = {upload["data"] for upload in FakeFrameTv.uploads}
    assert len(rendered) == len(FakeFrameTv.uploads)


def test_the_roster_says_which_number_is_which_matte_on_which_shape(project):
    result = invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    assert roster_line(result.output, 1, "4:3", "flexible_polar"), result.output
    assert roster_line(result.output, 3, "4:3", "flexible_black"), result.output


def test_a_round_starts_by_emptying_the_tv(project):
    FakeFrameTv.rows = [tv_row("MY_F0001"), tv_row("MY_F0002")]

    invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    assert FakeFrameTv.deletes == ["MY_F0001", "MY_F0002"]


def test_samsungs_own_art_survives_a_round(project):
    FakeFrameTv.rows = [
        tv_row("SAM-S10000", content_type="server", category_id="MY-C0008"),
        tv_row("SAM-F0206", content_type="preinstall", category_id="MY-C0008"),
    ]

    invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    assert FakeFrameTv.deletes == []


def test_saying_no_to_the_question_leaves_the_tv_alone(project):
    FakeFrameTv.rows = [tv_row("MY_F0001")]

    result = invoke(project, "--compare=colors", "--orientation=landscape", answer="n\n")

    assert result.exit_code != 0
    assert FakeFrameTv.deletes == []
    assert FakeFrameTv.uploads == []


def test_the_question_names_the_uploads_nothing_accounts_for(project):
    FakeFrameTv.rows = [tv_row("MY_F0009")]

    result = invoke(project, "--compare=colors", "--orientation=landscape", answer="n\n")

    assert "MY_F0009" in result.output
    assert "not this tool's" in result.output


def test_clearing_empties_the_tv_and_uploads_nothing(project):
    FakeFrameTv.rows = [tv_row("MY_F0001")]

    result = invoke(project, "--clear", "--yes")

    assert result.exit_code == 0, result.output
    assert FakeFrameTv.deletes == ["MY_F0001"]
    assert FakeFrameTv.uploads == []


def test_clearing_and_comparing_at_once_is_refused(project):
    FakeFrameTv.rows = [tv_row("MY_F0001")]

    result = invoke(project, "--clear", "--compare=colors", "--yes")

    assert result.exit_code != 0
    assert FakeFrameTv.deletes == []


def test_a_rounds_uploads_are_attributed_to_the_bakeoff_rather_than_the_album(project):
    """A sync scopes its deletes by source, so this is what keeps one off them."""
    invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    sources = {entry["source"] for entry in inventory_of(project).values()}
    assert sources == {"bakeoff"}


def test_each_variant_gets_its_own_inventory_entry(project):
    invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    stored = inventory_of(project)
    assert len(stored) == 3
    assert len({entry["source_id"] for entry in stored.values()}) == 3


def test_a_round_that_dies_partway_still_prints_what_went_up(project, monkeypatch):
    """The roster is the only record of which number is which, and half a round is still up."""
    real_upload = FakeFrameTv.upload

    def fail_on_the_second(self, data, **keywords):
        if len(FakeFrameTv.uploads) >= 1:
            raise TvTimeout("the TV never answered")
        return real_upload(self, data, **keywords)

    monkeypatch.setattr(FakeFrameTv, "upload", fail_on_the_second)

    result = invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    assert result.exit_code != 0
    assert roster_line(result.output, 1, "4:3", "flexible_polar"), result.output
    assert len(inventory_of(project)) == 1


def test_the_bakeoff_table_narrows_which_colors_go_up(project):
    (project / "config.toml").write_text(CONFIG + '\n[bakeoff]\ncolors = ["polar", "black"]\n')

    result = invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    assert result.exit_code == 0, result.output
    assert [upload["matte_id"] for upload in FakeFrameTv.uploads] == [
        "flexible_polar",
        "flexible_black",
    ]


def test_a_round_narrowed_to_nothing_says_so_rather_than_uploading(project):
    (project / "config.toml").write_text(CONFIG + '\n[bakeoff]\ntypes = ["modernwide"]\n')
    FakeAlbum.items_to_return = [item("AF1QipB", width=3024, height=4032)]

    result = invoke(
        project, "--compare=types", "--orientation=portrait", "--color=polar", "--yes"
    )

    assert result.exit_code != 0
    assert FakeFrameTv.uploads == []


def test_a_shape_a_narrowed_round_cant_cover_stops_it_rather_than_dropping_out(project):
    """`types = ["modernwide"]` would otherwise shrink a whole-album round to the 16:9 alone."""
    (project / "config.toml").write_text(CONFIG + '\n[bakeoff]\ntypes = ["modernwide"]\n')
    FakeAlbum.items_to_return = [item("AF1QipA"), item("AF1QipW", width=1920, height=1080)]

    result = invoke(project, "--compare=types", "--color=polar", "--yes")

    assert result.exit_code != 0
    assert "4:3" in result.output
    assert FakeFrameTv.uploads == []


def test_a_round_leaves_the_image_date_to_the_library(project):
    """The firmware parses it, so a name sent there is stored as the epoch and shows as 1970."""
    invoke(project, "--compare=colors", "--orientation=landscape", "--yes")

    assert [upload["date"] for upload in FakeFrameTv.uploads] == [None, None, None]


def with_crop_rule(project):
    (project / "config.toml").write_text(
        CONFIG + '\n[[pipeline.crop]]\nwhen = "4:3"\nto = "16:9"\n'
    )
    return project


def test_a_round_says_it_is_ignoring_the_crop_rules(project):
    """A round settles the mat, and a crop is the one thing that would change what sits under it."""
    result = invoke(
        with_crop_rule(project), "--compare=colors", "--orientation=landscape", "--dry-run"
    )

    assert result.exit_code == 0, result.output
    assert "Crop rules are ignored for a bakeoff" in result.output


def test_a_round_with_no_crop_rules_says_nothing_about_them(project):
    result = invoke(project, "--compare=colors", "--orientation=landscape", "--dry-run")

    assert "Crop rules" not in result.output


def test_a_round_uploads_the_whole_photo_even_with_a_crop_rule_configured(project):
    result = invoke(
        with_crop_rule(project), "--compare=colors", "--orientation=landscape", "--yes"
    )

    # The photo is 4:3, and the configured rule crops a 4:3 to 16:9, so an upload that kept
    # its own shape is the whole photo and one at 16:9 would be the rule having fired.
    assert result.exit_code == 0, result.output
    assert {(upload["width"], upload["height"]) for upload in FakeFrameTv.uploads} == {
        (1440, 1080)
    }
