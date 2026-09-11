"""Client library and CLI entry point."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .config import CONFIG_DIR, CONFIG_PATH, SOCKET_PATH, load_config


# ---------- client ----------
async def request(req: dict, timeout: float = 600) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout)
    writer.close()
    resp = json.loads(line)
    if not resp.get("ok", True):
        raise RuntimeError(resp.get("error", "daemon error"))
    return resp


def call(req: dict, timeout: float = 600) -> dict:
    return asyncio.run(request(req, timeout))


def daemon_running() -> bool:
    if not SOCKET_PATH.exists():
        return False
    try:
        call({"cmd": "status"}, timeout=2)
        return True
    except Exception:
        return False


def ensure_daemon() -> None:
    if daemon_running():
        return
    subprocess.Popen(
        [sys.executable, "-m", "muesli.cli", "daemon"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(40):
        time.sleep(0.1)
        if daemon_running():
            return
    raise RuntimeError("could not start muesli daemon")


# ---------- commands ----------
def cmd_status(args) -> None:
    if not daemon_running():
        if args.waybar:
            print(json.dumps({"text": "", "class": "off", "tooltip": "muesli daemon not running"}))
            return
        print("daemon not running")
        return
    st = call({"cmd": "status"})
    if args.waybar:
        if st["recording"]:
            out = {"text": f"● {st['elapsed_text']}", "class": "recording", "alt": "recording",
                   "tooltip": f"Recording: {st['title']}\n{st['lines']} lines · {st['pending']} chunks in flight\n"
                              f"left-click: stop · right-click: enhance · middle: open TUI"}
        elif st.get("meeting"):
            out = {"text": f"◉ {st['meeting']}", "class": "meeting", "alt": "meeting",
                   "tooltip": f"{st['meeting']} is using the microphone\nleft-click: start recording"}
        else:
            out = {"text": "○", "class": "idle", "alt": "idle",
                   "tooltip": "muesli idle\nleft-click: start recording · middle: open TUI"}
        print(json.dumps(out))
    elif args.json:
        print(json.dumps(st, indent=2))
    else:
        state = f"recording {st['elapsed_text']} · {st['title']} · {st['lines']} lines" if st["recording"] else "idle"
        print(state + (f" · meeting: {st['meeting']}" if st.get("meeting") else ""))


def cmd_start(args) -> None:
    ensure_daemon()
    st = call({"cmd": "start", "title": " ".join(args.title)})
    print(f"recording → {st['session_dir']}")


def cmd_stop(args) -> None:
    st = call({"cmd": "stop"})
    print(f"stopped → {st['session_dir']}")


def cmd_toggle(args) -> None:
    ensure_daemon()
    st = call({"cmd": "toggle", "title": " ".join(args.title)})
    print("recording" if st["recording"] else "stopped")


def cmd_enhance(args) -> None:
    ensure_daemon()
    sdir = None
    if args.session:
        sdir = str(latest_session(load_config().notes_dir) if args.session == "latest" else Path(args.session))
    res = call({"cmd": "enhance", "template": args.template or "", "tone": args.tone or "", "session_dir": sdir})
    print(f"enhanced → {res['output']}")


def latest_session(notes_dir: Path) -> Path:
    dirs = sorted(p for p in notes_dir.iterdir() if p.is_dir()) if notes_dir.exists() else []
    if not dirs:
        raise SystemExit("no sessions found")
    return dirs[-1]


def cmd_list(args) -> None:
    notes_dir = load_config().notes_dir
    if not notes_dir.exists():
        if args.json:
            print("[]")
        return
    sessions = sorted(p for p in notes_dir.iterdir() if p.is_dir())[-args.n:]
    if args.json:  # newest first; consumed by the bar widgets
        def utterances(p):
            t = p / "transcript.md"
            return sum(1 for ln in t.open(encoding="utf-8") if ln.startswith("**[")) if t.exists() else 0
        print(json.dumps([{"name": p.name, "path": str(p), "enhanced": (p / "enhanced.md").exists(), "lines": utterances(p)}
                          for p in reversed(sessions)]))
        return
    for p in sessions:
        flags = "E" if (p / "enhanced.md").exists() else " "
        print(f"{flags} {p.name}")


def cmd_templates(args) -> None:
    ensure_daemon()
    t = call({"cmd": "templates"})
    print("templates:", ", ".join(t["templates"]))
    print("tones:    ", ", ".join(t["tones"]))


def cmd_daemon(args) -> None:
    from .daemon import serve

    asyncio.run(serve())


def cmd_quit(args) -> None:
    if daemon_running():
        call({"cmd": "quit"})
    print("daemon stopped")


def cmd_tui(args) -> None:
    ensure_daemon()
    from .tui import Muesli

    Muesli(load_config()).run()


def cmd_init(args) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "templates").mkdir(exist_ok=True)
    example = Path(__file__).parent / "config.example.toml"
    if CONFIG_PATH.exists() and not args.force:
        print(f"{CONFIG_PATH} already exists (use --force to overwrite)")
    else:
        CONFIG_PATH.write_text(example.read_text())
        print(f"wrote {CONFIG_PATH}")
    wb = CONFIG_DIR / "waybar"
    wb.mkdir(exist_ok=True)
    for name in ("muesli.jsonc", "muesli.css"):
        (wb / name).write_text((Path(__file__).parent / "contrib" / name).read_text())
    print(f"waybar module + css in {wb}/  — see README for the two lines to add to your waybar config")
    print(f"notes dir: {load_config().notes_dir}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="muesli", description="Meeting transcriber for Linux")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("status", help="show daemon status"); sp.add_argument("--waybar", action="store_true")
    sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_status)
    sp = sub.add_parser("start", help="start recording"); sp.add_argument("title", nargs="*"); sp.set_defaults(func=cmd_start)
    sp = sub.add_parser("stop", help="stop recording"); sp.set_defaults(func=cmd_stop)
    sp = sub.add_parser("toggle", help="start/stop recording"); sp.add_argument("title", nargs="*"); sp.set_defaults(func=cmd_toggle)
    sp = sub.add_parser("enhance", help="generate enhanced notes"); sp.add_argument("-t", "--template")
    sp.add_argument("--tone"); sp.add_argument("-s", "--session", help="session dir or 'latest'"); sp.set_defaults(func=cmd_enhance)
    sp = sub.add_parser("list", help="list recent sessions"); sp.add_argument("-n", type=int, default=10)
    sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_list)
    sp = sub.add_parser("templates", help="list templates and tones"); sp.set_defaults(func=cmd_templates)
    sp = sub.add_parser("daemon", help="run the daemon in the foreground"); sp.set_defaults(func=cmd_daemon)
    sp = sub.add_parser("quit", help="stop the daemon"); sp.set_defaults(func=cmd_quit)
    sp = sub.add_parser("tui", help="open the terminal UI (default)"); sp.set_defaults(func=cmd_tui)
    sp = sub.add_parser("init", help="write config, templates dir and waybar files"); sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    args = p.parse_args(argv)
    if not args.command:
        args.func = cmd_tui
    try:
        args.func(args)
    except (RuntimeError, ConnectionRefusedError, FileNotFoundError) as e:
        print(f"muesli: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
