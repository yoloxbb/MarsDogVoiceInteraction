from __future__ import annotations

from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from serial.tools import list_ports

from marsdog_voice_interaction.providers import wakeup_xfyun_serial as wakeup_module
from marsdog_voice_interaction.providers.wakeup_xfyun_serial import WakeupXFYunSerialProvider
from marsdog_voice_interaction.utils.config_loader import load_config


def _port(device, serial_number, vid=0x1A86, pid=0x55D4):
    return SimpleNamespace(device=device, serial_number=serial_number, vid=vid, pid=pid)


@pytest.fixture
def serial_devices(monkeypatch):
    ports = []
    monkeypatch.setattr(list_ports, "comports", lambda: list(ports))

    class Reader:
        instances = []
        fail_open = False

        def __init__(self, **config):
            self.config = config
            self.is_running = False
            self.last_error = None
            self.closed = False
            self.messages = deque()
            self.instances.append(self)

        def open(self):
            self.is_running = True
            if self.fail_open:
                raise OSError("permission denied opening serial port")

        def close(self):
            self.is_running = False
            self.closed = True

        def get_message(self, **kwargs):
            return self.messages.popleft() if self.messages else None

    monkeypatch.setattr(wakeup_module, "XFYunSerialReader", Reader)
    return ports, Reader


@pytest.mark.parametrize("device,serial_number", [
    ("/dev/ttyACM0", "5B0A023771"),
    ("/dev/ttyACM7", "0004"),
    ("/dev/ttyUSB2", "replacement-unit"),
])
def test_shipped_config_accepts_replacement_unit_and_changed_tty_number(
    serial_devices, device, serial_number,
):
    ports, reader_class = serial_devices
    ports.extend([
        _port("/dev/ttyS0", None, None, None),
        _port("/dev/ttyACM1", "microphone-cdc", 0x2208, 0x0001),
        _port("/dev/ttyUSB0", "other-wch", 0x1A86, 0x7523),
        _port(device, serial_number),
    ])
    config = load_config(Path(__file__).parents[1] / "config/voice.yaml")
    provider = WakeupXFYunSerialProvider(config["providers"]["wakeup"]["config"])
    provider.start()
    try:
        assert provider.is_available()
        assert len(reader_class.instances) == 1
        assert reader_class.instances[0].config["port"] == device
        reader_class.instances[0].messages.append({
            "type": "aiui_event",
            "content": {"eventType": 4, "result": "旺财旺财"},
        })
        assert provider.poll_event()["wake_word"] == "旺财旺财"
    finally:
        provider.stop()
    assert reader_class.instances[0].closed


@pytest.mark.parametrize("vid,pid", [
    (0x1234, 0x5678), ("0x1234", "0x5678"), ("4660", "22136"),
])
def test_usb_model_selector_is_configurable(serial_devices, vid, pid):
    ports, reader_class = serial_devices
    ports.extend([
        _port("/dev/ttyACM0", "default-model"),
        _port("/dev/ttyACM3", "another-model", 0x1234, 0x5678),
    ])
    provider = WakeupXFYunSerialProvider({"usb_vid": vid, "usb_pid": pid})
    provider.start()
    assert provider.is_available()
    assert reader_class.instances[0].config["port"] == "/dev/ttyACM3"
    provider.stop()


def test_missing_device_recovers_after_plug_in(serial_devices, caplog):
    ports, reader_class = serial_devices
    ports.append(_port("/dev/ttyS0", None, None, None))
    provider = WakeupXFYunSerialProvider({"reconnect_interval_sec": 0.0})
    provider.start()
    assert not provider.is_available()
    assert not reader_class.instances
    assert "No wakeup serial port matches" in caplog.text
    ports.append(_port("/dev/ttyACM4", "new-unit"))
    assert provider.poll_event() is None
    assert provider.is_available()
    assert reader_class.instances[0].config["port"] == "/dev/ttyACM4"
    provider.stop()


