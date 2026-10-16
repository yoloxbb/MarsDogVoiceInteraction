import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import set_respeaker_vad as cli


def line(message):
    return json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"


class FakeSerial:
    def __init__(self):
        self.commands = []
        self.pending = bytearray()
        self.sensitivity = "medium"
        self.closed = False
        self.options = None
        self.respond = self.default_response

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def open(self):
        assert self.dtr is True and self.rts is False

    def reset_input_buffer(self):
        self.pending.clear()

    @property
    def in_waiting(self):
        return len(self.pending)

    def read(self, size):
        # Split JSON messages and UTF-8 characters across serial reads.
        chunk = bytes(self.pending[:min(size, 7)])
        del self.pending[:len(chunk)]
        return chunk

    def default_response(self, command):
        if command["cmd"] == "ping":
            return {"type": "pong"}
        if command["cmd"] == "set_vad":
            self.sensitivity = command["sensitivity"]
            # The acknowledgement alone must not be treated as readback proof.
            return {"type": "vad_config"}
        return {"type": "vad_config", "sensitivity": self.sensitivity, "persisted": False}

    def write(self, payload):
        assert payload.endswith(b"\n")
        command = json.loads(payload)
        self.commands.append(command)
        response = self.respond(command)
        # All command responses are interleaved with unsolicited events and junk.
        self.pending.extend(b'bad json\n' + line({"type": "vad", "state": "update"})
                            + line({"type": "wake", "keyword": "旺财旺财"}))
        if response is not None:
            self.pending.extend(line(response))
        return len(payload)


@pytest.fixture
def device(monkeypatch):
    port = FakeSerial()
    factory = Mock(return_value=port)
    monkeypatch.setattr(cli.serial, "Serial", factory)
    monkeypatch.setattr(cli, "check_port_available", lambda _: None)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    return port, factory


def run(*args):
    return cli.main(["--port", "/dev/flex-test", "--timeout", "0.02", *args])


@pytest.mark.parametrize("args", [(), ("--get",)])
def test_query_never_sends_a_setting(device, args, capsys):
    port, _ = device
    assert run(*args) == 0
    assert port.commands == [{"cmd": "ping"}, {"cmd": "get_vad"}]
    assert '"sensitivity": "medium"' in capsys.readouterr().out
    assert port.closed


@pytest.mark.parametrize("sensitivity", ["low", "medium", "high"])
def test_each_preset_requires_fresh_readback_amid_split_events(device, sensitivity, capsys):
    port, _ = device
    assert run("--sensitivity", sensitivity) == 0
    assert port.commands == [
        {"cmd": "ping"}, {"cmd": "get_vad"},
        {"cmd": "set_vad", "sensitivity": sensitivity}, {"cmd": "get_vad"},
    ]
    assert f"已确认 VAD 档位为 {sensitivity}" in capsys.readouterr().out
    assert port.closed


def test_firmware_rejection_fails_without_claiming_success(device, capsys):
    port, _ = device
    normal_response = port.respond
    port.respond = lambda cmd: (
        {"type": "error", "message": "不支持 set_vad"}
        if cmd["cmd"] == "set_vad" else normal_response(cmd)
    )
    assert run("--sensitivity", "low") == 1
    output = capsys.readouterr()
    assert "不支持 set_vad" in output.err and "已确认" not in output.out
    assert port.commands[-1]["cmd"] == "set_vad"
    assert port.closed


@pytest.mark.parametrize("reported", ["medium", None])
def test_mismatched_or_missing_readback_cannot_confirm_a_setting(device, reported, capsys):
    port, _ = device
    normal_response = port.respond

    def respond(command):
        response = normal_response(command)
        if command["cmd"] == "get_vad" and port.sensitivity == "low":
            response["sensitivity"] = reported
        return response

    port.respond = respond
    assert run("--sensitivity", "low") == 1
    output = capsys.readouterr()
    assert "未能确认设置成功" in output.err and "已确认" not in output.out
    assert port.closed


@pytest.mark.parametrize("missing_response", ["ping", "set_vad"])
def test_timeout_is_reported_without_retrying_a_setting(device, missing_response, capsys):
    port, _ = device
    normal_response = port.respond
    port.respond = lambda cmd: None if cmd["cmd"] == missing_response else normal_response(cmd)
    assert run("--sensitivity", "low") == 1
    output = capsys.readouterr()
    assert "超时" in output.err and "已确认" not in output.out
    settings = [cmd for cmd in port.commands if cmd["cmd"] == "set_vad"]
    assert len(settings) == (0 if missing_response == "ping" else 1)
    assert port.closed


def test_busy_port_is_rejected_before_opening(monkeypatch, capsys):
    factory = Mock()
    monkeypatch.setattr(cli.serial, "Serial", factory)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/fuser")
    monkeypatch.setattr(cli.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0)))
    if cli.os.name != "posix":
        pytest.skip("fuser occupancy check is POSIX-only")
    assert run("--sensitivity", "low") == 1
    factory.assert_not_called()
    assert "被占用" in capsys.readouterr().err


@pytest.mark.parametrize("args", [
    ("--sensitivity", "0.006"),
    ("--get", "--sensitivity", "low"),
    ("--timeout", "0"),
    ("--timeout", "nan"),
    ("--timeout", "inf"),
])
def test_invalid_options_never_open_the_device(device, args):
    _, factory = device
    with pytest.raises(SystemExit) as exc:
        run(*args)
    assert exc.value.code == 2
    factory.assert_not_called()
