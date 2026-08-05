# EFT-Flea

Finds items worth buying on the Escape from Tarkov (PvE) flea market and
reselling to traders for a profit. One backend (`app.py`), two ways to run
it from the same repository:

- **Standalone desktop app** (`desktop.py`) — a native window, distributed
  as a self-updating Windows `.exe`.
- **Web service** (`app.py` alone) — deployable as-is to Azure App Service
  or any other WSGI host.

## Architecture

`app.py` is the entire application: the flea market/trader profit logic
and a Flask REST API (`/api/items`, plus update-check endpoints used only
by the desktop build). It has no dependency on how it's run.

- `requirements.txt` — the complete dependency list for running `app.py`
  as a plain web service. This is what a web host installs.
- `requirements-desktop.txt` — adds `pywebview` (native window) and
  `pythonnet` (Windows WebView2 bridge) on top of the above, for the
  desktop build only.
- `desktop.py` — starts `app.py` on a local port in a background thread,
  then opens it in a native window via `pywebview`. This is the only file
  that imports `pywebview`; a web deployment never touches it.

This split means changes to the actual price/profit logic in `app.py`
automatically apply to both the desktop app and any web deployment, with
nothing to keep in sync by hand.

## For users (desktop app)

Download the latest `EFT-Flea.exe` from the
[Releases](https://github.com/stompingsnowman/eft-flea/releases) page and
run it — no installation needed. The app checks for updates on every
startup and will offer to update itself when a new version is published.

## Running from source

Desktop app:
```
pip install -r requirements-desktop.txt
python desktop.py
```

Web service (what a host like Azure runs):
```
pip install -r requirements.txt
gunicorn app:app
```

## Deploying to Azure App Service

The repo already includes what App Service's Python/Linux (Oryx) build
expects:
- `requirements.txt` — installed automatically during deployment.
- `startup.txt` — tells App Service to run
  `gunicorn --bind=0.0.0.0 --timeout 600 app:app`.

So deployment is just pointing an App Service (Linux, Python runtime) at
this repo/branch — no extra configuration needed. `pywebview` and
`pythonnet` are never installed there since they only live in
`requirements-desktop.txt`.

## Releasing a new desktop version (maintainers)

1. Bump `__version__` in `version.py` (follow [Semantic
   Versioning](https://semver.org)).
2. Commit and push the change.
3. Trigger `.github/workflows/release.yml` with that version number
   (either by pushing a matching `vX.Y.Z` tag, or via the "Run workflow"
   button using the `version` input).

The workflow builds `EFT-Flea.exe` on Windows using
`requirements-desktop.txt` and publishes it as a GitHub Release. Running
desktop apps compare their own `version.py` value against the latest
release tag to decide whether to prompt for a self-update.
