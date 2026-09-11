from __future__ import annotations

import asyncio
import json

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Input, RichLog, Static, TextArea

from .cli import request
from .config import CONFIG_PATH, SOCKET_PATH, Config
from .engine import mmss

HELP = (
    "[b]/start[/b] [title]              begin recording (mic + system audio)\n"
    "[b]/stop[/b]                       stop and save transcript.md + notes.md\n"
    "[b]/enhance[/b] [template] [tone]  generate enhanced.md   (see /templates)\n"
    "[b]/title[/b] <text>  ·  [b]/save[/b]  ·  [b]/open[/b]  ·  [b]/templates[/b]  ·  [b]/help[/b]  ·  [b]/quit[/b]\n"
    "Type your own notes in the right pane; they are saved alongside the transcript.\n"
    "[b]Tab[/b] switches between the command line and the notes pane."
)


class Muesli(App):
    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; background: $primary-background; padding: 0 1; }
    #main { height: 1fr; }
    #transcript { width: 2fr; border: round $primary; padding: 0 1; }
    #notes { width: 1fr; border: round $secondary; }
    #cmd { dock: bottom; }
    """
    BINDINGS = [Binding("ctrl+q", "quit", "Quit"), Binding("tab", "focus_next", show=False)]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.st: dict = {}
        self._sub: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        with Horizontal(id="main"):
            yield RichLog(id="transcript", wrap=True, markup=True, highlight=False)
            yield TextArea(id="notes")
        yield Input(placeholder="/start [title]  ·  /stop  ·  /enhance [template] [tone]  ·  /help", id="cmd")

    async def on_mount(self) -> None:
        self.log_ui(HELP)
        if not CONFIG_PATH.exists():
            self.log_ui(f"[yellow]No config at {CONFIG_PATH} — run `muesli init`. Using defaults (Groq).[/yellow]")
        self._sub = asyncio.create_task(self.subscribe())
        self.set_interval(1, self.render_status)
        self.query_one("#cmd", Input).focus()

    # ---------- daemon link ----------
    async def subscribe(self) -> None:
        try:
            reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
            writer.write(b'{"cmd": "subscribe"}\n')
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    break
                self.on_event(json.loads(line))
        except Exception as e:
            self.log_ui(f"[red]lost connection to daemon: {e}[/red]")

    def on_event(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "line":
            self.write_line(ev["t"], ev["speaker"], ev["text"])
        elif kind == "error":
            self.log_ui(f"[red]{ev['message']}[/red]")
        elif kind == "started":
            self.st = ev
            self.log_ui(f"[green]recording → {ev['session_dir']}[/green]")
        elif kind == "stopped":
            self.st = ev
            self.log_ui(f"[green]stopped · saved to {ev['session_dir']}[/green]")
        elif kind == "meeting":
            self.st["meeting"] = ev.get("app")
            if ev.get("app"):
                self.log_ui(f"[yellow]{ev['app']} opened the microphone — /start to record[/yellow]")
        elif kind == "enhancing":
            self.log_ui(f"[dim]$ {ev['command']}[/dim]")
        elif kind == "enhanced":
            self.log_ui(f"[green]enhanced → {ev['output']}[/green]" if ev.get("ok") else f"[red]enhance failed: {ev.get('stderr')}[/red]")
        elif kind == "status":
            meeting = self.st.get("meeting")
            self.st = ev
            self.st.setdefault("meeting", meeting)
            for ln in ev.get("lines_data", []):  # replay on (re)connect
                self.write_line(ln["t"], ln["speaker"], ln["text"])
        self.render_status()

    async def send(self, req: dict) -> dict:
        return await request(req)

    # ---------- UI ----------
    def log_ui(self, text: str) -> None:
        self.query_one("#transcript", RichLog).write(text)

    def write_line(self, t: float, speaker: str, text: str) -> None:
        color = "cyan" if speaker == "Me" else "magenta"
        self.log_ui(f"[dim]{mmss(t)}[/dim] [{color}]{speaker}:[/{color}] {text}")

    def render_status(self) -> None:
        st = self.st
        rec = st.get("recording")
        if rec and st.get("elapsed") is not None:
            st["elapsed"] = st.get("elapsed", 0) + 1
            elapsed = mmss(st["elapsed"])
        else:
            elapsed = "--:--"
        state = "● REC" if rec else "○ idle"
        pend = f" · transcribing {st['pending']}" if st.get("pending") else ""
        meet = f" · {st['meeting']} on mic" if st.get("meeting") and not rec else ""
        self.query_one("#status", Static).update(
            f" muesli  {state}  {elapsed}  · {st.get('title', 'meeting')}{pend}{meet}  · {st.get('provider', '')}"
        )

    async def push_notes(self) -> None:
        await self.send({"cmd": "notes", "text": self.query_one("#notes", TextArea).text})

    async def on_input_submitted(self, ev: Input.Submitted) -> None:
        line = ev.value.strip()
        ev.input.value = ""
        if not line:
            return
        cmd, _, arg = line.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        try:
            if cmd == "/start":
                self.query_one("#notes", TextArea).text = ""
                await self.send({"cmd": "start", "title": arg})
            elif cmd == "/stop":
                await self.push_notes()
                await self.send({"cmd": "stop"})
            elif cmd == "/save":
                await self.push_notes()
                await self.send({"cmd": "save"})
                self.log_ui("[green]saved[/green]")
            elif cmd == "/enhance":
                await self.push_notes()
                parts = arg.split()
                asyncio.create_task(self.send({"cmd": "enhance", "template": parts[0] if parts else "",
                                               "tone": parts[1] if len(parts) > 1 else ""}))
            elif cmd == "/templates":
                t = await self.send({"cmd": "templates"})
                self.log_ui(f"templates: {', '.join(t['templates'])}\ntones: {', '.join(t['tones'])}")
            elif cmd == "/title":
                await self.send({"cmd": "title", "title": arg})
            elif cmd == "/open":
                self.log_ui(self.st.get("session_dir") or "no session yet")
            elif cmd == "/help":
                self.log_ui(HELP)
            elif cmd in ("/quit", "/q", "/exit"):
                await self.action_quit()
            else:
                self.log_ui(f"[yellow]unknown command {cmd} — /help[/yellow]")
        except Exception as e:
            self.log_ui(f"[red]{e}[/red]")

    async def action_quit(self) -> None:
        # the daemon keeps recording; quitting the TUI never stops a meeting
        try:
            await self.push_notes()
        except Exception:
            pass
        if self._sub:
            self._sub.cancel()
        self.exit()
