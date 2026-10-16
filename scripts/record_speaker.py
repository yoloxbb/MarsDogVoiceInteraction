#!/usr/bin/env python3
"""直接从麦克风录入声纹，默认主人、3 条、每条 6 秒。"""

from __future__ import annotations

import argparse
import io
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from marsdog_voice_interaction.core.speaker_enrollment_manager import (
    ENROLL_SENTENCES,
    MAX_SAMPLES_PER_SPEAKER,
)
from marsdog_voice_interaction.messages.speaker_identity import ALLOWED_SPEAKER_IDENTITIES
from marsdog_voice_interaction.speaker_service import StandaloneSpeakerService
from marsdog_voice_interaction.utils.config_loader import load_config


SAMPLE_RATE = 16000


def record_wav(device: str, seconds: float) -> bytes:
    """Capture bounded PCM from the configured ALSA device, without fallback."""
    if not device.strip():
        raise ValueError("未配置 ALSA 设备，请设置 arecord_device 或使用 --device")
    if not math.isfinite(seconds) or not 1 <= seconds <= 60:
        raise ValueError("录音时长必须在 1 到 60 秒之间")
    executable = shutil.which("arecord")
    if executable is None:
        raise RuntimeError("找不到 arecord，请安装 alsa-utils")
    count = round(seconds * SAMPLE_RATE)
    command = [
        executable, "-q", "-N", "--fatal-errors", "-D", device,
        "-t", "raw", "-f", "S16_LE", "-r", str(SAMPLE_RATE),
        "-c", "1", "-s", str(count),
    ]
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "LC_ALL": "C"},
    )
    try:
        raw, stderr = process.communicate(timeout=seconds + 5.0)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("录音超时，未收到完整音频，请检查麦克风连接和占用") from exc
    finally:
        # Also release the child on Ctrl+C, including non-terminal callers.
        if process.poll() is None:
            process.kill()
            process.communicate()

    error = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode:
        if "busy" in error.lower():
            raise RuntimeError(
                f"麦克风 {device} 被占用，请先停止语音节点并结束浏览器录音，再重试。"
            )
        raise RuntimeError(f"录音失败：{error or f'arecord 退出码 {process.returncode}'}")
    if len(raw) != count * 2:
        raise RuntimeError(
            f"录音数据不完整：收到 {len(raw) // 2} 个采样，预期 {count} 个；本次不保存"
        )
    if not any(raw):
        raise RuntimeError("麦克风返回全零音频，没有录到声音；请检查麦克风是否静音")
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(raw)
    return target.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path,
        default=PROJECT_ROOT / "config/voice.respeaker.yaml",
        help="语音配置文件（默认 config/voice.respeaker.yaml）",
    )
    parser.add_argument("--speaker", choices=ALLOWED_SPEAKER_IDENTITIES, default="owner",
                        help="录入身份，默认 owner（主人）")
    parser.add_argument("--shots", type=int, default=3, help="新增样本数，默认 3，每人最多 5 条")
    parser.add_argument("--seconds", type=float, default=6.0, help="每条录音秒数，默认 6")
    parser.add_argument("--device", help="覆盖配置中的 ALSA arecord_device")
    parser.add_argument("--no-prompt", action="store_true", help="连续录制，不等待每条录音前按回车")
    args = parser.parse_args(argv)
    if not 1 <= args.shots <= MAX_SAMPLES_PER_SPEAKER:
        parser.error("--shots 必须在 1 到 5 之间")
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 60:
        parser.error("--seconds 必须在 1 到 60 之间")

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    service = None
    try:
        config = load_config(args.config)
        audio = config.get("providers", {}).get("audio", {}).get("config", {})
        device = args.device or str(audio.get("arecord_device", "")).strip()
        if not device:
            raise ValueError("配置缺少 arecord_device，请用 --device 指定 ALSA 麦克风")
        print(f"身份：{args.speaker}；麦克风：{device}；每条 {args.seconds:g} 秒", flush=True)
        print("请先停止语音节点及独立声纹服务，结束浏览器录音。正在加载模型…", flush=True)
        # Reuse the validated enrollment runtime; never start its HTTP server.
        service = StandaloneSpeakerService(config)
        records = service.enrollment.list_speaker_records()
        current = next((item["shots"] for item in records["speakers"]
                        if item["name"] == args.speaker), 0)
        remaining = MAX_SAMPLES_PER_SPEAKER - current
        if args.shots > remaining:
            raise ValueError(
                f"{args.speaker} 已有 {current} 条，最多还能新增 {remaining} 条；"
                "请减小 --shots，已满时请先在网页管理样本"
            )

        recordings = []
        for index in range(args.shots):
            sentence = ENROLL_SENTENCES[index % len(ENROLL_SENTENCES)]
            print(f"\n第 {index + 1}/{args.shots} 条，请朗读：{sentence}", flush=True)
            if not args.no_prompt:
                input("准备好后按回车开始录音（Ctrl+C 取消）：")
            print(f"正在录音，请说话（{args.seconds:g} 秒）…", flush=True)
            payload = record_wav(device, args.seconds)
            trimmed = service.vad.trim_wav(payload)
            recordings.append(payload)
            print(f"有效语音 {trimmed.speech_duration_ms / 1000:.2f} 秒", flush=True)

        print("正在校验声纹并保存…", flush=True)
        result = service.enrollment.enroll_speaker_batch_from_audio(
            args.speaker, recordings, vad=service.vad,
        )
        if not result.get("ok"):
            code = result.get("code", "enrollment_failed")
            raise RuntimeError(f"{result.get('error', '注册失败')}（{code}）")
        print(f"录入成功：{args.speaker} 新增 {result['added_count']} 条，共 {result['shots']} 条。")
        for sample in result["samples"]:
            print(f"  {sample['audio_path']}")
        print("重新启动语音节点后即可加载这些声纹。")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\n录入已中止。", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"录入失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if service is not None:
            service.close()


if __name__ == "__main__":
    raise SystemExit(main())
