"""Command line entry point.

Most commands here are still stubs. The interface is settled, but the implementations land
with their own checkboxes in `docs/TODO.md`.
"""

from pathlib import Path

import click

from .config import DEFAULT_CONFIG_FILENAME, ConfigError, load_config
from .sources import SourceError
from .sources.google_album import GoogleAlbumSource


def _unimplemented(command: str) -> None:
    raise click.ClickException(f"`frame {command}` is not implemented yet.")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_CONFIG_FILENAME,
    show_default=True,
    help="Path to the config file. A scheduled job wants an absolute one.",
)
@click.version_option(package_name="frame-tv-art-sync")
@click.pass_context
def main(ctx: click.Context, config_path: Path) -> None:
    """Curate and control Art Mode on a Samsung Frame TV."""
    ctx.obj = config_path


@main.command()
def pair() -> None:
    """Do the first-run token handshake. Accept the prompt on the TV within 30 seconds."""
    _unimplemented("pair")


@main.command()
@click.option("--dry-run", is_flag=True, help="Print the plan without uploading or deleting.")
@click.pass_obj
def sync(config_path: Path, dry_run: bool) -> None:
    """Mirror the configured album onto the TV, deleting anything it no longer holds."""
    if not dry_run:
        _unimplemented("sync")

    try:
        config = load_config(config_path)
        items = GoogleAlbumSource(config.google_album.url).items()
    except (ConfigError, SourceError) as error:
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
def art_mode(state: str) -> None:
    """Turn art mode on or off. Off is standby, not a full power down."""
    _unimplemented("art-mode")


@main.command()
@click.argument("level", type=int)
def brightness(level: int) -> None:
    """Set the art mode brightness. The accepted range is whatever the TV reports."""
    _unimplemented("brightness")


@main.command()
@click.argument("minutes", type=click.IntRange(min=0))
@click.option("--ordered", is_flag=True, help="Play in order instead of shuffling.")
@click.option(
    "--category",
    type=click.Choice(["favourites", "photos"]),
    default="photos",
    show_default=True,
    help="Which set of images the slideshow draws from.",
)
def slideshow(minutes: int, ordered: bool, category: str) -> None:
    """Rotate the artwork every MINUTES minutes, or pass 0 to turn the slideshow off."""
    _unimplemented("slideshow")


@main.command()
def mattes() -> None:
    """List the matte types and colors this firmware offers."""
    _unimplemented("mattes")


@main.command()
@click.argument("matte_id")
@click.option("--only", "content_id", help="Apply to one image instead of the whole inventory.")
def matte(matte_id: str, content_id: str | None) -> None:
    """Apply a matte to every image in the inventory."""
    _unimplemented("matte")


@main.command()
def status() -> None:
    """Show the current artwork, the art mode state, and an inventory summary."""
    _unimplemented("status")


if __name__ == "__main__":
    main()
