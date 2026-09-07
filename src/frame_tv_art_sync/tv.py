"""The one place that talks to the TV, so every firmware quirk lives here.

`samsungtvws` is a faithful wrapper around an undocumented protocol, which means it passes the
firmware's misbehavior straight through. This module is what turns that into something a CLI can
use: one connection per invocation, held under a machine-wide lock, with a wall-clock deadline on
every request and an exception rather than a hang when one blows. `CLAUDE.md` has the findings
behind each rule and `docs/spikes.md` the runs that produced them.

Several requests are refused rather than wrapped, and `_Channel` names each one and raises rather
than leaving it reachable by accident. `set_favourite`, `get_thumbnail`, and the auto-rotation
pair hang and take the channel down with them. `change_matte` is the other kind: it answers, with
`error -7` on every image, so `upload(matte=)` is the only place a matte can be set and
restyling one means re-uploading the photo under a new `content_id`.
"""

from __future__ import annotations

import fcntl
import os
import socket
import sys
import threading
import time
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import websocket
from samsungtvws import SamsungTVArt, SamsungTVWS
from samsungtvws import exceptions as samsung
from samsungtvws.rest import SamsungTVRest

from . import mattes
from .config import Config

# Outside every checkout, because the point is that two worktrees and a scheduled job all
# contend for the same TV. Beside the token it would also leave an untracked file in whichever
# tree happened to run last.
LOCK_PATH = Path("/tmp/frame-tv-art-sync.lock")

LOCK_WAIT_SECONDS = 90

# The art channel only works on 8002. 8001 is unencrypted and tokenless and doesn't serve art
# mode on recent firmware, so there is nothing to fall back to.
ART_PORT = 8002

# How long any one request may take before the watchdog cuts the socket. Every reply that ever
# arrived arrived inside a second, so this is generous enough that a slow answer isn't mistaken
# for a hang, and short enough that a hang isn't mistaken for a slow answer.
REQUEST_DEADLINE_SECONDS = 30

# Connecting is slower than requesting: the TV can sit on the upgrade before deciding, and
# `ms.channel.ready` follows the handshake rather than arriving with it.
CONNECT_DEADLINE_SECONDS = 45

# Pairing waits for somebody to walk over and accept the prompt, which the TV gives about 30
# seconds before timing the connection out itself. This deadline doesn't shorten that -- the
# prompt window sits inside the handshake, where there is no socket to cut -- it only keeps a
# connection that answers neither way from waiting forever.
PAIR_DEADLINE_SECONDS = 120

# An upload is one request: the header, then the image over a second socket, then the wait for
# `image_added` that carries the new `content_id`. Cutting that early is the one timeout that
# can lose data, because the TV may have stored the image before the id came back and nothing
# would then attribute it to the inventory. Well past the largest file the pipeline produces.
UPLOAD_DEADLINE_SECONDS = 120

# Passed to the library so that the one window the watchdog can't reach -- the handshake, where
# the socket isn't exposed yet -- still has a bound. It is not the deadline: a blocked read has
# outlasted this by a minute, which is the whole reason the watchdog exists.
SOCKET_TIMEOUT_SECONDS = 30

# REST answers or refuses promptly, not being the channel with the firmware problems.
REST_TIMEOUT_SECONDS = 10

# The TV takes on the order of ten seconds to notice a client with this name has gone, and a
# connection inside that window gets silence rather than a refusal. Past it by enough to be sure.
RETRY_DELAY_SECONDS = 25

# Waking is a toggle, and the panel needs a moment between the two halves of it.
WAKE_SETTLE_SECONDS = 3

# REST `PowerState` when the panel is dark. The TV is fully awake behind it and answers every art
# request, so this is the only way to tell a dark panel from a lit one.
STANDBY = "standby"

# What `get_artmode_settings` reported for this panel, and the fallback for a firmware that
# doesn't publish the range in a shape `brightness_range()` can read.
BRIGHTNESS_RANGE = (0, 10)

