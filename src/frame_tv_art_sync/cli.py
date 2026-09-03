"""Command line entry point.

`sync` and `matte` are still stubs. Everything else drives the TV through `tv.FrameTv`, one
connection per invocation, and turns a `TvError` into a `ClickException` so a failure prints a
sentence and exits non-zero rather than hanging or half-succeeding quietly.
"""

from __future__ import annotations

from collections.abc import Container, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import click

# Aliased because `mattes` is also the name of the command that prints what it knows.
from . import mattes as mattes_rules
from .config import DEFAULT_CONFIG_FILENAME, Config, ConfigError, load_config
from .inventory import InventoryError, load_inventory
from .sources import SourceError
from .sources.google_album import GoogleAlbumSource
from .sync import tv_content_ids
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
    """Say what's happening, on stderr so it can't be mistaken for output."""
    click.echo(message, err=True)


def _config(options: Options) -> Config:
    try:
        return load_config(options.config_path)
    except ConfigError as error:
        raise click.ClickException(str(error)) from None


@contextmanager
def _connected(options: Options) -> Iterator[FrameTv]:
    """Hold one art channel connection for the length of a command."""
    config = _config(options)

    try:
        with FrameTv(config, retry=options.retry, announce=_note) as tv:
            yield tv
    except TvError as error:
        raise click.ClickException(str(error)) from None


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
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
@click.version_option(package_name="frame-tv-art-sync")
@click.pass_context
def main(ctx: click.Context, config_path: Path, retry: bool) -> None:
    """Curate and control Art Mode on a Samsung Frame TV."""
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
@click.pass_obj
def sync(options: Options, dry_run: bool) -> None:
    """Mirror the configured album onto the TV, deleting anything it no longer holds."""
    if not dry_run:
        _unimplemented("sync")

    try:
        config = _config(options)
        items = GoogleAlbumSource(config.google_album.url).items()
    except SourceError as error:
        raise click.ClickException(str(error)) from None

    click.echo(f"{len(items)} photos in the album.")
    for index, item in enumerate(items):
        shape = "portrait" if item.is_portrait else "landscape"
        click.echo(
            f"  {index:2}  {item.source_id[:16]}..  {item.width:>4}x{item.height:<4}  "
            f"{shape:9}  {item.url}"
        )

    click.echo(
        "\nThis is the album read only. The three-way diff against the inventory and the TV "
        "is not wired up yet, so nothing here says what would be uploaded or deleted."
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
