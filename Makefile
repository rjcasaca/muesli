.PHONY: install uninstall build pkg check release clean

install:
	uv tool install --force --editable .
	muesli init
	mkdir -p ~/.config/systemd/user && cp muesli/contrib/muesli.service ~/.config/systemd/user/
	systemctl --user daemon-reload && systemctl --user enable --now muesli.service

uninstall:
	-systemctl --user disable --now muesli.service
	-rm -f ~/.config/systemd/user/muesli.service
	-uv tool uninstall muesli

build:
	rm -rf dist && uv build

pkg:
	cd packaging && makepkg -sfi

check:
	uv run --with ruff ruff check muesli
	uv run python -c "import muesli.cli, muesli.daemon, muesli.tui, muesli.stt, muesli.detect"

release:
	@test -n "$(VERSION)" || (echo "usage: make release VERSION=x.y.z" && exit 1)
	sed -i 's/^version = ".*"/version = "$(VERSION)"/' pyproject.toml
	sed -i 's/^__version__ = ".*"/__version__ = "$(VERSION)"/' muesli/__init__.py
	sed -i 's/^pkgver=.*/pkgver=$(VERSION)/' packaging/PKGBUILD
	git add pyproject.toml muesli/__init__.py packaging/PKGBUILD
	git commit -m "release v$(VERSION)"
	git tag -a v$(VERSION) -m "v$(VERSION)"
	git push && git push --tags

clean:
	rm -rf dist build *.egg-info packaging/src packaging/pkg packaging/*.pkg.tar.*
