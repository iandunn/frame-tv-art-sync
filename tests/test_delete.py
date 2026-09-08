"""Covers what `frame delete` will and won't take off the TV.

The rules worth testing are the refusals, because everything else here is a loop over ids: an
id naming Samsung's own art and an id the TV doesn't hold both stop the run rather than being
skipped, and `by-hand` reaches uploads and nothing else.
"""

from __future__ import annotations

import pytest

from frame_tv_art_sync.delete import (
    DeleteError,
    DeletePlan,
    carry_out,
    check_names,
    plan_by_hand,
    plan_named,
)
from frame_tv_art_sync.inventory import Inventory
from frame_tv_art_sync.tv import TvRefused


def row(content_id, content_type="mobile", category_id="MY-C0002") -> dict:
    return {"content_id": content_id, "category_id": category_id, "content_type": content_type}


def inventory_of(*content_ids) -> Inventory:
    inventory = Inventory(existed=True)
    for content_id in content_ids:
        inventory.record(content_id, "google_album", f"AF1Qip{content_id}")

    return inventory


class FakeTv:
    """Deletes for real out of its own row list, so a confirmation can be tested."""

    def __init__(self, rows, refuse=()) -> None:
        self.rows = list(rows)
        self.refuse = set(refuse)
        self.deletes: list[str] = []

    def available(self) -> list[dict]:
        return [dict(one) for one in self.rows]

    def delete(self, content_id: str) -> None:
        self.deletes.append(content_id)
        if content_id in self.refuse:
            raise TvRefused("error -7")

        self.rows = [one for one in self.rows if one["content_id"] != content_id]


class DeafTv(FakeTv):
    """Lists an image as present after deleting it, which is the unconfirmed case."""

    def delete(self, content_id: str) -> None:
        self.deletes.append(content_id)


def test_naming_samsungs_own_art_is_refused():
    with pytest.raises(DeleteError, match="Samsung's own art"):
        check_names(["MY_F0481", "SAM-F0222"])


def test_naming_an_image_the_tv_doesnt_hold_is_refused():
    with pytest.raises(DeleteError, match="MY_F0999"):
        plan_named(["MY_F0999"], [row("MY_F0481")], inventory_of())


def test_a_refused_id_takes_the_whole_run_with_it_rather_than_dropping_out_of_it():
    """The rest of the list was typed under the same mistake, so none of it is acted on."""
    with pytest.raises(DeleteError):
        plan_named(["MY_F0481", "MY_F0999"], [row("MY_F0481")], inventory_of("MY_F0481"))


def test_a_repeated_id_describes_one_delete():
    plan = plan_named(["MY_F0481", "MY_F0481"], [row("MY_F0481")], inventory_of("MY_F0481"))

    assert plan.delete == ["MY_F0481"]


def test_named_ids_are_split_by_whether_the_inventory_claims_them():
    rows = [row("MY_F0481"), row("MY_F0482")]

    plan = plan_named(["MY_F0482", "MY_F0481"], rows, inventory_of("MY_F0481"))

    assert plan.mine == ["MY_F0481"]
    assert plan.unmanaged == ["MY_F0482"]


def test_by_hand_takes_the_uploads_the_inventory_doesnt_claim():
    rows = [row("MY_F0481"), row("MY_F0482"), row("MY_F0483")]

    plan = plan_by_hand(rows, inventory_of("MY_F0481"))

    assert plan.mine == []
    assert plan.unmanaged == ["MY_F0482", "MY_F0483"]


def test_by_hand_leaves_samsungs_own_art_alone():
    """`frame status` lists the bundled `SAM-` row under "not this tool's", and this must not."""
    rows = [
        row("SAM-F0222", content_type="preinstall"),
        row("SAM-S1000", content_type="server"),
        row("MY_F0482"),
    ]

    assert plan_by_hand(rows, inventory_of()).unmanaged == ["MY_F0482"]


def test_by_hand_leaves_an_id_whose_rows_disagree_about_what_it_is():
    rows = [row("MY_F0482"), row("MY_F0482", content_type="preinstall", category_id="MY-C0008")]

    assert plan_by_hand(rows, inventory_of()).unmanaged == []


def test_a_delete_drops_the_inventory_entry_once_the_tv_has_stopped_listing_the_image(tmp_path):
    inventory = inventory_of("MY_F0481")
    tv = FakeTv([row("MY_F0481")])

    report = carry_out(
        DeletePlan(mine=["MY_F0481"], unmanaged=[]),
        tv=tv,
        inventory=inventory,
        config=_config(tmp_path),
    )

    assert report.deleted == ["MY_F0481"]
    assert "MY_F0481" not in inventory
    assert (tmp_path / "inventory.json").exists()


def test_an_image_the_tv_still_lists_keeps_its_entry(tmp_path):
    inventory = inventory_of("MY_F0481")

    report = carry_out(
        DeletePlan(mine=["MY_F0481"], unmanaged=[]),
        tv=DeafTv([row("MY_F0481")]),
        inventory=inventory,
        config=_config(tmp_path),
    )

    assert report.deleted == []
    assert report.unconfirmed == ["MY_F0481"]
    assert "MY_F0481" in inventory


def test_a_refused_delete_doesnt_stop_the_rest_or_the_inventory_being_saved(tmp_path):
    inventory = inventory_of("MY_F0481", "MY_F0482")
    tv = FakeTv([row("MY_F0481"), row("MY_F0482")], refuse=["MY_F0481"])

    report = carry_out(
        DeletePlan(mine=["MY_F0481", "MY_F0482"], unmanaged=[]),
        tv=tv,
        inventory=inventory,
        config=_config(tmp_path),
    )

    assert report.deleted == ["MY_F0482"]
    assert [failure.split(":")[0] for failure in report.failures] == ["MY_F0481"]
    assert "MY_F0481" in inventory
    assert "MY_F0482" not in inventory


def _config(tmp_path):
    """Enough of a config for `carry_out`, which reads the inventory path and nothing else."""

    class Stub:
        inventory_file = tmp_path / "inventory.json"

    return Stub()
