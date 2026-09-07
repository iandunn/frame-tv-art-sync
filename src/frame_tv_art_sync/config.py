"""Reads `config.toml`.

Every failure here raises `ConfigError` naming the file or the key, because the realistic
failure mode for a scheduled job is a silent no-op rather than a crash.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import crop, mattes
from .inventory import INVENTORY_FILENAME

# The `[pipeline]` table is optional, so a config written before it existed still loads,
# and the defaults live next to the code that applies them.
from .pipeline import (
    DEFAULT_HIGHLIGHT_ROLLOFF,
    DEFAULT_JPEG_QUALITY,
    MAX_HIGHLIGHT_ROLLOFF,
)

DEFAULT_CONFIG_FILENAME = "config.toml"


class ConfigError(Exception):
    """The config file is missing, unreadable, or missing a key the CLI needs."""


@dataclass(frozen=True)
class TvConfig:
    host: str
    name: str
    token_file: Path

    # Seconds to wait between uploads, off by default. See `syncer._upload_all` for what it is
    # a guess at.
    upload_pause: float = 0.0


@dataclass(frozen=True)
class GoogleAlbumConfig:
    url: str


@dataclass(frozen=True)
class ArtConfig:
    """One matte per image shape, keyed by aspect ratio, plus one for the shapes not named.

    Aspect ratio rather than orientation because that is what the TV keys on: three of the six
    types draw a fixed 16:9 aperture and crash Art Mode on anything else, so a single
    `landscape` matte would be wrong for a 4:3 the moment it was right for a 16:9.

    None of this is the API's `portrait_matte_id`, which is keyed to the panel's orientation
    and inert here; `mattes.INERT_PORTRAIT_MATTE_ID` is what gets sent for that.
    """

    # A read-only view rather than the dict itself, so `frozen=True` means it. Nothing mutates
    # this, and a `Mapping` is what `mattes.choose()` wants, so the proxy costs no caller
    # anything while making the promise true.
    matte_by_ratio: Mapping[Fraction, str]
    fallback_matte: str


@dataclass(frozen=True)
class PipelineConfig:
    """`crop` is a table read top to bottom, and the first rule a photo's shape matches wins.

    An empty tuple means nothing is cropped, which is what a config with no `[[pipeline.crop]]`
    rows gives and what this tool did before the rows existed. `crop_overrides` is checked
    first and names one photo by its source id or a unique prefix of one, because the reason
    to write an override is that a rule got that photo wrong.
    """

    highlight_rolloff: float
    jpeg_quality: int
    crop: tuple[crop.CropRule, ...] = ()
    crop_overrides: tuple[tuple[str, crop.CropRule], ...] = ()


@dataclass(frozen=True)
class SyncConfig:
    """`short_run` narrows a sync to the newest few photos of each orientation, 0 being off.

    It still mirrors, so everything it leaves out is deleted off the TV. That is the point:
    what it is for is putting a handful of photos on the wall to look at, and a run that
    narrowed the uploads but kept the rest would leave nothing to compare them against.

    The two delete flags are independent, and they default to the mirror this tool has always
    been: it removes what it put up once the album stops holding it, and it touches nothing
    else. `delete_added_by_hand` is the one that widens what a sync may destroy, which is why
    it defaults off and why a dry run names every image it would reach.
    """

    short_run: int = 0
    delete_removed_from_album: bool = True
    delete_added_by_hand: bool = False


@dataclass(frozen=True)
class BakeoffConfig:
    """Which mattes a bakeoff round is allowed to put on the wall.

    `None` means every one, which is what a missing key gives, and it is the right default
    because a round is for seeing what the TV can do. Narrowing is for the second pass, once
    looking at all sixteen has ruled most of them out.
    """

    colors: tuple[str, ...] | None = None
    types: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Config:
    path: Path
    inventory_file: Path
    tv: TvConfig
    google_album: GoogleAlbumConfig
    art: ArtConfig
    pipeline: PipelineConfig

    # Defaulted rather than required, because `short_run` is an iteration aid and everything
    # that builds a `Config` for anything but a sync has no opinion about it.
    sync: SyncConfig = field(default_factory=SyncConfig)

    # Same reasoning: only `frame bakeoff` reads this, and an absent table means every matte.
    bakeoff: BakeoffConfig = field(default_factory=BakeoffConfig)


def load_config(path: Path) -> Config:
    path = Path(path)

    try:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(
            f"No config file at {path}. Copy `config.example.toml` to `{path}` and fill it in."
        ) from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from None
    except OSError as error:
        raise ConfigError(f"Could not read {path}: {error}") from None

    tv = TvConfig(
        host=_require(raw, path, "tv", "host"),
        name=_require(raw, path, "tv", "name"),
        # Relative to the config file rather than the working directory, so the same config
        # works from a shell and from a scheduled job started anywhere.
        token_file=path.parent / _require(raw, path, "tv", "token_file"),
        upload_pause=float(_optional_number(raw, path, 0.0, 0.0, 60.0, "tv", "upload_pause")),
    )
    google_album = GoogleAlbumConfig(url=_require(raw, path, "source", "google_album", "url"))
    art = _art(raw, path)
    rolloff = _optional_number(
        raw, path, DEFAULT_HIGHLIGHT_ROLLOFF, 0.0, MAX_HIGHLIGHT_ROLLOFF,
        "pipeline", "highlight_rolloff",
    )
    quality = _optional_number(
        raw, path, DEFAULT_JPEG_QUALITY, 1, 100, "pipeline", "jpeg_quality"
    )
    pipeline = PipelineConfig(
        highlight_rolloff=float(rolloff),
        jpeg_quality=int(quality),
        crop=_crop_rules(raw, path),
        crop_overrides=_crop_overrides(raw, path),
    )
    sync = _sync(raw, path)
    bakeoff = _bakeoff(raw, path)

    return Config(
        path=path,
        # Not a key, because there is no reason to name it and every reason for it to travel
        # with the config a scheduled job was pointed at.
        inventory_file=path.parent / INVENTORY_FILENAME,
        tv=tv,
        google_album=google_album,
        art=art,
        pipeline=pipeline,
        sync=sync,
        bakeoff=bakeoff,
    )


def _crop_rules(raw: dict[str, Any], path: Path) -> tuple[crop.CropRule, ...]:
    """Read `[[pipeline.crop]]` in file order, since the first rule a photo matches wins.

    A rule that can never fire is refused rather than ignored, because a table read top to
    bottom makes it easy to bury one under a `*` and nothing else would ever say so.
    """
    pipeline = raw.get("pipeline")
    rows = pipeline.get("crop") if isinstance(pipeline, dict) else None
    if rows is None:
        return ()

    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ConfigError(
            f"`pipeline.crop` in {path} has to be a list of `[[pipeline.crop]]` tables."
        )

    rules = tuple(
        _crop_rule(row, path, f"pipeline.crop[{index}]") for index, row in enumerate(rows)
    )
    for index, rule in enumerate(rules):
        buried = next(
            (later for later in rules[index + 1 :] if rule.shadows(later)),
            None,
        )
        if buried is not None:
            raise ConfigError(
                f"`pipeline.crop[{index}]` in {path} matches `{rule.when}`, which already "
                f"covers the `{buried.when}` rule under it, so that one can never fire. Put "
                "the narrower rule first."
            )

    return rules


def _crop_overrides(raw: dict[str, Any], path: Path) -> tuple[tuple[str, crop.CropRule], ...]:
    """Read `[pipeline.crop_overrides]`, keyed by a source id or a unique prefix of one.

    Whether a key names more than one photo can't be answered here, since it depends on what
    the source lists, so `frame sync` says so per run. What is answerable is a key too short
    to name one photo at all, and one override sitting inside another.
    """
    pipeline = raw.get("pipeline")
    table = pipeline.get("crop_overrides") if isinstance(pipeline, dict) else None
    if table is None:
        return ()

    if not isinstance(table, dict):
        raise ConfigError(
            f"`pipeline.crop_overrides` in {path} has to be a table of source ids to rules."
        )

    overrides = []
    for key, row in table.items():
        if len(key) < crop.MIN_OVERRIDE_KEY:
            raise ConfigError(
                f"`pipeline.crop_overrides` in {path} has the key `{key}`, which is shorter "
                f"than {crop.MIN_OVERRIDE_KEY} characters. An override names one photo, and a "
                "key that short would match most of an album."
            )
        if not isinstance(row, dict):
            raise ConfigError(
                f"`pipeline.crop_overrides.{key}` in {path} has to be a table, like "
                '`{ to = "none" }`.'
            )

        # An override carries no `when`, since the key it sits under names the photo already.
        overrides.append(
            (key, _crop_rule(row, path, f"pipeline.crop_overrides.{key}", shaped=False))
        )

    for key, _ in overrides:
        shadowing = [other for other, _ in overrides if other != key and key.startswith(other)]
        if shadowing:
            raise ConfigError(
                f"`pipeline.crop_overrides` in {path} has `{key}` sitting inside "
                f"`{shadowing[0]}`, so only one of them can ever apply. Keep whichever is right."
            )

    return tuple(overrides)


def _crop_rule(row: dict[str, Any], path: Path, name: str, *, shaped: bool = True) -> crop.CropRule:
    """One rule, with the strings checked by `crop.py` rather than here.

    `shaped` says whether this rule is allowed to pick the photos it applies to. An override
    isn't, because its key already did.
    """
    allowed = {"when", "to", "anchor"} if shaped else {"to", "anchor"}
    unknown = sorted(set(row) - allowed)
    if unknown:
        raise ConfigError(
            f"`{name}` in {path} has the unrecognized key `{unknown[0]}`. It takes "
            f"{', '.join(f'`{key}`' for key in sorted(allowed))}."
        )

    values = {key: row.get(key) for key in ("when", "to", "anchor")}
    for key, value in values.items():
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"`{name}.{key}` in {path} has to be a string.")

    if values["to"] is None:
        raise ConfigError(
            f"`{name}` in {path} is missing `to`, which is the shape the photo becomes. Use "
            f'`to = "{crop.NO_CROP}"` to leave it alone.'
        )

    try:
        return crop.CropRule(
            when=values["when"] or crop.ANY,
            to=values["to"],
            anchor=values["anchor"] or crop.CENTER,
        )
    except crop.CropError as error:
        raise ConfigError(f"`{name}` in {path}: {error}") from None


def _bakeoff(raw: dict[str, Any], path: Path) -> BakeoffConfig:
    """Read which mattes a round may cover, refusing a name the TV has no record of.

    A type is checked against every drawable type at once rather than one shape's set, because
    the same list serves rounds over several shapes and a 16:9 accepts three types no other
    shape does. Which of them apply is decided per round, and a type no shape accepts is what
    gets refused here.
    """
    every_type = sorted(mattes.ACCEPTED_ON_ANY_SHAPE | mattes.FIXED_APERTURE_TYPES)

    return BakeoffConfig(
        colors=_optional_names(raw, path, sorted(mattes.COLORS), "bakeoff", "colors"),
        types=_optional_names(raw, path, every_type, "bakeoff", "types"),
    )


def _optional_names(
    raw: dict[str, Any], path: Path, allowed: list[str], *keys: str
) -> tuple[str, ...] | None:
    """A list of names off a fixed set, where all of them is what nothing in particular means.

    An empty list reads the same as a missing key rather than as a round covering nothing,
    because the one is a typo every time and the other is how you say `everything the TV
    offers` while leaving the key in the file to edit later.
    """
    value: Any = raw
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]

    name = ".".join(keys)
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise ConfigError(f"`{name}` in {path} has to be a list of strings.")

    if not value:
        return None

    unknown = [entry for entry in value if entry not in allowed]
    if unknown:
        raise ConfigError(
            f"`{name}` in {path} names {', '.join(unknown)}, which this firmware has no record "
            f"of. The ones it offers are: {', '.join(allowed)}."
        )

    return tuple(value)


def _sync(raw: dict[str, Any], path: Path) -> SyncConfig:
    """Read the sync settings, and refuse the one combination that quietly does the wrong thing.

    `short_run` works by mirroring the album down to a handful, so with the mirror turned off it
    would upload those few and leave everything else on the wall, which is the opposite of what
    it is for. Refusing at load time rather than at delete time matches the mattes: a scheduled
    job hits this with nobody watching, and a run that half-works is worse than one that stops.
    """
    sync = SyncConfig(
        short_run=_optional_count(raw, path, "sync", "short_run"),
        delete_removed_from_album=_optional_flag(
            raw, path, True, "sync", "delete_removed_from_album"
        ),
        delete_added_by_hand=_optional_flag(raw, path, False, "sync", "delete_added_by_hand"),
    )

    if sync.short_run and not sync.delete_removed_from_album:
        raise ConfigError(
            f"`sync.short_run` in {path} is {sync.short_run} while "
            "`sync.delete_removed_from_album` is false. A short run narrows the album down to a "
            "few photos so you can see them on the wall, and it needs the mirror to take the "
            "rest down. With the mirror off it would upload those few and leave everything else "
            "up, so set one of the two back."
        )

    return sync


def _art(raw: dict[str, Any], path: Path) -> ArtConfig:
    """Read a matte per aspect ratio, refusing any the TV wouldn't draw around that shape.

    Every key names the shape its matte is for, so the whole check happens here rather than at
    upload time, and that is deliberate: a type the TV can't draw reaches the panel as a crash
    needing a power cycle, and a scheduled job would hit it with nobody watching. `tv.upload()`
    checks the same rule again against the real pixels, because a config is not the only way a
    matte reaches the TV.
    """
    art = raw.get("art") if isinstance(raw.get("art"), dict) else {}
    _refuse_superseded_keys(art, path)

    fallback = _require(raw, path, "art", "fallback_matte")
    _validate_fallback(fallback, path)

    return ArtConfig(
        matte_by_ratio=MappingProxyType(_matte_by_ratio(art, path)),
        fallback_matte=fallback,
    )


def _refuse_superseded_keys(art: dict[str, Any], path: Path) -> None:
    """Name the replacement for a key that used to work, rather than reporting one missing.

    Both spellings were keyed to orientation, which turned out to be a proxy for aspect ratio
    that held only while every landscape anyone tested was 16:9.
    """
    superseded = [key for key in ("matte", "landscape_matte", "portrait_matte") if key in art]
    if not superseded:
        return

    raise ConfigError(
        f"`art.{'`, `art.'.join(superseded)}` in {path} has been replaced by "
        "`[art.matte_by_ratio]` and `art.fallback_matte`. A matte is keyed to the image's "
        "aspect ratio now, not its orientation, because three of the six types draw a fixed "
        "16:9 aperture and crash Art Mode on any other shape -- a 4:3 landscape included. See "
        "`config.example.toml`."
    )


def _matte_by_ratio(art: dict[str, Any], path: Path) -> dict[Fraction, str]:
    """Read `[art.matte_by_ratio]`, whose keys are ratios like `16:9` and `3:4`.

    Ratios are reduced on the way in, so `16:10` and `8:5` are one key rather than two that
    silently shadow each other.
    """
    table = art.get("matte_by_ratio")
    if table is None:
        return {}
    if not isinstance(table, dict):
        raise ConfigError(
            f"`art.matte_by_ratio` in {path} has to be a table of ratio names to matte ids, "
            'such as `"16:9" = "modern_polar"`.'
        )

    by_ratio: dict[Fraction, str] = {}
    spelled: dict[Fraction, str] = {}
    for name, matte_id in table.items():
        try:
            ratio = mattes.parse_ratio_name(name)
        except mattes.MatteError as error:
            raise ConfigError(f"`art.matte_by_ratio` in {path}: {error}") from None

        if not isinstance(matte_id, str) or not matte_id.strip():
            raise ConfigError(
                f"`art.matte_by_ratio.\"{name}\"` in {path} has to be a non-empty string."
            )

        if ratio in by_ratio:
            raise ConfigError(
                f"`art.matte_by_ratio` in {path} names one shape twice, as `{spelled[ratio]}` "
                f"and as `{name}`. Ratios are reduced, so both of those are "
                f"{mattes.ratio_name(ratio)} and only one of the two mattes could win."
            )

        try:
            mattes.validate_for_ratio(matte_id, ratio)
        except mattes.MatteError as error:
            raise ConfigError(f"`art.matte_by_ratio.\"{name}\"` in {path}: {error}") from None

        by_ratio[ratio] = matte_id
        spelled[ratio] = name

    return by_ratio


def _validate_fallback(matte_id: str, path: Path) -> None:
    """The fallback has to fit any shape, since what it is for is the shapes nobody named."""
    try:
        matte_type, _ = mattes.split_matte_id(matte_id)
    except mattes.MatteError as error:
        raise ConfigError(f"`art.fallback_matte` in {path}: {error}") from None

    if matte_type not in mattes.ACCEPTED_ON_ANY_SHAPE:
        raise ConfigError(
            f"`art.fallback_matte` in {path} is `{matte_id}`, and `{matte_type}` draws a fixed "
            f"{mattes.ratio_name(mattes.FIXED_APERTURE_RATIO)} aperture. The fallback is what "
            "catches a shape nothing else names, so it has to be one the TV can draw around "
            f"anything: {', '.join(sorted(mattes.ACCEPTED_ON_ANY_SHAPE))}."
        )

    # Reuse the full check for the color half, against a shape every type is allowed on.
    try:
        mattes.validate_for_ratio(matte_id, mattes.FIXED_APERTURE_RATIO)
    except mattes.MatteError as error:
        raise ConfigError(f"`art.fallback_matte` in {path}: {error}") from None


def _require(raw: dict[str, Any], path: Path, *keys: str) -> str:
    value: Any = raw
    for depth, key in enumerate(keys):
        if not isinstance(value, dict) or key not in value:
            raise ConfigError(f"{path} is missing `{'.'.join(keys[: depth + 1])}`.")
        value = value[key]

    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"`{'.'.join(keys)}` in {path} has to be a non-empty string.")

    return value


def _optional_count(raw: dict[str, Any], path: Path, *keys: str) -> int:
    """A whole non-negative count, defaulting to 0.

    A fractional value is refused rather than truncated, because a photo count of 2.5 means
    the file says something its author didn't mean and quietly rounding hides that.
    """
    value = _optional_number(raw, path, 0.0, 0.0, 10_000, *keys)
    if value != int(value):
        raise ConfigError(f"`{'.'.join(keys)}` in {path} has to be a whole number.")

    return int(value)


def _optional_flag(raw: dict[str, Any], path: Path, default: bool, *keys: str) -> bool:
    """A TOML boolean, defaulting to `default`.

    `1` and `"true"` are refused rather than read as true. Both of these decide whether a run
    destroys photos, and a config that says something its author didn't mean is exactly the
    thing to fail on rather than interpret.
    """
    value: Any = raw
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]

    if not isinstance(value, bool):
        raise ConfigError(
            f"`{'.'.join(keys)}` in {path} has to be `true` or `false`, unquoted."
        )

    return value


def _optional_number(
    raw: dict[str, Any],
    path: Path,
    default: float,
    minimum: float,
    maximum: float,
    *keys: str,
) -> float:
    value: Any = raw
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]

    name = ".".join(keys)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"`{name}` in {path} has to be a number.")
    if not minimum <= value <= maximum:
        raise ConfigError(f"`{name}` in {path} has to be between {minimum} and {maximum}.")

    return value


