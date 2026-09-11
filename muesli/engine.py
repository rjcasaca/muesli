"""Recording session engine — no UI. Emits events; the daemon fans them out to clients."""
from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from importlib import resources
from pathlib import Path

from . import usage
from .audio import BYTES_PER_SEC, PwCapture, peak, pipewire_available
from .config import USER_TEMPLATES, Config, EnhanceConfig
from .stt import Backend, get_backend

Emit = Callable[[dict], Awaitable[None]]


def slug(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", text) or "meeting"


def mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def list_templates() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in resources.files("muesli.templates").iterdir():
        if p.name.endswith(".md"):
            out[p.name[:-3]] = Path(str(p))
    if USER_TEMPLATES.exists():
        for p in USER_TEMPLATES.glob("*.md"):
            out[p.stem] = p  # user templates override built-ins
    return out


def template_info() -> list[dict]:
    """Name, human title, first-line description and whether it is the user's own copy — for UIs."""
    rows = []
    for name, path in sorted(list_templates().items()):
        first = next((ln.strip() for ln in path.read_text().splitlines() if ln.strip()), "")
        rows.append({
            "name": name,
            "title": name.replace("-", " ").capitalize().replace("One on one", "One-on-one"),
            "description": first[:160],
            "path": str(path),
            "user": path.parent == USER_TEMPLATES,
        })
    return rows


def enhance_argv(e: EnhanceConfig) -> list[str] | None:
    """argv for a built-in backend (prompt on stdin, notes on stdout), or None for a shell `command`."""
    if e.backend == "claude":
        return ["claude", "-p", "--output-format", "json"] + (["--model", e.model] if e.model else [])
    if e.backend == "grok":
        return ["grok", "-p"] + (["--model", e.model] if e.model else [])
    if e.backend == "ollama":
        return ["ollama", "run", e.model or "llama3.1"]
    return None


class Session:
    def __init__(self, cfg: Config, emit: Emit):
        self.cfg = cfg
        self.emit = emit
        self.backend: Backend | None = None
        self.recording = False
        self.title = "meeting"
        self.session_dir: Path | None = None
        self.started_at: datetime | None = None
        self.entries: list[tuple[float, str, str]] = []
        self.notes = ""
        self.pending = 0
        self.auto_started = False
        self._caps: list[PwCapture] = []
        self._bufs = [bytearray(), bytearray()]
        self._tasks: list[asyncio.Task] = []
        self._consumed = 0
        self._inflight: set[asyncio.Task] = set()

    @property
    def stt_model(self) -> str:
        st = self.cfg.stt
        return st.local_model if st.provider == "local" else ("grok-stt" if st.provider == "xai" else st.model)

    # ---------- status ----------
    def status(self, extra: dict | None = None) -> dict:
        elapsed = (datetime.now() - self.started_at).total_seconds() if self.recording and self.started_at else 0
        d = {
            "recording": self.recording,
            "title": self.title,
            "elapsed": int(elapsed),
            "elapsed_text": mmss(elapsed) if self.recording else "--:--",
            "session_dir": str(self.session_dir) if self.session_dir else None,
            "pending": self.pending,
            "lines": len(self.entries),
            "provider": self.cfg.stt.provider,
        }
        if extra:
            d.update(extra)
        return d

    # ---------- lifecycle ----------
    async def start(self, title: str = "", auto: bool = False) -> dict:
        if self.recording:
            raise RuntimeError("already recording")
        if not pipewire_available():
            raise RuntimeError("pw-record not found — install pipewire")
        self.backend = get_backend(self.cfg)
        if title:
            self.title = title
        self.auto_started = auto
        self.started_at = datetime.now()
        self.entries.clear()
        self.notes = ""
        self._bufs = [bytearray(), bytearray()]
        self._consumed = 0
        self.session_dir = self.cfg.notes_dir / f"{self.started_at:%Y-%m-%d-%H%M}-{slug(self.title)}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._caps = [PwCapture(sink=False), PwCapture(sink=True)]
        for cap in self._caps:
            await cap.start()
        self.recording = True
        self._tasks = [asyncio.create_task(self._reader(i)) for i in range(2)]
        self._tasks.append(asyncio.create_task(self._cutter()))
        self.save()
        await self.emit({"event": "started", **self.status()})
        return self.status()

    async def stop(self) -> dict:
        if not self.recording:
            return self.status()
        self.recording = False
        for cap in self._caps:
            await cap.stop()
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        self._cut(final=True)
        if self._inflight:
            await asyncio.wait(self._inflight, timeout=300)
        self.save()
        await self.emit({"event": "stopped", **self.status()})
        if self.backend:
            await self.backend.close()
        return self.status()

    # ---------- capture ----------
    async def _reader(self, i: int) -> None:
        cap = self._caps[i]
        while self.recording:
            data = await cap.read()
            if not data:
                break
            self._bufs[i].extend(data)

    async def _cutter(self) -> None:
        while self.recording:
            await asyncio.sleep(self.cfg.chunk_seconds)
            self._cut()

    def _cut(self, final: bool = False) -> None:
        mic, sys_ = self._bufs
        n = max(len(mic), len(sys_)) if final else min(len(mic), len(sys_))
        n -= n % 2
        if n < BYTES_PER_SEC // 2:
            return
        mic_pcm = bytes(mic[:n]).ljust(n, b"\0")
        sys_pcm = bytes(sys_[:n]).ljust(n, b"\0")
        del mic[:n]
        del sys_[:n]
        offset = self._consumed / BYTES_PER_SEC
        self._consumed += n
        t = asyncio.create_task(self._transcribe_chunk(mic_pcm, sys_pcm, offset))
        self._inflight.add(t)
        t.add_done_callback(self._inflight.discard)

    async def _transcribe_chunk(self, mic_pcm: bytes, sys_pcm: bytes, offset: float) -> None:
        assert self.backend
        jobs = [(mic_pcm, "Me"), (sys_pcm, "Them")]
        jobs = [(pcm, spk) for pcm, spk in jobs if peak(pcm) >= self.cfg.silence_peak]
        if not jobs:
            return
        self.pending += 1
        await self.emit({"event": "status", **self.status()})
        try:
            results = await asyncio.gather(
                *(self.backend.transcribe(pcm) for pcm, _ in jobs), return_exceptions=True
            )
            for (pcm, speaker), res in zip(jobs, results):
                usage.record({"kind": "stt", "provider": self.cfg.stt.provider, "model": self.stt_model,
                              "seconds": round(len(pcm) / BYTES_PER_SEC, 2), "speaker": speaker,
                              "session": self.session_dir.name if self.session_dir else "",
                              "error": str(res)[:200] if isinstance(res, Exception) else ""})
                if isinstance(res, Exception):
                    await self.emit({"event": "error", "message": f"stt {speaker} @ {mmss(offset)}: {res}"})
                    continue
                for start, text in res:
                    t = offset + start
                    self.entries.append((t, speaker, text))
                    await self.emit({"event": "line", "t": t, "time": mmss(t), "speaker": speaker, "text": text})
            self.save()
        finally:
            self.pending -= 1
            await self.emit({"event": "status", **self.status()})

    # ---------- persistence ----------
    def transcript_markdown(self) -> str:
        lines = [f"# {self.title}", "", f"Date: {self.started_at:%Y-%m-%d %H:%M}" if self.started_at else "", ""]
        for t, speaker, text in sorted(self.entries, key=lambda e: e[0]):
            lines.append(f"**[{mmss(t)}] {speaker}:** {text}")
        return "\n".join(lines) + "\n"

    def save(self) -> None:
        if not self.session_dir:
            return
        (self.session_dir / "transcript.md").write_text(self.transcript_markdown())
        (self.session_dir / "notes.md").write_text(self.notes)

    # ---------- enhance ----------
    async def enhance(self, template: str = "", tone: str = "", session_dir: Path | None = None) -> dict:
        sdir = session_dir or self.session_dir
        if not sdir:
            raise RuntimeError("no session to enhance")
        if sdir == self.session_dir:
            self.save()
        e = self.cfg.enhance
        template = template or e.default_template
        tone = tone or e.default_tone
        templates = list_templates()
        if template not in templates:
            raise RuntimeError(f"unknown template {template!r}; available: {', '.join(sorted(templates))}")
        if tone not in e.tones:
            raise RuntimeError(f"unknown tone {tone!r}; available: {', '.join(sorted(e.tones))}")
        transcript = (sdir / "transcript.md").read_text() if (sdir / "transcript.md").exists() else ""
        notes = (sdir / "notes.md").read_text() if (sdir / "notes.md").exists() else ""
        prompt = (
            templates[template].read_text().strip()
            + f"\n\nTone: {e.tones[tone]}\n"
            + "\nSpeaker labels: 'Me' is the user (the person you are writing notes for); 'Them' is everyone else on the call.\n"
            + "\n---\n\n# Transcript\n\n" + transcript
            + ("\n\n# The user's own notes taken during the meeting\n\n" + notes if notes.strip() else "")
        )
        prompt_file = sdir / "prompt.md"
        output = sdir / "enhanced.md"
        prompt_file.write_text(prompt)
        argv = enhance_argv(e)
        if argv:
            cmd = shlex.join(argv) + f' < "{prompt_file}" > "{output}"'
        else:
            cmd = e.command.format(
                prompt_file=prompt_file, transcript=sdir / "transcript.md", notes=sdir / "notes.md",
                output=output, dir=sdir,
            )
        await self.emit({"event": "enhancing", "command": cmd, "session_dir": str(sdir)})
        t0 = time.monotonic()
        if argv:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=sdir, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate(prompt.encode())
        else:
            proc = await asyncio.create_subprocess_shell(
                cmd, cwd=sdir, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
        ok = proc.returncode == 0
        meter = {"kind": "enhance", "backend": e.backend, "model": e.model, "template": template, "tone": tone,
                 "session": sdir.name, "ok": ok, "duration": round(time.monotonic() - t0, 1)}
        if argv:
            text = out.decode(errors="ignore")
            if e.backend == "claude" and ok:
                # `claude -p --output-format json` wraps the answer with real usage numbers.
                try:
                    j = json.loads(text)
                    text = str(j.get("result", ""))
                    u = j.get("usage", {})
                    meter.update({"input_tokens": int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0))
                                  + int(u.get("cache_creation_input_tokens", 0)),
                                  "output_tokens": int(u.get("output_tokens", 0)),
                                  "cost_usd": j.get("total_cost_usd"),
                                  "model": e.model or next(iter(j.get("modelUsage", {})), "")})
                    if j.get("is_error"):
                        ok = False
                        err = text.encode()
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            if ok:
                output.write_text(text)
        if "input_tokens" not in meter:  # no usage from the backend: ~4 chars per token
            out_len = len(output.read_text()) if ok and output.exists() else 0
            meter.update({"input_tokens": len(prompt) // 4, "output_tokens": out_len // 4, "tokens_estimated": True})
        usage.record(meter)
        result = {"ok": ok, "output": str(output), "returncode": proc.returncode,
                  "stderr": err.decode(errors="ignore")[-800:], "usage": meter}
        await self.emit({"event": "enhanced", **result})
        if not ok:
            raise RuntimeError(f"enhance command failed ({proc.returncode}): {result['stderr']}")
        return result
