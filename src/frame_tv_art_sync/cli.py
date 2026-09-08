"""Command line entry point.

Every command drives the TV through `tv.FrameTv`, one connection per invocation, and turns a
`TvError` into a `ClickException` so a failure prints a sentence and exits non-zero rather than
hanging or half-succeeding quietly.

There is no command for applying a matte. A matte can only be set at upload time, so changing
one means uploading the photo again, which is a thing `frame sync` already knows how to do:
edit `config.toml` and sync, and every photo whose rendering no longer matches is replaced.

Formatting lives here and decisions live below it, so `syncer` returns what a run did and this
is the only place that says how it reads.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from collections import Counter
from collections.abc import Container, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

# Aliased because `mattes` is also the name of the command that prints what it knows.
from . import bakeoff as bakeoff_rounds
from . import composite
from . import crop
from . import logs
from . import mattes as mattes_rules
from . import syncer
from .config import DEFAULT_CONFIG_FILENAME, Config, ConfigError, load_config
from .inventory import Inventory, InventoryError, load_inventory
from .pipeline import PreparedImage, label_center, prepare
from .render import RenderSettings, describe_change
from .sources import SourceError, SourceItem
from .sources.google_album import GoogleAlbumSource
from .sync import SyncPlan, newest_per_orientation, plan_sync, tv_content_ids
from .syncer import SyncReport
from .tv import STANDBY, FrameTv, TvError, brightness_range
from .tv import pair as pair_channels


@dataclass(frozen=True)
class Options:
    """The global options, passed down to every command."""

    config_path: Path


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
        with FrameTv(config, announce=_note) as tv:
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
    "--debug",
    is_flag=True,
    help=(
        "Also print the TV's protocol frames as they arrive. They are in the log either way; "
        "this is for watching a run live."
    ),
)
@click.version_option(package_name="frame-tv-art-sync")
@click.pass_context
def main(ctx: click.Context, config_path: Path, debug: bool) -> None:
    """Curate and control Art Mode on a Samsung Frame TV.

    Every run is logged to `frame.log` beside the config, tokens and addresses masked.
    """
    # Before anything else, so a failure reading the config is in the log too. The path is
    # taken from the config's own directory rather than the config, which hasn't been read yet.
    logs.start(config_path.parent / logs.LOG_FILENAME, debug=debug)
    logs.note(f"frame {' '.join(sys.argv[1:])}")

    ctx.obj = Options(config_path=config_path)


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
@click.option(
    "--label",
    "label_crop",
    is_flag=True,
    help=(
        "Burn the crop that fired, and enough of the photo's id to name it, into the middle of "
        "each image. For judging crop rules off the panel; re-run without it once they are "
        "settled, since the label is part of the JPEG and nothing takes it off in place."
    ),
)
@click.pass_obj
def sync(options: Options, dry_run: bool, first_run: bool, label_crop: bool) -> None:
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

    # After the narrowing, because a short run is the case these rules are usually being tried
    # out under, and counting the whole album would describe photos this run never touches.
    _report_crop(config, items, label_crop)

    if config.sync.delete_added_by_hand:
        _note(
            "`sync.delete_added_by_hand` is on, so this run may delete images this tool did not "
            "upload. Samsung's own art is never touched."
        )
    if not config.sync.delete_removed_from_album:
        _note(
            "`sync.delete_removed_from_album` is off, so a photo that has left the album keeps "
            "its place on the TV."
        )

    if dry_run:
        with _connected(options) as tv:
            rows = tv.available()

        _report_plan(
            _plan(source.name, items, inventory, rows, config, label_crop),
            inventory,
            rows,
            config,
            first_run,
            label_crop,
        )
        return

    # Everything that can refuse the run happens before a photo is downloaded, because
    # preparing the album fetches all of them and a run that dies afterwards spent that
    # bandwidth for nothing. A TV that is off, on another subnet, or wedged is the common case,
    # and a lost inventory is the other. This cannot promise that an upload will finish -- one
    # has failed 96 into a run with the channel perfectly healthy -- so what it establishes is
    # that the handshake completes and the TV answers a request.
    _note("Checking the TV before preparing anything.")
    with _connected(options) as tv:
        _refuse_a_lost_inventory(inventory, tv.available(), first_run, config)

    # Every photo is then fetched and prepared before the uploading channel is opened, because
    # that channel closes itself after about 25 seconds of silence and a live download between
    # two uploads would eventually outlast it.
    pending = syncer.provisional_uploads(
        source.name, _groups(items, config), inventory, _render(config, label_crop)
    )
    with tempfile.TemporaryDirectory(prefix="frame-sync-") as directory:
        spooled, failed = syncer.prefetch(
            pending, Path(directory), config=config, announce=_note, label_crop=label_crop
        )

        with _connected(options) as tv:
            # Read again rather than carried over from the check, because minutes of preparing
            # sit in between and the plan has to be built on what the TV holds now.
            rows = tv.available()

            try:
                report = syncer.run(
                    _plan(source.name, items, inventory, rows, config, label_crop),
                    source=source.name,
                    tv=tv,
                    inventory=inventory,
                    config=config,
                    render=_render(config, label_crop),
                    spooled=spooled,
                    failed=failed,
                    announce=_note,
                    label_crop=label_crop,
                )
            except syncer.SyncAborted as aborted:
                # What it managed before the channel died is the more useful half, and it is
                # what says whether re-running picks up where this left off.
                _report_run(aborted.report, config)
                raise click.ClickException(
                    f"{aborted}\n\nThe inventory holds everything that did upload, so "
                    "re-running takes it from there rather than starting over."
                ) from None

    _report_run(report, config)

    total = len(report.failures) + len(report.unconfirmed)
    if total:
        raise click.ClickException(f"{total} photos did not sync. The rest did.")


def _crop_for(item: SourceItem, config: Config) -> crop.Crop:
    """The crop one album item gets, from the shape its source reported.

    `syncer` resolves the same thing the same way, so a dry run and the run it predicts never
    disagree about which rule fired.
    """
    return crop.resolve(
        item.width,
        item.height,
        item.source_id,
        config.pipeline.crop,
        config.pipeline.crop_overrides,
    )


def _report_crop(config: Config, items: list[SourceItem], label_crop: bool) -> None:
    """Say what the crop rules will do to this run, and name the overrides that won't fire.

    Whether an override key names one photo, several, or none depends on what the source
    listed, so it can only be answered here rather than when the config loads. Both cases are
    a note rather than a refusal: the run is still correct, it is the config that is stale.
    """
    if not config.pipeline.crop and not config.pipeline.crop_overrides:
        return

    counts = Counter(_crop_for(item, config).label for item in items)

    _note("Crop rules for this run:")
    for label, count in counts.most_common():
        _note(f"  {count:>4}  {label}")

    source_ids = [item.source_id for item in items]
    for key in crop.unmatched_overrides(source_ids, config.pipeline.crop_overrides):
        _note(f"`pipeline.crop_overrides` has `{key}`, which names no photo in this run.")
    for key in crop.ambiguous_overrides(source_ids, config.pipeline.crop_overrides):
        _note(
            f"`pipeline.crop_overrides` has `{key}`, which names more than one photo in this "
            "run. Lengthen it, or it crops all of them the same way."
        )

    if label_crop:
        _note(
            "`--label` is on, so each photo goes up with its crop and id drawn across it. "
            "Re-run without it once the rules are settled."
        )


def _plan(
    source: str,
    items: list[SourceItem],
    inventory: Inventory,
    rows: list[dict[str, object]],
    config: Config,
    label_crop: bool = False,
) -> SyncPlan:
    """The plan for this run, with the config's two delete flags and its rendering applied.

    Both call sites go through here so that a dry run and the run it is predicting can never
    disagree about what a sync is allowed to destroy.
    """
    return plan_sync(
        source,
        _groups(items, config),
        inventory,
        rows,
        render=_render(config, label_crop),
        delete_removed_from_album=config.sync.delete_removed_from_album,
        delete_added_by_hand=config.sync.delete_added_by_hand,
    )


def _render(config: Config, labelled: bool = False) -> RenderSettings:
    """How config says a photo should look, gathered from the two tables that decide it.

    The diff, the spool and the upload all read this rather than the config directly, so that
    what a run decides is stale and what it then produces can't drift apart. The crop rules are
    in here for the same reason: a photo's matte depends on the shape a crop leaves it, and the
    record has to say which crop that was or editing a rule would change nothing already up.
    """
    return RenderSettings(
        matte_by_ratio=config.art.matte_by_ratio,
        fallback_matte=config.art.fallback_matte,
        highlight_rolloff=config.pipeline.highlight_rolloff,
        jpeg_quality=config.pipeline.jpeg_quality,
        crop=config.pipeline.crop,
        crop_overrides=config.pipeline.crop_overrides,
        composite_style=config.pipeline.composite_style,
        labelled=labelled,
    )


def _groups(items: list[SourceItem], config: Config) -> list[composite.Group]:
    """Which photos share an image, which every part of a run has to agree about.

    One call site, for the same reason `_plan` has one: the spool, the diff and the upload
    deciding differently would leave an image replaced on every run forever.
    """
    return composite.plan_groups(
        items,
        config.pipeline.composite,
        crop=config.pipeline.crop,
        crop_overrides=config.pipeline.crop_overrides,
    )


def _refuse_a_lost_inventory(
    inventory: Inventory, rows: list[dict[str, object]], first_run: bool, config: Config
) -> None:
    """Stop before anything is written when the inventory is gone and the TV is not empty."""
    stranded = syncer.lost_inventory_ids(inventory, rows)
    if not stranded or first_run:
        return

    raise click.ClickException(
        f"There is no inventory file, but the TV already holds {len(stranded)} images: "
        f"{', '.join(stranded)}. Without the inventory none of them can be attributed to this "
        f"tool, so syncing would upload the album a second time and {_lost_inventory_cost(config)}"
        " Restore the inventory, or pass `--first-run` if none of them are this tool's."
    )


def _lost_inventory_cost(config: Config) -> str:
    """What a lost inventory costs, which `sync.delete_added_by_hand` changes from one to the other.

    With the flag off the copies survive unattributed. With it on they are exactly what the flag
    reaches, so the same run that duplicates the album also destroys the images it duplicated,
    and saying "left on the TV" there would send somebody past the one warning that mattered.
    """
    if config.sync.delete_added_by_hand:
        return (
            "then delete those copies, because `sync.delete_added_by_hand` is on and nothing "
            "would attribute them to this tool."
        )

    return (
        "leave those copies on the TV, reachable afterward only from its own picker or by "
        "turning on `sync.delete_added_by_hand`."
    )


def _report_fallbacks(counts: Counter[str], config: Config) -> None:
    """Name every shape that took the fallback, and what to write to stop it.

    Silence when nothing fell back is the point of printing this at all: a line here means a
    mat nobody chose is going on the wall, and the count says how much of the album it covers.
    """
    if not counts:
        return

    click.echo(
        f"\nFallback    {sum(counts.values())} images in `{config.art.fallback_matte}`, "
        "because `[art.matte_by_ratio]` doesn't name their shape:"
    )
    for shape, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
        click.echo(f'  {count:>4}  at {shape:<7}  add `"{shape}" = "..."` to choose one')


def _report_plan(
    plan: SyncPlan,
    inventory: Inventory,
    rows: list[dict[str, object]],
    config: Config,
    first_run: bool,
    label_crop: bool = False,
) -> None:
    """Say what a real run would do, in enough detail to be worth reading before one."""
    render = _render(config, label_crop)
    fell_back: Counter[str] = Counter()

    # A replaced photo is in `plan.upload` as well as in `plan.superseded`, since the upload is
    # how a matte gets set. Splitting them here rather than printing it under both is what makes
    # the two counts add up to what a reader sees on the wall afterward.
    replacing = {entry.source_ids: entry for entry in plan.superseded}
    fresh = [group for group in plan.upload if group.source_ids not in replacing]

    click.echo(f"Upload      {len(fresh)} images, {sum(len(group.items) for group in fresh)} photos")
    for group in fresh:
        click.echo(f"  {_upload_line(group, render, fell_back)}")

    click.echo(f"Replace     {len(plan.superseded)}, already up but rendered differently")
    for group in plan.upload:
        entry = replacing.get(group.source_ids)
        if entry is None:
            continue

        change = describe_change(entry.render, render.for_group(group))
        click.echo(f"  {_upload_line(group, render, fell_back)}  {change}")

    click.echo(f"Delete      {len(plan.delete)}")
    for entry in plan.delete:
        click.echo(f"  {entry.content_id:<20}  uploaded {entry.uploaded_at}")

    # Its own label rather than a second `Delete` line, because this is the count somebody
    # checks hardest before a destructive run and two lines differing only in a trailing clause
    # are read as one. Printed at zero as well, or its absence reads as the flag being safe.
    click.echo(f"Purge       {len(plan.delete_unmanaged)}, not this tool's")
    for content_id in plan.delete_unmanaged:
        click.echo(f"  {content_id}")

    _report_fallbacks(fell_back, config)

    click.echo(f"Unchanged   {len(plan.keep)}")
    click.echo(f"Orphaned    {len(plan.orphaned)}, deleted from the TV by hand")
    click.echo(f"Kept        {len(plan.left_in_place)}, no longer in the album")
    click.echo(f"Left alone  {len(plan.unmanaged)}, not this tool's")

    stranded = syncer.lost_inventory_ids(inventory, rows)
    if stranded and not first_run:
        click.echo(
            f"\nThere is no inventory file and the TV holds {len(stranded)} images, so a real "
            f"run would refuse rather than upload the album a second time and "
            f"{_lost_inventory_cost(config)} The images are {', '.join(stranded)}, and "
            "`--first-run` is what says none of them are this tool's."
        )

    click.echo("\nNothing was changed. Drop `--dry-run` to do it.")


def _upload_line(
    group: composite.Group, render: RenderSettings, fell_back: Counter[str]
) -> str:
    """One image about to go up: which photos it holds, their shape, and how it is finished.

    The shape is the one after the crop rather than before it, because that is what the panel
    is handed and what decides both the grouping and the matte. A rule can reshape a photo, and
    the TV draws different types around different shapes.

    Everything comes off `render`, which is the same object the run itself uses, so a dry run
    cannot name one matte and the run apply another. `fell_back` is tallied as a side effect
    rather than returned, because the caller wants one line per image and one count at the end.
    """
    width, height = render.resolved_size(group.items[0])

    # A line naming one photo has room to name it usefully, which is what an override key is
    # copied out of. Several have to share the column, so they get the shorter form.
    shown = 20 if len(group.source_ids) == 1 else crop.LABEL_ID_CHARS
    names = "+".join(source_id[:shown] for source_id in group.source_ids)

    if not group.is_full:
        return f"{names:<28}  {width:>4}x{height:<4}  {group.name}"

    choice = render.matte_choice(group.items[0])
    shape = mattes_rules.ratio_name(choice.ratio)
    if choice.fell_back:
        fell_back[shape] += 1

    return (
        f"{names:<28}  {width:>4}x{height:<4}  {shape:<7}  "
        f"{choice.matte_id}{'  (fallback)' if choice.fell_back else ''}"
    )


def _report_run(report: SyncReport, config: Config) -> None:
    """Say what the run did. It prints for an aborted run too, so it never decides the exit."""
    click.echo(
        f"Uploaded {len(report.uploaded)} of {report.planned_uploads}, "
        f"deleted {len(report.deleted)} of {report.planned_deletes}, "
        f"{report.kept} unchanged."
    )
    if report.planned_supersedes:
        click.echo(
            f"Replaced {len(report.superseded)} of {report.planned_supersedes} photos whose "
            "rendering had changed, taking the old copy down after the new one went up."
        )

    if report.planned_unmanaged_deletes:
        click.echo(
            f"Deleted {len(report.deleted_unmanaged)} of {report.planned_unmanaged_deletes} "
            "images this tool did not upload."
        )

    if report.dropped:
        click.echo(f"Dropped {len(report.dropped)} entries whose image was gone from the TV.")

    _report_fallbacks(report.fell_back, config)

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
    """List the matte types the TV will draw around each image shape, and every color."""
    with _connected(options) as tv:
        reported_types, colors = tv.matte_list()

    any_shape = sorted(mattes_rules.ACCEPTED_ON_ANY_SHAPE)
    click.echo(f"{'Any shape':<12}({len(any_shape)}): {', '.join(any_shape)}")

    fixed = sorted(mattes_rules.FIXED_APERTURE_TYPES)
    widescreen = mattes_rules.ratio_name(mattes_rules.FIXED_APERTURE_RATIO)
    click.echo(f"{widescreen + ' only':<12}({len(fixed)}): {', '.join(fixed)}")

    unoffered = sorted(mattes_rules.UNOFFERED_TYPES)
    click.echo(f"{'No shape':<12}({len(unoffered)}): {', '.join(unoffered)}")

    click.echo(f"\nColors ({len(colors)})")
    for color in colors:
        red, green, blue = color.rgb
        click.echo(f"  {color.name:<12} {red:>3},{green:>3},{blue:>3}")

    click.echo(
        f"\nA matte id joins a type and a color, as in `flexible_black`. What splits the types "
        f"is the aperture: the {widescreen}-only three are cut to a fixed shape, and `flexible` "
        "and `shadowbox` take the image's own, so they crop nothing whatever it is. `none` "
        "draws no mat and lets the panel center-crop to fill.\n\n"
        "Those lists are what the TV will draw, not what the API accepts. The API is more "
        f"permissive and takes a type it can't render, which is how a fixed-aperture matte on a "
        "4:3 photo crashed Art Mode and needed a power cycle."
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
        "which image shapes the TV will draw it around, the way `docs/spikes.md` T15 and T22 "
        "did."
    )


@main.command()
@click.option(
    "--compare",
    type=click.Choice([bakeoff_rounds.COMPARE_COLORS, bakeoff_rounds.COMPARE_TYPES]),
    help="What varies from one upload to the next. Everything else is held still.",
)
@click.option(
    "--orientation",
    type=click.Choice([mattes_rules.LANDSCAPE, mattes_rules.PORTRAIT]),
    help=(
        "Narrow the round to one orientation. A round covers every shape the album holds "
        "otherwise, which is what you want, since the TV draws different types around each."
    ),
)
@click.option(
    "--color",
    help="The color to draw each type in. `--compare=types` needs it, nothing else reads it.",
)
@click.option("--clear", "clear_only", is_flag=True, help="Empty the TV and upload nothing.")
@click.option("--dry-run", is_flag=True, help="Print the plan without deleting or uploading.")
@click.option("--yes", is_flag=True, help="Delete without asking first.")
@click.pass_obj
def bakeoff(
    options: Options,
    compare: str | None,
    orientation: str | None,
    color: str | None,
    clear_only: bool,
    dry_run: bool,
    yes: bool,
) -> None:
    """Put a photo on the wall once per matte, to choose a mat by looking at it.

    A round covers every shape the album holds, one photo per shape and the newest of each, and
    that is not thoroughness for its own sake: three of the six types draw a fixed 16:9 aperture
    and the TV refuses them on any other shape, so a mat chosen on a 4:3 may not exist for the
    16:9 beside it. `--orientation` narrows a round when that isn't what you want.

    Each upload carries the name of what varies and its own shape drawn across the middle,
    because the TV's picker shows thumbnails and no names.

    A round empties the TV first, and that delete reaches uploads no inventory claims, which is
    the one thing here that touches an image this tool didn't put up. Samsung's own art is never
    a candidate. Restoring the album afterwards is a plain `frame sync`, and `--clear` on its
    own is what takes the last round's variants down before that.

    `[art.matte_by_ratio]`, `art.fallback_matte`, `sync.short_run` and both delete flags are
    ignored while this runs, since a round settles all of them itself.
    """
    config = _config(options)
    _check_round(compare, orientation, color, clear_only)

    try:
        inventory = load_inventory(config.inventory_file)
    except InventoryError as error:
        raise click.ClickException(str(error)) from None

    # Every image is rendered before the channel is opened, because the channel closes itself
    # after about 25 seconds of silence. Which mattes a round covers isn't known until the TV
    # has been asked, but every text one could burn is, and that text is all a label needs.
    photos = [] if clear_only else _round_photos(config, orientation)
    labels: dict[tuple[str, str], PreparedImage] = {}
    for photo in photos:
        base = _round_image(photo, config)
        candidates = bakeoff_rounds.candidate_labels(str(compare), photo, config.bakeoff)
        for text, image in _round_labels(base, config, candidates).items():
            labels[(photo.source_id, text)] = image

    with _connected(options) as tv:
        clear = bakeoff_rounds.plan_clear(tv.available(), inventory)

        chosen: list[bakeoff_rounds.Variant] = []
        if not clear_only:
            _, colors = tv.matte_list()
            try:
                chosen = bakeoff_rounds.plan_round(
                    str(compare),
                    photos,
                    color=color,
                    color_order=bakeoff_rounds.by_luminance(colors),
                    allowed=config.bakeoff,
                )
            except (mattes_rules.MatteError, ValueError) as error:
                raise click.ClickException(str(error)) from None

        if dry_run:
            _report_round_plan(clear, photos, chosen)
            return

        _confirm_clear(clear, yes)

        try:
            report = bakeoff_rounds.carry_out(
                clear,
                [(variant, labels[(variant.source_id, variant.label)]) for variant in chosen],
                tv=tv,
                inventory=inventory,
                config=config,
                announce=_note,
            )
        except bakeoff_rounds.BakeoffAborted as aborted:
            # The roster is the whole point of a round, and the variants that did go up are on
            # the wall whether or not the rest did.
            _report_round_run(aborted.report)
            raise click.ClickException(str(aborted)) from None

    _report_round_run(report)

    total = len(report.failures) + len(report.unconfirmed)
    if total:
        raise click.ClickException(f"{total} images did not do what they were told.")


def _check_round(
    compare: str | None, orientation: str | None, color: str | None, clear_only: bool
) -> None:
    """Refuse a combination of options that doesn't describe a round.

    All of it happens before the album is read and before the TV is reached, so a round named
    wrongly costs neither a download nor a connection.
    """
    if clear_only and (compare or orientation):
        raise click.ClickException(
            "`--clear` empties the TV and uploads nothing, so it takes neither `--compare` nor "
            "`--orientation`."
        )

    if not clear_only and not compare:
        raise click.ClickException(
            "A round needs `--compare`, as in `frame bakeoff --compare=colors`. `--orientation` "
            "is optional and narrows it to one. `--clear` on its own empties the TV."
        )

    if compare != bakeoff_rounds.COMPARE_TYPES:
        return

    if not color:
        raise click.ClickException(
            "Comparing types needs `--color` too, since a type can only be judged with the mat "
            "colored some particular way. Pass the one the color round settled on."
        )

    # Through a type the TV draws around any shape, so what this actually checks is the color.
    try:
        mattes_rules.validate_for_ratio(
            f"{bakeoff_rounds.COLOR_ROUND_TYPE}_{color}", mattes_rules.FIXED_APERTURE_RATIO
        )
    except mattes_rules.MatteError as error:
        raise click.ClickException(str(error)) from None


def _round_photos(config: Config, orientation: str | None) -> list[SourceItem]:
    """The newest photo of each shape the album holds, which is what a round compares."""
    source = GoogleAlbumSource(config.google_album.url)

    try:
        items = source.items()
    except SourceError as error:
        raise click.ClickException(str(error)) from None

    photos = bakeoff_rounds.newest_per_shape(items, orientation=orientation)
    if not photos:
        held = f"is a {orientation}" if orientation else "can be read"
        raise click.ClickException(
            f"None of the {len(items)} photos in the album {held}, so there is nothing to test "
            "a mat against."
        )

    _note(f"Comparing {len(photos)} shapes out of {len(items)} photos:")
    for photo in photos:
        shape = mattes_rules.shape_of(photo.width, photo.height)
        _note(f"  {shape:<7}  {photo.source_id[:20]:<20}  {photo.width}x{photo.height}")

    # A round holds everything but the mat still, and a crop is the one thing that would change
    # what sits under it. It is also what would make a color round unreadable: a landscape
    # cropped to the panel's own shape leaves `flexible` almost no mat to judge the color on.
    # Ignoring crops is what makes these shapes the album's own rather than the config's.
    if config.pipeline.crop or config.pipeline.crop_overrides:
        _note(
            "Crop rules are ignored for a bakeoff, so every variant here is the whole photo "
            "and only the mat differs. `frame sync --label` is where crops get judged."
        )

    # Same reasoning, and the same reason to say so: a round is about the mat the TV draws, and
    # a composite paints its own and goes up under `none`, so there would be no mat to judge.
    if config.pipeline.composite:
        _note(
            "`[[pipeline.composite]]` is ignored for a bakeoff, so every variant here is one "
            "photo under a matte the TV draws."
        )

    return photos


def _round_image(photo: SourceItem, config: Config) -> PreparedImage:
    """One shape's photo, prepared once. Its variants are this image with different text on it."""
    try:
        data = syncer.fetch_image(photo.url)
    except syncer.ImageUnusable as error:
        raise click.ClickException(f"{photo.source_id}: {error}") from None

    try:
        return prepare(
            data,
            highlight_rolloff=config.pipeline.highlight_rolloff,
            quality=config.pipeline.jpeg_quality,
        )
    except (OSError, ValueError) as error:
        raise click.ClickException(
            f"{photo.source_id} is not an image this tool can prepare: {error}"
        ) from None


