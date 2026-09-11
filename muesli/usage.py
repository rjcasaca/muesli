"""Consumption log: one JSON line per STT request and per enhance run, aggregated on demand.

Nothing here talks to a network; costs are computed from the [costs] table in config.toml except
when the enhance backend reports its own (Claude Code's --output-format json does)."""
from __future__ import annotations

import json
from datetime import date, datetime

from .config import USAGE_LOG, Config

PERIODS = ("today", "month", "all")


def record(event: dict) -> None:
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": datetime.now().isoformat(timespec="seconds"), **event}
    with USAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _events(period: str) -> list[dict]:
    if not USAGE_LOG.exists():
        return []
    today = date.today()
    out = []
    for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
            d = datetime.fromisoformat(ev["ts"]).date()
        except (ValueError, KeyError):
            continue
        if period == "today" and d != today:
            continue
        if period == "month" and (d.year, d.month) != (today.year, today.month):
            continue
        out.append(ev)
    return out


def summary(cfg: Config, period: str = "month") -> dict:
    """Totals for the period plus a per-provider/backend breakdown. Estimated figures are flagged."""
    stt: dict[str, dict] = {}
    enh: dict[str, dict] = {}
    for ev in _events(period):
        if ev.get("kind") == "stt":
            key = f"{ev.get('provider', '?')}/{ev.get('model', '')}".rstrip("/")
            row = stt.setdefault(key, {"provider": ev.get("provider", ""), "model": ev.get("model", ""),
                                       "requests": 0, "seconds": 0.0, "errors": 0, "cost_usd": 0.0})
            row["requests"] += 1
            row["seconds"] += float(ev.get("seconds", 0))
            row["errors"] += 1 if ev.get("error") else 0
            row["cost_usd"] += float(ev.get("seconds", 0)) / 3600 * cfg.costs.stt_hourly(row["provider"], row["model"])
        elif ev.get("kind") == "enhance":
            key = f"{ev.get('backend', '?')}/{ev.get('model', '')}".rstrip("/")
            row = enh.setdefault(key, {"backend": ev.get("backend", ""), "model": ev.get("model", ""), "runs": 0,
                                       "failed": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0,
                                       "cost_usd": 0.0, "estimated": False})
            row["runs"] += 1
            row["failed"] += 0 if ev.get("ok", True) else 1
            row["input_tokens"] += int(ev.get("input_tokens", 0))
            row["output_tokens"] += int(ev.get("output_tokens", 0))
            row["seconds"] += float(ev.get("duration", 0))
            if ev.get("cost_usd") is not None:
                row["cost_usd"] += float(ev["cost_usd"])
            else:
                row["estimated"] = True
                row["cost_usd"] += (int(ev.get("input_tokens", 0)) * cfg.costs.llm_input_per_mtok
                                    + int(ev.get("output_tokens", 0)) * cfg.costs.llm_output_per_mtok) / 1e6
            if ev.get("tokens_estimated"):
                row["estimated"] = True
    stt_rows = sorted(stt.values(), key=lambda r: -r["seconds"])
    enh_rows = sorted(enh.values(), key=lambda r: -r["runs"])
    return {
        "period": period,
        "stt": {"seconds": sum(r["seconds"] for r in stt_rows), "requests": sum(r["requests"] for r in stt_rows),
                "cost_usd": sum(r["cost_usd"] for r in stt_rows), "rows": stt_rows},
        "enhance": {"runs": sum(r["runs"] for r in enh_rows), "input_tokens": sum(r["input_tokens"] for r in enh_rows),
                    "output_tokens": sum(r["output_tokens"] for r in enh_rows),
                    "cost_usd": sum(r["cost_usd"] for r in enh_rows),
                    "estimated": any(r["estimated"] for r in enh_rows), "rows": enh_rows},
        "log": str(USAGE_LOG),
    }


def format_summary(s: dict) -> str:
    mins = s["stt"]["seconds"] / 60
    lines = [f"usage · {s['period']}",
             f"  transcription  {mins:6.1f} min in {s['stt']['requests']} requests  ≈ ${s['stt']['cost_usd']:.3f}"]
    for r in s["stt"]["rows"]:
        lines.append(f"    {r['provider']}/{r['model']:<28} {r['seconds'] / 60:6.1f} min  ${r['cost_usd']:.3f}"
                     + (f"  ({r['errors']} errors)" if r["errors"] else ""))
    e = s["enhance"]
    est = "~" if e["estimated"] else ""
    lines.append(f"  enhance        {e['runs']} runs · {e['input_tokens']:,} in / {e['output_tokens']:,} out tokens"
                 f"  {est}${e['cost_usd']:.3f}")
    for r in e["rows"]:
        lines.append(f"    {r['backend']}/{r['model'] or 'default':<28} {r['runs']} runs  "
                     f"{r['input_tokens']:,}/{r['output_tokens']:,} tok  {'~' if r['estimated'] else ''}${r['cost_usd']:.3f}")
    lines.append(f"  log: {s['log']}")
    return "\n".join(lines)
