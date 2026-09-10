#
# smart_ffmpeg.py
#
# Shared FFmpeg path resolution for Smart Save Video and Smart Load Video.
#

from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

ENCODE_ARGS = ("utf-8", "backslashreplace")
FFMPEG_UNSET = object()
_FFMPEG_PATH: str | None | object = FFMPEG_UNSET


def _ffmpeg_candidate_ok(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        subprocess.run(
            [path, "-version"],
            capture_output=True,
            check=True,
            timeout=15,
        )
        return True
    except Exception:
        return False


def resolve_ffmpeg(caller: str = "SmartTools") -> str:
    global _FFMPEG_PATH
    if _FFMPEG_PATH is not FFMPEG_UNSET:
        if _FFMPEG_PATH is None:
            raise RuntimeError(
                f"{caller}: ffmpeg is required and could not be found. "
                "Install imageio-ffmpeg (`pip install imageio-ffmpeg`), place ffmpeg "
                "next to ComfyUI, or add ffmpeg to PATH."
            )
        return str(_FFMPEG_PATH)

    paths: list[str] = []
    forced = os.environ.get("SMART_SAVE_VIDEO_FFMPEG") or os.environ.get("VHS_FORCE_FFMPEG_PATH")
    if forced:
        paths.append(forced)
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        paths.append(get_ffmpeg_exe())
    except Exception:
        pass
    which = shutil.which("ffmpeg")
    if which:
        paths.append(which)
    for name in ("ffmpeg.exe", "ffmpeg"):
        if os.path.isfile(name):
            paths.append(os.path.abspath(name))

    resolved = next((path for path in paths if _ffmpeg_candidate_ok(path)), None)
    _FFMPEG_PATH = resolved
    if _FFMPEG_PATH is None:
        raise RuntimeError(
            f"{caller}: ffmpeg is required and could not be found. "
            "Install imageio-ffmpeg (`pip install imageio-ffmpeg`), place ffmpeg "
            "next to ComfyUI, or add ffmpeg to PATH."
        )
    logger.info("%s: using ffmpeg at %s", caller, _FFMPEG_PATH)
    return str(_FFMPEG_PATH)