def _round_labels(
    base: PreparedImage, config: Config, names: list[str]
) -> dict[str, PreparedImage]:
    """The photo with each name drawn on it, keyed by that name.

    The whole prepared image travels rather than its bytes alone, because the dimensions the
    upload is checked against have to be the ones the bytes actually are. The source photo is
    several times larger, and only its aspect ratio survives into these.
    """
    return {
        name: label_center(base.data, name, quality=config.pipeline.jpeg_quality)
        for name in names
    }


def _confirm_clear(clear: bakeoff_rounds.ClearPlan, yes: bool) -> None:
    """Name what is about to be deleted and get a yes for it.

    The art channel is open while this waits, and it closes itself after about 25 seconds of
    silence, so a question left unanswered costs the run. That is why it is asked before
    anything else happens rather than partway through: a channel that dies here has deleted
    nothing, and re-running is safe.
    """
    if not clear.delete or yes:
        return

    click.echo(f"About to delete {len(clear.delete)} images from the TV:")
    for content_id in clear.mine:
        click.echo(f"  {content_id:<20}  uploaded by this tool")
    for content_id in clear.unmanaged:
        click.echo(f"  {content_id:<20}  not this tool's, and reachable no other way")

    if not click.confirm("Delete them?"):
        raise click.ClickException("Nothing was deleted.")


