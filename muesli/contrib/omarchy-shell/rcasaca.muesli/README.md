# muesli · omarchy-shell bar widget

For Omarchy 4.x, whose bar is omarchy-shell (Quickshell), not Waybar.

```sh
cp -r muesli/contrib/omarchy-shell/rcasaca.muesli ~/.config/omarchy/plugins/
omarchy bar put rcasaca.muesli --section right
```

Left-click start/stop · right-click enhance the latest meeting · middle-click open the TUI.
Settings (poll interval, hide while idle) live in `~/.config/omarchy/shell.json` on the widget entry.
