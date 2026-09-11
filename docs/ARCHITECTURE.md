# What muesli is, and how it is built

muesli is a personal, terminal-native replacement for Granola on Linux. It records both sides of a call, transcribes it, and hands the transcript to whatever LLM CLI you already use. Nothing floats on screen: a daemon does the work, a TUI and a Waybar module are windows onto it.

## Components

```
                ┌────────────────────────────────────────────┐
                │  muesli daemon  (systemd --user service)   │
                │                                            │
  pw-record ───►│  engine.Session                            │
  (mic)         │   ├─ aligned chunking (mic ‖ system)       │──► ~/notes/meetings/<date>-<title>/
  pw-record ───►│   ├─ stt backend (openai | xai | local)    │       transcript.md  notes.md
  (sink monitor)│   ├─ persistence                           │       prompt.md      enhanced.md
                │   └─ enhance (shell out to claude/grok/…)  │
  pw-dump ─────►│  detect loop (every 3 s)                   │──► notify-send
                │                                            │
                │  unix socket  $XDG_RUNTIME_DIR/muesli.sock │
                └──────────┬──────────────┬─────────────┬────┘
                           │              │             │
                     muesli tui     muesli status   muesli start/stop/
                     (Textual)        --waybar      toggle/enhance (CLI)
```

| module | responsibility |
|---|---|
| `muesli/audio.py` | `PwCapture` wraps `pw-record --raw` → stdout. `sink=True` adds `stream.capture.sink=true` to record the default sink's monitor. PCM helpers (wav, peak, stereo interleave). |
| `muesli/stt.py` | Backends with one interface: `transcribe(pcm) -> [(start, text)]`. `OpenAICompat` (Groq/OpenAI/local servers, `verbose_json` segments), `XaiSTT` (`POST /v1/stt`, word timestamps regrouped into sentences), `FasterWhisper` (in-process, serialised with a lock), `WhisperCpp` (`whisper-cli -oj`). |
| `muesli/engine.py` | `Session`: two reader tasks fill two byte buffers; a cutter task slices both at the same byte offset every `chunk_seconds`, so `Me`/`Them` timestamps line up. Silent channels are skipped. Each chunk is transcribed concurrently per channel and appended; `transcript.md` is rewritten after every chunk. `enhance()` composes template + tone + transcript + notes into `prompt.md` and runs the configured shell command. |
| `muesli/detect.py` | Parses `pw-dump` JSON, returns apps with a running `Stream/Input/Audio` node that is not a monitor and not muesli itself. |
| `muesli/daemon.py` | JSON-lines server on a unix socket. Commands: `status start stop toggle title notes save enhance templates reload subscribe quit`. `subscribe` keeps the connection open and streams events (`line`, `status`, `started`, `stopped`, `meeting`, `error`, `enhancing`, `enhanced`). Runs the detect loop; notifies or auto-starts. |
| `muesli/cli.py` | Client helpers (`request`, `call`, `ensure_daemon` which spawns the daemon if the socket is dead) and the `muesli` command. `status --waybar` prints Waybar's JSON contract (`text`, `class`, `tooltip`). |
| `muesli/tui.py` | Textual app: transcript pane (left), notes pane (right), command line (bottom). It only talks to the daemon — closing it never stops a recording. |
| `muesli/templates/*.md` | Built-in enhance templates. User templates in `~/.config/muesli/templates/` override by name. |
| `muesli/contrib/` | Waybar module + CSS, systemd unit. Copied by `muesli init`. |

## Data flow of one chunk

1. Both `pw-record` processes stream s16/16 kHz/mono PCM into `_bufs[0]` (mic) and `_bufs[1]` (system).
2. Every `chunk_seconds` the cutter takes `n = min(len(mic), len(sys))` bytes from both; `offset = consumed / bytes_per_second` becomes the chunk's start time.
3. Channels whose peak sample is below `silence_peak` are dropped (Whisper hallucinates on silence).
4. Remaining channels go to the backend concurrently (`asyncio.gather`). Results are appended as `(offset + start, speaker, text)` and broadcast as `line` events.
5. `transcript.md` and `notes.md` are rewritten. On `/stop`, a final cut pads the shorter buffer with zeros and waits for in-flight requests.

## Decisions worth knowing

- **Two mono streams instead of diarization.** The mic/monitor split gives a reliable `Me`/`Them` split for free. Multi-party "Them" is not separated; xAI's diarization could be added per channel later.
- **Chunked REST, not streaming websockets.** 30 s chunks are simple, provider-agnostic and good enough for notes. The cost is an occasional split word at chunk borders.
- **Enhance is a shell command.** No LLM SDK in the app; you bring your own CLI and subscription. The prompt is a file you can read.
- **Daemon-first.** Required for the bar module and meeting detection, and it means a crashed TUI loses nothing.
- **No editing of Waybar config by scripts.** Omarchy users own their `config.jsonc`; the installer prints the two lines instead.

## Files on disk

```
~/.config/muesli/config.toml        settings (see config.example.toml)
~/.config/muesli/env                API keys, read by the systemd unit
~/.config/muesli/templates/*.md     your enhance templates
~/.config/muesli/waybar/            module + css to include from Waybar
~/notes/meetings/<date>-<title>/    transcript.md · notes.md · prompt.md · enhanced.md
$XDG_RUNTIME_DIR/muesli.sock        daemon socket (0600)
```