# Each of these hangs on this firmware and takes the art channel with it, so the run has to be
# killed and the next connection waits out the TV's ten seconds. Never reachable from here.
BANNED_REQUESTS = frozenset(
    {
        "set_favourite",
        "get_thumbnail",
        "get_thumbnail_list",
        "get_auto_rotation_status",
        "set_auto_rotation_status",
    }
)

# Answered, and inert. This one is refused with `error -7` on every image, including one that
# asked for the matte it already carried, so there is nothing it can be used for.
DEAD_REQUESTS = frozenset({"change_matte"})

# Which matte the TV will accept for an image of a given shape is `mattes.py`'s subject, not
# this file's. Nothing here decides one; `upload()` only enforces what that module says.


class TvError(Exception):
    """The TV did not do what it was asked. Every failure in this module raises one of these."""


class TvLockBusy(TvError):
    """Another process is on the art channel, and it stayed there."""


class TvTimeout(TvError):
    """A request was sent and the firmware never answered, so the socket was cut."""


class TvUnreachable(TvError):
    """The TV could not be reached, or it refused the connection outright."""


class TvClientTooSoon(TvUnreachable):
    """This client reconnected before the TV had finished forgetting the last one.

    Its own class rather than a message, because it is the one connect failure that a wait
    cures, and `FrameTv._connect` waits only for this one. Everything else it could be -- a TV
    that is off, a client on the wrong subnet, a prompt nobody accepted -- fails the same way
    25 seconds later, so retrying those buys a slower error and nothing else.
    """


class TvRefused(TvError):
    """The TV answered, and the answer was an error."""


def brightness_range(setting: Any) -> tuple[int, int]:
    """The brightness range this TV publishes, or the recorded one if it publishes none.

    `get_artmode_settings` is the only place the ranges appear, and the entry it returns for
    `brightness` carries them as `min` and `max`, both strings, the way this API writes every
    number. An entry without them is not a reason to refuse to set a brightness at all.
    """
    if isinstance(setting, dict):
        try:
            low, high = int(setting["min"]), int(setting["max"])
        except (KeyError, TypeError, ValueError):
            return BRIGHTNESS_RANGE

        if low < high:
            return low, high

    return BRIGHTNESS_RANGE


def normalize_matte_id(value: str) -> str:
    """Lower-case a matte id, because the TV reports the same one in two cases.

    `available()` returns `SHADOWBOX_ANTIQUE` while `get_matte_list()` returns
    `shadowbox_antique`, so anything comparing the two has to agree on a case first.
    """
    return value.strip().lower()


@dataclass(frozen=True)
class MatteColor:
    """One mat color, with the triple the TV publishes for it.

    The triple is worth carrying because a color can't be chosen from its name alone: `polar`
    photographed light blue and `black` medium blue in a dark room, and only re-shooting in
    daylight showed the declared numbers were right after all.
    """

    name: str
    rgb: tuple[int, int, int]


def parse_matte_list(payload: Any) -> tuple[list[str], list[MatteColor]]:
    """Pull the type names and the colors out of what `get_matte_list()` answers with.

    Each list holds tables rather than names -- `{'matte_type': 'flexible'}` and
    `{'color': 'black', 'r': 34, 'g': 34, 'b': 33}` -- which is easy to miss, because printing
    them looks right up until every id is the string form of a dict.
    """
    if not isinstance(payload, dict):
        return [], []

    types = [
        normalize_matte_id(_field(row, "matte_type")) for row in payload.get("matte_types", [])
    ]
    colors = [_matte_color(row) for row in payload.get("matte_colors", [])]

    return [name for name in types if name], [color for color in colors if color.name]


def _field(row: Any, key: str) -> str:
    """Read one field out of a row that may be a table or already just the value.

    `get_matte_list()` answers with a list of tables here, `{'matte_type': 'flexible'}`, but the
    library normalizes two firmware shapes into that key and a plain list of names is the other
    thing it has been seen returning.
    """
    if isinstance(row, dict):
        value = row.get(key, "")
        return value if isinstance(value, str) else ""

    return row if isinstance(row, str) else ""


