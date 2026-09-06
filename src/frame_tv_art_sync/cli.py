"""Command line entry point.

`matte` is still a stub. Everything else drives the TV through `tv.FrameTv`, one connection per
invocation, and turns a `TvError` into a `ClickException` so a failure prints a sentence and
exits non-zero rather than hanging or half-succeeding quietly.

Formatting lives here and decisions live below it, so `syncer` returns what a run did and this
is the only place that says how it reads.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from collections.abc import Container, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

# Aliased because `mattes` is also the name of the command that prints what it knows.
from . import logs
from . import mattes as mattes_rules
from . import syncer
from .config import DEFAULT_CONFIG_FILENAME, Config, ConfigError, load_config
from .inventory import Inventory, InventoryError, load_inventory
from .sources import SourceError
from .sources.google_album import GoogleAlbumSource
from .sync import SyncPlan, newest_per_orientation, plan_sync, tv_content_ids
from .syncer import SyncReport
from .tv import STANDBY, FrameTv, TvError, brightness_range
from .tv import pair as pair_channels


@dataclass(frozen=True)
class Options:
    """The global options, passed down to every command."""

    config_path: Path
    retry: bool


def _unimplemented(command: str) -> None:
    raise click.ClickException(f"`frame {command}` is not implemented yet.")


def _note(message: str) -> None:
    """Say what's happening, on stderr so it can't be mistaken for output, and in the log."""
    click.echo(message, err=True)
    logs.note(message)


def _config(options: Options) -> Config:
    try:
        config = load_config(options.config_path)
    except ConfigError as error:
        raise click.ClickException(str(error)) from None

    # The log is already open by now, so this is what stops the album's share key and the TV's
    # name reaching it. The patterns cover the token and any address on their own.
    logs.note_secrets(config.tv.host, config.google_album.url, _token(config))
    return config


def _token(config: Config) -> str | None:
    try:
        return config.tv.token_file.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


@contextmanager
def _connected(options: Options) -> Iterator[FrameTv]:
    """Hold one art channel connection for the length of a command."""
    config = _config(options)

    try:
        with FrameTv(config, retry=options.retry, announce=_note) as tv:
            yield tv
    except TvError as error:
        raise click.ClickException(str(error)) from None


class LoggedGroup(click.Group):
    """Puts the reason a run failed in the log, not only on the terminal.

    Without this the log would end mid-sentence on exactly the runs it exists for, since a
    `ClickException` prints itself and never passes through anything this tool owns.
    """

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except click.ClickException as error:
            logs.note(f"Failed: {error.format_message()}")
            raise
        except Exception:
            logging.getLogger(logs.LOGGER).exception("Crashed")
            raise


@click.group(cls=LoggedGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_CONFIG_FILENAME,
    show_default=True,
    help="Path to the config file. A scheduled job wants an absolute one.",
)
@click.option(
    "--retry",
    is_flag=True,
    help=(
        "Wait and try once more if the TV doesn't answer. This is for scheduled jobs; a person "
        "at a terminal is better served by the immediate failure and re-running by hand."
    ),
)
@click.option(
    "--debug",
    is_flag=True,
    help=(
        "Also print the TV's protocol frames as they arrive. They are in the log either way; "
        "this is for watching a run live."
    ),
)
@click.version_option(package_name="frame-tv-art-sync")
@click.pass_context
def main(ctx: click.Context, config_path: Path, retry: bool, debug: bool) -> None:
    """Curate and control Art Mode on a Samsung Frame TV.

    Every run is logged to `frame.log` beside the config, tokens and addresses masked.
    """
    # Before anything else, so a failure reading the config is in the log too. The path is
    # taken from the config's own directory rather than the config, which hasn't been read yet.
    logs.start(config_path.parent / logs.LOG_FILENAME, debug=debug)
    logs.note(f"frame {' '.join(sys.argv[1:])}")

    ctx.obj = Options(config_path=config_path, retry=retry)


@main.command()
@click.pass_obj
def pair(options: Options) -> None:
    """Do the first-run handshake, accepting a prompt on the TV for each of the two channels.

    Each prompt has to be accepted within about 30 seconds, after which the TV times the
    connection out itself. Expect a prompt again whenever this machine's address changes.
    """
    config = _config(options)

    _note(
        "Turn Access Notification on first, under Settings > General > External Device Manager "
        "> Device Connect Manager, or pairing fails silently."
    )

    try:
        pair_channels(config, _note)
    except TvError as error:
        raise click.ClickException(str(error)) from None

    click.echo("Both channels are paired.")


@main.command()
@click.option("--dry-run", is_flag=True, help="Print the plan without uploading or deleting.")
@click.option(
    "--first-run",
    is_flag=True,
    help=(
        "Sync even though there is no inventory and the TV already holds images. Say this only "
        "when none of them came from this tool, because it is what stops a lost inventory from "
        "uploading the album a second time."
    ),
)
@click.pass_obj
def sync(options: Options, dry_run: bool, first_run: bool) -> None:
    """Mirror the configured album onto the TV, deleting anything it no longer holds."""
    config = _config(options)
    source = GoogleAlbumSource(config.google_album.url)

    try:
        inventory = load_inventory(config.inventory_file)
    except InventoryError as error:
        raise click.ClickException(str(error)) from None

    try:
        items = source.items()
    except SourceError as error:
        raise click.ClickException(str(error)) from None

    # The Google source refuses an empty page itself, so this is here for every source after
    # it. Mirroring nothing means deleting everything, and no source can tell an album somebody
    # emptied from one it failed to read.
    if not items:
        raise click.ClickException(
            f"`{source.name}` returned no photos at all. Syncing that would delete everything "
            "this tool has uploaded, so it is refused. If the source really is empty, delete "
            "the images from the TV instead."
        )

    _note(f"{len(items)} photos in the album.")

    if config.sync.short_run:
        items = newest_per_orientation(items, config.sync.short_run)
        _note(
            f"`sync.short_run` is {config.sync.short_run}, so this run covers the newest "
            f"{config.sync.short_run} photos of each orientation and mirrors the album down to "
            f"the {len(items)} of them. Everything else this tool uploaded is a delete."
        )

    if dry_run:
        with _connected(options) as tv:
            rows = tv.available()

        _report_plan(
            plan_sync(source.name, items, inventory, rows), inventory, rows, config, first_run
        )
        return

    # Every photo is fetched and prepared before the channel is opened, because the channel
    # closes itself after about 25 seconds of silence and nothing reopens it. A run that is
    # about to be refused below downloads them for nothing, which costs bandwidth and changes
    # nothing on the TV, and that only happens when the inventory has been lost.
    pending = syncer.provisional_uploads(source.name, items, inventory)
    with tempfile.TemporaryDirectory(prefix="frame-sync-") as directory:
        spooled, failed = syncer.prefetch(
            pending, Path(directory), config=config, announce=_note
        )

        with _connected(options) as tv:
            rows = tv.available()
            _refuse_a_lost_inventory(inventory, rows, first_run)

            try:
                report = syncer.run(
                    plan_sync(source.name, items, inventory, rows),
                    source=source.name,
                    tv=tv,
                    inventory=inventory,
                    config=config,
                    spooled=spooled,
                    failed=failed,
                    announce=_note,
                )
            except syncer.SyncAborted as aborted:
                # What it managed before the channel died is the more useful half, and it is
                # what says whether re-running picks up where this left off.
                _report_run(aborted.report)
                raise click.ClickException(
                    f"{aborted}\n\nThe inventory holds everything that did upload, so "
                    "re-running takes it from there rather than starting over."
                ) from None

    _report_run(report)

    total = len(report.failures) + len(report.unconfirmed)
    if total:
        raise click.ClickException(f"{total} photos did not sync. The rest did.")


def _refuse_a_lost_inventory(
    inventory: Inventory, rows: list[dict[str, object]], first_run: bool
) -> None:
    """Stop before anything is written when the inventory is gone and the TV is not empty."""
    stranded = syncer.lost_inventory_ids(inventory, rows)
    if not stranded or first_run:
        return

    raise click.ClickException(
        f"There is no inventory file, but the TV already holds {len(stranded)} images: "
        f"{', '.join(stranded)}. Without the inventory none of them can be attributed to this "
        "tool, so syncing would upload the album a second time and leave those copies on the "
        "TV forever. Restore the inventory, or pass `--first-run` if none of them are this "
        "tool's."
    )


def _report_plan(
    plan: SyncPlan,
    inventory: Inventory,
    rows: list[dict[str, object]],
    config: Config,
    first_run: bool,
) -> None:
    """Say what a real run would do, in enough detail to be worth reading before one."""
    click.echo(f"Upload      {len(plan.upload)}")
    for item in plan.upload:
        matte_id = mattes_rules.matte_for(
            item.width, item.height, config.art.landscape_matte, config.art.portrait_matte
        )
        shape = "portrait" if item.is_portrait else "landscape"
        click.echo(
            f"  {item.source_id[:20]:<20}  {item.width:>4}x{item.height:<4}  {shape:<9}  "
            f"{matte_id}"
        )

    click.echo(f"Delete      {len(plan.delete)}")
    for entry in plan.delete:
        click.echo(f"  {entry.content_id:<20}  uploaded {entry.uploaded_at}")

    click.echo(f"Unchanged   {len(plan.keep)}")
    click.echo(f"Orphaned    {len(plan.orphaned)}, deleted from the TV by hand")
    click.echo(f"Left alone  {len(plan.unmanaged)}, not this tool's")

    stranded = syncer.lost_inventory_ids(inventory, rows)
    if stranded and not first_run:
        click.echo(
            f"\nThere is no inventory file and the TV holds {len(stranded)} images, so a real "
            f"run would refuse rather than upload the album a second time: "
            f"{', '.join(stranded)}. `--first-run` is what says none of them are this tool's."
        )

    click.echo("\nNothing was changed. Drop `--dry-run` to do it.")


def _report_run(report: SyncReport) -> None:
    """Say what the run did. It prints for an aborted run too, so it never decides the exit."""
    click.echo(
        f"Uploaded {len(report.uploaded)} of {report.planned_uploads}, "
        f"deleted {len(report.deleted)} of {report.planned_deletes}, "
        f"{report.kept} unchanged."
    )
    if report.dropped:
        click.echo(f"Dropped {len(report.dropped)} entries whose image was gone from the TV.")

    if report.upload_seconds:
        click.echo(f"Upload times  {_timing(report.upload_seconds)}")

    for content_id in report.unconfirmed:
        click.echo(
            f"The TV still lists {content_id} after deleting it, so its entry was kept and the "
            "next run will try again.",
            err=True,
        )

    for failure in report.failures:
        click.echo(f"Skipped {failure}", err=True)

    if report.in_flight:
        click.echo(
            f"\n{report.in_flight} was mid-upload when the connection died, and it may be on "
            "the TV anyway: the bytes go out over a socket of their own and only the "
            "confirmation comes back on the channel. An upload that landed without being "
            "confirmed has no inventory entry, so nothing here will ever delete it. `frame "
            "status` lists anything on the TV the inventory doesn't claim, and the TV's own "
            "picker is where to remove it.",
            err=True,
        )


def _timing(seconds: list[float]) -> str:
    """Fastest, median, slowest, and the last ten, which is where a slowdown shows first."""
    ordered = sorted(seconds)
    median = ordered[len(ordered) // 2]
    recent = seconds[-10:]

    return (
        f"{ordered[0]:.1f}s fastest, {median:.1f}s median, {ordered[-1]:.1f}s slowest, "
        f"{sum(recent) / len(recent):.1f}s over the last {len(recent)}"
    )


@main.command("art-mode")
@click.argument("state", type=click.Choice(["on", "off"]))
@click.pass_obj
def art_mode(options: Options, state: str) -> None:
    """Turn art mode on or off. Off leaves the panel lit, showing the TV's own UI."""
    with _connected(options) as tv:
        if state == "on":
            woken = tv.wake()
            click.echo("Art mode is on." if not woken else "Art mode is on, panel woken.")
            return

        tv.set_art_mode(False)

    click.echo(
        "Art mode is off. That drops the TV to its last input rather than darkening the panel, "
        "because no call on this API darkens it."
    )


