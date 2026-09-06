"""Reads `config.toml`.

Every failure here raises `ConfigError` naming the file or the key, because the realistic
failure mode for a scheduled job is a silent no-op rather than a crash.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import mattes
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
    """One matte per image orientation, because the TV accepts different types for each.

    Neither of these is the API's `portrait_matte_id`, which is keyed to the panel's
    orientation and inert here; `mattes.INERT_PORTRAIT_MATTE_ID` is what gets sent for that.
    """

    landscape_matte: str
    portrait_matte: str


@dataclass(frozen=True)
class PipelineConfig:
    highlight_rolloff: float
    jpeg_quality: int


@dataclass(frozen=True)
class SyncConfig:
    """`short_run` narrows a sync to the newest few photos of each orientation, 0 being off.

    It still mirrors, so everything it leaves out is deleted off the TV. That is the point:
    what it is for is putting a handful of photos on the wall to look at, and a run that
    narrowed the uploads but kept the rest would leave nothing to compare them against.
    """

    short_run: int = 0


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
    )
    sync = SyncConfig(short_run=_optional_count(raw, path, "sync", "short_run"))

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
    )


def _art(raw: dict[str, Any], path: Path) -> ArtConfig:
    """Read both mattes and refuse one the TV wouldn't accept for that orientation.

    Validating here rather than at upload time is deliberate: a wrong type reaches the panel
    as a crash needing a power cycle, and a scheduled job would hit it with nobody watching.
    """
    art = raw.get("art")
    if isinstance(art, dict) and "matte" in art and "landscape_matte" not in art:
        raise ConfigError(
            f"`art.matte` in {path} has been replaced by `art.landscape_matte` and "
            "`art.portrait_matte`, because the TV accepts six matte types on a landscape and "
            "only two on a portrait. See `config.example.toml`."
        )

    values = {
        mattes.LANDSCAPE: _require(raw, path, "art", "landscape_matte"),
        mattes.PORTRAIT: _require(raw, path, "art", "portrait_matte"),
    }
    for orientation, matte_id in values.items():
        try:
            mattes.validate(matte_id, orientation)
        except mattes.MatteError as error:
            raise ConfigError(f"`art.{orientation}_matte` in {path}: {error}") from None

    return ArtConfig(
        landscape_matte=values[mattes.LANDSCAPE],
        portrait_matte=values[mattes.PORTRAIT],
    )


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


