#
# SmartLoadVideo.py
#
# Self-contained FFmpeg video loader with slice_index and a multiple snap.
# Does not depend on VideoHelperSuite.
#

from __future__ import annotations

import asyncio
import ctypes
import logging
import math
import os
import re
import subprocess
import sys
import time
from typing import Any

import numpy as np
import torch
from aiohttp import web
from server import PromptServer

import folder_paths
from comfy.utils import ProgressBar

from . import smart_ffmpeg

logger = logging.getLogger(__name__)

_ENCODE_ARGS = smart_ffmpeg.ENCODE_ARGS
_VIDEO_EXTENSIONS = ("webm", "mp4", "mkv", "gif", "mov", "avi", "webp")
_DIM_MAX = 16384
_TIME_MAX = 1_000_000.0
LOAD_FORMATS: dict[str, dict[str, Any]] = {
    "None": {},
    "AnimateDiff": {"target_rate": 8, "dim": [8, 0, 512, 512]},
    "Mochi": {"target_rate": 24, "dim": [16, 0, 848, 480], "frames": [6, 1]},
    "LTXV": {"target_rate": 24, "dim": [32, 0, 768, 512], "frames": [8, 1]},
    "Hunyuan": {"target_rate": 24, "dim": [16, 0, 848, 480], "frames": [4, 1]},
    "Cosmos": {"target_rate": 24, "dim": [8, 0, 1280, 704], "frames": [8, 1]},
    "Wan": {"target_rate": 16, "dim": [8, 0, 832, 480], "frames": [4, 1]},
    "H3": {"target_rate": 24, "dim": [32, 0, 1344, 768], "frames": [17, 5]},
}
_STREAM_SIZE_RE = re.compile(r"^ *Stream .* Video.*, ([1-9]|\d{2,})x(\d+)")
_FPS_RE = re.compile(r", ([\d.]+) fps")
_DURATION_RE = re.compile(r"Duration: (\d+:\d+:\d+\.\d+),")
_AUDIO_RE = re.compile(r", (\d+) Hz, (\w+), ")


def _resolve_ffmpeg() -> str:
    return smart_ffmpeg.resolve_ffmpeg("SmartLoadVideo")


def _list_input_videos() -> list[str]:
    input_dir = folder_paths.get_input_directory()
    files: list[str] = []
    if not os.path.isdir(input_dir):
        return files
    for name in os.listdir(input_dir):
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _VIDEO_EXTENSIONS:
            files.append(name)
    return sorted(files)


def get_load_format(name: str) -> dict[str, Any]:
    return dict(LOAD_FORMATS.get(str(name or "None"), {}))


def snap_frame_count(count: int, div: int, mod: int) -> int:
    n = int(count)
    step = int(div)
    remainder = int(mod)
    if n <= 0 or step <= 0:
        return max(0, n)
    k = n - ((n - remainder) % step)
    if k > n:
        k -= step
    return max(0, k)


def snap_frame_count_up(count: int, div: int, mod: int) -> int:
    """Smallest n >= max(count, mod) with n % div == mod. 0 stays 0."""
    n = int(count)
    step = int(div)
    remainder = int(mod)
    if n <= 0 or step <= 0:
        return max(0, n)
    n = max(n, remainder)
    return n + (remainder - n % step) % step


def frames_from_cap_seconds(
    seconds: float,
    fps: float,
    frames_rule: list[int] | tuple[int, ...] | None = None,
) -> int:
    if float(seconds) <= 0 or float(fps) <= 0:
        return 0
    raw = int(round(float(fps) * float(seconds)))
    if frames_rule and len(frames_rule) >= 2:
        return snap_frame_count_up(raw, frames_rule[0], frames_rule[1])
    return raw


def resolve_frame_load_cap(
    frame_load_cap: int,
    cap_seconds: float,
    fps: float,
    frames_rule: list[int] | tuple[int, ...] | None = None,
) -> int:
    if float(cap_seconds) > 0:
        return frames_from_cap_seconds(cap_seconds, fps, frames_rule)
    cap = int(frame_load_cap)
    if cap > 0 and frames_rule and len(frames_rule) >= 2:
        return snap_frame_count_up(cap, frames_rule[0], frames_rule[1])
    return cap


