"""Covers the parts of the TV wrapper that don't need the TV.

The deadline is the one worth reading. It runs against a real socket that completes nothing, the
same shape as a firmware request that is never answered, because the whole mechanism is that
shutting the socket down from another thread makes the blocked read raise. A mock would only
prove the mock raises.

Everything that talks the protocol is verified by hand against the TV instead. Mocking an
undocumented WebSocket would prove the mock matches the code and nothing about the firmware.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest
import websocket
from samsungtvws import exceptions as samsung

from frame_tv_art_sync import tv as tv_module
from frame_tv_art_sync.tv import (
    BRIGHTNESS_RANGE,
    REOPEN_GAP_SECONDS,
    RETRY_DELAY_SECONDS,
    FrameTv,
    MatteColor,
    TvClientTooSoon,
    TvError,
    TvLockBusy,
    TvTimeout,
    TvUnreachable,
    _Channel,
    brightness_range,
    channel_lock,
    normalize_matte_id,
    parse_matte_list,
)


@pytest.fixture(autouse=True)
def with_no_channel_closed_yet(monkeypatch):
    """Start every test with the module having no record of a close.

    `_Channel.close()` records one, and the record is deliberately module level, so a test that
    closes a channel would otherwise make a later test's connect wait for the gap. How long it
    waited would depend on how fast the suite happened to run.
    """
    monkeypatch.setattr("frame_tv_art_sync.tv._closed_at", None)


class Blocking:
    """A stand-in for `SamsungTVArt` whose request blocks until its socket is shut down.

    `_Channel` reaches the socket through `.connection.sock`, which is the path the library
    exposes it on, so the fake carries the same shape. The read raising on end of stream is the
    library's own behavior rather than the socket's: `recv` returns empty bytes and
    `websocket-client` turns that into `WebSocketConnectionClosedException`.
    """

    def __init__(self) -> None:
        self.sock, self._peer = socket.socketpair()
        self.connection = self

    def blocking_request(self) -> bytes:
        received = self.sock.recv(1)
        if not received:
            raise websocket.WebSocketConnectionClosedException("Connection to remote host lost.")

        return received

    def answered_request(self) -> str:
        return "on"

    def close(self) -> None:
        self.sock.close()
        self._peer.close()


class Detached:
    """A connection whose socket isn't reachable yet, as during the handshake."""

    connection = None


def test_a_request_that_is_never_answered_raises_rather_than_hanging():
    connection = Blocking()
    channel = _Channel(connection, "10.0.0.1", "test-client")

    started = time.monotonic()
    with pytest.raises(TvTimeout) as raised:
        channel.run("blocking_request", connection.blocking_request, deadline=0.2)

    assert time.monotonic() - started < 2
    assert "blocking_request" in str(raised.value)
    connection.close()


def test_a_cut_channel_refuses_the_next_request_instead_of_sending_it():
    connection = Blocking()
    channel = _Channel(connection, "10.0.0.1", "test-client")

    with pytest.raises(TvTimeout):
        channel.run("blocking_request", connection.blocking_request, deadline=0.2)

    with pytest.raises(TvTimeout, match="already cut"):
        channel.run("answered_request", connection.answered_request)

    connection.close()


def test_an_answered_request_is_not_touched_by_the_deadline():
    connection = Blocking()
    channel = _Channel(connection, "10.0.0.1", "test-client")

    assert channel.run("answered_request", connection.answered_request, deadline=5) == "on"

    connection.close()


def test_a_deadline_with_no_socket_to_cut_leaves_the_channel_alone():
    """During the handshake there is no socket to cut, so the deadline can't end the wait.

    The library's own socket timeout is the only bound in that window, and no hang has ever been
    observed there. What this pins down is that a watchdog which couldn't cut anything doesn't
    then declare the channel dead. Bounding that window too would mean running the call on a
    worker thread and joining it with the deadline, which is worth adding only if the handshake
    ever does hang.
    """
    channel = _Channel(Detached(), "10.0.0.1", "test-client")

    assert channel.run("slow_handshake", lambda: time.sleep(0.3), deadline=0.1) is None
    assert channel.run("next_request", lambda: "on") == "on"


def test_requests_that_wedge_the_channel_are_refused_before_they_are_sent():
    connection = Blocking()
    channel = _Channel(connection, "10.0.0.1", "test-client")

    for method in ("set_favourite", "get_thumbnail", "set_auto_rotation_status"):
        with pytest.raises(TvError, match="hangs on this firmware"):
            channel.request(method)

    connection.close()