def _matte_color(row: Any) -> MatteColor:
    """Read one color row, whose channel keys are upper case where the name's key is lower.

    Matching either case rather than the observed one, because a row that mixes them is already
    evidence that the case is nobody's guarantee.
    """
    fields = row.items() if isinstance(row, dict) else ()
    lowered = {str(key).lower(): value for key, value in fields}

    def channel(key: str) -> int:
        try:
            return int(lowered[key])
        except (KeyError, TypeError, ValueError):
            return 0

    return MatteColor(
        name=normalize_matte_id(_field(row, "color")),
        rgb=(channel("r"), channel("g"), channel("b")),
    )


@contextmanager
def channel_lock(
    path: Path = LOCK_PATH,
    wait: float = LOCK_WAIT_SECONDS,
    announce: Callable[[str], None] | None = None,
) -> Iterator[None]:
    """Hold an exclusive lock for as long as this process is on a channel.

    The TV's Device List keys on the client name, so two processes using the same name look like
    one client to it and the second connection tears the first one down. Waiting is right here
    rather than failing, because the nightly job and a person at a terminal both want their turn
    rather than an error.
    """
    try:
        path.touch(exist_ok=True)
        handle = open(path, "r+")
    except OSError as error:
        raise TvError(
            f"Could not take the TV lock at {path}: {error}. Nothing was sent. The lock has to "
            "sit outside every checkout, so that two worktrees and a scheduled job contend for "
            "the same one."
        ) from None

    deadline = time.monotonic() + wait
    acquired = False

    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                handle.seek(0)
                holder = handle.read().strip() or "an unnamed process"
                if time.monotonic() >= deadline:
                    raise TvLockBusy(
                        f"The TV lock at {path} is still held by {holder} after {wait:g}s. "
                        "Nothing was sent."
                    ) from None
                if announce:
                    announce(f"Waiting for the TV lock, held by {holder}.")
                time.sleep(5)

        handle.seek(0)
        handle.truncate()
        handle.write(f"pid {os.getpid()} {Path(sys.argv[0]).name}\n")
        handle.flush()

        yield
    finally:
        # Only the holder clears the name, and only while it still holds the lock, so that a
        # waiter which gave up doesn't erase the name of the process it was waiting for. `flock`
        # is advisory, so nothing but this check stops it.
        if acquired:
            try:
                handle.seek(0)
                handle.truncate()
                handle.flush()
            except OSError:
                pass
        handle.close()


