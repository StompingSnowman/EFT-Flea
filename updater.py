import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from version import __version__

GITHUB_REPO = "stompingsnowman/eft-flea"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(version_string):
    return tuple(int(part) for part in version_string.lstrip("v").split("."))


def check_for_update():
    """Returns update info dict if a newer release is available, else None."""
    try:
        request = urllib.request.Request(
            LATEST_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "EFT-Flea-Updater",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            release = json.load(response)

        latest_version = release["tag_name"]
        if _parse_version(latest_version) <= _parse_version(__version__):
            return None

        asset = next(
            (a for a in release.get("assets", []) if a["name"].endswith(".exe")),
            None,
        )
        if not asset:
            return None

        return {
            "version": latest_version.lstrip("v"),
            "download_url": asset["browser_download_url"],
            "notes": release.get("body") or "",
        }
    except Exception:
        # Any failure (offline, rate limit, no releases yet) just means
        # "no update available" - never block the app from starting.
        return None


def download_and_apply_update(download_url):
    """Downloads the new exe, then hands off to a helper script that waits
    for this process to exit, replaces the exe, and relaunches it."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update only works from a packaged executable.")

    current_exe = sys.executable
    exe_dir = os.path.dirname(current_exe)
    downloaded_path = os.path.join(exe_dir, "_update_download.exe")

    urllib.request.urlretrieve(download_url, downloaded_path)

    helper_script = os.path.join(tempfile.gettempdir(), "eft_flea_apply_update.bat")
    with open(helper_script, "w") as f:
        f.write(
            "@echo off\r\n"
            ":retry\r\n"
            f'move /y "{downloaded_path}" "{current_exe}" >nul 2>&1\r\n'
            "if errorlevel 1 (\r\n"
            "    timeout /t 1 /nobreak > NUL\r\n"
            "    goto retry\r\n"
            ")\r\n"
            f'start "" "{current_exe}"\r\n'
            'del "%~f0"\r\n'
        )

    subprocess.Popen(["cmd", "/c", "start", "", helper_script])
    os._exit(0)
