"""In-memory ring-buffer log stream for the webui /logs page."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class LogLine:
    ts: float
    level: str
    logger: str
    message: str


class RingLogHandler(logging.Handler):
    """Logging handler that keeps the most recent N formatted records in memory."""

    def __init__(self, maxlen: int = 3000) -> None:
        super().__init__()
        self._buf: deque[LogLine] = deque(maxlen=maxlen)
        self._cv = threading.Condition()
        self._counter = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = record.msg if isinstance(record.msg, str) else repr(record.msg)
        line = LogLine(
            ts=record.created,
            level=record.levelname,
            logger=record.name,
            message=msg,
        )
        with self._cv:
            self._buf.append(line)
            self._counter += 1
            self._cv.notify_all()

    def push(self, level: str, message: str, logger: str = "magpie.webui") -> None:
        """Manual push for non-stdlib sources (print() redirects, job events)."""
        with self._cv:
            self._buf.append(
                LogLine(ts=time.time(), level=level, logger=logger, message=message)
            )
            self._counter += 1
            self._cv.notify_all()

    def tail(self, limit: int = 300) -> list[LogLine]:
        with self._cv:
            return list(self._buf)[-limit:]

    def wait_for_new(self, last_counter: int, timeout: float = 10.0) -> tuple[list[LogLine], int]:
        with self._cv:
            if self._counter == last_counter:
                self._cv.wait(timeout=timeout)
            new = []
            if self._counter > last_counter:
                start = max(0, len(self._buf) - (self._counter - last_counter))
                new = list(self._buf)[start:]
            return new, self._counter


RING = RingLogHandler()


def install() -> None:
    """Attach the ring handler to root + uvicorn + magpie loggers (idempotent)."""
    fmt = logging.Formatter(
        "%(message)s",
        datefmt="%H:%M:%S",
    )
    RING.setLevel(logging.INFO)
    RING.setFormatter(fmt)
    targets = [
        logging.getLogger(),  # root
        *(
            logging.getLogger(n)
            for n in (
                "uvicorn",
                "uvicorn.error",
                "uvicorn.access",
                "magpie",
                "magpie.webui",
            )
        ),
    ]
    for lg in targets:
        if not any(isinstance(h, RingLogHandler) for h in lg.handlers):
            lg.addHandler(RING)
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)


def line_to_dict(line: LogLine) -> dict:
    return {
        "ts": line.ts,
        "level": line.level,
        "logger": line.logger,
        "message": line.message,
    }
