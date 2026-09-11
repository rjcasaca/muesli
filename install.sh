#!/usr/bin/env bash
# One-shot install for Omarchy / Arch. Uses uv so nothing touches system python.
set -euo pipefail
echo "→ system packages"
sudo pacman -S --needed --noconfirm pipewire pipewire-audio libnotify uv
echo "→ muesli"
cd "$(dirname "$0")"
uv tool install --force --editable .
echo "→ config, templates, waybar files"
muesli init
echo "→ user service"
mkdir -p ~/.config/systemd/user
cp muesli/contrib/muesli.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now muesli.service
if [ -d ~/.config/omarchy/plugins ] && pgrep -x quickshell > /dev/null; then
  echo "→ omarchy-shell bar widget"
  cp -r muesli/contrib/omarchy-shell/rcasaca.muesli ~/.config/omarchy/plugins/
  omarchy-shell shell rescanPlugins 2>/dev/null || true; sleep 1
  omarchy bar put rcasaca.muesli --section right 2>/dev/null || echo "   (add it later with: omarchy bar put rcasaca.muesli --section right)"
  cat << 'MSG'

Done. The muesli widget is on your omarchy-shell bar (left-click start/stop, right-click enhance, middle-click TUI).
API keys go in ~/.config/muesli/env  (e.g. GROQ_API_KEY=..., XAI_API_KEY=...)
Then: systemctl --user restart muesli
MSG
  exit 0
fi
cat << 'MSG'

Done. Last two manual steps (Waybar can't be edited safely by a script):
  1. ~/.config/waybar/config.jsonc  →  add  "include": ["~/.config/muesli/waybar/muesli.jsonc"]
     and put "custom/muesli" in modules-right (or wherever you like)
  2. ~/.config/waybar/style.css     →  add  @import "../muesli/waybar/muesli.css";
Then: omarchy-restart-waybar  (or pkill -SIGUSR2 waybar)

API keys go in ~/.config/muesli/env  (e.g. GROQ_API_KEY=..., XAI_API_KEY=...)
Then: systemctl --user restart muesli
MSG