@main.command()
@click.argument("level", type=int)
@click.pass_obj
def brightness(options: Options, level: int) -> None:
    """Set the art mode brightness, within the range the TV reports."""
    with _connected(options) as tv:
        low, high = brightness_range(tv.artmode_settings("brightness"))
        if not low <= level <= high:
            raise click.ClickException(f"This TV takes a brightness from {low} to {high}.")

        tv.set_brightness(level)
        click.echo(f"Brightness is {tv.brightness()}.")


@main.command()
@click.argument("minutes", type=click.IntRange(min=0))
@click.pass_obj
def slideshow(options: Options, minutes: int) -> None:
    """Turn the slideshow off, which is all this firmware will do with one.

    Pass 0. Every non-zero duration is refused by the TV itself, whatever the category, shuffle
    flag, or interval, so there is no way to start one from here.
    """
    if minutes:
        raise click.ClickException(
            "This firmware refuses to start a slideshow over the API, so only `frame slideshow "
            "0` does anything. `docs/spikes.md` T5 has the nine variants that were tried."
        )

    with _connected(options) as tv:
        tv.slideshow_off()

    click.echo("The slideshow is off.")


@main.command()
@click.pass_obj
def mattes(options: Options) -> None:
    """List the matte types the TV offers for each orientation, and every color."""
    with _connected(options) as tv:
        reported_types, colors = tv.matte_list()

    for orientation in (mattes_rules.PORTRAIT, mattes_rules.LANDSCAPE):
        offered = mattes_rules.offered_for(orientation)
        click.echo(f"{orientation.capitalize():<10}({len(offered)}): {', '.join(offered)}")

    unoffered = sorted(mattes_rules.UNOFFERED_TYPES)
    click.echo(f"{'Neither':<10}({len(unoffered)}): {', '.join(unoffered)}")

    click.echo(f"\nColors ({len(colors)})")
    for color in colors:
        red, green, blue = color.rgb
        click.echo(f"  {color.name:<12} {red:>3},{green:>3},{blue:>3}")

    click.echo(
        "\nA matte id joins a type and a color, as in `flexible_black`, and `flexible` is the "
        "one type that crops nothing whatever the image's shape. Those lists are what the TV's "
        "own picker offers, not what the API accepts: the API is more permissive and will take "
        "a type the picker withholds, which is how `modernwide` on a portrait crashed Art Mode."
    )

    # The picker can't be read over the API, so `mattes.py` records what it offers rather than
    # fetching it. A firmware that has gained a type or a color is worth hearing about instead
    # of being silently held to a list written against an older one.
    _report_unrecorded("types", reported_types, mattes_rules.every_known_type())
    _report_unrecorded("colors", [color.name for color in colors], mattes_rules.COLORS)


