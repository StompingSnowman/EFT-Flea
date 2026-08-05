# EFT-Flea

Standalone desktop app that finds items worth buying on the Escape from
Tarkov (PvE) flea market and reselling to traders for a profit.

## For users

Download the latest `EFT-Flea.exe` from the
[Releases](https://github.com/stompingsnowman/eft-flea/releases) page and
run it — no installation needed. The app checks for updates on every
startup and will offer to update itself when a new version is published.

## Releasing a new version (maintainers)

1. Bump `__version__` in `version.py` (follow [Semantic
   Versioning](https://semver.org)).
2. Commit the change.
3. Tag the commit to match, e.g. `git tag v1.0.1`, then `git push --tags`.

Pushing a tag matching `v*.*.*` triggers `.github/workflows/release.yml`,
which builds `EFT-Flea.exe` on Windows and publishes it as a GitHub
Release with that tag. Running apps compare their own `version.py` value
against the latest release tag to decide whether to prompt for an update.

## Running from source

```
pip install -r requirements.txt
python desktop.py
```
