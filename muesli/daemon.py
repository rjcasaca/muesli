"""muesli daemon — owns the recording engine and exposes it over a unix socket (JSON lines)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .config import SOCKET_PATH, Config, load_config
from .detect import meeting_app, mic_users
from .engine import Session, list_templates


class Daemon:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = Session(cfg, self.broadcast)
        self.subscribers: set[asyncio.StreamWriter] = set()
        self.meeting: str | None = None
        self.notified_for: str | None = None
        self.mic_free_since: datetime | None = None

    # ---------- events ----------
    async def broadcast(self, event: dict) -> None:
        if event.get("event") in ("started", "stopped", "status"):
            event = {**event, "meeting": self.meeting}
        dead = []
        for w in self.subscribers:
            try:
                w.write((json.dumps(event) + "\n").encode())
                await w.drain()
            except Exception:
                dead.append(w)
        for w in dead:
            self.subscribers.discard(w)

    def status(self) -> dict:
        return self.session.status({"meeting": self.meeting, "pid": os.getpid()})

    # ---------- request handling ----------
    async def handle(self, req: dict, writer: asyncio.StreamWriter) -> dict:
        cmd = req.get("cmd")
        s = self.session
        if cmd == "status":
            return self.status()
        if cmd == "start":
            return await s.start(req.get("title", ""))
        if cmd == "stop":
            return await s.stop()
        if cmd == "toggle":
            if s.recording:
                return await s.stop()
            return await s.start(req.get("title") or (self.meeting or ""))
        if cmd == "title":
            s.title = req.get("title") or s.title
            return self.status()
        if cmd == "notes":
            s.notes = req.get("text", "")
            s.save()
            return {"ok": True}
        if cmd == "save":
            s.save()
            return self.status()
        if cmd == "enhance":
            sdir = Path(req["session_dir"]) if req.get("session_dir") else None
            return await s.enhance(req.get("template", ""), req.get("tone", ""), sdir)
        if cmd == "templates":
            return {"templates": sorted(list_templates()), "tones": sorted(self.cfg.enhance.tones)}
        if cmd == "reload":
            self.cfg = load_config()
            s.cfg = self.cfg
            return {"ok": True}
        if cmd == "subscribe":
            self.subscribers.add(writer)
            return {"event": "status", **self.status(), "lines_data": [
                {"t": t, "speaker": sp, "text": tx} for t, sp, tx in s.entries]}
        if cmd == "quit":
            await s.stop()
            asyncio.get_event_loop().call_later(0.2, lambda: os._exit(0))
            return {"ok": True}
        raise RuntimeError(f"unknown command {cmd!r}")

    async def client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    req = json.loads(line)
                    resp = await self.handle(req, writer)
                    resp = {"ok": True, **resp} if "ok" not in resp else resp
                except Exception as e:
                    resp = {"ok": False, "error": str(e)}
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.subscribers.discard(writer)
            writer.close()

    # ---------- meeting detection ----------
    async def detect_loop(self) -> None:
        d = self.cfg.detect
        while True:
            try:
                await self._detect_tick()
            except Exception as e:
                await self.broadcast({"event": "error", "message": f"detect: {e}"})
            await asyncio.sleep(3 if d.enabled else 30)

    async def _detect_tick(self) -> None:
        d = self.cfg.detect
        if not d.enabled:
            return
        app = meeting_app(d, await mic_users())
        s = self.session
        if app and app != self.meeting:
            self.meeting = app
            await self.broadcast({"event": "meeting", "app": app})
            if not s.recording:
                if d.auto_start:
                    await s.start(app, auto=True)
                elif d.notify and self.notified_for != app:
                    self.notified_for = app
                    self._notify(f"Meeting detected: {app}", "Click the muesli bar icon to start recording")
        if not app and self.meeting:
            self.meeting = None
            self.notified_for = None
            await self.broadcast({"event": "meeting", "app": None})
            self.mic_free_since = datetime.now()
        if not app and s.recording and s.auto_started and self.mic_free_since:
            if (datetime.now() - self.mic_free_since).total_seconds() >= d.auto_stop_seconds:
                await s.stop()
                self._notify("Recording stopped", f"{s.title} · saved to {s.session_dir}")
        if app:
            self.mic_free_since = None

    @staticmethod
    def _notify(title: str, body: str) -> None:
        if shutil.which("notify-send"):
            os.spawnlp(os.P_NOWAIT, "notify-send", "notify-send", "-a", "muesli", "-i", "audio-input-microphone",
                       title, body)


async def serve(cfg: Config | None = None) -> None:
    cfg = cfg or load_config()
    d = Daemon(cfg)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = await asyncio.start_unix_server(d.client, path=str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o600)
    asyncio.create_task(d.detect_loop())
    async with server:
        await server.serve_forever()