def test_a_request_this_firmware_refuses_outright_is_not_sent_either():
    """`change_matte` answers rather than hanging, and does nothing whatever it is asked."""
    connection = Blocking()
    channel = _Channel(connection, "10.0.0.1", "test-client")

    with pytest.raises(TvError, match="error -7"):
        channel.request("change_matte", "MY_F0001", "flexible_black")

    connection.close()


def test_the_lock_makes_a_second_holder_wait_and_then_give_up(tmp_path):
    """The holder is a separate process, because that's the case the lock exists for.

    Two worktrees and a scheduled job are what contend here, and `flock` from a second file
    descriptor inside one process is not reliably refused, so testing it in a thread would
    measure the platform rather than the lock.
    """
    path = tmp_path / "frame.lock"
    ready = tmp_path / "held"

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl,pathlib,sys,time\n"
            "handle = open(sys.argv[1], 'w')\n"
            "fcntl.flock(handle, fcntl.LOCK_EX)\n"
            "handle.write('pid 4242 holder\\n')\n"
            "handle.flush()\n"
            "pathlib.Path(sys.argv[2]).touch()\n"
            "time.sleep(30)\n",
            str(path),
            str(ready),
        ]
    )

    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "the holder process never took the lock"

        waited: list[str] = []
        with pytest.raises(TvLockBusy) as raised:
            with channel_lock(path, wait=0.1, announce=waited.append):
                pass

        assert "still held by pid 4242 holder" in str(raised.value)
        assert "Nothing was sent" in str(raised.value)

        # The waiter must not clear the name on its way out. `flock` is advisory, so truncating
        # a file this process never locked would succeed and leave the next waiter with nothing
        # to name.
        assert path.read_text().strip() == "pid 4242 holder"
    finally:
        holder.terminate()
        holder.wait(10)


def test_the_lock_is_reusable_once_the_holder_is_done(tmp_path):
    path = tmp_path / "frame.lock"

    with channel_lock(path, wait=1):
        pass

    with channel_lock(path, wait=1):
        pass

    # Released rather than merely unlocked: the name is cleared, so a later waiter can't name a
    # process that has already gone as the holder.
    assert path.read_text() == ""


def test_a_matte_id_reads_the_same_in_either_case_the_tv_reports_it():
    assert normalize_matte_id("SHADOWBOX_ANTIQUE") == "shadowbox_antique"
    assert normalize_matte_id(" none ") == "none"
    assert normalize_matte_id("flexible_black") == "flexible_black"


def test_the_matte_list_is_read_out_of_the_tables_the_tv_sends():
    """The real payload, trimmed. Every entry is a table rather than a name."""
    types, colors = parse_matte_list(
        {
            "matte_types": [{"matte_type": "none"}, {"matte_type": "flexible"}],
            # The channel keys really are upper case while the name's key is lower.
            "matte_colors": [
                {"color": "black", "R": 34, "G": 34, "B": 33},
                {"color": "polar", "r": 232, "g": 230, "b": 231},
            ],
        }
    )

    assert types == ["none", "flexible"]
    assert colors == [MatteColor("black", (34, 34, 33)), MatteColor("polar", (232, 230, 231))]


def test_a_matte_list_of_bare_names_still_reads():
    """The library normalizes two firmware shapes, and a plain list is the other one."""
    types, colors = parse_matte_list({"matte_types": ["none", "shadowbox"], "matte_colors": []})

    assert types == ["none", "shadowbox"]
    assert colors == []


@pytest.mark.parametrize("payload", [None, {}, "flexible", {"matte_types": [{}, 7, None]}])
def test_an_unreadable_matte_list_comes_back_empty_rather_than_as_junk(payload):
    assert parse_matte_list(payload) == ([], [])


@pytest.mark.parametrize(
    "setting,expected",
    [
        # The shape this TV answers with, both bounds as strings like every number this API
        # reports.
        ({"item": "brightness", "value": "4", "min": "0", "max": "10"}, (0, 10)),
        ({"item": "brightness", "value": 4, "min": 2, "max": 8}, (2, 8)),
    ],
)
def test_a_published_brightness_range_is_used(setting, expected):
    assert brightness_range(setting) == expected


@pytest.mark.parametrize(
    "setting",
    [
        {"item": "brightness_sensor_setting", "value": "on"},
        {"min": "0"},
        {"min": "low", "max": "high"},
        # A range that doesn't ascend is a misread rather than a range.
        {"min": 10, "max": 0},
        "brightness",
        None,
    ],
)
def test_an_unreadable_brightness_range_falls_back_to_the_measured_one(setting):
    assert brightness_range(setting) == BRIGHTNESS_RANGE


def unreachable_for(detail: str, elapsed: float = 5.0):
    """What `_Channel` would raise for a connect failure carrying `detail`."""
    return _Channel(Detached(), "10.0.0.1", "test-client")._unreachable(
        "connect", samsung.ConnectionFailure(detail), elapsed
    )


