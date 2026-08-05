import os
import subprocess
import sys
import tempfile

import requests

from version import __version__

GITHUB_REPO = "stompingsnowman/eft-flea"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(version_string):
    return tuple(int(part) for part in version_string.lstrip("v").split("."))


def check_for_update():
    """Returns update info dict if a newer release is available, else None."""
    try:
        response = requests.get(
            LATEST_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "EFT-Flea-Updater",
            },
            timeout=5,
        )
        response.raise_for_status()
        release = response.json()

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
    """Downloads the new exe, verifies it downloaded completely, then hands
    off to a helper script that waits for this process to exit, backs up
    the current exe, swaps in the new one (rolling back on failure), and
    relaunches it."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update only works from a packaged executable.")

    current_exe = sys.executable
    exe_dir = os.path.dirname(current_exe)
    exe_name = os.path.basename(current_exe)
    downloaded_path = os.path.join(exe_dir, "_update_download.exe")
    backup_path = current_exe + ".bak"

    response = requests.get(download_url, stream=True, timeout=60)
    response.raise_for_status()
    expected_size = int(response.headers.get("Content-Length") or 0)

    with open(downloaded_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    actual_size = os.path.getsize(downloaded_path)
    if expected_size and actual_size != expected_size:
        # Download was cut short - bail out without touching the exe that's
        # currently running. Never swap in a file we're not sure is intact.
        os.remove(downloaded_path)
        raise RuntimeError(
            f"Update download incomplete ({actual_size} of {expected_size} "
            "bytes) - not applying it, still running the current version."
        )

    helper_script = os.path.join(tempfile.gettempdir(), "eft_flea_apply_update.bat")
    with open(helper_script, "w") as f:
        f.write(
            "@echo off\r\n"
            f'del "{backup_path}" >nul 2>&1\r\n'
            ":retry\r\n"
            f'move /y "{current_exe}" "{backup_path}" >nul 2>&1\r\n'
            "if errorlevel 1 (\r\n"
            "    timeout /t 1 /nobreak > NUL\r\n"
            "    goto retry\r\n"
            ")\r\n"
            f'move /y "{downloaded_path}" "{current_exe}" >nul 2>&1\r\n'
            "if errorlevel 1 (\r\n"
            f'    move /y "{backup_path}" "{current_exe}" >nul 2>&1\r\n'
            "    exit /b 1\r\n"
            ")\r\n"
            # Brief pause before launching - a freshly-written exe can get
            # briefly locked by antivirus real-time scanning, which fails
            # an immediate launch attempt even though the file is fine.
            "timeout /t 2 /nobreak > NUL\r\n"
            f'start "" "{current_exe}"\r\n'
            "timeout /t 2 /nobreak > NUL\r\n"
            f'tasklist /FI "IMAGENAME eq {exe_name}" 2>NUL | find /I "{exe_name}" >NUL\r\n'
            "if errorlevel 1 (\r\n"
            f'    start "" "{current_exe}"\r\n'
            ")\r\n"
            'del "%~f0"\r\n'
        )

    subprocess.Popen(["cmd", "/c", "start", "", helper_script])
    os._exit(0)