class _Channel:
    """One open websocket to the TV, with a wall-clock deadline on every request.

    Requests go through by name rather than as bound methods, which is what lets the banned ones
    be refused in one place instead of relying on nobody reaching for them.
    """

    def __init__(self, connection: SamsungTVArt | SamsungTVWS, host: str, name: str) -> None:
        self._connection = connection
        self._host = host
        self._name = name
        self._cut = False

    def open(self, deadline: float = CONNECT_DEADLINE_SECONDS) -> None:
        self.run("connect", self._connection.open, deadline=deadline)

    def close(self) -> None:
        try:
            self._connection.close()
        except (OSError, websocket.WebSocketException):
            # The socket may already be gone, which is the normal case after a timeout cut it.
            pass

    def request(
        self,
        method: str,
        *args: Any,
        deadline: float = REQUEST_DEADLINE_SECONDS,
        **kwargs: Any,
    ) -> Any:
        """Send one named request, refusing the ones that wedge the channel."""
        if method in BANNED_REQUESTS:
            raise TvError(
                f"`{method}` hangs on this firmware and takes the art channel with it, so it is "
                "not callable. `CLAUDE.md` has what to use instead."
            )
        if method in DEAD_REQUESTS:
            raise TvError(
                f"`{method}` is refused with `error -7` by this firmware on every image, so it "
                "is not callable. A matte can only be set by `upload(matte=)`, which means "
                "re-uploading the photo and rewriting its inventory entry."
            )

        return self.run(
            method, getattr(self._connection, method), *args, deadline=deadline, **kwargs
        )

    def run(
        self,
        label: str,
        request: Callable[..., Any],
        *args: Any,
        deadline: float = REQUEST_DEADLINE_SECONDS,
        **kwargs: Any,
    ) -> Any:
        """Run one library call under a wall-clock deadline.

        The library waits on the socket with no deadline of its own, and the socket timeout it
        accepts does not end the wait: a blocked read has outlasted a 45s timeout indefinitely,
        because the TV keeps the socket busy enough that it is never idle long enough to trip. So
        the deadline is enforced from outside, by shutting the socket down underneath the blocked
        read, which makes it raise. Which exception it raises depends on the transport --
        `BrokenPipeError` over TLS and `WebSocketConnectionClosedException` without it -- so a
        timeout is identified by the watchdog having fired rather than by the type that came out.
        """
        if self._cut:
            raise TvTimeout(
                f"The channel was already cut by an earlier timeout, so `{label}` was not sent. "
                "Re-run the command."
            )

        fired = threading.Event()
        started = time.monotonic()

        def watchdog() -> None:
            fired.set()
            # Only a socket that was actually shut down makes the channel unusable. A deadline
            # that passes on a request which then answers anyway leaves it alone.
            self._cut = self._cut_socket()

        timer = threading.Timer(deadline, watchdog)
        timer.daemon = True
        timer.start()

        try:
            return request(*args, **kwargs)
        except samsung.ResponseError as error:
            raise TvRefused(f"The TV refused `{label}`: {error}") from None
        except samsung.UnauthorizedError:
            raise TvUnreachable(
                f"The TV refused `{label}` as an unknown client. Run `frame pair`, and if no "
                f"prompt appears, clear `{self._name}` from Settings > General > External Device "
                "Manager > Device List first, because the TV remembers a denial."
            ) from None
        except (OSError, websocket.WebSocketException, samsung.ConnectionFailure) as error:
            if fired.is_set():
                raise TvTimeout(
                    f"No answer to `{label}` in {deadline:g}s, so the connection was cut. The TV "
                    "is reachable but the Art app isn't answering. The one screen known to "
                    "accept a connection and then never answer is Art Mode's settings screen."
                ) from None
            raise self._unreachable(label, error, time.monotonic() - started) from None
        finally:
            timer.cancel()

    def _unreachable(self, label: str, error: Exception, elapsed: float) -> TvUnreachable:
        """Name the likeliest cause, because three unrelated ones arrive as the same exception.

        The TV reports each as a channel event and the library raises `ConnectionFailure` around
        all of them, so the event name and how long it took are the only discriminators. How long
        matters because the same `ms.channel.timeOut` means two different things: instantly, the
        TV is refusing a client off its own subnet, and after about 30 seconds, nobody accepted
        the prompt. `docs/spikes.md` T2 and T2c have both.

        The cause decides the type as well as the sentence, because only one of these is worth
        waiting out and the connect path has no other way to tell them apart.
        """
        detail = str(error)

        if "clientDisconnect" in detail:
            return TvClientTooSoon(
                f"`{label}` was answered with the previous session's disconnect. The TV needs "
                "about ten seconds to notice a client with this name left."
            )
        if "timeOut" in detail and elapsed < 2:
            return TvUnreachable(
                f"The TV refused `{label}` in {elapsed:.2f}s without prompting, which is what it "
                "does to every WebSocket client that isn't on its own subnet. Whatever runs this "
                "needs an address on that subnet; `docs/network.md` has the design."
            )
        if "timeOut" in detail:
            return TvUnreachable(
                f"`{label}` waited {elapsed:.0f}s and the TV timed it out, which is a prompt "
                "nobody accepted. Run `frame pair` with the TV in view, and if no prompt "
                f"appears, clear `{self._name}` from Settings > General > External Device "
                "Manager > Device List first, because the TV remembers a denial."
            )

        return TvUnreachable(
            f"`{label}` failed against {self._host}: {error}. Check that the TV is on and at "
            "that address, and that this machine is on the TV's own subnet."
        )

    def _cut_socket(self) -> bool:
        """Shut the socket down under the blocked read, and say whether there was one to cut."""
        connection = getattr(self._connection, "connection", None)
        sock = getattr(connection, "sock", None)
        if sock is None:
            # Still inside the handshake, where the library holds the socket privately and its
            # own socket timeout is the only bound. No hang has been observed in that window.
            return False

        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Already gone, which is as good as cut for anything that comes next.
            pass

        return True


