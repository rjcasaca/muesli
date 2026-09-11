"""Speech-to-text backends. Each returns [(start_seconds_within_chunk, text), ...] for one mono PCM chunk."""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from .audio import pcm_to_wav
from .config import Config

Segment = tuple[float, str]


def words_to_segments(words: list[dict], gap: float = 1.0) -> list[Segment]:
    """Group word-level output into sentence-ish segments."""
    segs: list[Segment] = []
    cur: list[str] = []
    start = 0.0
    prev_end = 0.0
    for w in words:
        text = str(w.get("text", w.get("word", ""))).strip()
        if not text:
            continue
        ws, we = float(w.get("start", prev_end)), float(w.get("end", prev_end))
        if cur and ws - prev_end > gap:
            segs.append((start, " ".join(cur)))
            cur = []
        if not cur:
            start = ws
        cur.append(text)
        prev_end = we
        if text[-1] in ".?!":
            segs.append((start, " ".join(cur)))
            cur = []
    if cur:
        segs.append((start, " ".join(cur)))
    return segs


class Backend:
    name = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = httpx.AsyncClient(timeout=180)

    async def transcribe(self, pcm: bytes) -> list[Segment]:
        raise NotImplementedError

    async def close(self) -> None:
        await self.client.aclose()

    @property
    def vocab_prompt(self) -> str:
        return ", ".join(self.cfg.vocabulary)


class OpenAICompat(Backend):
    """Any /v1/audio/transcriptions endpoint: OpenAI, Groq, local servers (whisper.cpp server, faster-whisper-server)."""

    name = "openai"

    async def transcribe(self, pcm: bytes) -> list[Segment]:
        s = self.cfg.stt
        data = {"model": s.model, "response_format": "verbose_json"}
        if self.cfg.language:
            data["language"] = self.cfg.language
        if self.vocab_prompt:
            data["prompt"] = self.vocab_prompt
        r = await self.client.post(
            f"{s.base_url.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.cfg.env(s.api_key_env)}"},
            files={"file": ("chunk.wav", pcm_to_wav(pcm), "audio/wav")},
            data=data,
        )
        r.raise_for_status()
        j = r.json()
        segs = [(float(x.get("start", 0.0)), x.get("text", "").strip()) for x in j.get("segments", [])]
        segs = [x for x in segs if x[1]]
        if not segs and j.get("text", "").strip():
            segs = [(0.0, j["text"].strip())]
        return segs


class XaiSTT(Backend):
    """xAI Grok Speech-to-Text: POST https://api.x.ai/v1/stt (word-level timestamps)."""

    name = "xai"

    async def transcribe(self, pcm: bytes) -> list[Segment]:
        s = self.cfg.stt
        data: dict[str, str] = {"format": "true" if self.cfg.language else "false", "timestamps": "true"}
        if self.cfg.language:
            data["language"] = self.cfg.language
        if self.vocab_prompt:
            data["keyterm"] = self.vocab_prompt
        # xAI requires the file part after all other fields
        parts = [(k, (None, v)) for k, v in data.items()] + [("file", ("chunk.wav", pcm_to_wav(pcm), "audio/wav"))]
        r = await self.client.post(
            "https://api.x.ai/v1/stt",
            headers={"Authorization": f"Bearer {self.cfg.env(s.xai_api_key_env)}"},
            files=parts,
        )
        r.raise_for_status()
        j = r.json()
        segs = words_to_segments(j.get("words", []))
        if not segs and j.get("text", "").strip():
            segs = [(0.0, j["text"].strip())]
        return segs


class FasterWhisper(Backend):
    name = "local"
    _model = None
    _lock = asyncio.Lock()

    def _load(self):
        if FasterWhisper._model is None:
            from faster_whisper import WhisperModel  # optional dependency

            s = self.cfg.stt
            FasterWhisper._model = WhisperModel(s.local_model, device=s.local_device, compute_type=s.local_compute)
        return FasterWhisper._model

    def _run(self, pcm: bytes) -> list[Segment]:
        model = self._load()
        segments, _ = model.transcribe(
            io.BytesIO(pcm_to_wav(pcm)),
            language=self.cfg.language or None,
            initial_prompt=self.vocab_prompt or None,
            vad_filter=True,
            beam_size=5,
        )
        return [(float(x.start), x.text.strip()) for x in segments if x.text.strip()]

    async def transcribe(self, pcm: bytes) -> list[Segment]:
        async with self._lock:  # one GPU/CPU job at a time
            return await asyncio.to_thread(self._run, pcm)


class WhisperCpp(Backend):
    name = "whisper-cpp"
    _lock = asyncio.Lock()

    def _run(self, pcm: bytes) -> list[Segment]:
        s = self.cfg.stt
        binary = shutil.which(s.whisper_cpp_bin)
        if not binary:
            raise RuntimeError(f"{s.whisper_cpp_bin} not found on PATH (install whisper.cpp)")
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "chunk.wav"
            wav.write_bytes(pcm_to_wav(pcm))
            args = [binary, "-m", s.local_model, "-f", str(wav), "-oj", "-of", str(Path(td) / "out"), "-np"]
            if self.cfg.language:
                args += ["-l", self.cfg.language]
            if self.vocab_prompt:
                args += ["--prompt", self.vocab_prompt]
            subprocess.run(args, check=True, capture_output=True)
            j = json.loads((Path(td) / "out.json").read_text())
        out = []
        for seg in j.get("transcription", []):
            text = seg.get("text", "").strip()
            if text:
                out.append((float(seg.get("offsets", {}).get("from", 0)) / 1000.0, text))
        return out

    async def transcribe(self, pcm: bytes) -> list[Segment]:
        async with self._lock:
            return await asyncio.to_thread(self._run, pcm)


def get_backend(cfg: Config) -> Backend:
    p = cfg.stt.provider
    if p == "openai":
        return OpenAICompat(cfg)
    if p == "xai":
        return XaiSTT(cfg)
    if p == "local":
        return WhisperCpp(cfg) if cfg.stt.local_engine == "whisper-cpp" else FasterWhisper(cfg)
    raise RuntimeError(f"unknown stt.provider {p!r}")
