"""Meeting detection: watch PipeWire for applications capturing the microphone."""
from __future__ import annotations

import asyncio
import json
import shutil

from .config import DetectConfig

_OWN = {"muesli", "pw-record", "pw-cat", "pw-play"}


async def mic_users() -> list[str]:
    """Names of applications currently recording from an audio source (not monitors)."""
    if not shutil.which("pw-dump"):
        return []
    proc = await asyncio.create_subprocess_exec(
        "pw-dump", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    try:
        objects = json.loads(out or b"[]")
    except json.JSONDecodeError:
        return []
    users: list[str] = []
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Stream/Input/Audio":
            continue
        if info.get("state") != "running":
            continue
        if str(props.get("stream.capture.sink", "")).lower() == "true" or props.get("stream.monitor"):
            continue
        name = (
            props.get("application.name")
            or props.get("application.process.binary")
            or props.get("node.name")
            or "unknown"
        )
        if name.lower() in _OWN:
            continue
        users.append(str(name))
    return users


def meeting_app(cfg: DetectConfig, users: list[str]) -> str | None:
    """First mic user that looks like a meeting app (or any user if cfg.apps is empty)."""
    for u in users:
        if not cfg.apps or any(a.lower() in u.lower() for a in cfg.apps):
            return u
    return None
