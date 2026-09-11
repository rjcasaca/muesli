# muesli

**Granola-style meeting notes for Linux. Terminal-native, no floating widgets.**

Granola doesn't ship for Linux. muesli does the part that matters — capture both sides of the call, transcribe it, hand the transcript to the LLM of your choice — and stays out of the way: a background daemon, a small TUI, and a Waybar indicator with click actions.

```
○  idle          ◉ Teams   (meeting detected)          ● 12:34   (recording)
```

## What it does

- **Mic + system audio**, always. Two PipeWire streams, so the transcript is labelled `Me:` / `Them:` with no diarization model.
- **STT you choose**: any OpenAI-compatible endpoint (Groq, OpenAI, a local whisper server), **xAI Grok STT**, or fully **local** via faster-whisper or whisper.cpp.
- **Near-live transcript** in 30 s chunks, written to `transcript.md` after every chunk. Kill the app mid-meeting and nothing is lost.
- **Enhance** with any CLI — Claude Code, grok, ollama — using **templates** (general, standup, one-on-one, client-call, interview, brainstorm, or your own) and **tones** (concise, formal, casual, detailed, or your own).
- **Meeting detection**: the daemon watches PipeWire; when Teams / Zoom / a browser opens the microphone you get a notification and a pulsing bar icon. Optional auto-start/auto-stop.
- **Bar widget** for Omarchy (omarchy-shell) or any Waybar setup: left-click opens a flyout (live transcript, recent meetings, start/stop, enhance, TUI), right-click start/stop, middle-click enhance.
- **Custom vocabulary** to bias the recogniser toward your names and jargon.
- One folder per meeting: `transcript.md`, `notes.md` (what you typed), `prompt.md`, `enhanced.md`.

## Install

### Omarchy / Arch (recommended)

```sh
git clone https://github.com/rjcasaca/muesli ~/src/muesli
cd ~/src/muesli && ./install.sh
```

The script installs `pipewire`, `uv`, `libnotify`, installs muesli as a `uv tool`, writes `~/.config/muesli/config.toml`, and enables the user service.

- **Omarchy 4.x** (omarchy-shell bar): the script installs the `rcasaca.muesli` bar widget from [`muesli/contrib/omarchy-shell/`](muesli/contrib/omarchy-shell/) and puts it on the bar.
- **Waybar** (Omarchy 3.x or plain Hyprland): it prints the two lines to add to your Waybar config (it won't edit that for you).

Put your API key in `~/.config/muesli/env`:

```
GROQ_API_KEY=gsk_...
```

An AUR-style `packaging/PKGBUILD` is included if you prefer `makepkg -si`. See [docs/PACKAGING.md](docs/PACKAGING.md) for all install options and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how it is built.

### Anything else with PipeWire

```sh
uv tool install git+https://github.com/rjcasaca/muesli      # or: pipx install ...
muesli init
muesli daemon &                                             # or use muesli/contrib/muesli.service
```

Local transcription: `uv tool install --with faster-whisper git+...` and set `provider = "local"`.

## Use

```
muesli                    # opens the TUI (starts the daemon if needed)
/start Weekly with LFF    # or click the bar icon, or `muesli start Weekly with LFF`
...type notes on the right while it transcribes...
/stop
/enhance client-call formal
```

CLI equivalents: `muesli start|stop|toggle|status|enhance [-t template] [--tone tone] [-s latest]|list|templates`.

The TUI is only a window onto the daemon: closing it never stops a recording.

## Configure

Everything lives in `~/.config/muesli/config.toml` — see [`config.example.toml`](config.example.toml). Highlights:

| key | what |
|---|---|
| `stt.provider` | `openai` · `xai` · `local` |
| `vocabulary` | list of names/terms to bias transcription |
| `detect.apps` | substrings of app names that count as a meeting; `[]` = any app on the mic |
| `detect.auto_start` | record automatically when a meeting app opens the mic |
| `enhance.command` | the CLI that turns `prompt.md` into `enhanced.md` |
| `enhance.tones` | add your own tones here |

Custom templates: drop `~/.config/muesli/templates/<name>.md` — the file is the instruction; the transcript and your notes are appended automatically. Same name as a built-in overrides it.

## STT cost & choice

| provider | model | rough cost / hour | notes |
|---|---|---|---|
| Groq | whisper-large-v3-turbo | cents | default; fast |
| OpenAI | whisper-1 | ~$0.36 | |
| xAI | Grok STT | ~$0.10 | strong on names & numbers, 25 languages |
| local | faster-whisper `small`..`large-v3` | free | needs CPU/GPU; `large-v3` on CPU is slow |

Claude cannot transcribe audio; it's the right tool for `enhance`, not for STT.

## How it works

```
pw-record (mic) ──────────┐                        ┌─ Me ──┐
                          ├─ aligned 30 s chunks ──┤       ├─ transcript.md ─► enhance cmd ─► enhanced.md
pw-record (sink monitor) ─┘                        └─ Them ┘
        ▲
        └── pw-dump every 3 s: is Teams/Zoom/… holding the mic?  → notify / auto-start
```

Daemon ⇄ clients over a unix socket (`$XDG_RUNTIME_DIR/muesli.sock`, JSON lines). `muesli status --waybar` is just another client.

## Gotchas

- System audio is the **default sink's monitor**. If the call plays through a headset that isn't the default, `wpctl set-default <id>` it first.
- Chunks are cut on a fixed clock; a word can occasionally split across chunks. Raise `chunk_seconds` to trade latency for fewer splits.
- Whisper "hallucinates" on pure silence; muesli never uploads silent chunks (`silence_peak`).
- This is a personal tool. It records people. Tell them.

## License

MIT
