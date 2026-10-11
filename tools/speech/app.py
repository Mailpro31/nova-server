"""Service de transcription au format OpenAI : faster-whisper ou transcribe.cpp.

`SPEECH_ENGINE` choisit le moteur :
- `faster_whisper` : Whisper (turbo, large-v3, large-v3-french…), avec le
  repli de température et un faisceau de 5. Banc v2 du RTX (11/10) : le même
  turbo fait 0,117 d'erreurs normalisées ainsi réglé, contre 0,235 derrière
  `faster-whisper-server`, qui fixe la température à 0 ;
- `transcribe_cpp` : un modèle GGUF (Cohere Transcribe, Canary…).

Le banc v2 du RTX (11/10, 21 dictées de Sash) : Cohere Transcribe en GGUF fait
8,9 % d'erreurs normalisées, contre 23,5 % pour le Whisper turbo du service.
transcribe.cpp n'a pas de mode serveur : ce service l'expose sous la même forme
que `faster-whisper-server`, pour que le serveur Nova l'appelle sans changer de
code. Seuls `NOVA_WHISPER_URL` et `NOVA_WHISPER_MODEL` changent.

- `GET /health` : `ready`, ou `loading` pendant le chargement du modèle ;
- `POST /v1/audio/transcriptions` : le fichier audio, `language`,
  `response_format` (`json` ou `verbose_json`). Un audio illisible reçoit 400,
  que le serveur Nova rend en `INVALID_AUDIO`.

Une seule transcription à la fois par modèle (transcribe.cpp 0.x partage le
moteur de calcul entre les sessions) ; la file d'attente est celle du serveur
Nova (`NOVA_AI_MAX_QUEUE`).

Le modèle ne rend pas de confiance par passage (`avg_logprob`) : la détection
« dictée incomprise » du serveur s'appuie alors sur les boucles et les crédits
de sous-titres seulement.
"""

from __future__ import annotations

import array
import asyncio
import io
import os
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass, field

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = int(os.getenv("SPEECH_MAX_AUDIO_BYTES", str(200 * 1024 * 1024)))
DEFAULT_LANGUAGE = os.getenv("SPEECH_DEFAULT_LANGUAGE", "fr") or None
# En dessous, aucun mot ne peut avoir été dit.
MIN_AUDIO_SECONDS = 0.05


@dataclass
class Segment:
    start: float
    end: float
    text: str
    avg_logprob: float | None = None


@dataclass
class Transcript:
    text: str
    language: str | None = None
    segments: list[Segment] = field(default_factory=list)


class AudioUnreadable(ValueError):
    """L'enregistrement ne se lit pas comme de l'audio."""