def _art_channel(config: Config) -> _Channel:
    # No token, ever. The art channel never issues one and its authorization is the Device List
    # entry, so a token the TV no longer recognises -- which is what clearing Device List
    # produces -- gets the upgrade accepted and then silence rather than a refusal. Building
    # `SamsungTVArt` directly matters for the same reason: `SamsungTVWS.art()` copies its own
    # `token_file` onto the art channel.
    return _Channel(
        SamsungTVArt(
            host=config.tv.host,
            port=ART_PORT,
            token_file=None,
            name=config.tv.name,
            timeout=SOCKET_TIMEOUT_SECONDS,
        ),
        config.tv.host,
        config.tv.name,
    )


def _remote_channel(config: Config) -> _Channel:
    """The `samsung.remote.control` channel, which is the one that issues the token.

    It pairs separately from the art channel and carries the power key a nightly schedule needs.
    Nothing but `frame pair` opens it today.
    """
    return _Channel(
        SamsungTVWS(
            host=config.tv.host,
            port=ART_PORT,
            token_file=str(config.tv.token_file),
            name=config.tv.name,
            timeout=SOCKET_TIMEOUT_SECONDS,
        ),
        config.tv.host,
        config.tv.name,
    )


def power_state(config: Config) -> str:
    """`on` when the panel is lit and `standby` when it's dark, over REST rather than art.

    The art channel can't tell those apart: it answers either way and `get_artmode()` says `on`
    for both, so a dark panel is invisible from there. This needs no lock and no channel, since
    REST is a plain request that the TV answers even from another subnet.
    """
    rest = SamsungTVRest(config.tv.host, ART_PORT, REST_TIMEOUT_SECONDS)

    try:
        # The TV's certificate is self-signed, which the library already accepts. The warning
        # about it is noise on a LAN device that can't have anything better.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            info = rest.rest_device_info()
    except (samsung.HttpApiError, requests.RequestException) as error:
        raise TvUnreachable(f"REST on {config.tv.host}:{ART_PORT} failed: {error}") from None

    state = info.get("device", {}).get("PowerState") if isinstance(info, dict) else None
    if not isinstance(state, str):
        raise TvError(f"REST on {config.tv.host} answered without a `PowerState`.")

    return state


