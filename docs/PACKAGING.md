# Packaging and installing muesli on Linux

muesli is a pure-Python package (hatchling build) with two runtime deps (`textual`, `httpx`) and system deps on PipeWire (`pw-record`, `pw-dump`) and optionally `libnotify`. There are four ways to get it onto a machine, from quickest to most "proper". All of them produce the same `muesli` command.

## 1. `uv tool` straight from git (any distro)

```sh
uv tool install git+https://github.com/rjcasaca/muesli
uv tool install --with faster-whisper git+https://github.com/rjcasaca/muesli   # with local STT
uv tool upgrade muesli
```

`pipx install git+…` works identically. Binary lands in `~/.local/bin/muesli`, which is what the systemd unit expects.

## 2. Omarchy / Arch one-shot: `./install.sh`

Clones are editable installs (`uv tool install --editable .`), so `git pull` updates the running code. The script also installs system packages, runs `muesli init`, and enables `muesli.service`. Uninstall with `make uninstall`.

## 3. Arch package (`makepkg` / AUR)

```sh
cd packaging && makepkg -si
```

`PKGBUILD` builds a wheel with `python-build` and installs it with `python-installer`, depending on the distro's `python-textual` and `python-httpx`. It also installs the systemd unit system-wide (`/usr/lib/systemd/user/muesli.service`). To publish on the AUR, push this `PKGBUILD` (plus `makepkg --printsrcinfo > .SRCINFO`) to `ssh://aur@aur.archlinux.org/muesli-git.git`.

## 4. Release wheel from GitHub (any distro, offline-friendly)

Tagging `vX.Y.Z` runs `.github/workflows/release.yml`, which builds `muesli-X.Y.Z-py3-none-any.whl` + sdist and attaches them to a GitHub release. Install anywhere with:

```sh
uv tool install https://github.com/rjcasaca/muesli/releases/download/vX.Y.Z/muesli-X.Y.Z-py3-none-any.whl
```

Cut a release with `make release VERSION=0.3.0` (bumps `pyproject.toml` + `muesli/__init__.py`, commits, tags, pushes).

## Makefile targets

| target | does |
|---|---|
| `make install` | `uv tool install --editable .` + `muesli init` + enable service |
| `make uninstall` | disable service, `uv tool uninstall muesli` |
| `make build` | wheel + sdist into `dist/` |
| `make pkg` | `makepkg` in `packaging/` |
| `make check` | ruff + import smoke test |
| `make release VERSION=x.y.z` | bump, commit, tag, push |

## Other distros

- **Debian/Ubuntu**: `sudo apt install pipewire pipewire-audio-client-libraries libnotify-bin` then method 1 or 4. Waybar module works on any wlroots compositor; on GNOME/KDE just use the TUI/CLI.
- **Fedora**: `sudo dnf install pipewire pipewire-utils libnotify` then method 1 or 4.
- **NixOS**: not packaged yet; `uv tool` inside a shell with `pipewire` works.

## Service management

```sh
systemctl --user status muesli
systemctl --user restart muesli          # after editing config or env
journalctl --user -u muesli -f           # logs
```
