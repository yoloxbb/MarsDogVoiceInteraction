"""Audio (VAD) provider using sherpa-onnx VoiceActivityDetector.

Real-time VAD-driven capture — streams microphone audio to VAD chunk-by-chunk
until a complete speech segment is detected or timeout is reached.
No fixed-duration recording.

Requires: sherpa-onnx, sounddevice (or pyaudio)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import numpy as np

from marsdog_voice_interaction.providers.base import BaseProvider

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


class AudioSherpaProvider(BaseProvider):
    """VAD provider with real-time sherpa-onnx Silero VAD.

    Streams microphone audio to VAD in 20ms chunks. Speech detection
    is continuous — VAD fires when a complete utterance is detected
    (after min_silence_dur of silence following min_speech_dur of speech).

    Attributes:
        _vad: VoiceActivityDetector instance.
        _sample_rate: Audio sample rate (16000).
        _max_duration_sec: Max total recording before timeout (no speech).
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
        self._vad_threshold = float(config.get("vad_threshold", 0.6))
        self._min_silence_dur = float(config.get("min_silence_dur", 0.5))
        self._min_speech_dur = float(config.get("min_speech_dur", 0.4))
        self._pre_roll_sec = max(0.0, float(config.get("pre_roll_sec", 0.3)))
        self._num_threads = int(config.get("num_threads", 4))
        self._device = config.get("device")  # None = default mic

        self._vad: Any = None  # VoiceActivityDetector
        self._capture_thread: threading.Thread | None = None
        self._capture_result: dict[str, Any] | None = None
        self._capture_lock = threading.Lock()
        self._capturing = False
        self._speech_active = False
        self._capture_cancel_event: threading.Event | None = None
        self._active_input_stream: Any = None
        self._active_arecord_process: Any = None
        self._chunk_callback: (
            Callable[[np.ndarray, int], None] | None
        ) = None

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        try:
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
                config=VadModelConfig(silero_vad=vad_config),
                buffer_size_in_seconds=60,
            )

            if not _HAS_AUDIO_CAPTURE:
                logger.warning(
                    "No audio capture library — install sounddevice: "
                    "uv pip install sounddevice"
                )

            self.available = True
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

        except FileNotFoundError as exc:
            self.available = False
            logger.warning("AudioSherpaProvider unavailable: %s", exc)
        except Exception as exc:
            self.available = False
            logger.warning("AudioSherpaProvider unavailable: %s", exc, exc_info=True)

    def stop(self) -> None:
        self.cancel_capture()
        # Set _vad to None after capture cancellation so the worker cannot use
        # a released detector.
        self._vad = None
        self.available = False
        logger.info("AudioSherpaProvider stopped")

    # ── Public API ─────────────────────────────────────────────

    def set_chunk_callback(
        self,
        callback: Callable[[np.ndarray, int], None] | None,
    ) -> None:
        """Observe live microphone chunks without opening a second device."""
        self._chunk_callback = callback

    def start_capture(self) -> None:
        """Start background capture (non-blocking).

        Call this when wakeup is detected. The capture thread will
        stream audio to VAD until a speech segment is found or
        max_duration_sec elapses. Call poll_result() to check.
        """
        if not self.available:
            return
        if self._capturing:
            return  # already capturing
        if (
            self._capture_thread is not None
            and self._capture_thread.is_alive()
        ):
            logger.warning(
                "VAD capture start ignored because the previous worker "
                "has not exited"
            )
            return

        cancel_event = threading.Event()
        with self._capture_lock:
            self._capturing = True
            self._speech_active = False
            self._capture_result = None
            self._capture_cancel_event = cancel_event

        self._capture_thread = threading.Thread(
            target=self._capture_thread_fn,
            args=(cancel_event,),
            name="vad-capture",
            daemon=True,
        )
        self._capture_thread.start()
        logger.debug("VAD capture thread started")

    def cancel_capture(self, timeout: float = 2.0) -> bool:
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

        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            deadline = time.monotonic() + max(0.0, timeout)
            interrupted_stream: Any = None
            interrupted_process: Any = None
            while thread.is_alive():
                with self._capture_lock:
                    active_stream = self._active_input_stream
                    active_process = self._active_arecord_process
                if (
                    active_stream is not None
                    and active_stream is not interrupted_stream
                ):
                    self._interrupt_capture_backend(active_stream, None)
                    interrupted_stream = active_stream
                if (
                    active_process is not None
                    and active_process is not interrupted_process
                ):
                    self._interrupt_capture_backend(None, active_process)
                    interrupted_process = active_process
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                thread.join(timeout=min(0.1, remaining))

        worker_stopped = thread is None or not thread.is_alive()
        with self._capture_lock:
            self._capture_result = None
            if self._capture_thread is thread and worker_stopped:
                self._capture_thread = None
                self._capture_cancel_event = None

        if not worker_stopped:
            logger.error("VAD capture worker did not stop within %.1fs", timeout)
        return worker_stopped

    @staticmethod
    def _interrupt_capture_backend(
        stream: Any,
        process: Any,
    ) -> None:
        """Unblock a backend read so the worker can observe cancellation."""

        if stream is not None:
            # The capture worker owns the InputStream lifecycle and closes it
            # in _stream_vad()'s finally block.  Closing it here races that
            # cleanup and can make PortAudio free the same native stream twice.
            abort = getattr(stream, "abort", None)
            if callable(abort):
                try:
                    abort()
                except Exception:
                    pass
        if process is not None:
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
            Dict with audio_samples, sample_rate, duration_ms, has_voice,
            or None if capture is still in progress.
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

    def _refresh_speech_active(self) -> None:
        detected = getattr(self._vad, "is_speech_detected", False)
        if callable(detected):
            detected = detected()
        with self._capture_lock:
            self._speech_active = bool(detected)

    # ── Thread: streaming VAD capture ──────────────────────────

    def _capture_thread_fn(
        self,
        cancel_event: threading.Event,
    ) -> None:
        """Background thread: stream mic to VAD until speech detected."""
        try:
            result = self._stream_vad(cancel_event)
            with self._capture_lock:
                if self._capture_cancel_event is cancel_event:
                    if not cancel_event.is_set():
                        self._capture_result = result
                    self._capturing = False
                    self._speech_active = False
                    self._capture_thread = None
                    self._capture_cancel_event = None
        except Exception as exc:
            import traceback as _tb
            print(f"[VAD ERROR] {exc}", flush=True)
            _tb.print_exc()
            logger.error("VAD capture thread error: %s", exc, exc_info=True)
            with self._capture_lock:
                if self._capture_cancel_event is cancel_event:
                    if not cancel_event.is_set():
                        self._capture_result = {
                            "audio_samples": np.array([], dtype=np.float32),
                            "sample_rate": self._sample_rate,
                            "duration_ms": 0.0,
                            "has_voice": False,
                        }
                    self._capturing = False
                    self._speech_active = False
                    self._capture_thread = None
                    self._capture_cancel_event = None

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
        if not _HAS_AUDIO_CAPTURE:
            return self._stream_vad_arecord(cancel_event)

        self._vad.reset()

        # Accumulate all raw audio (for debugging / re-processing)
        all_audio: list[float] = []
        speech_segment: np.ndarray | None = None
        t_start = time.perf_counter()
        stream: Any = None
        fallback_to_arecord = False

        try:
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
            stream.start()

            while True:
                # Check for shutdown
                if (
                    self._vad is None
                    or not self._capturing
                    or cancel_event.is_set()
                ):
                    break

                # Read one chunk
                chunk, _ = stream.read(_CHUNK_SAMPLES)
                if cancel_event.is_set() or not self._capturing:
                    break
                chunk = chunk.flatten()
                all_audio.extend(chunk.tolist())
                self._notify_chunk(chunk)

                # Feed to VAD
                self._vad.accept_waveform(chunk.tolist())
                self._refresh_speech_active()
                elapsed = time.perf_counter() - t_start

                # ── Check for completed speech segment ──
                if not self._vad.empty():
                    # VAD found a complete utterance!
                    segment = self._vad.front
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
                        break

                # ── Timeout: no speech after max duration ──
                if elapsed > self._max_duration_sec:
                    # Flush any pending segments
                    self._vad.flush()
                    while not self._vad.empty():
                        seg = self._vad.front
                        s = self._segment_with_pre_roll(
                            seg,
                            np.asarray(all_audio, dtype=np.float32),
                        )
                        if len(s) > 0:
                            speech_segment = (
                                np.concatenate([speech_segment, s])
                                if speech_segment is not None
                                else s
                            )
                        self._vad.pop()

                    if speech_segment is not None and len(speech_segment) > 0:
                        duration_ms = (len(speech_segment) / self._sample_rate) * 1000.0
                        logger.info(
                            "VAD got speech at timeout: %.1fms", duration_ms,
                        )
                        break

                    # True timeout — no speech at all
                    logger.debug("VAD max duration reached, no speech detected")
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
            with self._capture_lock:
                if self._active_input_stream is stream:
                    self._active_input_stream = None
            if stream is not None:
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
            return {
                "audio_samples": speech_segment,
                "sample_rate": self._sample_rate,
                "duration_ms": duration_ms,
                "has_voice": True,
            }

        # No speech detected
        return {
            "audio_samples": np.array(all_audio, dtype=np.float32),
            "sample_rate": self._sample_rate,
            "duration_ms": (len(all_audio) / self._sample_rate) * 1000.0,
            "has_voice": False,
        }

    def _stream_vad_arecord(
        self,
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        """Fallback: stream arecord output through KWS and VAD."""
        import subprocess

        all_audio: list[np.ndarray] = []
        speech_segment: np.ndarray | None = None
        process: subprocess.Popen[bytes] | None = None
        try:
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
                stderr=subprocess.PIPE,
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
            chunk_bytes = _CHUNK_SAMPLES * 2
            while self._capturing and not cancel_event.is_set():
                raw = process.stdout.read(chunk_bytes)
                if not raw:
                    break
                if cancel_event.is_set() or not self._capturing:
                    break
                if len(raw) % 2:
                    raw = raw[:-1]
                samples = (
                    np.frombuffer(raw, dtype="<i2").astype(np.float32)
                    / 32768.0
                )
                if samples.size == 0:
                    continue
                all_audio.append(samples)
                self._notify_chunk(samples)
                self._vad.accept_waveform(samples.tolist())
                self._refresh_speech_active()

                if not self._vad.empty():
                    segment = self._vad.front
                    speech_segment = self._segment_with_pre_roll(
                        segment,
                        np.concatenate(all_audio),
                    )
                    self._vad.pop()
                    if speech_segment.size:
                        break

                if time.perf_counter() - started > self._max_duration_sec:
                    break

            if speech_segment is None and not cancel_event.is_set():
                self._vad.flush()
                speech_parts: list[np.ndarray] = []
                while not self._vad.empty():
                    segment = self._vad.front
                    captured = (
                        np.concatenate(all_audio)
                        if all_audio
                        else np.array([], dtype=np.float32)
                    )
                    samples = self._segment_with_pre_roll(
                        segment,
                        captured,
                    )
                    if samples.size:
                        speech_parts.append(samples)
                    self._vad.pop()
                if speech_parts:
                    speech_segment = np.concatenate(speech_parts)

        except FileNotFoundError:
            logger.error("arecord not found")
        except Exception as exc:
            logger.error("arecord error: %s", exc, exc_info=True)
        finally:
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

        if speech_segment is not None and speech_segment.size:
            duration_ms = (
                speech_segment.size / self._sample_rate
            ) * 1000.0
            logger.info(
                "VAD (arecord): detected %.1fms speech",
                duration_ms,
            )
            return {
                "audio_samples": speech_segment,
                "sample_rate": self._sample_rate,
                "duration_ms": duration_ms,
                "has_voice": True,
            }

        captured = (
            np.concatenate(all_audio)
            if all_audio
            else np.array([], dtype=np.float32)
        )

        return {
            "audio_samples": captured,
            "sample_rate": self._sample_rate,
            "duration_ms": (captured.size / self._sample_rate) * 1000.0,
            "has_voice": False,
        }

    def has_voice(self, audio_data: dict[str, Any] | None = None) -> bool:
        """Check if audio segment contains voice activity."""
        if audio_data is not None:
            return audio_data.get("has_voice", False)
        return False

    def _segment_with_pre_roll(
        self,
        segment: Any,
        captured_audio: np.ndarray,
    ) -> np.ndarray:
        """Prepend raw audio before the VAD start so quiet initials survive."""
        samples = np.asarray(segment.samples, dtype=np.float32)
        if samples.size == 0 or self._pre_roll_sec <= 0:
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