class FrameTv:
    """One art channel connection, held for the length of one invocation.

    A context manager rather than a set of functions, because reconnecting per call is what
    breaks: the TV is slow to notice a client left and the next connection under the same name
    gets silence. Everything inside is one connection, and the lock is held for all of it.
    """

    def __init__(
        self,
        config: Config,
        *,
        announce: Callable[[str], None] | None = None,
        lock_path: Path = LOCK_PATH,
    ) -> None:
        self._config = config
        self._announce = announce or (lambda message: None)
        self._lock = channel_lock(lock_path, announce=self._announce)
        self._channel: _Channel | None = None

    # Lifecycle

    def __enter__(self) -> FrameTv:
        self._lock.__enter__()
        try:
            self._connect()
        except BaseException:
            self._lock.__exit__(None, None, None)
            raise

        return self

    def __exit__(self, *exception: object) -> None:
        channel, self._channel = self._channel, None
        try:
            if channel is not None:
                channel.close()
        finally:
            self._lock.__exit__(None, None, None)

    def _connect(self) -> None:
        """Open the art channel, waiting out the one failure that a wait cures.

        Only `TvClientTooSoon` is retried, and it always is. The TV takes about ten seconds to
        notice a client with this name has gone, so two commands in a row fail the second one,
        and re-running by hand is the same wait with a person doing the counting. Every other
        connect failure is raised straight away, because none of them gets better in 25 seconds
        and a slower error is worse than a fast one.
        """
        try:
            self._channel = _art_channel(self._config)
            self._channel.open()
        except TvClientTooSoon:
            self._announce(
                f"The TV is still holding the last connection under this name. Waiting "
                f"{RETRY_DELAY_SECONDS}s for it to let go, then trying once more."
            )
            # Close the first one before waiting, or the TV has two clients under this name to
            # forget rather than one.
            if self._channel is not None:
                self._channel.close()

            time.sleep(RETRY_DELAY_SECONDS)
            self._channel = _art_channel(self._config)
            self._channel.open()

    @property
    def _open_channel(self) -> _Channel:
        if self._channel is None:
            raise TvError("The TV connection is closed. Use `FrameTv` as a context manager.")

        return self._channel

    # Panel state

    def power_state(self) -> str:
        """`on` when the panel is lit, `standby` when it's dark."""
        return power_state(self._config)

    def art_mode(self) -> str:
        """`on` while the Art app is in the foreground, whether the panel is lit or dark."""
        return str(self._open_channel.request("get_artmode"))

    def set_art_mode(self, on: bool) -> None:
        """Enter or leave art mode. `off` exits to the last input rather than darkening anything.

        Use `wake()` rather than `set_art_mode(True)` when the panel might be dark, because on a
        dark panel this call alone hangs and changes nothing.
        """
        self._open_channel.request("set_artmode", on)

    def wake(self) -> bool:
        """Light the panel, and say whether it had to be woken.

        Waking is a toggle rather than a call: `set_artmode(False)` followed a few seconds later
        by `set_artmode(True)` lights a dark panel and flips REST `PowerState` to `on`.
        """
        if self.power_state() != STANDBY:
            self.set_art_mode(True)
            return False

        self._announce("The panel is dark, so waking it takes an off-then-on toggle.")
        self.set_art_mode(False)
        time.sleep(WAKE_SETTLE_SECONDS)
        self.set_art_mode(True)
        return True

    # Settings

    def brightness(self) -> int:
        return int(self._open_channel.request("get_brightness"))

    def set_brightness(self, level: int) -> None:
        self._open_channel.request("set_brightness", level)

    def artmode_settings(self, setting: str = "") -> Any:
        """The art mode settings, which is the one place the valid ranges are published.

        Naming a setting returns that one entry, since the payload's `data` field is JSON inside
        a string and the library is what unpacks it.
        """
        return self._open_channel.request("get_artmode_settings", setting)

    # Content

    def available(self) -> list[dict[str, Any]]:
        """Every row `get_content_list` returned, unfiltered and undeduped.

        Deliberately raw. The same `content_id` comes back once per category it appears under, and
        the Art Store's rotating stream image is in there too, but the deduping and the filtering
        belong to `sync.tv_content_ids()`, which is pure and tested. Doing them here as well would
        put the one rule that decides what gets deleted in two places.
        """
        rows = self._open_channel.request("available")
        return rows if isinstance(rows, list) else []

    def current(self) -> dict[str, Any]:
        """What's on the panel now.

        It describes itself differently from how `available()` lists the same image, with
        `category_id: ARTSTREAM` and `content_type: artstore` for a Store item, so don't join the
        two on category.
        """
        payload = self._open_channel.request("get_current")
        return payload if isinstance(payload, dict) else {}

    def matte_list(self) -> tuple[list[str], list[MatteColor]]:
        """The matte types and colors this firmware offers.

        Everything it returns is offered for every image, which is not the same as being safe.
        The TV's own picker offers two types for a portrait and six for a landscape, and
        `modernwide` on a portrait crashed Art Mode outright.
        """
        return parse_matte_list(self._open_channel.request("get_matte_list"))

    def upload(
        self,
        data: bytes,
        *,
        matte_id: str,
        width: int,
        height: int,
        file_type: str = "jpg",
        date: str | None = None,
    ) -> str:
        """Upload one image and return the `content_id` the TV assigned it.

        This is the only place a matte can be set, since `change_matte` is refused on every
        image, so it is also the only way to restyle one already on the TV. The dimensions are
        taken rather than the matte alone because the two have to be checked together: the
        library defaults to `shadowbox_polar`, and a type the TV won't draw around the
        image's shape is accepted by the API and then crashes Art Mode. The returned id is the
        inventory key.

        `date` goes into the request's `image_date`, and it is the only text an upload carries:
        the request holds a file type, a size, two matte ids, and this. Left out, `samsungtvws`
        fills in the moment of the upload, formatted `%Y:%m:%d %H:%M:%S`. **It has to be a real
        date.** The firmware parses it rather than displaying it, so a string that isn't one is
        accepted, stored as the epoch, and shown on the panel as 1970. That rules it out as a
        way to label an image, which is what it was added for.
        """
        matte = normalize_matte_id(matte_id)
        mattes.validate(matte, width, height)

        content_id = self._open_channel.request(
            "upload",
            data,
            matte=matte,
            # Keyed to the panel's orientation rather than the image's, and never read on a
            # fixed-landscape panel. Sent as the inert value so the library's own default,
            # which is a crash on portrait input, can't get through.
            portrait_matte=mattes.INERT_PORTRAIT_MATTE_ID,
            file_type=file_type,
            date=date,
            deadline=UPLOAD_DEADLINE_SECONDS,
        )
        return str(content_id)

    def delete(self, content_id: str) -> None:
        """Delete one image. The caller confirms it by re-reading `available()`.

        `delete()` returns a bool that proves nothing -- it has returned `True` for deletes that
        did happen, but this is the one place a mistake destroys photos, so the return value is
        not treated as evidence and isn't passed on.
        """
        self._open_channel.request("delete", content_id)

    # Slideshow

    def slideshow_status(self) -> Any:
        return self._open_channel.request("get_slideshow_status")

    def slideshow_off(self) -> None:
        """Turn the slideshow off, which is all this firmware will do with one.

        Every non-zero duration is refused with `error -7`, whatever the category, shuffle flag,
        or interval, so there is no `slideshow_on` to write.
        """
        self._open_channel.request("set_slideshow_status", 0)


