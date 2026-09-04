"""Writes every run to a log file beside the config, with the secrets masked.

The log is always on, because the run worth having a log of is the one nobody expected to
fail. Everything goes in it: this tool's own progress, and every frame `samsungtvws` sees,
which is the only way to catch the ones nothing asked for. The library matches replies by
`request_id` and drops the rest, so the TV announcing that it has left art mode is invisible
at any other level.

That makes the masking load-bearing rather than a convenience. `samsungtvws` logs the pairing
token at INFO and the websocket URL, which carries the token and the TV's address, at DEBUG,
and a log that always exists is a log that will eventually be pasted into an issue. Two layers
cover it: patterns, which catch a value nothing here knows about, and the literal strings out
of the config once it has been read. The patterns over-redact a little, which is the right way
for them to be wrong.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

LOG_FILENAME = "frame.log"
REDACTED = "[redacted]"

# One run of a full album is a few megabytes, so a nightly job needs a ceiling. Three
# generations is enough to still have the run before the one that broke.
MAX_LOG_BYTES = 5_000_000
LOG_GENERATIONS = 3

# This tool's own messages, so the file holds the progress as well as the protocol.
LOGGER = "frame"

# The library's. Debug is scoped to these two rather than turned on at the root, because
# `websocket-client` at that level dumps whole frame payloads for no added meaning.
LIBRARY_LOGGER = "samsungtvws"

_PATTERNS = (
    # `?token=abc123` in a websocket URL, and `?key=abc123` in an album share link.
    re.compile(r"((?:token|key)=)([^&\s\"']+)"),
    # `New token abc123`, `Got token abc123`, `Save token to file: abc123`.
    re.compile(r"(token(?:\s+to\s+file)?[:\s]+)(\S+)", re.IGNORECASE),
    # The TV's own address, and the one it hands back for each upload's D2D socket.
    re.compile(r"(\b)(\d{1,3}(?:\.\d{1,3}){3}\b)"),
)

# Filled in from the config once it has been read, since the patterns can't know a share
# link's shape or a hostname that isn't an address.
_SECRETS: set[str] = set()


def note_secrets(*values: str | None) -> None:
    """Mask these literally from here on, on top of what the patterns catch."""
    for value in values:
        # A short value would redact half the log. Nothing worth hiding is that short.
        if value and len(value) > 6:
            _SECRETS.add(value)


def redact(text: str) -> str:
    """Mask every token, address, and known secret in one log line."""
    for secret in _SECRETS:
        text = text.replace(secret, REDACTED)

    for pattern in _PATTERNS:
        text = pattern.sub(rf"\1{REDACTED}", text)

    return text


class Redactor(logging.Filter):
    """Rewrites each record's text before a handler can write it out.

    The message is formatted here and the arguments dropped, because a secret arrives as an
    argument rather than as part of the format string and would survive being masked otherwise.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def note(message: str) -> None:
    """Put one of this tool's own lines in the log."""
    logging.getLogger(LOGGER).info(message)


def start(path: Path, *, debug: bool = False) -> None:
    """Open the log, and tee the protocol frames to stderr as well when asked.

    `debug` is only worth passing when somebody is watching the run, since the frames are what
    make the file worth keeping and stderr is already carrying the progress.
    """
    handlers: list[logging.Handler] = [_file_handler(path)]
    if debug:
        handlers.append(_stderr_handler())

    for name in (LOGGER, LIBRARY_LOGGER):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        for handler in handlers:
            logger.addHandler(handler)


def _file_handler(path: Path) -> logging.Handler:
    # Opened on the first write rather than here, so `frame sync --help` doesn't leave a file
    # behind and an unwritable directory fails at the point something had to be said.
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_GENERATIONS, delay=True, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s  %(name)s  %(message)s"))
    handler.addFilter(Redactor())
    return handler


def _stderr_handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    # On the handler rather than the logger, because a filter on a logger doesn't see records
    # that propagate up from a child of it.
    handler.addFilter(Redactor())
    # The progress is already on stderr by another route, so this one carries the frames only.
    handler.addFilter(lambda record: not record.name.startswith(LOGGER))
    return handler
