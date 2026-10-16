#!/usr/bin/env python3
"""Check Flex transport and PCM without ROS, motion, NVS writes or saved audio."""

from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marsdog_voice_interaction.providers.audio_capture import BufferedAudioCapture
from marsdog_voice_interaction.providers.wakeup_respeaker_serial import WakeupReSpeakerSerialProvider
from marsdog_voice_interaction.utils.config_loader import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).resolve().parents[1] / "config/voice.respeaker.yaml")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--audio-only", action="store_true", help="Leave CDC untouched")
    parser.add_argument("--arecord", action="store_true", help="Check ALSA fallback instead of PortAudio")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 600:
        parser.error("--seconds must be between 0 and 600")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config = load_config(args.config)
    providers = config["providers"]
    audio = providers["audio"]["config"]
    wakeup = None
    if not args.audio_only:
        if providers["wakeup"]["type"] != "respeaker_serial":
            parser.error("Configuration must select respeaker_serial")
        wakeup = WakeupReSpeakerSerialProvider(providers["wakeup"]["config"])
        port = wakeup.reader.resolve_port()
        # POSIX exclusive locks only coordinate cooperating serial programs.
        # Avoid competing with a monitor that opened CDC without a lock.
        if shutil.which("fuser") and subprocess.run(
            ["fuser", "-s", port], check=False,
        ).returncode == 0:
            parser.error(f"{port} is in use; close its monitor or use --audio-only")
    capture = BufferedAudioCapture(
        16000, audio["device"], None if args.arecord else sd, 6.0, 2.0,
        arecord_device=audio["arecord_device"], reconnect_interval_sec=0.0,
    )
    count, square_sum, peak, wake_count = 0, 0.0, 0.0, 0
    pong_seen = False
    capture.start()
    if wakeup is not None:
        wakeup.start()
    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            samples = capture.read()
            if samples is not None:
                count += samples.size
                square_sum += float(np.sum(samples.astype(np.float64) ** 2))
                peak = max(peak, float(np.max(np.abs(samples))))
            if wakeup is not None:
                pong_seen |= wakeup.is_available()
                event = wakeup.poll_event()
                if event is not None:
                    wake_count += 1
                    print(json.dumps(event, ensure_ascii=False), flush=True)
    finally:
        capture.close()
        if wakeup is not None:
            wakeup.stop()
    report = {
        "sample_rate": 16000, "channels": 1, "samples": count,
        "audio_seconds": count / 16000, "rms": math.sqrt(square_sum / count) if count else 0,
        "peak": peak, "cdc_checked": wakeup is not None,
        "pong_seen": pong_seen, "wake_events": wake_count,
        "pcm_saved": False, "ros_events_published": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if count > 0 and (wakeup is None or pong_seen) else 1


if __name__ == "__main__":
    raise SystemExit(main())