def _report_unrecorded(kind: str, reported: list[str], recorded: Container[str]) -> None:
    unrecorded = sorted(name for name in reported if name not in recorded)
    if not unrecorded:
        return

    click.echo(
        f"\nThis TV reports {kind} that `mattes.py` has no record of: {', '.join(unrecorded)}. "
        "Nothing will send one until it's added there, and for a type that means finding out "
        "which orientations the TV's own picker offers it for, the way `docs/spikes.md` T15 did."
    )


@main.command()
@click.argument("matte_id")
@click.option("--only", "content_id", help="Apply to one image instead of the whole inventory.")
def matte(matte_id: str, content_id: str | None) -> None:
    """Apply a matte to every image in the inventory."""
    _unimplemented("matte")


@main.command()
@click.pass_obj
def status(options: Options) -> None:
    """Show the panel state, the current artwork, and an inventory summary."""
    config = _config(options)

    try:
        inventory = load_inventory(config.inventory_file)
    except InventoryError as error:
        raise click.ClickException(str(error)) from None

    with _connected(options) as tv:
        power = tv.power_state()
        mode = tv.art_mode()
        level = tv.brightness()
        current = tv.current()
        rows = tv.available()

    on_tv = tv_content_ids(rows)
    mine = {entry.content_id for entry in inventory}

    click.echo(f"Panel       {'lit' if power != STANDBY else 'dark'} (PowerState {power})")
    click.echo(f"Art mode    {mode}")
    click.echo(f"Brightness  {level}")
    click.echo(
        f"Showing     {current.get('content_id', 'unknown')} "
        f"({current.get('content_type', 'unknown type')})"
    )
    click.echo(f"On the TV   {len(on_tv)} images, {len(rows)} rows before deduping")
    click.echo(f"Inventory   {len(inventory)} entries, {len(mine & on_tv)} of them still on the TV")

    if not inventory.existed:
        click.echo(
            f"\nThere is no inventory at {config.inventory_file}, so nothing on the TV is "
            "attributed to this tool yet and a sync would upload the whole album."
        )

    unmanaged = sorted(on_tv - mine)
    if unmanaged:
        click.echo(f"\nNot this tool's, and left alone: {', '.join(unmanaged)}")


if __name__ == "__main__":
    main()
