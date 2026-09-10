#
# SmartLoadVideo.py
#
# Self-contained FFmpeg video loader with slice_index and a multiple snap.
# Does not depend on VideoHelperSuite.
#

from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import time
from typing import Any

import numpy as np
import torch

import folder_paths
from comfy.utils import ProgressBar

from . import smart_ffmpeg

logger = logging.getLogger(__name__)

_ENCODE_ARGS = smart_ffmpeg.ENCODE_ARGS
_VIDEO_EXTENSIONS = ("webm", "mp4", "mkv", "gif", "mov", "avi", "webp")
_DIM_MAX = 16384
_TIME_MAX = 1_000_000.0
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


def _video_path(video: str) -> str:
    return folder_paths.get_annotated_filepath(str(video).strip())


class SmartLoadVideo:
    @classmethod
    def INPUT_TYPES(cls):
        files = _list_input_videos()
        return {
            "required": {
                "video": (files or [""],),
                "force_rate": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 60.0,
                        "step": 1.0,
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
                        "tooltip": "Snap width and height up to this multiple. 1 leaves pixels unchanged.",
                    },
                ),
                "frame_load_cap": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 1_000_000,
                        "step": 1,
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
        "Loads a video from the input folder with FFmpeg. "
        "slice_index selects contiguous frame_load_cap chunks from start_time."
    )

    def load_video(
        self,
        video: str,
        force_rate: float = 0.0,
        custom_width: int = 0,
        custom_height: int = 0,
        multiple: int = 1,
        frame_load_cap: int = 0,
        start_time: float = 0.0,
        slice_index: int = 0,
    ):
        path = _video_path(video)
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(f"SmartLoadVideo: invalid video file: {video}")

        ffmpeg_path = _resolve_ffmpeg()
        probe = _probe_video(ffmpeg_path, path)
        fps = loaded_framerate(force_rate, probe["fps"])
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

        duration_s = images.shape[0] / fps
        audio = _extract_audio(ffmpeg_path, path, seek, duration_s)
        return (images, mask, audio, float(fps))

    @classmethod
    def IS_CHANGED(
        cls,
        video,
        force_rate=0.0,
        custom_width=0,
        custom_height=0,
        multiple=1,
        frame_load_cap=0,
        start_time=0.0,
        slice_index=0,
    ):
        try:
            path = _video_path(video)
            stat = os.stat(path)
            file_key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            file_key = str(video)
        return (
            file_key,
            float(force_rate),
            int(custom_width),
            int(custom_height),
            int(multiple),
            int(frame_load_cap),
            float(start_time),
            int(slice_index),
        )

    @classmethod
    def VALIDATE_INPUTS(cls, video, **kwargs):
        if not video:
            return "Invalid video file: (empty)"
        if not folder_paths.exists_annotated_filepath(video):
            return f"Invalid video file: {video}"
        return True


NODE_CLASS_MAPPINGS = {"SmartLoadVideo": SmartLoadVideo}
NODE_DISPLAY_NAME_MAPPINGS = {"SmartLoadVideo": "Smart Load Video"}