def _report_round_plan(
    clear: bakeoff_rounds.ClearPlan,
    photos: list[SourceItem],
    chosen: list[bakeoff_rounds.Variant],
) -> None:
    """Say what a real round would do, which is mostly what it would delete."""
    click.echo(f"Delete      {len(clear.delete)}")
    for content_id in clear.mine:
        click.echo(f"  {content_id:<20}  uploaded by this tool")
    for content_id in clear.unmanaged:
        click.echo(f"  {content_id:<20}  not this tool's")

    click.echo(f"Drop        {len(clear.stale)} entries whose image is already off the TV")

    click.echo(f"Photos      {len(photos)}, one per shape")
    for photo in photos:
        shape = mattes_rules.shape_of(photo.width, photo.height)
        click.echo(f"  {shape:<7}  {photo.source_id[:20]:<20}  {photo.width}x{photo.height}")

    click.echo(f"Upload      {len(chosen)}")
    for variant in chosen:
        click.echo(f"  {variant.number:>2}  {variant.shape:<7}  {variant.matte_id}")

    click.echo("\nNothing was changed. Drop `--dry-run` to do it.")


def _report_round_run(report: bakeoff_rounds.BakeoffReport) -> None:
    """Print the roster, which is the whole output of a round."""
    click.echo(f"Deleted {len(report.deleted)}, uploaded {len(report.uploaded)}.")
    if report.dropped:
        click.echo(f"Dropped {len(report.dropped)} entries whose image was gone from the TV.")

    if report.uploaded:
        click.echo("\nOn the wall now, by the text drawn across each one:")
        for variant, content_id in report.uploaded:
            click.echo(
                f"  {variant.number:>2}  {variant.shape:<7}  {variant.matte_id:<22}  {content_id}"
            )
        click.echo(
            "\nUploading changes nothing on the panel, so open the TV's own picker and step "
            "through them. Put the winner in `config.toml` yourself when you've picked."
        )

    if report.upload_seconds:
        click.echo(f"Upload times  {_timing(report.upload_seconds)}")

    for content_id in report.unconfirmed:
        click.echo(f"The TV still lists {content_id} after deleting it.", err=True)

    for failure in report.failures:
        click.echo(f"Skipped {failure}", err=True)

    if report.in_flight:
        click.echo(
            f"\nThe {report.in_flight} upload was in flight when the connection died, and it "
            "may be on the TV anyway. The next round's clear is what takes it down.",
            err=True,
        )


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
