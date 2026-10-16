#!/usr/bin/env python3
"""查询或修改 ReSpeaker Flex 固件的 VAD 事件档位（low / medium / high）。"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marsdog_voice_interaction.adapters.wakeup.respeaker_serial_reader import (
    JsonLineDecoder,
    ReSpeakerSerialReader,
)


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("等待时间必须是有限正数")
    return seconds


def check_port_available(port_name: str) -> None:
    # Exclusive locks only exclude cooperating programs; fuser also finds Voice
    # or monitors that opened the port without requesting an exclusive lock.
    fuser = shutil.which("fuser") if os.name == "posix" else None
    if fuser and subprocess.run(
        [fuser, "-s", os.path.realpath(port_name)],
        capture_output=True, check=False, timeout=3,
    ).returncode == 0:
        raise RuntimeError(
            f"{port_name} 被占用，请先停止使用该串口的 Voice 节点或串口监视器，再运行脚本"
        )


def request(
    port: Any,
    decoder: JsonLineDecoder,
    command: dict[str, Any],
    response_type: str,
    timeout: float,
) -> dict[str, Any]:
    payload = json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n"
    if port.write(payload) != len(payload):
        raise RuntimeError(f"{command['cmd']} 未完整写入串口，无法确认设备状态")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = port.read(min(port.in_waiting or 1, 4096))
        for message in decoder.feed(data):
            kind = message.get("type")
            if kind == "error":
                raise RuntimeError(f"设备拒绝 {command['cmd']}：{json.dumps(message, ensure_ascii=False)}")
            if kind == response_type:
                return message
            # vad / wake / keyword can arrive before a command's response.
    raise RuntimeError(
        f"{command['cmd']} 等待 {response_type} 超时，无法确认设备状态；"
        "检查端口或固件，修改后超时可用 --get 查询"
    )


def show_config(label: str, config: dict[str, Any]) -> None:
    print(label, flush=True)
    print(json.dumps(config, ensure_ascii=False, indent=2), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="设置立即生效，设备重启恢复 medium；不带修改参数时只查询。",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--get", action="store_true", help="只查询当前 VAD 配置（默认）")
    action.add_argument(
        "--sensitivity", choices=("very_low", "low", "medium", "high"),
        help="设置固件 VAD 事件档位，不是主机 Silero vad_threshold",
    )
    parser.add_argument("--port", default="auto", help="CDC 端口，如 /dev/ttyACM0 或 COM15；默认自动发现")
    parser.add_argument("--serial-number", default="", help="多块 Flex 时，用 USB 序列号选择设备")
    parser.add_argument("--timeout", type=positive_seconds, default=3.0, help="每条命令等待回包的秒数（默认 3）")
    args = parser.parse_args(argv)

    try:
        port_name = ReSpeakerSerialReader({
            "port": args.port, "serial_number": args.serial_number,
        }).resolve_port()
        check_port_available(port_name)
        print(f"CDC: {port_name}", flush=True)
        options = {"exclusive": True} if os.name == "posix" else {}
        with serial.Serial(
            port=None, baudrate=115200,
            timeout=min(0.1, args.timeout), write_timeout=args.timeout, **options,
        ) as port:
            port.dtr = True
            port.rts = False
            port.port = port_name
            port.open()
            time.sleep(0.5)
            port.reset_input_buffer()
            decoder = JsonLineDecoder()
            request(port, decoder, {"cmd": "ping"}, "pong", args.timeout)
            before = request(port, decoder, {"cmd": "get_vad"}, "vad_config", args.timeout)
            show_config("当前 VAD 配置：", before)
            if args.sensitivity is None:
                return 0

            request(port, decoder, {
                "cmd": "set_vad", "sensitivity": args.sensitivity,
            }, "vad_config", args.timeout)
            after = request(port, decoder, {"cmd": "get_vad"}, "vad_config", args.timeout)
            show_config("设置后读回的 VAD 配置：", after)
            if after.get("sensitivity") != args.sensitivity:
                raise RuntimeError(
                    f"读回档位 {after.get('sensitivity')!r} 与请求的 {args.sensitivity!r} 不一致，"
                    "未能确认设置成功"
                )
            print(f"已确认 VAD 档位为 {args.sensitivity}；设备重启恢复 medium，需要重新设置。")
        return 0
    except (OSError, serial.SerialException, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断；如已发送设置命令，可用 --get 查询当前档位。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