def pair(config: Config, announce: Callable[[str], None]) -> None:
    """Open both channels once, so the TV shows its Allow prompt for each.

    They pair separately and only one issues a token. The art channel's connect frame carries no
    token at all and its authorization is the Device List entry, keyed on the client name. The
    remote channel prompts on its own and does issue a token, which is what the token file holds
    and what the power key for a nightly schedule needs.

    Expect one more prompt whenever the controlling machine's address changes, because Device
    List holds an entry per name and address rather than re-keying the one it has.
    """
    with channel_lock(announce=announce):
        announce(f"Opening the art channel as `{config.tv.name}`. Accept the prompt on the TV.")
        art = _art_channel(config)
        art.open(PAIR_DEADLINE_SECONDS)
        try:
            announce(f"The art channel answered, with art mode {art.request('get_artmode')}.")
        finally:
            art.close()

        announce(
            f"Waiting {RETRY_DELAY_SECONDS}s for the TV to notice that client left, since both "
            "channels use the same name."
        )
        time.sleep(RETRY_DELAY_SECONDS)

        announce("Opening the remote channel. Accept its prompt too; this is the one that "
                 "issues the token.")
        before = _token(config)
        remote = _remote_channel(config)
        remote.open(PAIR_DEADLINE_SECONDS)
        try:
            after = _token(config)
            if not after:
                raise TvError(
                    f"The remote channel connected but wrote no token to {config.tv.token_file}, "
                    "so the power key a nightly schedule needs won't work yet."
                )
            if after == before:
                # A token is only issued to a client the TV doesn't already trust, so pairing a
                # second time normally writes nothing. Saying it issued one either way would
                # make a re-pair look like it had fixed something.
                announce(f"The TV accepted the token already in {config.tv.token_file}.")
            else:
                announce(f"The remote channel issued a new token, saved to {config.tv.token_file}.")
        finally:
            remote.close()


def _token(config: Config) -> str:
    try:
        return config.tv.token_file.read_text().strip()
    except OSError:
        return ""
