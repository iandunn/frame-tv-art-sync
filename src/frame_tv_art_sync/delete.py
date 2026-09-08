"""Takes images off the TV by name, or takes down every upload the inventory doesn't claim.

`frame sync` already deletes, but only as the other half of mirroring an album, so there is no
way through it to reach one image or to reach an upload it never made. That is what this is
for: a photo added from a phone, one stranded by an upload that timed out after the bytes had
landed, or a single copy somebody wants gone without waiting for the album to say so.

The planning half is pure and the carrying-out half is not, the same split `sync.py` and
`syncer.py` have. What the rules protect is the same thing everywhere else here protects:
Samsung's own art is never a candidate, in either shape, and `sync.unmanaged_uploads()` is
what enforces that rather than a second filter written to the same description.

A named id that the inventory owns is refused by nothing, because deleting it is temporary:
the photo is still in the album, so the next `frame sync` puts it back. What the delete has to
do is drop the entry with it, or the inventory would claim an image the TV no longer holds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Config
from .inventory import Inventory
from .sync import ART_STORE_ID_PREFIX, tv_content_ids, unmanaged_uploads
from .tv import TvError, TvRefused

# The subcommand word for the second shape. It matches `sync.delete_added_by_hand`, because the
# set of images it reaches is the same one, and two names for that would read as two ideas.
BY_HAND = "by-hand"

Announce = Callable[[str], None]


class DeleteError(Exception):
    """The ids given don't describe a delete this command will make."""


class DeleteAborted(Exception):
    """The channel died partway through, and `report` is what had already happened."""

    def __init__(self, report: DeleteReport, error: TvError) -> None:
        super().__init__(str(error))
        self.report = report


@dataclass(frozen=True)
class DeletePlan:
    """What a run would take down, split by whether the inventory claims it.

    The two are separated because they are different promises, the way a bakeoff's clear plan
    separates them. Taking down one of this tool's own uploads undoes itself on the next sync,
    while taking down an upload nothing accounts for is the one thing here that reaches an
    image this tool never put up.
    """

    mine: list[str]
    unmanaged: list[str]

    @property
    def delete(self) -> list[str]:
        return [*self.mine, *self.unmanaged]

    @property
    def is_empty(self) -> bool:
        return not self.delete


@dataclass
class DeleteReport:
    """What a run did. `unconfirmed` is an image the TV still listed after being told to drop it."""

    deleted: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


class ArtTv(Protocol):
    """The two things a delete asks of the TV, so the tests can hand it a fake."""

    def available(self) -> list[dict[str, Any]]: ...

    def delete(self, content_id: str) -> None: ...


def check_names(content_ids: list[str]) -> list[str]:
    """The ids with the repeats collapsed, refusing any that names Samsung's own art.

    It is separate from the plan so that the caller can ask it before opening a channel, since
    a mistyped id shouldn't cost a connection. The plan asks it again, because a name reaching
    a delete another way would otherwise be checked nowhere.
    """
    named = list(dict.fromkeys(content_ids))

    samsung = [content_id for content_id in named if content_id.startswith(ART_STORE_ID_PREFIX)]
    if samsung:
        raise DeleteError(
            f"{', '.join(samsung)} is Samsung's own art, which this will not delete. Only an "
            "image somebody uploaded can be deleted from here."
        )

    return named


def plan_named(
    content_ids: list[str], available: list[dict[str, Any]], inventory: Inventory
) -> DeletePlan:
    """The plan for a list of ids somebody typed, in the order they typed them.

    Nothing here is skipped quietly. An id naming Samsung's own art, and an id the TV doesn't
    report, both refuse the whole run rather than dropping out of it, because either one is a
    mistake about what is on the TV and the rest of the list was typed under the same mistake.
    A repeated id is the exception, since it describes the same delete twice.
    """
    named = check_names(content_ids)

    on_tv = tv_content_ids(available)
    missing = [content_id for content_id in named if content_id not in on_tv]
    if missing:
        raise DeleteError(
            f"The TV doesn't list {', '.join(missing)}, so there is nothing to delete. `frame "
            "status` says what it holds."
        )

    return DeletePlan(
        mine=[content_id for content_id in named if content_id in inventory],
        unmanaged=[content_id for content_id in named if content_id not in inventory],
    )


def plan_by_hand(available: list[dict[str, Any]], inventory: Inventory) -> DeletePlan:
    """Every upload on the TV that no inventory entry claims.

    Nothing lands in `mine`, by definition. The set is `sync.unmanaged_uploads()`'s rather than
    the wider one `frame status` prints under "not this tool's", which includes the bundled
    `SAM-` art the TV falls back on when My Pictures empties.
    """
    known = {entry.content_id for entry in inventory}

    return DeletePlan(
        mine=[], unmanaged=sorted(unmanaged_uploads(available, known) & tv_content_ids(available))
    )


def carry_out(
    plan: DeletePlan,
    *,
    tv: ArtTv,
    inventory: Inventory,
    config: Config,
    announce: Announce = lambda message: None,
) -> DeleteReport:
    """Delete the images, then confirm the lot with one re-read of `available()`.

    `delete()` returns a bool that proves nothing, and this is the one place a mistake destroys
    photos, so an entry is dropped only once the TV has stopped listing its image. The
    inventory is saved whatever happens, including when the channel dies partway, or a run that
    deleted nine images out of ten would leave the entries for all ten behind.
    """
    report = DeleteReport()

    try:
        _delete(plan, report, tv=tv, inventory=inventory, announce=announce)
    except TvError as error:
        inventory.save(config.inventory_file)
        raise DeleteAborted(report, error) from None

    inventory.save(config.inventory_file)
    return report


def _delete(
    plan: DeletePlan,
    report: DeleteReport,
    *,
    tv: ArtTv,
    inventory: Inventory,
    announce: Announce,
) -> None:
    if not plan.delete:
        return

    refused: set[str] = set()

    for index, content_id in enumerate(plan.delete, start=1):
        announce(f"Deleting {index}/{len(plan.delete)}  {content_id}")
        try:
            tv.delete(content_id)
        except TvRefused as error:
            refused.add(content_id)
            report.failures.append(f"{content_id}: the TV refused the delete: {error}")

    still_there = tv_content_ids(tv.available())
    for content_id in plan.delete:
        if content_id in refused:
            continue

        if content_id in still_there:
            report.unconfirmed.append(content_id)
            continue

        inventory.drop(content_id)
        report.deleted.append(content_id)