def test_a_reconnect_inside_the_tvs_forgetting_window_is_its_own_failure():
    """It is the one connect failure a wait cures, and the connect path has to see that."""
    raised = unreachable_for("{'event': 'ms.channel.clientDisconnect'}")

    assert isinstance(raised, TvClientTooSoon)


@pytest.mark.parametrize(
    "detail, elapsed",
    [
        # Off the TV's own subnet, which it refuses instantly.
        ("{'event': 'ms.channel.timeOut'}", 0.4),
        # A pairing prompt nobody accepted.
        ("{'event': 'ms.channel.timeOut'}", 31.0),
        ("[Errno 61] Connection refused", 0.1),
    ],
)
def test_a_connect_failure_a_wait_cannot_cure_is_not_the_too_soon_one(detail, elapsed):
    raised = unreachable_for(detail, elapsed)

    assert isinstance(raised, TvUnreachable)
    assert not isinstance(raised, TvClientTooSoon)


class Refusing:
    """An art channel that raises `error` from `open()`, or opens cleanly when it has none."""

    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.opens = 0
        self.closes = 0

    def open(self) -> None:
        self.opens += 1
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closes += 1


def connect_with(monkeypatch, tmp_path, error, failures=1):
    """Run `FrameTv._connect` where the first `failures` connections fail that way.

    `_connect` builds a fresh channel for the retry rather than reopening the first, so the two
    attempts are two objects and the test has to hand over two.
    """
    channels = [Refusing(error if index < failures else None) for index in range(2)]
    handed = iter(channels)
    slept: list[float] = []

    monkeypatch.setattr("frame_tv_art_sync.tv._art_channel", lambda config: next(handed))
    monkeypatch.setattr("frame_tv_art_sync.tv.time.sleep", slept.append)

    tv = FrameTv(config=None, lock_path=tmp_path / "lock")
    return tv, channels, slept


def test_reconnecting_too_soon_waits_the_tv_out_and_tries_once_more(monkeypatch, tmp_path):
    tv, channels, slept = connect_with(monkeypatch, tmp_path, TvClientTooSoon("too soon"))

    tv._connect()

    assert slept == [RETRY_DELAY_SECONDS]
    # The first connection is closed before the wait, or the TV has two to forget rather than one.
    assert channels[0].closes == 1
    assert channels[1].opens == 1


def test_a_failure_a_wait_cannot_cure_is_raised_straight_away(monkeypatch, tmp_path):
    """Waiting 25s to report a TV that is off is a slower error and nothing else."""
    tv, channels, slept = connect_with(monkeypatch, tmp_path, TvUnreachable("the TV is off"))

    with pytest.raises(TvUnreachable, match="the TV is off"):
        tv._connect()

    assert slept == []
    assert channels[1].opens == 0


def test_the_first_connect_of_a_run_waits_for_nothing(monkeypatch, tmp_path):
    tv, channels, slept = connect_with(monkeypatch, tmp_path, None, failures=0)

    tv._connect()

    assert slept == []
    assert channels[0].opens == 1


def test_a_connect_after_this_process_closed_a_channel_waits_the_gap_out(monkeypatch, tmp_path):
    """A command that connects twice pays the shorter gap rather than tripping the window."""
    tv, channels, slept = connect_with(monkeypatch, tmp_path, None, failures=0)
    monkeypatch.setattr("frame_tv_art_sync.tv._closed_at", time.monotonic())

    tv._connect()

    assert slept == [pytest.approx(REOPEN_GAP_SECONDS, abs=1)]
    assert channels[0].opens == 1


def test_a_close_far_enough_back_costs_no_wait(monkeypatch, tmp_path):
    """Preparing an album takes longer than the gap, so the usual second connect waits nothing."""
    tv, _, slept = connect_with(monkeypatch, tmp_path, None, failures=0)
    monkeypatch.setattr(
        "frame_tv_art_sync.tv._closed_at", time.monotonic() - REOPEN_GAP_SECONDS - 1
    )

    tv._connect()

    assert slept == []


def test_closing_a_channel_is_what_records_the_gap_to_wait():
    channel = _Channel(Blocking(), host="10.0.0.5", name="frame")

    channel.close()

    assert tv_module._closed_at is not None


def test_a_second_too_soon_failure_is_not_waited_out_again(monkeypatch, tmp_path):
    """One wait is the whole of it, so a TV that keeps refusing fails rather than looping."""
    tv, _, slept = connect_with(
        monkeypatch, tmp_path, TvClientTooSoon("too soon"), failures=2
    )

    with pytest.raises(TvClientTooSoon):
        tv._connect()

    assert slept == [RETRY_DELAY_SECONDS]
