"""Tests for ace_keepalive.attempt_keepalive — the per-tick helper that
sends a frame, drains the buffer, and on write/read failure closes and
reopens the serial by-path so a stale fd left behind by an ACE Pro USB
re-enumeration cycle stops re-using a dead descriptor.

This is the crux of fix for issue tracked as task #88 (extends #70).
"""
import logging

import pytest

import ace_keepalive as ak


class FakeSerial:
    """Minimal stand-in for serial.Serial — supports is_open / write /
    in_waiting / read / close, with knobs to inject errors per call."""

    def __init__(self, *, write_raises=None, in_waiting=0,
                 read_returns=b"", is_open=True):
        self.is_open = is_open
        self.writes = []
        self.reads = []
        self.closed = False
        self._write_raises = list(write_raises or [])
        self._in_waiting = in_waiting
        self._read_returns = read_returns

    def write(self, data):
        if self._write_raises:
            exc = self._write_raises.pop(0)
            if exc is not None:
                raise exc
        self.writes.append(data)
        return len(data)

    @property
    def in_waiting(self):
        return self._in_waiting

    def read(self, n):
        self.reads.append(n)
        return self._read_returns[:n]

    def close(self):
        self.closed = True
        self.is_open = False


@pytest.fixture
def usb_log():
    log = logging.getLogger("test_ace_keepalive")
    log.handlers = []
    log.setLevel(logging.DEBUG)
    return log


@pytest.fixture
def frame():
    return b"\xff\xaa\x00\x05{\"id\":0,\"method\":\"get_status\"}\x00\x00\xfe"


def test_happy_path_writes_and_drains(usb_log, frame):
    ser = FakeSerial(in_waiting=42, read_returns=b"x" * 42)
    factory_calls = []

    def factory(path, baud):
        factory_calls.append((path, baud))
        return FakeSerial()

    new_ser, connected = ak.attempt_keepalive(
        idx=1, ser=ser, path="/dev/serial/by-path/usb-1.3.3.3",
        baud=115200, frame=frame, usb_log=usb_log, serial_factory=factory)

    assert new_ser is ser
    assert connected is True
    assert ser.writes == [frame]
    assert ser.reads == [42]
    assert factory_calls == []


def test_write_oserror_triggers_reopen(usb_log, frame):
    """Errno 5 / EIO is what we see after ACE Pro re-enumerates while
    pyserial still thinks is_open == True."""
    stale = FakeSerial(write_raises=[OSError(5, "Input/output error")])
    fresh = FakeSerial()
    factory_calls = []

    def factory(path, baud):
        factory_calls.append((path, baud))
        return fresh

    new_ser, connected = ak.attempt_keepalive(
        idx=2, ser=stale, path="/dev/serial/by-path/PATH",
        baud=115200, frame=frame, usb_log=usb_log, serial_factory=factory)

    assert stale.closed is True
    assert factory_calls == [("/dev/serial/by-path/PATH", 115200)]
    assert new_ser is fresh
    assert connected is True
    assert fresh.writes == [frame]


def test_reopen_factory_failure_is_terminal(usb_log, frame):
    stale = FakeSerial(write_raises=[OSError(5, "EIO")])

    def factory(path, baud):
        raise FileNotFoundError(path)

    new_ser, connected = ak.attempt_keepalive(
        idx=0, ser=stale, path="/dev/missing",
        baud=115200, frame=frame, usb_log=usb_log, serial_factory=factory)

    assert stale.closed is True
    assert new_ser is None
    assert connected is False


def test_reopen_returns_closed_handle_is_terminal(usb_log, frame):
    stale = FakeSerial(write_raises=[OSError(5, "EIO")])
    born_closed = FakeSerial(is_open=False)

    def factory(path, baud):
        return born_closed

    new_ser, connected = ak.attempt_keepalive(
        idx=3, ser=stale, path="/dev/x",
        baud=115200, frame=frame, usb_log=usb_log, serial_factory=factory)

    assert new_ser is None
    assert connected is False


def test_post_reopen_write_failure_is_terminal(usb_log, frame):
    stale = FakeSerial(write_raises=[OSError(5, "EIO")])
    fresh = FakeSerial(write_raises=[OSError(5, "EIO")])

    def factory(path, baud):
        return fresh

    new_ser, connected = ak.attempt_keepalive(
        idx=1, ser=stale, path="/dev/x",
        baud=115200, frame=frame, usb_log=usb_log, serial_factory=factory)

    assert stale.closed is True
    assert fresh.closed is True
    assert new_ser is None
    assert connected is False


def test_cached_closed_handle_attempts_reopen(usb_log, frame):
    """If the cache holds a closed handle (e.g. we closed it ourselves
    last tick on terminal failure but didn't pop), the next tick still
    tries to reopen so we self-heal."""
    closed = FakeSerial(is_open=False)
    fresh = FakeSerial()

    def factory(path, baud):
        return fresh

    new_ser, connected = ak.attempt_keepalive(
        idx=0, ser=closed, path="/dev/x",
        baud=115200, frame=frame, usb_log=usb_log, serial_factory=factory)

    assert new_ser is fresh
    assert connected is True
    assert fresh.writes == [frame]


def test_none_path_skips_reopen(usb_log, frame):
    """When we don't know the by-path (idx beyond _ace_devices length),
    a write failure just marks disconnected and closes the stale handle;
    no factory call attempted."""
    stale = FakeSerial(write_raises=[OSError(5, "EIO")])
    factory_calls = []

    def factory(path, baud):
        factory_calls.append((path, baud))
        return FakeSerial()

    new_ser, connected = ak.attempt_keepalive(
        idx=7, ser=stale, path=None,
        baud=115200, frame=frame, usb_log=usb_log, serial_factory=factory)

    assert stale.closed is True
    assert factory_calls == []
    assert new_ser is None
    assert connected is False


def test_drain_only_when_in_waiting_nonzero(usb_log, frame):
    """Don't call ser.read(0) — empty drain is a no-op."""
    ser = FakeSerial(in_waiting=0)

    def factory(path, baud):
        return FakeSerial()

    ak.attempt_keepalive(
        idx=1, ser=ser, path="/dev/x",
        baud=115200, frame=frame, usb_log=usb_log, serial_factory=factory)

    assert ser.reads == []