def snap_up(dim: int, multiple: int) -> int:
    m = max(1, int(multiple))
    d = max(1, int(dim))
    return ((d + m - 1) // m) * m


def target_size(
    src_w: int,
    src_h: int,
    custom_width: int,
    custom_height: int,
    multiple: int,
) -> tuple[int, int]:
    width = max(1, int(src_w))
    height = max(1, int(src_h))
    custom_w = int(custom_width)
    custom_h = int(custom_height)
    if custom_w == 0 and custom_h == 0:
        pass
    elif custom_h == 0:
        height = max(1, int(round(height * (custom_w / width))))
        width = custom_w
    elif custom_w == 0:
        width = max(1, int(round(width * (custom_h / height))))
        height = custom_h
    else:
        width = custom_w
        height = custom_h
    return snap_up(width, multiple), snap_up(height, multiple)


def effective_start_seconds(
    start_time: float,
    slice_index: int,
    frame_load_cap: int,
    loaded_fps: float,
) -> float:
    index = int(slice_index)
    cap = int(frame_load_cap)
    if index < 0:
        raise ValueError("SmartLoadVideo: slice_index must be >= 0.")
    if cap <= 0 and index > 0:
        raise ValueError(
            "SmartLoadVideo: slice_index requires frame_load_cap > 0 "
            "(unlimited load has no slice width)."
        )
    fps = float(loaded_fps)
    if fps <= 0:
        raise ValueError("SmartLoadVideo: could not determine framerate.")
    return float(start_time) + (index * max(0, cap)) / fps


def loaded_framerate(force_rate: float, source_fps: float) -> float:
    rate = float(force_rate)
    if rate > 0:
        return rate
    fps = float(source_fps)
    if fps <= 0:
        raise ValueError("SmartLoadVideo: could not determine source framerate.")
    return fps


def _parse_duration(text: str) -> float:
    match = _DURATION_RE.search(text)
    if not match:
        return 0.0
    hours, minutes, seconds = match.group(1).split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _probe_video(ffmpeg_path: str, video: str) -> dict[str, Any]:
    args_input = ["-i", video]
    dummy = [ffmpeg_path, *args_input, "-c", "copy", "-frames:v", "1", "-f", "null", "-"]
    try:
        dummy_res = subprocess.run(
            dummy,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "SmartLoadVideo: ffmpeg failed to probe the video:\n"
            + exc.stderr.decode(*_ENCODE_ARGS)
        ) from exc
    lines = dummy_res.stderr.decode(*_ENCODE_ARGS)
    if "Video: vp9 " in lines:
        args_input = ["-c:v", "libvpx-vp9", "-i", video]
        dummy = [ffmpeg_path, *args_input, "-c", "copy", "-frames:v", "1", "-f", "null", "-"]
        try:
            dummy_res = subprocess.run(
                dummy,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "SmartLoadVideo: ffmpeg failed to probe the VP9 video:\n"
                + exc.stderr.decode(*_ENCODE_ARGS)
            ) from exc
        lines = dummy_res.stderr.decode(*_ENCODE_ARGS)

    size = None
    fps = 1.0
    alpha = False
    for line in lines.splitlines():
        match = _STREAM_SIZE_RE.search(line)
        if match is None:
            continue
        size = (int(match.group(1)), int(match.group(2)))
        fps_match = _FPS_RE.search(line)
        fps = float(fps_match.group(1)) if fps_match else 1.0
        alpha = re.search(r"(yuva|rgba|bgra|gbra)", line) is not None
        break
    if size is None:
        raise RuntimeError(
            "SmartLoadVideo: failed to parse video information. FFmpeg output:\n" + lines
        )
    return {
        "args_input": args_input,
        "width": size[0],
        "height": size[1],
        "fps": fps,
        "duration": _parse_duration(lines),
        "alpha": alpha,
    }


def _vfilters(
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    custom_width: int,
    custom_height: int,
    force_rate: float,
) -> list[str]:
    filters: list[str] = []
    if float(force_rate) > 0:
        filters.append(f"fps=fps={force_rate}")
    if out_w == src_w and out_h == src_h:
        return filters
    if int(custom_width) > 0 and int(custom_height) > 0:
        ar = float(custom_width) / float(custom_height)
        if abs(src_w * ar - src_h) >= 1:
            filters.append(
                f"crop=if(gt({ar}\\,a)\\,iw\\,ih*{ar}):if(gt({ar}\\,a)\\,iw/{ar}\\,ih)"
            )
    filters.append(f"scale={out_w}:{out_h}")
    return filters


def _decode_frames(
    ffmpeg_path: str,
    args_input: list[str],
    start_time: float,
    force_rate: float,
    frame_load_cap: int,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    custom_width: int,
    custom_height: int,
    alpha: bool,
    yieldable_frames: int,
) -> np.ndarray:
    if start_time > 0:
        if start_time > 4:
            post_seek = ["-ss", "4"]
            args_input = ["-ss", str(start_time - 4), *args_input]
        else:
            post_seek = ["-ss", str(start_time)]
    else:
        post_seek = []

    args = [ffmpeg_path, "-v", "error", "-an", *args_input, "-pix_fmt", "rgba64le", *post_seek]
    filters = _vfilters(
        src_w, src_h, out_w, out_h, custom_width, custom_height, force_rate
    )
    if filters:
        args += ["-vf", ",".join(filters)]
    if int(frame_load_cap) > 0:
        args += ["-frames:v", str(int(frame_load_cap))]
    args += ["-f", "rawvideo", "-"]

    bytes_per_image = out_w * out_h * 8
    frames: list[np.ndarray] = []
    pbar = ProgressBar(max(1, int(math.ceil(yieldable_frames)))) if yieldable_frames else None
    try:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            assert proc.stdout is not None
            current = bytearray(bytes_per_image)
            offset = 0
            while True:
                chunk = proc.stdout.read(bytes_per_image - offset)
                if chunk is None:
                    time.sleep(0.1)
                    continue
                if len(chunk) == 0:
                    break
                current[offset : offset + len(chunk)] = chunk
                offset += len(chunk)
                if offset < bytes_per_image:
                    continue
                raw = np.frombuffer(
                    current,
                    dtype=np.dtype(np.uint16).newbyteorder("<"),
                ).reshape(out_h, out_w, 4) / (2**16 - 1)
                if not alpha:
                    raw = raw[:, :, :3]
                frames.append(np.ascontiguousarray(raw, dtype=np.float32))
                offset = 0
                if pbar is not None:
                    pbar.update(1)
            stderr = proc.stderr.read() if proc.stderr is not None else b""
            if proc.wait() != 0 and not frames:
                raise RuntimeError(
                    "SmartLoadVideo: ffmpeg failed to decode frames:\n"
                    + stderr.decode(*_ENCODE_ARGS)
                )
    except BrokenPipeError as exc:
        raise RuntimeError("SmartLoadVideo: ffmpeg decode pipe closed unexpectedly.") from exc

    if not frames:
        raise RuntimeError("SmartLoadVideo: no frames generated.")
    return np.stack(frames, axis=0)


def _silent_audio(duration_s: float, sample_rate: int = 44100, channels: int = 2) -> dict[str, Any]:
    samples = max(1, int(round(max(0.0, duration_s) * sample_rate)))
    waveform = torch.zeros((1, channels, samples), dtype=torch.float32)
    return {"waveform": waveform, "sample_rate": sample_rate}


def _extract_audio(
    ffmpeg_path: str,
    video: str,
    start_time: float,
    duration_s: float,
) -> dict[str, Any]:
    args = [ffmpeg_path, "-i", video]
    if start_time > 0:
        args += ["-ss", str(start_time)]
    if duration_s > 0:
        args += ["-t", str(duration_s)]
    args += ["-f", "f32le", "-"]
    try:
        result = subprocess.run(args, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode(*_ENCODE_ARGS)
        if re.search(r"does not contain any stream|Output file does not contain", err):
            logger.warning("SmartLoadVideo: no audio stream; returning silence.")
            return _silent_audio(duration_s)
        logger.warning("SmartLoadVideo: audio extract failed; returning silence.\n%s", err)
        return _silent_audio(duration_s)

    audio = torch.frombuffer(bytearray(result.stdout), dtype=torch.float32)
    match = _AUDIO_RE.search(result.stderr.decode(*_ENCODE_ARGS))
    if match:
        sample_rate = int(match.group(1))
        channels = {"mono": 1, "stereo": 2}.get(match.group(2))
        if channels is None:
            logger.warning(
                "SmartLoadVideo: unsupported audio layout %s; returning silence.",
                match.group(2),
            )
            return _silent_audio(duration_s, sample_rate)
    else:
        sample_rate = 44100
        channels = 2
    if audio.numel() == 0:
        return _silent_audio(duration_s, sample_rate, channels)
    usable = (audio.numel() // channels) * channels
    audio = audio[:usable].reshape((-1, channels)).transpose(0, 1).unsqueeze(0)
    return {"waveform": audio.contiguous(), "sample_rate": sample_rate}


def resolve_source(video: str, video_path: str = "") -> str:
    """Prefer an explicit disk path; otherwise resolve the input-folder combo."""
    path_text = str(video_path or "").strip()
    if path_text:
        resolved = os.path.abspath(os.path.expanduser(path_text))
        if not os.path.isfile(resolved):
            raise FileNotFoundError(f"SmartLoadVideo: invalid video path: {video_path}")
        return resolved

    combo = str(video or "").strip()
    if not combo:
        raise FileNotFoundError(
            "SmartLoadVideo: no video selected. Set video_path or choose a file."
        )
    path = folder_paths.get_annotated_filepath(combo)
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"SmartLoadVideo: invalid video file: {video}")
    return path


def _video_extension(path: str) -> str:
    name = os.path.basename(path)
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def safe_view_path(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        resolved = os.path.realpath(os.path.expanduser(text))
    except OSError:
        return None
    if not os.path.isfile(resolved):
        return None
    if _video_extension(resolved) not in _VIDEO_EXTENSIONS:
        return None
    return resolved


def _pick_video_file() -> str:
    filter_spec = "Video files|*.mp4;*.webm;*.mkv;*.gif;*.mov;*.avi;*.webp|All files|*.*"
    if sys.platform == "win32":
        try:
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$d = New-Object System.Windows.Forms.OpenFileDialog;"
                f"$d.Filter='{filter_spec}';"
                "$d.Title='Select video';"
                "$form = New-Object System.Windows.Forms.Form;"
                "$form.TopMost = $true;"
                "$form.WindowState = 'Minimized';"
                "$form.ShowInTaskbar = $false;"
                "$form.Add_Shown({$form.Activate()});"
                "if($d.ShowDialog($form) -eq 'OK'){ Write-Output $d.FileName };"
                "$form.Dispose();"
            )
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                creationflags=flags,
            )
            out = proc.stdout.strip()
            if out:
                try:
                    user32 = ctypes.windll.user32
                    hwnd = user32.GetForegroundWindow()
                    if hwnd:
                        user32.SetForegroundWindow(hwnd)
                        user32.BringWindowToTop(hwnd)
                except Exception:
                    pass
            return out
        except Exception:
            return ""

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        path = filedialog.askopenfilename(
            title="Select video",
            filetypes=[
                ("Video files", "*.mp4 *.webm *.mkv *.gif *.mov *.avi *.webp"),
                ("All files", "*.*"),
            ],
        )
        try:
            root.destroy()
        except Exception:
            pass
        return str(path or "")
    except Exception:
        return ""


@PromptServer.instance.routes.post("/smart_tools/load_video/browse_file")
async def browse_file_handler(request):
    path = await asyncio.get_event_loop().run_in_executor(None, _pick_video_file)
    if not path:
        return web.Response(status=204)
    return web.json_response({"path": path})


@PromptServer.instance.routes.get("/smart_tools/load_video/view")
async def view_video_handler(request):
    path = safe_view_path(request.rel_url.query.get("path", ""))
    if not path:
        return web.Response(status=404, text="Invalid video path")
    return web.FileResponse(path)


def source_info_from_probe(probe: dict[str, Any]) -> dict[str, Any]:
    fps = float(probe.get("fps") or 0)
    duration = float(probe.get("duration") or 0)
    frames = int(round(duration * fps)) if fps > 0 and duration > 0 else 0
    return {
        "fps": fps,
        "frames": frames,
        "duration": duration,
        "width": int(probe.get("width") or 0),
        "height": int(probe.get("height") or 0),
    }


_query_cache: dict[str, tuple[int, dict[str, Any]]] = {}


def query_video_source(path: str) -> dict[str, Any]:
    real = os.path.realpath(path)
    mtime_ns = os.stat(real).st_mtime_ns
    cached = _query_cache.get(real)
    if cached and cached[0] == mtime_ns:
        return cached[1]
    source = source_info_from_probe(_probe_video(_resolve_ffmpeg(), real))
    _query_cache[real] = (mtime_ns, source)
    return source


def resolve_query_path(query: Any) -> str | None:
    path = safe_view_path(query.get("path", ""))
    if path:
        return path
    filename = str(query.get("filename") or "").strip()
    if not filename:
        return None
    try:
        resolved = folder_paths.get_annotated_filepath(filename)
    except Exception:
        return None
    if not resolved or not os.path.isfile(resolved):
        return None
    if _video_extension(resolved) not in _VIDEO_EXTENSIONS:
        return None
    return os.path.realpath(resolved)


@PromptServer.instance.routes.get("/smart_tools/load_video/query")
async def query_video_handler(request):
    path = resolve_query_path(request.rel_url.query)
    if not path:
        return web.Response(status=404, text="Invalid video path")
    try:
        source = query_video_source(path)
    except Exception as exc:
        logger.warning("SmartLoadVideo: query failed for %s: %s", path, exc)
        return web.json_response({})
    return web.json_response({"source": source})


class SmartLoadVideo:
    @classmethod
    def INPUT_TYPES(cls):
        files = _list_input_videos()
        return {
            "required": {
                "video": (files or [""],),
                "video_path": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": r"D:\videos\clip.mp4",
                        "tooltip": (
                            "Absolute path (or ~) to a video on disk. "
                            "When set, this is used instead of the input-folder combo."
                        ),
                    },
                ),
                "force_rate": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 240.0,
                        "step": 1.0,
                        "disable": 0,
                        "widgetType": "SLVFLOAT",
                        "tooltip": "0 uses the source framerate.",
                    },
                ),
                "custom_width": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": _DIM_MAX,
                        "step": 1,
                        "disable": 0,
                        "widgetType": "SLVINT",
                        "tooltip": "0 keeps or derives width from the other setting.",
                    },
                ),
                "custom_height": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": _DIM_MAX,
                        "step": 1,
                        "disable": 0,
                        "widgetType": "SLVINT",
                        "tooltip": "0 keeps or derives height from the other setting.",
                    },
                ),
                "multiple": (
                    "INT",
                    {
                        "default": 1,
                        "min": 1,
                        "max": _DIM_MAX,
                        "step": 1,
                        "tooltip": "Snap width and height up to this multiple. 1 leaves pixels unchanged. Format presets set this to the model grid (8/16/32).",
                    },
                ),
                "format": (
                    list(LOAD_FORMATS.keys()),
                    {
                        "default": "None",
                        "formats": LOAD_FORMATS,
                        "tooltip": (
                            "Model preset. Sets reset targets and snap rules; "
                            "does not change values until you click reset. "
                            "H3: 24 fps reset, 1344×768 reset, multiple 32, frames 5+17n."
                        ),
                    },
                ),
                "cap_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": _TIME_MAX,
                        "step": 0.1,
                        "tooltip": (
                            "0 leaves frame_load_cap unchanged. "
                            "Otherwise sets frame_load_cap from loaded fps × seconds, "
                            "then snaps to the format frame rule."
                        ),
                    },
                ),
                "frame_load_cap": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 1_000_000,
                        "step": 1,
                        "disable": 0,
                        "widgetType": "SLVINT",
                        "tooltip": "0 loads all remaining frames from the effective start.",
                    },
                ),
                "start_time": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": _TIME_MAX,
                        "step": 0.001,
                    },
                ),
                "slice_index": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 1_000_000,
                        "step": 1,
                        "tooltip": "0 starts at start_time. Each next index jumps forward by frame_load_cap frames.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "AUDIO", "FLOAT")
    RETURN_NAMES = ("IMAGE", "mask", "audio", "framerate")
    FUNCTION = "load_video"
    CATEGORY = "slikvik"
    DESCRIPTION = (
        "Loads a video from a disk path or the input folder with FFmpeg. "
        "slice_index selects contiguous frame_load_cap chunks from start_time. "
        "format presets set reset/snap rules (VHS-style)."
    )

    def load_video(
        self,
        video: str,
        video_path: str = "",
        force_rate: float = 0.0,
        custom_width: int = 0,
        custom_height: int = 0,
        multiple: int = 1,
        format: str = "None",
        cap_seconds: float = 0.0,
        frame_load_cap: int = 0,
        start_time: float = 0.0,
        slice_index: int = 0,
    ):
        path = resolve_source(video, video_path)

        ffmpeg_path = _resolve_ffmpeg()
        probe = _probe_video(ffmpeg_path, path)
        fps = loaded_framerate(force_rate, probe["fps"])
        spec = get_load_format(format)
        frames_rule = spec.get("frames")
        frame_load_cap = resolve_frame_load_cap(
            frame_load_cap, cap_seconds, fps, frames_rule
        )
        seek = effective_start_seconds(start_time, slice_index, frame_load_cap, fps)
        out_w, out_h = target_size(
            probe["width"],
            probe["height"],
            custom_width,
            custom_height,
            multiple,
        )

        remaining = 0.0
        if probe["duration"] > 0:
            remaining = max(0.0, probe["duration"] - seek)
        if int(frame_load_cap) > 0:
            yieldable = float(frame_load_cap)
            if remaining > 0:
                yieldable = min(yieldable, remaining * fps)
        else:
            yieldable = remaining * fps if remaining > 0 else 0.0

        images_np = _decode_frames(
            ffmpeg_path,
            list(probe["args_input"]),
            seek,
            force_rate,
            int(frame_load_cap),
            probe["width"],
            probe["height"],
            out_w,
            out_h,
            int(custom_width),
            int(custom_height),
            bool(probe["alpha"]),
            yieldable,
        )
        images = torch.from_numpy(images_np)
        if images.ndim != 4:
            raise RuntimeError("SmartLoadVideo: decoded frames have an unexpected shape.")

        if images.shape[-1] == 4:
            mask = 1.0 - images[:, :, :, 3]
            images = images[:, :, :, :3]
        else:
            mask = torch.ones(
                (images.shape[0], images.shape[1], images.shape[2]),
                dtype=torch.float32,
                device=images.device,
            )

        if frames_rule and len(frames_rule) >= 2:
            keep = snap_frame_count(int(images.shape[0]), frames_rule[0], frames_rule[1])
            if keep <= 0:
                raise RuntimeError(
                    f"SmartLoadVideo: {images.shape[0]} frames is incompatible with "
                    f"format {format!r} (need n %{frames_rule[0]} == {frames_rule[1]})."
                )
            if keep < int(images.shape[0]):
                images = images[:keep]
                mask = mask[:keep]

        duration_s = images.shape[0] / fps
        audio = _extract_audio(ffmpeg_path, path, seek, duration_s)
        return (images, mask, audio, float(fps))

    @classmethod
    def IS_CHANGED(
        cls,
        video,
        video_path="",
        force_rate=0.0,
        custom_width=0,
        custom_height=0,
        multiple=1,
        format="None",
        cap_seconds=0.0,
        frame_load_cap=0,
        start_time=0.0,
        slice_index=0,
    ):
        try:
            path = resolve_source(video, video_path)
            stat = os.stat(path)
            file_key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            file_key = (str(video), str(video_path))
        return (
            file_key,
            float(force_rate),
            int(custom_width),
            int(custom_height),
            int(multiple),
            str(format),
            float(cap_seconds),
            int(frame_load_cap),
            float(start_time),
            int(slice_index),
        )

    @classmethod
    def VALIDATE_INPUTS(cls, video, video_path="", **kwargs):
        try:
            resolve_source(video, video_path)
        except FileNotFoundError as exc:
            return str(exc)
        return True


NODE_CLASS_MAPPINGS = {"SmartLoadVideo": SmartLoadVideo}
NODE_DISPLAY_NAME_MAPPINGS = {"SmartLoadVideo": "Smart Load Video"}
