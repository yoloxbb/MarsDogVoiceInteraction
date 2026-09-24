"""Audio (VAD) provider using sherpa-onnx VoiceActivityDetector.

Real-time VAD-driven capture — streams microphone audio to VAD chunk-by-chunk
until a complete speech segment is detected or timeout is reached.
No fixed-duration recording.

Requires: sherpa-onnx, sounddevice (or pyaudio)
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Callable

import numpy as np

from marsdog_voice_interaction.providers.base import BaseProvider
from marsdog_voice_interaction.providers.audio_capture import (
    AudioCaptureGap, BufferedAudioCapture, read_pipe_chunk,
)
from marsdog_voice_interaction.utils.audio_debug import AudioDebugRecorder

logger = logging.getLogger(__name__)

# Audio capture library
try:
    import sounddevice as sd

    _HAS_AUDIO_CAPTURE = True
    _CAPTURE_BACKEND = "sounddevice"
except ImportError:
    sd = None  # type: ignore[assignment]
    _HAS_AUDIO_CAPTURE = False
    _CAPTURE_BACKEND = "arecord"

# ── Audio chunk size in samples (20ms at 16kHz) ────────────────
_CHUNK_SAMPLES = 320  # 20ms
_CAPTURE_POLL_SEC = 0.02


def _format_frame_chain(frame: Any, limit: int = 64) -> str:
    """Render a live frame chain without reading source files.

    ``traceback.format_stack`` resolves each frame's source line through
    ``linecache``, which touches the filesystem from the calling thread.
    On this target that stalls the caller for a second or more when the
    storage is busy, so walk ``f_back`` and emit file/line/function only.
    """
    lines: list[str] = []
    current = frame
    while current is not None and len(lines) < limit:
        code = current.f_code
        lines.append(
            f'  File "{code.co_filename}", '
            f"line {current.f_lineno}, in {code.co_name}"
        )
        current = current.f_back
    lines.reverse()
    return "\n".join(lines)


class AudioSherpaProvider(BaseProvider):
    """VAD provider with real-time sherpa-onnx Silero VAD.

    Streams microphone audio to VAD in 20ms chunks. Speech detection
    is continuous — VAD fires when a complete utterance is detected
    (after min_silence_dur of silence following min_speech_dur of speech).

    Attributes:
        _vad: VoiceActivityDetector instance.
        _sample_rate: Audio sample rate (16000).
        _max_duration_sec: Maximum wait before speech starts.
        _vad_threshold: VAD sensitivity (0.0-1.0).
        _min_silence_dur: Silence needed to finalize a segment.
        _min_speech_dur: Minimum speech to start a segment.
        _capture_thread: Background thread for non-blocking capture.
        _capture_result: Result from the capture thread.
        _capture_lock: Protects _capture_result.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        self._model_path = config.get("vad_model", "")
        self._sample_rate = int(config.get("sample_rate", 16000))
        self._max_duration_sec = float(config.get("max_duration_sec", 8.0))
        self._max_speech_duration_sec = float(config.get("max_speech_duration_sec", 8.0))
        self._read_timeout_sec = float(config.get("read_timeout_sec", 2.0))
        self._continuous_capture = bool(config.get("continuous_capture", False))
        self._capture_buffer_sec = float(config.get("capture_buffer_sec", 6.0))
        self._wake_history_sec = float(config.get("wake_history_sec", 0.0))
        if not math.isfinite(self._wake_history_sec) or self._wake_history_sec < 0:
            raise ValueError("wake_history_sec must be finite and non-negative")
        if self._wake_history_sec and not self._continuous_capture:
            raise ValueError("wake_history_sec requires continuous_capture")
        for value in (self._max_duration_sec, self._max_speech_duration_sec,
                      self._read_timeout_sec, self._capture_buffer_sec):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Audio capture durations must be finite and positive")
        self._buffered_capture: BufferedAudioCapture | None = None
        self._resync_required = False
        self._vad_threshold = float(config.get("vad_threshold", 0.6))
        self._min_silence_dur = float(config.get("min_silence_dur", 0.5))
        self._min_speech_dur = float(config.get("min_speech_dur", 0.4))
        self._pre_roll_sec = max(0.0, float(config.get("pre_roll_sec", 0.3)))
        self._num_threads = int(config.get("num_threads", 4))
        self._device = config.get("device")  # None = default mic
        self._audio_debug = AudioDebugRecorder(config.get("audio_debug"))
        self._debug_disable_extra_pre_roll = (
            self._audio_debug.enabled
            and self._audio_debug.disable_extra_pre_roll
        )

        self._vad: Any = None  # VoiceActivityDetector
        self._capture_thread: threading.Thread | None = None
        self._capture_result: dict[str, Any] | None = None
        self._capture_lock = threading.Lock()
        self._capturing = False
        self._speech_active = False
        self._capture_cancel_event: threading.Event | None = None
        self._active_input_stream: Any = None
        self._active_arecord_process: Any = None
        self._capture_phase = "idle"
        self._capture_started_monotonic = 0.0
        self._next_utterance_id = ""
        self._active_utterance_id = ""
        # Workers that outlived their join budget.  They are detached from
        # the capture state above so they cannot block the next capture.
        self._orphan_workers: set[threading.Thread] = set()
        self._chunk_callback: (
            Callable[[np.ndarray, int], None] | None
        ) = None

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        try:
            if self._sample_rate != 16000:
                raise ValueError("Live audio sample_rate must be 16000")
            from sherpa_onnx import SileroVadModelConfig, VadModelConfig, VoiceActivityDetector

            if not self._model_path:
                raise FileNotFoundError("VAD model path not configured")

            vad_config = SileroVadModelConfig(
                model=self._model_path,
                threshold=self._vad_threshold,
                min_silence_duration=self._min_silence_dur,
                min_speech_duration=self._min_speech_dur,
            )

            self._vad = VoiceActivityDetector(
                config=VadModelConfig(
                    silero_vad=vad_config, sample_rate=self._sample_rate,
                    num_threads=self._num_threads,
                ),
                buffer_size_in_seconds=60,
            )

            if not _HAS_AUDIO_CAPTURE:
                logger.warning(
                    "No audio capture library — install sounddevice: "
                    "uv pip install sounddevice"
                )

            self.available = True
            if self._wake_history_sec:
                self._ensure_buffered_capture()
            logger.info(
                "AudioSherpaProvider (streaming VAD) started — "
                "model=%s sr=%d chunk=%dms threshold=%.2f "
                "min_speech=%.2fs min_silence=%.1fs pre_roll=%.1fs "
                "max=%.0fs backend=%s",
                self._model_path,
                self._sample_rate,
                int(_CHUNK_SAMPLES / self._sample_rate * 1000),
                self._vad_threshold,
                self._min_speech_dur,
                self._min_silence_dur,
                self._pre_roll_sec,
                self._max_duration_sec,
                _CAPTURE_BACKEND or "none",
            )
            logger.info(
                "VAD capture policy: continuous=%s buffer_sec=%.2f "
                "wait_sec=%.2f speech_sec=%.2f read_timeout_sec=%.2f threads=%d",
                self._continuous_capture, self._capture_buffer_sec,
                self._max_duration_sec, self._max_speech_duration_sec,
                self._read_timeout_sec, self._num_threads,
            )

        except FileNotFoundError as exc:
            self.available = False
            logger.warning("AudioSherpaProvider unavailable: %s", exc)
        except Exception as exc:
            self.available = False
            logger.warning("AudioSherpaProvider unavailable: %s", exc, exc_info=True)

    def stop(self) -> None:
        self.cancel_capture(preserve_microphone=False)
        self._drain_orphan_workers()
        # Set _vad to None after capture cancellation so the worker cannot use
        # a released detector.
        self._vad = None
        self.available = False
        logger.info("AudioSherpaProvider stopped")

    def _drain_orphan_workers(self, timeout: float = 0.1) -> None:
        """Give detached workers a short chance to finish before shutdown."""
        with self._capture_lock:
            orphans = list(self._orphan_workers)
            self._orphan_workers.clear()
        for thread in orphans:
            if thread.is_alive():
                thread.join(timeout=timeout)

    def _prune_dead_orphans(self) -> None:
        """Forget detached workers that have since exited.

        A worker is only registered as an orphan when it outlived its cancel
        join budget, and on most runs it does exit a moment later. Without
        this the set would grow for the whole process lifetime, since only
        stop() drains it.
        """
        with self._capture_lock:
            if not self._orphan_workers:
                return
            dead = {t for t in self._orphan_workers if not t.is_alive()}
            if dead:
                self._orphan_workers -= dead

    # ── Public API ─────────────────────────────────────────────

    def set_chunk_callback(
        self,
        callback: Callable[[np.ndarray, int], None] | None,
    ) -> None:
        """Observe live microphone chunks without opening a second device."""
        self._chunk_callback = callback

    def set_utterance_id(self, utterance_id: str) -> None:
        """Associate the next capture with the node's utterance ID."""
        with self._capture_lock:
            self._next_utterance_id = str(utterance_id).strip()

    def start_capture(self) -> bool:
        """Start background capture (non-blocking).

        Call this when wakeup is detected. The capture thread will
        stream audio to VAD until a speech segment is found or
        max_duration_sec elapses. Call poll_result() to check.

        Returns:
            True when a new capture worker was started, False when the
            request was refused (unavailable, already capturing, or the
            previous worker still holds the device).
        """
        self._prune_dead_orphans()
        if self._continuous_capture and self._orphan_workers:
            logger.error("Capture refused: a cancelled worker still owns the VAD")
            return False
        if self._buffered_capture is not None and getattr(
            self._buffered_capture, "stopping", False
        ):
            if not self._buffered_capture.close(timeout=0):
                return False
            self._buffered_capture = None
        if not self.available:
            return False
        if self._capturing:
            return False  # already capturing
        if (
            self._capture_thread is not None
            and self._capture_thread.is_alive()
        ):
            logger.error(
                "VAD capture start ignored because the previous worker "
                "has not exited (utterance_id=%s)",
                self._active_utterance_id,
            )
            return False

        if self._buffered_capture is not None and not self._buffered_capture.delivery_active:
            self._buffered_capture.start_delivery(time.monotonic())

        cancel_event = threading.Event()
        with self._capture_lock:
            self._capturing = True
            self._speech_active = False
            self._capture_result = None
            self._capture_cancel_event = cancel_event
            self._capture_phase = "worker_starting"
            self._capture_started_monotonic = time.monotonic()
            self._active_utterance_id = (
                self._next_utterance_id or uuid.uuid4().hex
            )
            self._next_utterance_id = ""

        self._capture_thread = threading.Thread(
            target=self._capture_thread_fn,
            args=(cancel_event,),
            name="vad-capture",
            daemon=True,
        )
        self._capture_thread.start()
        logger.debug(
            "vad_worker_start_requested utterance_id=%s backend=%s",
            self._active_utterance_id,
            _CAPTURE_BACKEND or "none",
        )
        return True

    def cancel_capture(self, timeout: float = 2.0, *,
                       preserve_microphone: bool = False) -> bool:
        """Cancel an active capture and discard any pending result.

        Returns:
            True when no capture worker remains alive.
        """
        with self._capture_lock:
            cancel_event = self._capture_cancel_event
            thread = self._capture_thread
            self._capturing = False
            self._speech_active = False
            if cancel_event is not None:
                cancel_event.set()
            utterance_id = self._active_utterance_id
            phase_at_stop = self._capture_phase
        logger.debug(
            "stop_requested utterance_id=%s phase=%s",
            utterance_id,
            phase_at_stop,
        )

        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            deadline = time.monotonic() + max(0.0, timeout)
            interrupted_process: Any = None
            while thread.is_alive():
                with self._capture_lock:
                    active_process = self._active_arecord_process
                if (
                    active_process is not None
                    and active_process is not interrupted_process
                ):
                    self._terminate_arecord_process(active_process)
                    interrupted_process = active_process
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                thread.join(timeout=min(0.1, remaining))

        worker_stopped = thread is None or not thread.is_alive()
        with self._capture_lock:
            self._capture_result = None
            phase = self._capture_phase
            started = self._capture_started_monotonic
            if self._capture_thread is thread and worker_stopped:
                self._capture_thread = None
                self._capture_cancel_event = None
                self._capture_phase = "idle"
                self._capture_started_monotonic = 0.0
            elif self._capture_thread is thread:
                # The worker outlived its join budget.  Detach it so it can
                # no longer refuse the next capture; its own finally block
                # compares against a stale cancel_event and stays out of the
                # way of whichever worker comes next.
                self._capture_thread = None
                self._capture_cancel_event = None
                self._capture_phase = "idle"
                self._capture_started_monotonic = 0.0
                self._active_utterance_id = ""
                self._orphan_workers.add(thread)

        if not worker_stopped:
            worker_age = max(0.0, time.monotonic() - started) if started else 0.0
            logger.error(
                "VAD capture worker did not stop within %.1fs "
                "(backend=%s phase=%s worker_age_sec=%.2f)",
                timeout,
                _CAPTURE_BACKEND or "none",
                phase,
                worker_age,
            )
            frame = (
                sys._current_frames().get(thread.ident)
                if thread is not None and thread.ident is not None else None
            )
            if frame is not None:
                logger.error(
                    "VAD capture worker Python stack "
                    "utterance_id=%s thread=%s:\n%s",
                    utterance_id,
                    thread.name,
                    _format_frame_chain(frame),
                )
        self._prune_dead_orphans()
        if self._buffered_capture is not None:
            if preserve_microphone and worker_stopped and self._wake_history_sec:
                self._buffered_capture.stop_delivery()
            else:
                microphone_stopped = self._buffered_capture.close(timeout=timeout)
                if microphone_stopped:
                    self._buffered_capture = None
                worker_stopped = worker_stopped and microphone_stopped
        if worker_stopped:
            self._resync_required = False
        return worker_stopped

    @staticmethod
    def _terminate_arecord_process(process: Any) -> None:
        """Unblock an arecord pipe read during cancellation."""
        try:
            running = process.poll() is None
        except Exception:
            running = False
        if running:
            try:
                process.terminate()
            except Exception:
                pass

    def poll_result(self) -> dict[str, Any] | None:
        """Non-blocking poll for capture result.

        Returns:
            Dict with audio_samples, sample_rate, duration_ms, has_voice and
            the utterance_id the worker actually captured, or None if capture
            is still in progress.
        """
        with self._capture_lock:
            result = self._capture_result
            if result is not None:
                self._capture_result = None
                self._capturing = False
            return result

    def is_capturing(self) -> bool:
        """Check if a capture is currently in progress.

        Returns True while the capture thread is running OR has finished
        but the result hasn't been polled yet.
        """
        if self._capturing:
            return True
        # Thread finished — check if result is still waiting to be consumed
        with self._capture_lock:
            return self._capture_result is not None

    def is_speech_active(self) -> bool:
        """Return whether VAD currently sees an unfinished speech segment."""
        with self._capture_lock:
            return self._speech_active

    def _ensure_buffered_capture(self) -> BufferedAudioCapture:
        if self._buffered_capture is not None and getattr(
            self._buffered_capture, "stopping", False
        ):
            if not self._buffered_capture.close(timeout=0):
                raise RuntimeError("previous microphone capture is still stopping")
            self._buffered_capture = None
        if self._buffered_capture is None:
            self._buffered_capture = BufferedAudioCapture(
                self._sample_rate, self._device, sd if _HAS_AUDIO_CAPTURE else None,
                self._capture_buffer_sec, self._read_timeout_sec,
                history_sec=self._wake_history_sec,
            )
            self._buffered_capture.start()
        return self._buffered_capture

    def wake_audio_snapshot(self, end_monotonic: float,
                            duration_sec: float) -> dict[str, Any]:
        """Return wake-word audio from the single shared microphone stream."""
        if not self.available or not self._wake_history_sec:
            return {"audio_samples": np.array([], dtype=np.float32),
                    "sample_rate": self._sample_rate, "has_voice": False}
        capture = self._ensure_buffered_capture()
        samples = capture.snapshot(end_monotonic, min(duration_sec, self._wake_history_sec))
        return {"audio_samples": samples, "sample_rate": self._sample_rate,
                "has_voice": bool(samples.size)}

    def prepare_for_wakeup(self, received_monotonic: float) -> None:
        """Drop the wake word from the command stream before the next VAD turn."""
        if self._wake_history_sec and self.available:
            self._ensure_buffered_capture().start_delivery(received_monotonic)

    def _refresh_speech_active(self) -> None:
        detected = getattr(self._vad, "is_speech_detected", False)
        if callable(detected):
            detected = detected()
        with self._capture_lock:
            self._speech_active = bool(detected)

    def _set_capture_phase(
        self,
        cancel_event: threading.Event,
        phase: str,
    ) -> None:
        """Record the worker phase without overwriting a newer capture."""
        with self._capture_lock:
            if self._capture_cancel_event is cancel_event:
                self._capture_phase = phase

    def _read_sounddevice_chunk(
        self,
        stream: Any,
        cancel_event: threading.Event,
    ) -> np.ndarray | None:
        """Read only frames PortAudio reports as immediately available.

        ``InputStream.read()`` may remain blocked inside ALSA after another
        thread requests cancellation.  Polling ``read_available`` keeps the
        VAD worker cancellation-aware while preserving worker-only stream
        cleanup.
        """
        poll_deadline = time.monotonic() + _CAPTURE_POLL_SEC
        while self._capturing and not cancel_event.is_set():
            available = int(stream.read_available)
            if available >= _CHUNK_SAMPLES:
                chunk, overflowed = stream.read(_CHUNK_SAMPLES)
                if overflowed:
                    raise AudioCaptureGap("sounddevice input overflow")
                return chunk
            remaining = poll_deadline - time.monotonic()
            if remaining <= 0:
                return None
            cancel_event.wait(min(_CAPTURE_POLL_SEC, remaining))
        return None

    def _debug_segment(self, segment: Any) -> dict[str, Any]:
        samples = np.asarray(segment.samples, dtype=np.float32).reshape(-1)
        start = max(0, int(getattr(segment, "start", 0)))
        return {
            "start": start,
            "end": start + int(samples.size),
            "samples": samples.copy(),
        }

    def _save_capture_debug(
        self,
        utterance_id: str,
        raw_capture: np.ndarray,
        segments: list[dict[str, Any]],
        asr_input: np.ndarray,
    ) -> str:
        """Persist the three waveform stages and their boundary evidence."""
        if not self._audio_debug.enabled:
            return ""
        sample_rate = self._sample_rate
        raw = np.asarray(raw_capture, dtype=np.float32).reshape(-1)
        asr_waveform = np.asarray(asr_input, dtype=np.float32).reshape(-1)
        self._audio_debug.save(utterance_id, "raw", raw, sample_rate)

        vad_parts: list[np.ndarray] = []
        previous_end: int | None = None
        for index, item in enumerate(segments, start=1):
            start = int(item["start"])
            end = int(item["end"])
            samples = np.asarray(item["samples"], dtype=np.float32)
            if previous_end is not None and start > previous_end:
                # Preserve the real captured gap in the diagnostic VAD WAV.
                vad_parts.append(raw[previous_end:min(start, raw.size)])
            vad_parts.append(samples)
            logger.info(
                "vad_boundary utterance_id=%s segment_index=%d "
                "speech_start_sample=%d speech_end_sample=%d "
                "speech_start_ms=%.2f speech_end_ms=%.2f num_samples=%d",
                utterance_id,
                index,
                start,
                end,
                start / sample_rate * 1000.0,
                end / sample_rate * 1000.0,
                samples.size,
            )
            if previous_end is not None:
                original_gap = max(0, start - previous_end)
                joined_gap = (
                    min(original_gap, int(self._pre_roll_sec * sample_rate))
                    if not self._debug_disable_extra_pre_roll else 0
                )
                logger.info(
                    "vad_join utterance_id=%s segment_index=%d "
                    "segment_count=%d original_gap_ms=%.2f "
                    "joined_gap_ms=%.2f join_strategy=%s",
                    utterance_id,
                    index,
                    len(segments),
                    original_gap / sample_rate * 1000.0,
                    joined_gap / sample_rate * 1000.0,
                    (
                        "merge_source_intervals_with_pre_roll"
                        if not self._debug_disable_extra_pre_roll
                        else "direct_segment_concatenation"
                    ),
                )
            previous_end = end

        vad_waveform = (
            np.concatenate(vad_parts)
            if vad_parts else np.array([], dtype=np.float32)
        )
        self._audio_debug.save(utterance_id, "vad", vad_waveform, sample_rate)
        self._audio_debug.save(
            utterance_id, "asr_input", asr_waveform, sample_rate,
        )

        if segments:
            first_start = int(segments[0]["start"])
            configured = int(self._pre_roll_sec * sample_rate)
            asr_start = (
                first_start
                if self._debug_disable_extra_pre_roll
                else max(0, first_start - configured)
            )
            asr_end = int(segments[-1]["end"])
            actual_pre_roll = first_start - asr_start
        else:
            asr_start = 0
            asr_end = 0
            actual_pre_roll = 0
        logger.info(
            "asr_boundary utterance_id=%s asr_start_sample=%d "
            "asr_end_sample=%d asr_num_samples=%d "
            "configured_pre_roll_ms=%.2f actual_pre_roll_ms=%.2f "
            "extra_pre_roll_applied=%s segment_count=%d",
            utterance_id,
            asr_start,
            asr_end,
            asr_waveform.size,
            self._pre_roll_sec * 1000.0,
            actual_pre_roll / sample_rate * 1000.0,
            bool(not self._debug_disable_extra_pre_roll and actual_pre_roll),
            len(segments),
        )
        return str(self._audio_debug.utterance_dir(utterance_id))

    # ── Thread: streaming VAD capture ──────────────────────────

    def _capture_thread_fn(
        self,
        cancel_event: threading.Event,
    ) -> None:
        """Background thread: stream mic to VAD until speech detected."""
        result: dict[str, Any] | None = None
        with self._capture_lock:
            utterance_id = self._active_utterance_id
        logger.debug(
            "vad_worker_start utterance_id=%s backend=%s",
            utterance_id,
            _CAPTURE_BACKEND or "none",
        )
        try:
            result = self._stream_vad(cancel_event)
        except Exception as exc:
            if cancel_event.is_set():
                logger.debug("VAD capture worker cancelled: %s", exc)
            else:
                import traceback as _tb

                print(f"[VAD ERROR] {exc}", flush=True)
                _tb.print_exc()
                logger.error(
                    "VAD capture thread error: %s", exc, exc_info=True,
                )
                result = {
                    "audio_samples": np.array([], dtype=np.float32),
                    "sample_rate": self._sample_rate,
                    "duration_ms": 0.0,
                    "has_voice": False,
                    "capture_end_reason": "capture_error",
                }
        finally:
            with self._capture_lock:
                if self._capture_cancel_event is cancel_event:
                    if not cancel_event.is_set() and result is not None:
                        # Stamp the worker's own ID before it is cleared
                        # below. The node may have advanced to an utterance
                        # this provider refused to start; the caller must
                        # relabel nothing and must not adopt this audio under
                        # the newer ID.
                        if not result.get("utterance_id"):
                            result["utterance_id"] = utterance_id
                        self._capture_result = result
                    self._capturing = False
                    self._speech_active = False
                    self._capture_thread = None
                    self._capture_cancel_event = None
                    self._capture_phase = "idle"
                    self._capture_started_monotonic = 0.0
                    self._active_utterance_id = ""
            logger.debug(
                "vad_worker_stopped utterance_id=%s cancelled=%s",
                utterance_id,
                cancel_event.is_set(),
            )

    def _stream_vad(
        self,
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        """Stream microphone audio to VAD in real-time.

        Opens the mic, reads 20ms chunks, feeds to VAD.
        Returns immediately when VAD produces a complete speech segment,
        or after max_duration_sec without detecting speech.

        Returns:
            Dict with audio_samples, sample_rate, duration_ms, has_voice.
        """
        if self._continuous_capture:
            return self._stream_vad_buffered(cancel_event)
        if not _HAS_AUDIO_CAPTURE:
            return self._stream_vad_arecord(cancel_event)

        self._vad.reset()

        # Accumulate all raw audio (for debugging / re-processing)
        all_audio: list[float] = []
        speech_segment: np.ndarray | None = None
        debug_segments: list[dict[str, Any]] = []
        t_start = time.perf_counter()
        stream: Any = None
        fallback_to_arecord = False
        exit_reason = "unknown"
        before_read_logged = False
        first_read_logged = False
        first_vad_logged = False
        speech_started: float | None = None
        with self._capture_lock:
            utterance_id = self._active_utterance_id

        try:
            self._set_capture_phase(cancel_event, "sounddevice_open")
            stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                device=self._device,
                blocksize=_CHUNK_SAMPLES,
            )
            with self._capture_lock:
                if (
                    self._capture_cancel_event is cancel_event
                    and not cancel_event.is_set()
                ):
                    self._active_input_stream = stream
            if cancel_event.is_set():
                return {
                    "audio_samples": np.array([], dtype=np.float32),
                    "sample_rate": self._sample_rate,
                    "duration_ms": 0.0,
                    "has_voice": False,
                }
            self._set_capture_phase(cancel_event, "sounddevice_start")
            stream.start()
            self._set_capture_phase(cancel_event, "sounddevice_wait")

            while True:
                # Check for shutdown
                if (
                    self._vad is None
                    or not self._capturing
                    or cancel_event.is_set()
                ):
                    exit_reason = "stop_requested"
                    break

                elapsed = time.perf_counter() - t_start
                if self._capture_deadline_reached(elapsed, speech_started):
                    # Flush any pending segments
                    self._set_capture_phase(cancel_event, "vad_flush")
                    self._vad.flush()
                    flush_segments: list[dict[str, Any]] = []
                    while not self._vad.empty():
                        seg = self._vad.front
                        if self._audio_debug.enabled:
                            debug_segments.append(self._debug_segment(seg))
                        # Drain once using merged source intervals below.
                        flush_segments.append(self._debug_segment(seg))
                        self._vad.pop()

                    speech_segment = self._merge_speech_segments(
                        flush_segments,
                        np.asarray(all_audio, dtype=np.float32),
                    )

                    if speech_segment is not None and len(speech_segment) > 0:
                        duration_ms = (
                            len(speech_segment) / self._sample_rate
                        ) * 1000.0
                        logger.info(
                            "VAD got speech at timeout: %.1fms", duration_ms,
                        )
                        exit_reason = "vad_flush_voice"
                        break

                    # True timeout — no speech at all
                    logger.debug("VAD max duration reached, no speech detected")
                    exit_reason = "max_duration_silence"
                    break

                # Never enter a blocking read until PortAudio reports that a
                # complete chunk is ready.  This bounds cancellation latency.
                self._set_capture_phase(cancel_event, "sounddevice_wait")
                if not before_read_logged:
                    logger.debug(
                        "before_audio_read utterance_id=%s backend=sounddevice",
                        utterance_id,
                    )
                    before_read_logged = True
                chunk = self._read_sounddevice_chunk(stream, cancel_event)
                if chunk is None:
                    continue
                if not first_read_logged:
                    chunk_array = np.asarray(chunk)
                    channel_count = (
                        int(chunk_array.shape[1])
                        if chunk_array.ndim == 2 else 1
                    )
                    logger.debug(
                        "after_audio_read utterance_id=%s backend=sounddevice "
                        "num_samples=%d shape=%s dtype=%s channels=%d",
                        utterance_id,
                        int(chunk_array.size),
                        tuple(chunk_array.shape),
                        str(chunk_array.dtype),
                        channel_count,
                    )
                    if self._audio_debug.enabled:
                        logger.info(
                            "audio_capture_format utterance_id=%s "
                            "backend=sounddevice sample_rate=%d shape=%s "
                            "dtype=%s channels=%d",
                            utterance_id,
                            self._sample_rate,
                            tuple(chunk_array.shape),
                            str(chunk_array.dtype),
                            channel_count,
                        )
                    if channel_count != 1:
                        logger.error(
                            "audio_debug microphone channel mismatch "
                            "utterance_id=%s channels=%d expected=1",
                            utterance_id,
                            channel_count,
                        )
                    first_read_logged = True
                if cancel_event.is_set() or not self._capturing:
                    exit_reason = "stop_requested_after_read"
                    break
                chunk = chunk.flatten()
                all_audio.extend(chunk.tolist())
                self._notify_chunk(chunk)

                # Feed to VAD
                self._set_capture_phase(cancel_event, "vad_process")
                if not first_vad_logged:
                    logger.debug("before_vad utterance_id=%s", utterance_id)
                self._vad.accept_waveform(chunk.tolist())
                self._refresh_speech_active()
                if speech_started is None and self.is_speech_active():
                    speech_started = time.perf_counter() - t_start
                if not first_vad_logged:
                    logger.debug("after_vad utterance_id=%s", utterance_id)
                    first_vad_logged = True
                elapsed = time.perf_counter() - t_start

                # ── Check for completed speech segment ──
                if not self._vad.empty():
                    # VAD found a complete utterance!
                    segment = self._vad.front
                    if self._audio_debug.enabled:
                        debug_segments.append(self._debug_segment(segment))
                    samples = self._segment_with_pre_roll(
                        segment,
                        np.asarray(all_audio, dtype=np.float32),
                    )
                    if len(samples) > 0:
                        speech_segment = samples
                        self._vad.pop()
                        duration_ms = (len(samples) / self._sample_rate) * 1000.0
                        logger.info(
                            "VAD detected speech: %.1fms (%.1fs into capture)",
                            duration_ms, elapsed,
                        )
                        exit_reason = "vad_complete"
                        break

        except sd.PortAudioError as exc:
            if cancel_event.is_set():
                logger.debug("sounddevice capture interrupted for cancellation")
            else:
                print(
                    f"[VAD] sounddevice error: {exc} — fallback to arecord",
                    flush=True,
                )
                logger.error("sounddevice error: %s", exc)
                fallback_to_arecord = True
        except Exception as exc:
            if cancel_event.is_set():
                logger.debug("VAD stream interrupted for cancellation: %s", exc)
            else:
                import traceback as _tb
                print(f"[VAD] stream error: {exc}", flush=True)
                _tb.print_exc()
                logger.error("VAD stream error: %s", exc, exc_info=True)
        finally:
            logger.debug(
                "leaving_loop utterance_id=%s reason=%s phase=%s",
                utterance_id,
                exit_reason,
                self._capture_phase,
            )
            logger.debug(
                "releasing_resources utterance_id=%s backend=sounddevice",
                utterance_id,
            )
            with self._capture_lock:
                if self._active_input_stream is stream:
                    self._active_input_stream = None
            if stream is not None:
                self._set_capture_phase(cancel_event, "sounddevice_close")
                if not cancel_event.is_set():
                    try:
                        stream.stop()
                    except Exception:
                        pass
                try:
                    stream.close()
                except Exception:
                    pass

        if fallback_to_arecord and not cancel_event.is_set():
            return self._stream_vad_arecord(cancel_event)

        if speech_segment is not None and len(speech_segment) > 0:
            duration_ms = (len(speech_segment) / self._sample_rate) * 1000.0
            result = {
                "audio_samples": speech_segment,
                "sample_rate": self._sample_rate,
                "duration_ms": duration_ms,
                "has_voice": True,
            }
        else:
            # No speech detected
            result = {
                "audio_samples": np.array(all_audio, dtype=np.float32),
                "sample_rate": self._sample_rate,
                "duration_ms": (len(all_audio) / self._sample_rate) * 1000.0,
                "has_voice": False,
            }
        if self._audio_debug.enabled and not cancel_event.is_set():
            result["debug_audio_dir"] = self._save_capture_debug(
                utterance_id,
                np.asarray(all_audio, dtype=np.float32),
                debug_segments,
                np.asarray(result["audio_samples"], dtype=np.float32),
            )
            result["utterance_id"] = utterance_id
        return result

    def _stream_vad_arecord(
        self,
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        """Fallback: stream arecord output through KWS and VAD."""
        import subprocess

        all_audio: list[np.ndarray] = []
        speech_segment: np.ndarray | None = None
        debug_segments: list[dict[str, Any]] = []
        process: subprocess.Popen[bytes] | None = None
        before_read_logged = False
        first_read_logged = False
        first_vad_logged = False
        exit_reason = "unknown"
        with self._capture_lock:
            utterance_id = self._active_utterance_id
        try:
            self._set_capture_phase(cancel_event, "arecord_open")
            cmd = [
                "arecord",
                "-f", "S16_LE",
                "-r", str(self._sample_rate),
                "-c", "1",
                "-t", "raw",
            ]
            if self._device:
                cmd.extend(["-D", str(self._device)])

            logger.info(
                "VAD: streaming arecord fallback for up to %.0fs",
                self._max_duration_sec,
            )
            self._vad.reset()
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
            )
            with self._capture_lock:
                if (
                    self._capture_cancel_event is cancel_event
                    and not cancel_event.is_set()
                ):
                    self._active_arecord_process = process
            if process.stdout is None:
                raise RuntimeError("arecord stdout pipe was not created")

            started = time.perf_counter()
            speech_started: float | None = None
            last_read = started
            pending = bytearray()
            chunk_bytes = _CHUNK_SAMPLES * 2
            while self._capturing and not cancel_event.is_set():
                self._set_capture_phase(cancel_event, "arecord_read")
                if not before_read_logged:
                    logger.debug(
                        "before_audio_read utterance_id=%s backend=arecord",
                        utterance_id,
                    )
                    before_read_logged = True
                elapsed = time.perf_counter() - started
                if self._capture_deadline_reached(elapsed, speech_started):
                    exit_reason = "capture_deadline"
                    break
                raw = read_pipe_chunk(process.stdout, chunk_bytes)
                if raw is None:
                    if time.perf_counter() - last_read >= self._read_timeout_sec:
                        raise TimeoutError("arecord produced no audio")
                    continue
                if raw == b"":
                    exit_reason = "arecord_eof"
                    break
                if not first_read_logged:
                    logger.debug(
                        "after_audio_read utterance_id=%s backend=arecord "
                        "num_bytes=%d",
                        utterance_id,
                        len(raw),
                    )
                    first_read_logged = True
                if cancel_event.is_set() or not self._capturing:
                    exit_reason = "stop_requested_after_read"
                    break
                last_read = time.perf_counter()
                pending.extend(raw)
                complete_bytes = len(pending) // 2 * 2
                raw = bytes(pending[:complete_bytes])
                del pending[:complete_bytes]
                samples = (
                    np.frombuffer(raw, dtype="<i2").astype(np.float32)
                    / 32768.0
                )
                if samples.size == 0:
                    continue
                all_audio.append(samples)
                if (
                    first_read_logged
                    and len(all_audio) == 1
                    and self._audio_debug.enabled
                ):
                    logger.info(
                        "audio_capture_format utterance_id=%s backend=arecord "
                        "sample_rate=%d shape=%s dtype=%s channels=1 "
                        "source_encoding=PCM16_LE normalized=true",
                        utterance_id,
                        self._sample_rate,
                        tuple(samples.shape),
                        str(samples.dtype),
                    )
                self._notify_chunk(samples)
                self._set_capture_phase(cancel_event, "vad_process")
                if not first_vad_logged:
                    logger.debug("before_vad utterance_id=%s", utterance_id)
                self._vad.accept_waveform(samples.tolist())
                self._refresh_speech_active()
                if speech_started is None and self.is_speech_active():
                    speech_started = time.perf_counter() - started
                if not first_vad_logged:
                    logger.debug("after_vad utterance_id=%s", utterance_id)
                    first_vad_logged = True

                if not self._vad.empty():
                    segment = self._vad.front
                    if self._audio_debug.enabled:
                        debug_segments.append(self._debug_segment(segment))
                    speech_segment = self._segment_with_pre_roll(
                        segment,
                        np.concatenate(all_audio),
                    )
                    self._vad.pop()
                    if speech_segment.size:
                        exit_reason = "vad_complete"
                        break

                if self._capture_deadline_reached(time.perf_counter() - started, speech_started):
                    exit_reason = "max_duration"
                    break

            if speech_segment is None and not cancel_event.is_set():
                self._vad.flush()
                speech_parts: list[dict[str, Any]] = []
                while not self._vad.empty():
                    segment = self._vad.front
                    if self._audio_debug.enabled:
                        debug_segments.append(self._debug_segment(segment))
                    captured = (
                        np.concatenate(all_audio)
                        if all_audio
                        else np.array([], dtype=np.float32)
                    )
                    speech_parts.append(self._debug_segment(segment))
                    self._vad.pop()
                if speech_parts:
                    speech_segment = self._merge_speech_segments(speech_parts, captured)

        except FileNotFoundError:
            logger.error("arecord not found")
        except Exception as exc:
            logger.error("arecord error: %s", exc, exc_info=True)
        finally:
            logger.debug(
                "leaving_loop utterance_id=%s reason=%s phase=%s",
                utterance_id,
                exit_reason,
                self._capture_phase,
            )
            logger.debug(
                "releasing_resources utterance_id=%s backend=arecord",
                utterance_id,
            )
            self._set_capture_phase(cancel_event, "arecord_close")
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            with self._capture_lock:
                if self._active_arecord_process is process:
                    self._active_arecord_process = None

        captured = (
            np.concatenate(all_audio)
            if all_audio
            else np.array([], dtype=np.float32)
        )
        if speech_segment is not None and speech_segment.size:
            duration_ms = (
                speech_segment.size / self._sample_rate
            ) * 1000.0
            logger.info(
                "VAD (arecord): detected %.1fms speech",
                duration_ms,
            )
            result = {
                "audio_samples": speech_segment,
                "sample_rate": self._sample_rate,
                "duration_ms": duration_ms,
                "has_voice": True,
            }
        else:
            result = {
                "audio_samples": captured,
                "sample_rate": self._sample_rate,
                "duration_ms": (captured.size / self._sample_rate) * 1000.0,
                "has_voice": False,
            }
        if self._audio_debug.enabled and not cancel_event.is_set():
            result["debug_audio_dir"] = self._save_capture_debug(
                utterance_id,
                captured,
                debug_segments,
                np.asarray(result["audio_samples"], dtype=np.float32),
            )
            result["utterance_id"] = utterance_id
        return result

    def has_voice(self, audio_data: dict[str, Any] | None = None) -> bool:
        """Check if audio segment contains voice activity."""
        if audio_data is not None:
            return audio_data.get("has_voice", False)
        return False

    def _capture_deadline_reached(self, elapsed: float, speech_started: float | None) -> bool:
        if speech_started is None:
            return elapsed >= self._max_duration_sec
        return elapsed - speech_started >= self._max_speech_duration_sec

    def _merge_speech_segments(
        self, segments: list[dict[str, Any]], captured: np.ndarray,
    ) -> np.ndarray:
        """Slice each source sample once, including overlapping pre-roll."""
        pre = 0 if self._debug_disable_extra_pre_roll else int(self._pre_roll_sec * self._sample_rate)
        ranges: list[tuple[int, int]] = []
        for segment in sorted(segments, key=lambda item: item["start"]):
            start = max(0, int(segment["start"]) - pre)
            end = min(captured.size, int(segment["end"]))
            if end <= start:
                continue
            if ranges and start <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(end, ranges[-1][1]))
            else:
                ranges.append((start, end))
        if not ranges:
            return np.array([], dtype=np.float32)
        return np.concatenate([captured[start:end] for start, end in ranges])

    def _stream_vad_buffered(self, cancel_event: threading.Event) -> dict[str, Any]:
        """Consume one utterance while the microphone keeps buffering the next."""
        capture = self._ensure_buffered_capture()
        self._vad.reset()
        with self._capture_lock:
            utterance_id = self._active_utterance_id
        chunks: list[np.ndarray] = []
        segments: list[dict[str, Any]] = []
        sample_count = 0
        resync_samples = 0
        quiet_samples = 0
        speech_started: float | None = None
        last_read = time.monotonic()
        exit_reason = "cancelled"
        try:
            while not cancel_event.is_set():
                self._set_capture_phase(cancel_event, "buffered_read")
                chunk = capture.read()
                if chunk is None:
                    # Allow the producer to try both backends, each with its
                    # own no-data deadline; also bound a stuck device open.
                    if time.monotonic() - last_read >= 2 * self._read_timeout_sec + 1.0:
                        raise TimeoutError("microphone produced no audio")
                    continue
                last_read = time.monotonic()
                if self._resync_required:
                    # After lost audio, wait for a quiet boundary before KWS
                    # or ASR can see anything. Never treat a suffix as a command.
                    self._vad.accept_waveform(chunk.tolist())
                    self._refresh_speech_active()
                    resync_samples += chunk.size
                    quiet_samples = 0 if self.is_speech_active() else quiet_samples + chunk.size
                    while not self._vad.empty():
                        self._vad.pop()
                    if quiet_samples >= (self._min_silence_dur + self._min_speech_dur) * self._sample_rate:
                        self._resync_required = False
                        self._vad.reset()
                        logger.info("VAD resynchronized after audio gap: %s", utterance_id)
                    elif resync_samples / self._sample_rate >= self._max_duration_sec:
                        exit_reason = "audio_resync_timeout"
                        break
                    continue
                chunks.append(chunk)
                sample_count += chunk.size
                elapsed = sample_count / self._sample_rate
                # KWS sees only this consumer's utterance, never queued future audio.
                self._notify_chunk(chunk)
                self._vad.accept_waveform(chunk.tolist())
                self._refresh_speech_active()
                if speech_started is None and self.is_speech_active():
                    speech_started = elapsed
                if not self._vad.empty():
                    exit_reason = "vad_complete"
                    break
                if self._capture_deadline_reached(elapsed, speech_started):
                    self._vad.flush()
                    exit_reason = "speech_limit" if speech_started is not None else "wait_timeout"
                    break
            if not cancel_event.is_set():
                while not self._vad.empty():
                    segments.append(self._debug_segment(self._vad.front))
                    self._vad.pop()
        except AudioCaptureGap as exc:
            # Discard the entire current utterance, not just the missing chunk.
            exit_reason = "audio_gap"
            self._resync_required = True
            segments.clear()
            logger.warning("Discarding discontinuous utterance %s: %s", utterance_id, exc)
        except Exception:
            self._resync_required = True
            if capture.close():
                self._buffered_capture = None
            raise
        captured = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
        speech = self._merge_speech_segments(segments, captured)
        result = {
            "audio_samples": speech, "sample_rate": self._sample_rate,
            "duration_ms": speech.size / self._sample_rate * 1000.0,
            "has_voice": bool(speech.size), "utterance_id": utterance_id,
            "capture_end_reason": exit_reason,
        }
        if self._audio_debug.enabled and not cancel_event.is_set():
            result["debug_audio_dir"] = self._save_capture_debug(
                utterance_id, captured, segments, speech,
            )
        logger.debug("VAD capture ended: utterance_id=%s reason=%s", utterance_id, exit_reason)
        return result

    def _segment_with_pre_roll(
        self,
        segment: Any,
        captured_audio: np.ndarray,
    ) -> np.ndarray:
        """Prepend raw audio before the VAD start so quiet initials survive."""
        samples = np.asarray(segment.samples, dtype=np.float32)
        if (
            samples.size == 0
            or self._pre_roll_sec <= 0
            or self._debug_disable_extra_pre_roll
        ):
            return samples

        segment_start = int(getattr(segment, "start", 0))
        prefix_end = min(max(segment_start, 0), captured_audio.size)
        prefix_samples = int(self._pre_roll_sec * self._sample_rate)
        prefix_start = max(0, prefix_end - prefix_samples)
        if prefix_start == prefix_end:
            return samples
        return np.concatenate((captured_audio[prefix_start:prefix_end], samples))

    def _notify_chunk(self, samples: np.ndarray) -> None:
        callback = self._chunk_callback
        if callback is None:
            return
        try:
            callback(samples, self._sample_rate)
        except Exception as exc:
            logger.warning("Audio chunk callback failed: %s", exc)
