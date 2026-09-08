"""Covers `frame delete` end to end, which is the other command that destroys images.

What these prove is the shape of the run rather than the rules, which `test_delete.py` has: the
question is asked before anything is deleted, a no leaves the TV alone, a refusal never opens a
connection, and `by-hand` reaches the uploads nothing claims without touching Samsung's art.
"""

from __future__ import annotations

import json
import re

import pytest
from click.testing import CliRunner

from frame_tv_art_sync import cli
from frame_tv_art_sync.inventory import Inventory

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
"""


class FakeFrameTv:
    """Stands in for the wrapper, recording every write so a test can assert there were none."""

    rows: list[dict] = []
    deletes: list[str] = []
    connections = 0

    def __init__(self, config, **kwargs) -> None:
        self.config = config

    def __enter__(self):
        FakeFrameTv.connections += 1
        return self

    def __exit__(self, *exception) -> None:
        return None

    def available(self) -> list[dict]:
        return [dict(row) for row in FakeFrameTv.rows]

    def delete(self, content_id: str) -> None:
        FakeFrameTv.deletes.append(content_id)
        FakeFrameTv.rows = [row for row in FakeFrameTv.rows if row["content_id"] != content_id]


def tv_row(content_id, content_type="mobile", category_id="MY-C0002") -> dict:
    return {"content_id": content_id, "category_id": category_id, "content_type": content_type}


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(CONFIG)
    monkeypatch.setattr(cli, "FrameTv", FakeFrameTv)
    FakeFrameTv.rows = [tv_row("MY_F0481"), tv_row("MY_F0482")]
    FakeFrameTv.deletes = []
    FakeFrameTv.connections = 0
    write_inventory(tmp_path, "MY_F0481")
    return tmp_path


def write_inventory(project, *content_ids) -> None:
    """Written through `Inventory` rather than as JSON, so the file is whatever the code reads."""
    inventory = Inventory()
    for content_id in content_ids:
        inventory.record(content_id, "google_album", f"AF1Qip{content_id}")

    inventory.save(project / "inventory.json")


def inventory_of(project) -> dict:
    return json.loads((project / "inventory.json").read_text())["items"]


def invoke(project, *arguments, answer=None):
    return CliRunner().invoke(
        cli.main, ["--config", str(project / "config.toml"), "delete", *arguments], input=answer
    )


def test_a_dry_run_deletes_nothing_and_leaves_the_inventory_alone(project):
    result = invoke(project, "MY_F0481", "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Delete      1" in result.output
    assert FakeFrameTv.deletes == []
    assert list(inventory_of(project)) == ["MY_F0481"]


def test_answering_no_deletes_nothing(project):
    result = invoke(project, "MY_F0481", answer="n\n")

    assert result.exit_code != 0
    assert "Nothing was deleted." in result.output
    assert FakeFrameTv.deletes == []


def test_deleting_one_of_this_tools_uploads_drops_its_entry(project):
    result = invoke(project, "MY_F0481", "--yes")

    assert result.exit_code == 0, result.output
    assert FakeFrameTv.deletes == ["MY_F0481"]
    assert inventory_of(project) == {}


def test_a_run_that_asked_nothing_still_says_the_photo_comes_back(project):
    """`--yes` never sees the question, so the report is the only place it hears this."""
    result = invoke(project, "MY_F0481", "--yes")

    assert "next `frame sync`" in result.output


def test_deleting_an_image_the_inventory_doesnt_claim_says_nothing_about_coming_back(project):
    result = invoke(project, "MY_F0482", "--yes")

    assert "next `frame sync`" not in result.output


def test_deleting_an_image_the_inventory_doesnt_claim_leaves_the_entries_alone(project):
    result = invoke(project, "MY_F0482", "--yes")

    assert result.exit_code == 0, result.output
    assert FakeFrameTv.deletes == ["MY_F0482"]
    assert list(inventory_of(project)) == ["MY_F0481"]


def test_the_question_says_which_ids_this_tool_uploaded(project):
    result = invoke(project, "MY_F0481", "MY_F0482", answer="n\n")

    assert re.search(r"^\s+MY_F0481\s+uploaded by this tool$", result.output, re.M)
    assert re.search(r"^\s+MY_F0482\s+not this tool's", result.output, re.M)
    assert "next `frame sync`" in result.output


def test_samsungs_own_art_is_refused_without_opening_a_connection(project):
    FakeFrameTv.rows = [*FakeFrameTv.rows, tv_row("SAM-F0222", content_type="preinstall")]

    result = invoke(project, "SAM-F0222", "--yes")

    assert result.exit_code != 0
    assert "Samsung's own art" in result.output
    assert FakeFrameTv.connections == 0
    assert FakeFrameTv.deletes == []


def test_an_id_the_tv_doesnt_hold_is_refused_and_nothing_else_on_the_line_goes(project):
    result = invoke(project, "MY_F0481", "MY_F0999", "--yes")

    assert result.exit_code != 0
    assert "MY_F0999" in result.output
    assert FakeFrameTv.deletes == []


def test_by_hand_deletes_the_uploads_the_inventory_doesnt_claim_and_nothing_else(project):
    FakeFrameTv.rows = [*FakeFrameTv.rows, tv_row("SAM-F0222", content_type="preinstall")]

    result = invoke(project, "by-hand", "--yes")

    assert result.exit_code == 0, result.output
    assert FakeFrameTv.deletes == ["MY_F0482"]
    assert list(inventory_of(project)) == ["MY_F0481"]


def test_by_hand_says_why_it_is_leaving_an_unclaimed_image_alone(project):
    """`frame status` calls it "not this tool's", so a silent skip reads as a bug."""
    FakeFrameTv.rows = [*FakeFrameTv.rows, tv_row("MY_F0620", content_type="usb")]

    result = invoke(project, "by-hand", "--yes")

    assert result.exit_code == 0, result.output
    assert "MY_F0620" in result.output
    assert "`usb`" in result.output
    assert "MY_F0620" not in FakeFrameTv.deletes


def test_by_hand_with_nothing_to_take_down_deletes_nothing(project):
    FakeFrameTv.rows = [tv_row("MY_F0481")]

    result = invoke(project, "by-hand", "--yes")

    assert result.exit_code == 0, result.output
    assert "nothing to delete" in result.output
    assert FakeFrameTv.deletes == []


def test_by_hand_takes_no_ids_beside_it(project):
    result = invoke(project, "by-hand", "MY_F0481", "--yes")

    assert result.exit_code != 0
    assert FakeFrameTv.connections == 0
    assert FakeFrameTv.deletes == []


def test_naming_nothing_at_all_says_what_the_two_shapes_are(project):
    result = invoke(project)

    assert result.exit_code != 0
    assert "by-hand" in result.output
    assert FakeFrameTv.connections == 0
