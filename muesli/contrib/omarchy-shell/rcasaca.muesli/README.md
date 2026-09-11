# muesli · omarchy-shell bar widget

For Omarchy 4.x, whose bar is omarchy-shell (Quickshell), not Waybar.

```sh
cp -r muesli/contrib/omarchy-shell/rcasaca.muesli ~/.config/omarchy/plugins/
omarchy bar put rcasaca.muesli --section right
```

The bar shows `○` idle · `◉ Teams` pulsing when a meeting app opens the mic · `● 12:34` while recording.

**Left-click** opens the flyout: state + start/stop switch, live transcript tail, recent meetings
(enhance / open folder per row), this month's consumption, *Open TUI*, *Enhance latest*.
The **⚙** switches to settings — STT provider, model, language, vocabulary, API key (presence only; paste to replace),
AI backend and model, default template and tone, the template list (edit / customise / create), meeting detection
toggles, and a consumption breakdown by provider and backend. Every change is written to `~/.config/muesli/config.toml`
through `muesli config set`, and the daemon reloads.

Keys inside the flyout: `R` record · `E` enhance · `T` TUI · `S` settings · `Esc` back/close.
**Right-click** starts/stops recording, **middle-click** enhances the latest meeting.

Every click is remappable to `panel | toggle | enhance | tui | settings`, e.g. to get the Waybar module's mapping back:

```sh
omarchy bar set rcasaca.muesli leftClick toggle
omarchy bar set rcasaca.muesli rightClick enhance
omarchy bar set rcasaca.muesli middleClick panel
```

Other settings: `pollSeconds`, `hideWhenIdle`, `hideWhenOff`, `panelWidth`, `panelHeight`, `transcriptLines`.
IPC: `omarchy-shell rcasaca.muesli toggle|enhance|open|close|togglePanel|settings|refresh`.

Developing: edit the copy under `~/.config/omarchy/plugins/` and `omarchy restart shell` — the shell caches
QML by URL, so in-place edits of an already-loaded panel are not picked up by the hot reload.