def test_reconnect_resolves_new_unit_instead_of_reusing_old_path(serial_devices):
    ports, reader_class = serial_devices
    ports.append(_port("/dev/ttyACM0", "old-unit"))
    provider = WakeupXFYunSerialProvider({"reconnect_interval_sec": 0.0})
    provider.start()
    first = provider.reader
    first.is_running = False
    first.last_error = "USB disconnected"
    ports[:] = [_port("/dev/ttyACM5", "replacement-unit")]
    provider.poll_event()
    assert first.closed
    assert provider.is_available()
    assert [reader.config["port"] for reader in reader_class.instances] == [
        "/dev/ttyACM0", "/dev/ttyACM5",
    ]
    provider.stop()


def test_multiple_matching_devices_require_explicit_selection(serial_devices, caplog):
    ports, reader_class = serial_devices
    ports.extend([_port("/dev/ttyACM0", "first"), _port("/dev/ttyACM2", "second")])
    provider = WakeupXFYunSerialProvider({})
    provider.start()
    assert not provider.is_available()
    assert not reader_class.instances
    assert "Multiple wakeup serial ports match" in caplog.text
    assert "/dev/ttyACM0" in caplog.text and "/dev/ttyACM2" in caplog.text
    assert "first" in caplog.text and "second" in caplog.text


def test_optional_serial_number_disambiguates_units(serial_devices):
    ports, reader_class = serial_devices
    ports.extend([_port("/dev/ttyACM0", "other"), _port("/dev/ttyACM2", "0004")])
    provider = WakeupXFYunSerialProvider({"serial_number": "0004"})
    provider.start()
    assert provider.is_available()
    assert reader_class.instances[0].config["port"] == "/dev/ttyACM2"
    provider.stop()


def test_pinned_missing_unit_does_not_fall_back_to_another_unit(serial_devices):
    ports, reader_class = serial_devices
    ports.append(_port("/dev/ttyACM0", "other"))
    provider = WakeupXFYunSerialProvider({"serial_number": "missing-unit"})
    provider.start()
    assert not provider.is_available()
    assert not reader_class.instances


@pytest.mark.parametrize("fail_open", [False, True])
def test_explicit_path_never_enumerates_or_falls_back(serial_devices, monkeypatch, fail_open):
    ports, reader_class = serial_devices
    reader_class.fail_open = fail_open

    def forbidden_enumeration():
        pytest.fail("explicit ports must not enumerate or select other devices")

    monkeypatch.setattr(list_ports, "comports", forbidden_enumeration)
    provider = WakeupXFYunSerialProvider({"port": "/dev/custom-wakeup"})
    provider.start()
    assert provider.is_available() is not fail_open
    assert len(reader_class.instances) == 1
    assert reader_class.instances[0].config["port"] == "/dev/custom-wakeup"
    if fail_open:
        assert reader_class.instances[0].closed
        assert provider.reader is None
    provider.stop()


@pytest.mark.parametrize("field,value", [
    ("usb_vid", -1), ("usb_pid", 65536), ("usb_vid", True),
    ("usb_pid", "invalid"), ("usb_vid", None), ("usb_pid", 1.5),
])
def test_invalid_selector_is_reported_without_opening_any_device(
    serial_devices, caplog, field, value,
):
    ports, reader_class = serial_devices
    ports.append(_port("/dev/ttyACM0", "unit"))
    provider = WakeupXFYunSerialProvider({field: value})
    provider.start()
    assert not provider.is_available()
    assert not reader_class.instances
    assert f"{field} must be an integer" in caplog.text


def test_enumeration_failure_is_retried_and_stop_disables_reconnect(serial_devices, monkeypatch):
    ports, reader_class = serial_devices

    def failed_enumeration():
        raise OSError("USB enumeration temporarily unavailable")

    monkeypatch.setattr(list_ports, "comports", failed_enumeration)
    provider = WakeupXFYunSerialProvider({"reconnect_interval_sec": 0.0})
    provider.start()
    assert not provider.is_available()
    ports.append(_port("/dev/ttyACM0", "unit"))
    monkeypatch.setattr(list_ports, "comports", lambda: list(ports))
    provider.poll_event()
    assert provider.is_available()
    provider.stop()
    provider.poll_event()
    assert not provider.is_available()
    assert len(reader_class.instances) == 1
