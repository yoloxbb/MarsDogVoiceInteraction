"""FastAPI server for uploading WAV files into the speaker registry."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Path,
    Response,
    UploadFile,
)

from marsdog_voice_interaction.messages.speaker_identity import (
    SpeakerIdentity,
)


logger = logging.getLogger(__name__)


class SpeakerApiServer:
    """Run a bounded FastAPI/Uvicorn server in the ROS process."""

    def __init__(
        self,
        config: dict[str, Any],
        upload_handler: Callable[[str, bytes], dict[str, Any]],
        list_handler: Callable[[], dict[str, Any]] | None = None,
        sample_list_handler: (
            Callable[[str], dict[str, Any]] | None
        ) = None,
        sample_get_handler: (
            Callable[[str, int], dict[str, Any]] | None
        ) = None,
        sample_replace_handler: (
            Callable[[str, int, bytes], dict[str, Any]] | None
        ) = None,
        sample_delete_handler: (
            Callable[[str, int], dict[str, Any]] | None
        ) = None,
    ) -> None:
        self._config = dict(config)
        self._upload_handler = upload_handler
        self._list_handler = list_handler
        self._sample_list_handler = sample_list_handler
        self._sample_get_handler = sample_get_handler
        self._sample_replace_handler = sample_replace_handler
        self._sample_delete_handler = sample_delete_handler
        self._enabled = bool(config.get("enabled", True))
        self._host = str(config.get("host", "127.0.0.1")).strip()
        self._port = int(config.get("port", 8091))
        self._max_upload_bytes = max(
            1,
            int(float(config.get("max_upload_mb", 20.0)) * 1024 * 1024),
        )
        self._server: Any = None
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def address(self) -> str:
        return f"http://{self._host}:{self._port}"

    def _validate_host(self) -> None:
        if not self._host:
            raise RuntimeError("speaker_api.host 不能为空")

    def start(self) -> bool:
        if not self._enabled:
            return False
        self._validate_host()

        import uvicorn

        app = self.create_app()
        uvicorn_config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="info",
            log_config=None,
            access_log=True,
        )
        self._server = uvicorn.Server(uvicorn_config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="speaker-fastapi",
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if bool(getattr(self._server, "started", False)):
                logger.info("Speaker FastAPI ready: %s/docs", self.address)
                return True
            if not self._thread.is_alive():
                break
            time.sleep(0.02)
        self.stop()
        raise RuntimeError(f"声纹 FastAPI 启动失败: {self.address}")

    def stop(self) -> None:
        server = self._server
        if server is not None:
            server.should_exit = True
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=3.0)
        self._thread = None
        self._server = None

    def create_app(self) -> Any:
        app = FastAPI(
            title="MarsDog Voice Speaker API",
            version="2.0.0",
            description=(
                "Manage individual PCM16 WAV samples in fixed owner/family "
                "identity slots. Samples can be added, listed, downloaded, "
                "replaced, or deleted. Storage is config-owned."
            ),
        )

        def require_handler(handler: Any, operation: str) -> Any:
            if handler is None:
                raise HTTPException(
                    status_code=503,
                    detail=f"speaker {operation} is unavailable",
                )
            return handler

        def raise_for_result(result: dict[str, Any]) -> None:
            if result.get("ok", False):
                return
            error = str(result.get("error", "speaker operation failed"))
            configured_status = int(result.get("status", 0))
            if 400 <= configured_status <= 599:
                status = configured_status
            else:
                status = 503 if "不可用" in error or "未配置" in error else 422
            raise HTTPException(status_code=status, detail=error)

        async def run_handler(handler: Callable[..., Any], *args: Any) -> Any:
            # Uvicorn already runs in its own thread, separate from the ROS
            # executor. Keep biometric model operations serialized on that
            # API thread because the sherpa extractor is not thread-safe.
            return handler(*args)

        async def read_wav(audio: UploadFile) -> bytes:
            filename = str(audio.filename or "")
            if filename and not filename.lower().endswith(".wav"):
                raise HTTPException(status_code=415, detail="only WAV is supported")
            chunks: list[bytes] = []
            total = 0
            try:
                while True:
                    chunk = await audio.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self._max_upload_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail="uploaded audio is too large",
                        )
                    chunks.append(chunk)
            finally:
                await audio.close()
            if not chunks:
                raise HTTPException(status_code=400, detail="audio file is empty")
            return b"".join(chunks)

        @app.get("/health")
        async def health() -> dict[str, Any]:
            return {"ok": True, "service": "marsdog-voice-speaker-api"}

        @app.post(
            "/api/v1/speakers/{name}/samples",
            status_code=201,
        )
        async def add_speaker_sample(
            audio: UploadFile = File(...),
            name: SpeakerIdentity = Path(
                ...,
                description="owner 或 family_member_1 到 family_member_4",
            ),
        ) -> dict[str, Any]:
            payload = await read_wav(audio)
            result = await run_handler(
                self._upload_handler,
                name.value,
                payload,
            )
            raise_for_result(result)
            return {"request_id": uuid.uuid4().hex, **result}

        @app.get("/api/v1/speakers")
        async def list_speakers() -> dict[str, Any]:
            handler = require_handler(self._list_handler, "list")
            result = await run_handler(handler)
            raise_for_result(result)
            return result

        @app.get("/api/v1/speakers/{name}/samples")
        async def list_speaker_samples(
            name: SpeakerIdentity = Path(...),
        ) -> dict[str, Any]:
            handler = require_handler(self._sample_list_handler, "sample list")
            result = await run_handler(handler, name.value)
            raise_for_result(result)
            return result

        @app.get("/api/v1/speakers/{name}/samples/{sample_id}")
        async def get_speaker_sample(
            name: SpeakerIdentity = Path(...),
            sample_id: int = Path(..., ge=1, le=5),
        ) -> dict[str, Any]:
            handler = require_handler(self._sample_get_handler, "sample get")
            result = await run_handler(handler, name.value, sample_id)
            raise_for_result(result)
            return result

        @app.get("/api/v1/speakers/{name}/samples/{sample_id}/audio")
        async def download_speaker_sample_audio(
            name: SpeakerIdentity = Path(...),
            sample_id: int = Path(..., ge=1, le=5),
        ) -> Any:
            handler = require_handler(self._sample_get_handler, "sample get")
            result = await run_handler(handler, name.value, sample_id)
            raise_for_result(result)
            if not bool(result.get("audio_available", False)):
                raise HTTPException(
                    status_code=404,
                    detail="speaker sample audio not found",
                )
            try:
                with open(str(result["audio_path"]), "rb") as stream:
                    payload = stream.read()
            except OSError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="speaker sample audio not found",
                ) from exc
            return Response(
                content=payload,
                media_type="audio/wav",
                headers={
                    "Content-Disposition": (
                        "attachment; filename="
                        f'"{name.value}_{sample_id:03d}.wav"'
                    ),
                },
            )

        @app.put("/api/v1/speakers/{name}/samples/{sample_id}")
        async def replace_speaker_sample(
            audio: UploadFile = File(...),
            name: SpeakerIdentity = Path(...),
            sample_id: int = Path(..., ge=1, le=5),
        ) -> dict[str, Any]:
            payload = await read_wav(audio)
            handler = require_handler(
                self._sample_replace_handler,
                "sample replace",
            )
            result = await run_handler(
                handler,
                name.value,
                sample_id,
                payload,
            )
            raise_for_result(result)
            return {"request_id": uuid.uuid4().hex, **result}

        @app.delete("/api/v1/speakers/{name}/samples/{sample_id}")
        async def delete_speaker_sample(
            name: SpeakerIdentity = Path(...),
            sample_id: int = Path(..., ge=1, le=5),
        ) -> dict[str, Any]:
            handler = require_handler(
                self._sample_delete_handler,
                "sample delete",
            )
            result = await run_handler(handler, name.value, sample_id)
            raise_for_result(result)
            return {"request_id": uuid.uuid4().hex, **result}

        return app
