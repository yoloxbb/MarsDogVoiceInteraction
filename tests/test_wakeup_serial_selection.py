from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest
from serial.tools import list_ports

from marsdog_voice_interaction.providers import wakeup_xfyun_serial as wakeup_module
from marsdog_voice_interaction.providers.wakeup_xfyun_serial import WakeupXFYunSerialProvider


def _port(device, vid=0x1A86, pid=0x55D4, serial_number="0004"):
    return SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial_number)


@pytest.fixture
def ports(monkeypatch):
    devices = []
    monkeypatch.setattr(list_ports, "comports", lambda: list(devices))
    return devices


@pytest.fixture
def readers(monkeypatch):
    instances = []

    class Reader:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.is_running = False
            self.last_error = None
            self.closed = False
            self.messages = deque()
            instances.append(self)

        def open(self):
            self.is_running = True

        def close(self):
            self.closed = True
            self.is_running = False

        def get_message(self, **kwargs):
            return self.messages.popleft() if self.messages else None

    monkeypatch.setattr(wakeup_module, "XFYunSerialReader", Reader)
    return instances


@pytest.mark.parametrize("config", [
    {}, {"port": "auto"}, {"port": " AUTO "}, {"port": ""},
    {"port": "auto", "usb_vid": "0x1a86", "usb_pid": "0x55d4"},
])
def test_auto_selects_wakeup_board_without_opening_other_devices(config, ports, readers):
    ports.extend([
        _port("/dev/ttyACM0", 0x2208, 0x0001),  # ListenGo's unrelated CDC port
        _port("/dev/ttyUSB0", 0x0403, 0x6001),  # UWB serial port
        _port("/dev/ttyACM3"),
    ])
    provider = WakeupXFYunSerialProvider({**config, "protocol": "aiui"})
    provider.start()
    assert provider.is_available()
    assert len(readers) == 1
    assert readers[0].options["port"] == "/dev/ttyACM3"
    assert readers[0].options["protocol"] == "aiui"
    provider.stop()
    assert readers[0].closed


def test_multiple_matching_devices_wait_until_selection_is_unambiguous(ports, readers, caplog):
    ports.extend([_port("/dev/ttyACM0"), _port("/dev/ttyACM2", serial_number="0005")])
    provider = WakeupXFYunSerialProvider({"reconnect_interval_sec": 0})
    provider.start()
    assert not provider.is_available()
    assert readers == []
    assert "Multiple wakeup serial ports match" in caplog.text
    ports.pop(0)
    provider.poll_event()
    assert provider.is_available()
    assert readers[0].options["port"] == "/dev/ttyACM2"


def test_optional_serial_number_selects_one_device(ports, readers):
    ports.extend([_port("/dev/ttyACM0"), _port("/dev/ttyACM2", serial_number="0005")])
    provider = WakeupXFYunSerialProvider({"serial_number": "0005"})
    provider.start()
    assert readers[0].options["port"] == "/dev/ttyACM2"


def test_missing_selected_serial_does_not_open_other_wakeup_board(ports, readers):
    ports.append(_port("/dev/ttyACM0"))
    provider = WakeupXFYunSerialProvider({"serial_number": "0005"})
    provider.start()
    assert not provider.is_available()
    assert readers == []


def test_device_arrival_and_renumbering_recover_after_backoff(ports, readers, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(wakeup_module.time, "monotonic", lambda: clock[0])
    provider = WakeupXFYunSerialProvider({"reconnect_interval_sec": 2.0, "protocol": "aiui"})
    provider.start()
    assert not provider.is_available()
    assert readers == []

    ports.append(_port("/dev/ttyACM0"))
    clock[0] = 101.0
    assert provider.poll_event() is None
    assert readers == []  # Do not busy-loop while waiting for the retry deadline.
    clock[0] = 102.0
    provider.poll_event()
    first = readers[0]
    assert provider.is_available()

    first.is_running = False
    first.last_error = "USB disconnected"
    ports.clear()
    clock[0] = 103.0
    provider.poll_event()
    assert first.closed
    assert not provider.is_available()
    ports.append(_port("/dev/ttyACM2", serial_number="replacement"))
    clock[0] = 104.0
    provider.poll_event()
    assert len(readers) == 1
    clock[0] = 105.0
    provider.poll_event()
    assert len(readers) == 2
    assert readers[1].options["port"] == "/dev/ttyACM2"
    assert readers[1].options["protocol"] == "aiui"
    readers[1].messages.append({
        "type": "aiui_event",
        "content": {"eventType": 4, "result": "小薇小薇"},
    })
    assert provider.poll_event()["wake_word"] == "小薇小薇"

    provider.stop()
    clock[0] = 110.0
    assert provider.poll_event() is None
    assert len(readers) == 2
    assert readers[1].closed


def test_explicit_path_is_authoritative_even_when_open_fails(monkeypatch, readers):
    monkeypatch.setattr(list_ports, "comports", lambda: pytest.fail("Explicit path must not scan"))
    original_open = wakeup_module.XFYunSerialReader.open

    def fail_open(self):
        raise OSError("device missing")

    monkeypatch.setattr(wakeup_module.XFYunSerialReader, "open", fail_open)
    path = "/dev/serial/by-id/selected-wakeup"
    provider = WakeupXFYunSerialProvider({"port": path, "reconnect_interval_sec": 0})
    provider.start()
    assert not provider.is_available()
    assert provider.reader is None
    assert readers[0].closed  # Clean up even if opening only partially succeeds.
    assert readers[0].options["port"] == path

    monkeypatch.setattr(wakeup_module.XFYunSerialReader, "open", original_open)
    provider.poll_event()
    assert provider.is_available()
    assert readers[1].options["port"] == path


@pytest.mark.parametrize("name", ["usb_vid", "usb_pid"])
@pytest.mark.parametrize("value", [True, -1, 65536, "invalid", None, 1.5])
def test_invalid_usb_identity_never_opens_a_port(name, value, ports, readers, caplog):
    ports.append(_port("/dev/ttyACM0"))
    provider = WakeupXFYunSerialProvider({name: value})
    provider.start()
    assert not provider.is_available()
    assert readers == []
    assert f"{name} must be an integer" in caplog.text
