# muesli · omarchy-shell bar widget

For Omarchy 4.x, whose bar is omarchy-shell (Quickshell), not Waybar.

```sh
cp -r muesli/contrib/omarchy-shell/rcasaca.muesli ~/.config/omarchy/plugins/
omarchy bar put rcasaca.muesli --section right
```

The bar shows `○` idle · `◉ Teams` pulsing when a meeting app opens the mic · `● 12:34` while recording.

**Left-click** opens a flyout: state + start/stop switch, the live transcript tail, recent meetings
(enhance / open folder per row), *Open TUI*, *Enhance latest*. Keys inside the flyout: `R` record, `E` enhance, `T` TUI, `Esc` close.
**Right-click** starts/stops recording, **middle-click** enhances the latest meeting.

Every click is remappable to `panel | toggle | enhance | tui`, e.g. to get the Waybar module's mapping back:

```sh
omarchy bar set rcasaca.muesli leftClick toggle
omarchy bar set rcasaca.muesli rightClick enhance
omarchy bar set rcasaca.muesli middleClick panel
```

Other settings: `pollSeconds`, `hideWhenIdle`, `hideWhenOff`, `panelWidth`, `panelHeight`, `transcriptLines`.
IPC: `omarchy-shell rcasaca.muesli toggle|enhance|open|close|togglePanel|refresh`.
