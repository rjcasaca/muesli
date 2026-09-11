"""Client library and CLI entry point."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, usage
from .config import (
    CONFIG_DIR,
    CONFIG_PATH,
    ENHANCE_BACKENDS,
    ENV_PATH,
    SOCKET_PATH,
    USER_TEMPLATES,
    flatten,
    load_config,
    read_env_file,
    set_config_value,
    write_env_value,
)


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
    from .engine import list_templates, template_info

    cfg = load_config()
    if args.action == "edit":
        # Editing a built-in copies it to the user dir first, so updates never clobber the change.
        templates = list_templates()
        if args.name not in templates:
            raise RuntimeError(f"unknown template {args.name!r}")
        USER_TEMPLATES.mkdir(parents=True, exist_ok=True)
        dest = USER_TEMPLATES / f"{args.name}.md"
        if templates[args.name] != dest:
            dest.write_text(templates[args.name].read_text())
        print(dest)
        return
    if args.action == "new":
        USER_TEMPLATES.mkdir(parents=True, exist_ok=True)
        dest = USER_TEMPLATES / f"{args.name}.md"
        if dest.exists():
            raise RuntimeError(f"{dest} already exists")
        dest.write_text(list_templates()["general"].read_text())
        print(dest)
        return
    if args.json:
        print(json.dumps({"templates": template_info(), "tones": [{"name": k, "text": v} for k, v in cfg.enhance.tones.items()],
                          "default_template": cfg.enhance.default_template, "default_tone": cfg.enhance.default_tone,
                          "user_dir": str(USER_TEMPLATES)}))
        return
    print("templates:", ", ".join(sorted(list_templates())))
    print("tones:    ", ", ".join(sorted(cfg.enhance.tones)))


def _reload_daemon() -> None:
    if daemon_running():
        call({"cmd": "reload"})


def cmd_config(args) -> None:
    cfg = load_config()
    flat = flatten(cfg.as_dict())
    if args.action == "show":
        d = cfg.as_dict()
        d["backends"] = list(ENHANCE_BACKENDS)
        d["path"] = str(CONFIG_PATH)
        print(json.dumps(d, indent=None if args.json else 2, ensure_ascii=False))
        return
    if args.action == "get":
        if args.key not in flat:
            raise RuntimeError(f"unknown key {args.key!r}")
        v = flat[args.key]
        print(json.dumps(v) if args.json or not isinstance(v, str) else v)
        return
    # set: coerce the text to the type of the current value so `set detect.auto_start true` does the right thing
    if args.key not in flat and not args.key.startswith(("enhance.tones.", "costs.stt_per_hour.")):
        raise RuntimeError(f"unknown key {args.key!r} — see `muesli config show`")
    raw = " ".join(args.value)
    cur = flat.get(args.key, "")
    if args.json:
        val = json.loads(raw)
    elif isinstance(cur, bool):
        val = raw.strip().lower() in ("1", "true", "yes", "on")
    elif isinstance(cur, int):
        val = int(raw)
    elif isinstance(cur, float):
        val = float(raw)
    elif isinstance(cur, list):
        val = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        val = raw
    if args.key == "enhance.backend" and val not in ENHANCE_BACKENDS:
        raise RuntimeError(f"backend must be one of {', '.join(ENHANCE_BACKENDS)}")
    set_config_value(args.key, val)
    _reload_daemon()
    print(f"{args.key} = {json.dumps(val, ensure_ascii=False)}")


def cmd_keys(args) -> None:
    """Which API keys the current config needs and whether ~/.config/muesli/env provides them. Never prints values."""
    cfg = load_config()
    env = read_env_file()
    if args.set:
        name, _, value = args.set.partition("=")
        if not name or not value:
            raise RuntimeError("usage: muesli keys --set NAME=value")
        write_env_value(name.strip(), value.strip())
        print(f"{name.strip()} written to {ENV_PATH} — restart the daemon to pick it up: systemctl --user restart muesli")
        return
    needed = []
    if cfg.stt.provider == "openai":
        needed.append(cfg.stt.api_key_env)
    elif cfg.stt.provider == "xai":
        needed.append(cfg.stt.xai_api_key_env)
    rows = [{"name": n, "set": bool(env.get(n)), "hint": (env.get(n, "")[:4] + "…") if env.get(n) else ""} for n in needed]
    if args.json:
        print(json.dumps({"keys": rows, "env_file": str(ENV_PATH)}))
        return
    for r in rows:
        print(f"{r['name']:<16} {'set ' + r['hint'] if r['set'] else 'MISSING'}")
    if not rows:
        print("local provider — no API key needed")


def cmd_usage(args) -> None:
    s = usage.summary(load_config(), args.period)
    print(json.dumps(s) if args.json else usage.format_summary(s))


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
    sp = sub.add_parser("templates", help="list templates and tones"); sp.add_argument("--json", action="store_true")
    sp.add_argument("action", nargs="?", choices=["list", "edit", "new"], default="list")
    sp.add_argument("name", nargs="?"); sp.set_defaults(func=cmd_templates)
    sp = sub.add_parser("config", help="read or change config.toml (the daemon reloads)")
    sp.add_argument("action", choices=["show", "get", "set"]); sp.add_argument("key", nargs="?")
    sp.add_argument("value", nargs="*"); sp.add_argument("--json", action="store_true", help="value is JSON / output JSON")
    sp.set_defaults(func=cmd_config)
    sp = sub.add_parser("keys", help="check which API keys are needed and set (never prints them)")
    sp.add_argument("--json", action="store_true"); sp.add_argument("--set", metavar="NAME=value"); sp.set_defaults(func=cmd_keys)
    sp = sub.add_parser("usage", help="transcription minutes, enhance tokens and estimated cost")
    sp.add_argument("period", nargs="?", choices=list(usage.PERIODS), default="month")
    sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_usage)
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
