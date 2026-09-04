"""Covers the masking, which is load-bearing because the log is always on.

The lines here are the real ones `samsungtvws` emits, so a library update that changes their
wording shows up as a test failing rather than as a token in somebody's bug report.
"""

from __future__ import annotations

import io
import logging

import pytest

from frame_tv_art_sync import logs
from frame_tv_art_sync.logs import REDACTED, Redactor, note_secrets, redact

TOKEN = "12345678"
HOST = "192.168.1.50"
ALBUM = "https://photos.google.com/share/AF1QipAAAA?key=SsSsSsSsSsSs"


@pytest.fixture(autouse=True)
def forget_secrets():
    """The literal masks are module state, so one test's must not leak into the next."""
    logs._SECRETS.clear()
    yield
    logs._SECRETS.clear()


def test_a_token_in_a_websocket_url_is_masked():
    line = f"WS url wss://{HOST}:8002/api/v2/channels/samsung.remote.control?name=x&token={TOKEN}"

    masked = redact(line)

    assert TOKEN not in masked
    assert HOST not in masked


def test_the_token_the_library_announces_at_info_is_masked():
    assert TOKEN not in redact(f"New token {TOKEN}")
    assert TOKEN not in redact(f"Got token {TOKEN}")
    assert TOKEN not in redact(f"Save token to file: {TOKEN}")


def test_the_address_of_each_uploads_socket_is_masked():
    """The TV hands back a fresh one per upload, so it is in the log a couple of hundred times."""
    masked = redact(f'conn_info: {{"ip": "{HOST}", "port": 45678}}')

    assert HOST not in masked
    assert REDACTED in masked


def test_an_album_share_key_is_masked():
    """It is the whole authorization for the album, so it is as sensitive as the TV's token."""
    assert "SsSsSsSsSsSs" not in redact(f"Reading {ALBUM}")


def test_a_secret_named_from_the_config_is_masked_even_without_a_pattern():
    """A hostname isn't an address and no pattern would catch it."""
    note_secrets("frame-tv.lan")

    assert "frame-tv.lan" not in redact("connecting to frame-tv.lan")


def test_a_short_value_is_not_masked():
    """Masking something two characters long would redact half the log."""
    note_secrets("tv")

    assert redact("the tv answered") == "the tv answered"


def test_something_with_no_secret_in_it_is_left_alone():
    line = "sub_event: image_added, wait_for_sub_event: image_added"

    assert redact(line) == line


def test_the_frame_that_says_the_tv_left_art_mode_survives():
    """It is the whole reason the log records the protocol, so masking must not eat it."""
    line = 'websocket event: {"event":"art_mode_changed","status":"off"}'

    assert redact(line) == line


def test_a_secret_passed_as_a_log_argument_is_masked_too():
    """It arrives outside the format string, so masking the format string alone would miss it."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(Redactor())

    logger = logging.getLogger("test_logs_argument")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.debug("New token %s", TOKEN)

    assert TOKEN not in stream.getvalue()


def test_a_run_is_written_to_the_log_beside_the_config(tmp_path):
    path = tmp_path / logs.LOG_FILENAME
    logs.start(path)

    try:
        logs.note("179 photos in the album.")
        logging.getLogger("samsungtvws.connection").debug("Got token %s", TOKEN)
    finally:
        _detach(path)

    written = path.read_text(encoding="utf-8")
    assert "179 photos in the album." in written
    assert TOKEN not in written
    assert REDACTED in written


def test_the_log_is_not_created_until_something_is_written(tmp_path):
    """Otherwise `frame sync --help` would leave a file behind."""
    path = tmp_path / logs.LOG_FILENAME
    logs.start(path)
    _detach(path)

    assert not path.exists()


def _detach(path) -> None:
    """Take this test's handlers back off, since the loggers outlive it."""
    for name in (logs.LOGGER, logs.LIBRARY_LOGGER):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