def pcm16_to_float(frames: bytes, channels: int) -> array.array:
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        mono = array.array("h", bytes(2 * (len(samples) // channels)))
        for index in range(len(mono)):
            frame = samples[index * channels:(index + 1) * channels]
            mono[index] = int(sum(frame) / channels)
        samples = mono
    return array.array("f", (sample / 32768.0 for sample in samples))


def decode_audio(data: bytes) -> array.array:
    """PCM mono 16 kHz en float32, ce que prend transcribe.cpp.

    Un WAV 16 bits à 16 kHz (ce qu'envoie le poste Nova) se lit directement ;
    tout autre format passe par ffmpeg.
    """
    if not data:
        raise AudioUnreadable("empty")
    try:
        with wave.open(io.BytesIO(data), "rb") as recording:
            if recording.getsampwidth() == 2 and recording.getframerate() == SAMPLE_RATE:
                return pcm16_to_float(
                    recording.readframes(recording.getnframes()), recording.getnchannels())
    except (wave.Error, EOFError):
        pass
    return decode_with_ffmpeg(data)


def decode_with_ffmpeg(data: bytes) -> array.array:
    try:
        converted = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0",
             "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
            input=data, capture_output=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AudioUnreadable("ffmpeg") from error
    if converted.returncode != 0:
        raise AudioUnreadable("ffmpeg")
    return pcm16_to_float(converted.stdout, 1)


class TranscribeCppEngine:
    """Le modèle GGUF, chargé une fois, une transcription à la fois."""

    def __init__(self, model_path: str, backend: str = "auto"):
        self.model_path = model_path
        self.backend = backend
        self.version = None
        self._model = None
        self._lock = threading.Lock()
        self.error: str | None = None
        # Cohere Transcribe ne rend pas d'horodatage par passage : il répond
        # « unsupported timestamp granularity » (UnsupportedRequest). Mesuré
        # sur la carte du RTX le 11/10 : 100 % de 502 avant ce repli.
        self._timestamps = "segment"

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        try:
            import transcribe_cpp  # noqa: PLC0415 — chargé avec le moteur natif

            self.version = f"transcribe.cpp {transcribe_cpp.native_version()}"
            self._model = transcribe_cpp.Model(self.model_path, backend=self.backend)
        except Exception as error:  # noqa: BLE001 — l'état est rendu par /health
            self.error = type(error).__name__
            raise

    def transcribe(self, pcm: array.array, language: str | None) -> Transcript:
        with self._lock, self._model.session() as session:
            try:
                result = session.run(pcm, timestamps=self._timestamps, language=language)
            except Exception as error:  # noqa: BLE001 — seule cette erreur se rattrape
                if type(error).__name__ != "UnsupportedRequest" or self._timestamps == "none":
                    raise
                # Le texte entier fait alors un seul passage.
                self._timestamps = "none"
                result = session.run(pcm, timestamps="none", language=language)
        segments = [
            Segment(segment.t0_ms / 1000, segment.t1_ms / 1000, segment.text)
            for segment in (result.segments or [])
        ]
        return Transcript(result.text.strip(), result.language or language, segments)


class FasterWhisperEngine:
    """Whisper par faster-whisper, réglé comme sur le banc du RTX (11/10).

    Les réglages par défaut de faster-whisper : faisceau de 5 et repli de
    température (0,0 → 1,0) quand un passage échoue aux contrôles de
    compression ou de probabilité. La confiance de chaque passage
    (`avg_logprob`) est rendue : le serveur Nova s'en sert pour repérer une
    dictée incomprise.
    """

    def __init__(self, model: str, device: str = "auto", compute_type: str = "default",
                 beam_size: int = 5):
        self.model_path = model
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.version = None
        self._model = None
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        try:
            import faster_whisper  # noqa: PLC0415 — chargé avec le moteur

            self.version = f"faster-whisper {faster_whisper.__version__}"
            self._model = faster_whisper.WhisperModel(
                self.model_path, device=self.device, compute_type=self.compute_type)
        except Exception as error:  # noqa: BLE001 — l'état est rendu par /health
            self.error = type(error).__name__
            raise

    def transcribe(self, pcm: array.array, language: str | None) -> Transcript:
        import numpy  # noqa: PLC0415 — dépendance de faster-whisper

        audio = numpy.frombuffer(pcm.tobytes(), dtype=numpy.float32)
        with self._lock:
            segments, info = self._model.transcribe(
                audio, language=language, beam_size=self.beam_size)
            found = [
                Segment(segment.start, segment.end, segment.text, segment.avg_logprob)
                for segment in segments
            ]
        text = "".join(segment.text for segment in found).strip()
        return Transcript(text, getattr(info, "language", None) or language, found)


def engine_from_environment():
    if os.getenv("SPEECH_ENGINE", "transcribe_cpp") == "faster_whisper":
        return FasterWhisperEngine(
            os.getenv("SPEECH_MODEL", "large-v3-turbo"),
            os.getenv("SPEECH_DEVICE", "auto"),
            os.getenv("SPEECH_COMPUTE_TYPE", "default"),
            int(os.getenv("SPEECH_BEAM_SIZE", "5")),
        )
    return TranscribeCppEngine(
        os.getenv("SPEECH_MODEL_PATH", "/models/speech.gguf"),
        os.getenv("SPEECH_BACKEND", "auto"),
    )


def create_app(engine=None) -> FastAPI:
    if engine is None:
        engine = engine_from_environment()
    app = FastAPI(title="Nova speech")
    app.state.engine = engine
    model_name = os.getenv("SPEECH_MODEL_NAME", os.path.basename(getattr(engine, "model_path", "")))

    @app.on_event("startup")
    async def load_model() -> None:
        if not engine.ready:
            # En arrière-plan : /health répond `loading` pendant le chargement.
            threading.Thread(target=engine.load, daemon=True).start()

    @app.get("/health")
    async def health():
        if engine.ready:
            return {"status": "ready", "version": engine.version, "model": model_name}
        if getattr(engine, "error", None):
            return JSONResponse({"status": "error", "error": engine.error}, status_code=500)
        return JSONResponse({"status": "loading"}, status_code=503)

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(
        file: UploadFile = File(...),
        model: str | None = Form(default=None),
        language: str | None = Form(default=None),
        prompt: str | None = Form(default=None),
        response_format: str = Form(default="json"),
    ):
        del model, prompt  # un seul modèle par service ; pas d'amorce ici
        if not engine.ready:
            raise HTTPException(425, "model loading")
        data = await file.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "audio too large")
        try:
            pcm = await asyncio.to_thread(decode_audio, data)
        except AudioUnreadable as error:
            raise HTTPException(400, "invalid audio") from error
        started = time.monotonic()
        duration = len(pcm) / SAMPLE_RATE
        if duration < MIN_AUDIO_SECONDS:
            # Un WAV réduit à son en-tête : transcribe.cpp lève « empty PCM
            # buffer » (500, puis 502 côté Nova ; relevé par le RTX le 11/10),
            # faster-whisper rendait un texte vide. Rien à transcrire.
            transcript = Transcript("", language or DEFAULT_LANGUAGE)
        else:
            transcript = await asyncio.to_thread(
                engine.transcribe, pcm, (language or DEFAULT_LANGUAGE))
        if response_format != "verbose_json":
            return {"text": transcript.text}
        return {
            "text": transcript.text,
            "language": transcript.language,
            "duration": duration,
            "segments": [
                {"id": index, "start": segment.start, "end": segment.end, "text": segment.text,
                 **({"avg_logprob": segment.avg_logprob}
                    if segment.avg_logprob is not None else {})}
                for index, segment in enumerate(transcript.segments)
            ],
            "latency_ms": round((time.monotonic() - started) * 1000),
        }

    return app


if __name__ == "__main__":  # pragma: no cover — lancé par le conteneur
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
