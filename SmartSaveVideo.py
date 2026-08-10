#
# SmartSaveVideo.py
#
# Self-contained FFmpeg video encoder with Smart Save manual/autosave UX.
# Does not depend on VideoHelperSuite.
#

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import string
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

import numpy as np
import torch
from aiohttp import web
from server import PromptServer

import folder_paths
from comfy.cli_args import args as comfy_args
from comfy.utils import ProgressBar

from .SmartSave import next_available_path, resolve_output_dir

logger = logging.getLogger(__name__)

_FORMATS_DIR = Path(__file__).with_name("video_formats")
_ENCODE_ARGS = ("utf-8", "backslashreplace")
_FFMPEG_UNSET = object()
_FFMPEG_PATH: str | None | object = _FFMPEG_UNSET


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


def _resolve_ffmpeg() -> str:
    global _FFMPEG_PATH
    if _FFMPEG_PATH is not _FFMPEG_UNSET:
        if _FFMPEG_PATH is None:
            raise RuntimeError(
                "SmartSaveVideo: ffmpeg is required and could not be found. "
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
            "SmartSaveVideo: ffmpeg is required and could not be found. "
            "Install imageio-ffmpeg (`pip install imageio-ffmpeg`), place ffmpeg "
            "next to ComfyUI, or add ffmpeg to PATH."
        )
    logger.info("SmartSaveVideo: using ffmpeg at %s", _FFMPEG_PATH)
    return str(_FFMPEG_PATH)


def _list_formats() -> list[str]:
    if not _FORMATS_DIR.is_dir():
        return ["h264-mp4"]
    names = sorted(path.stem for path in _FORMATS_DIR.glob("*.json"))
    return names or ["h264-mp4"]


def _load_format_json(format_name: str) -> dict[str, Any]:
    path = _FORMATS_DIR / f"{format_name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"SmartSaveVideo: unknown format preset {format_name!r}.")
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


_FORMAT_OPTION_WIDGETS = (
    "crf",
    "pix_fmt",
    "bitrate",
    "megabit",
    "save_metadata",
    "trim_to_audio",
    "profile",
    "input_color_depth",
    "gop_size",
)


def _widget_names_for_format(format_name: str) -> list[str]:
    video_format = _load_format_json(format_name)
    names: list[str] = []
    for widget in _iterate_format(video_format, for_widgets=True):
        if isinstance(widget, list) and widget and isinstance(widget[0], str):
            names.append(widget[0])
    # Preserve stable UI order for known option widgets.
    ordered = [name for name in _FORMAT_OPTION_WIDGETS if name in names]
    extras = [name for name in names if name not in ordered]
    return ordered + extras


def _format_widget_map() -> dict[str, list[str]]:
    return {name: _widget_names_for_format(name) for name in _list_formats()}


def _flatten_list(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if isinstance(value, list):
            result.extend(value)
        else:
            result.append(value)
    return result


def _iterate_format(video_format: dict[str, Any], for_widgets: bool = True):
    def indirector(cont, index):
        item = cont[index]
        if isinstance(item, list) and (
            not for_widgets
            or (len(item) > 1 and not isinstance(item[1], dict))
        ):
            replacement = yield item
            if replacement is not None:
                cont[index] = replacement
                yield

    for key in list(video_format.keys()):
        if key == "extra_widgets":
            if for_widgets:
                yield from video_format["extra_widgets"]
        elif key.endswith("_pass"):
            for index in range(len(video_format[key])):
                yield from indirector(video_format[key], index)
            if not for_widgets:
                video_format[key] = _flatten_list(video_format[key])
        else:
            yield from indirector(video_format, key)


def apply_format_widgets(format_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    video_format = _load_format_json(format_name)
    for widget in _iterate_format(video_format):
        name = widget[0]
        if name in kwargs:
            continue
        if len(widget) > 2 and isinstance(widget[2], dict) and "default" in widget[2]:
            kwargs[name] = widget[2]["default"]
        elif isinstance(widget[1], list):
            kwargs[name] = widget[1][0]
        else:
            kwargs[name] = {"BOOLEAN": False, "INT": 0, "FLOAT": 0.0, "STRING": ""}.get(
                widget[1], ""
            )

    iterator = _iterate_format(video_format, for_widgets=False)
    for widget in iterator:
        while isinstance(widget, list):
            if len(widget) == 1:
                widget = [Template(item).substitute(**kwargs) for item in widget[0]]
                break
            if isinstance(widget[1], dict):
                widget = widget[1][str(kwargs[widget[0]])]
            elif len(widget) > 3:
                widget = Template(widget[3]).substitute(val=kwargs[widget[0]])
            else:
                widget = str(kwargs[widget[0]])
        iterator.send(widget)
    return video_format


def merge_filter_args(args: list[str], ftype: str = "-vf") -> None:
    try:
        start_index = args.index(ftype) + 1
        index = start_index
        while True:
            index = args.index(ftype, index)
            args[start_index] += "," + args[index + 1]
            args.pop(index)
            args.pop(index)
    except ValueError:
        pass


def tensor_to_int(tensor: torch.Tensor, bits: int) -> np.ndarray:
    array = tensor.detach().cpu().numpy() * ((2**bits) - 1) + 0.5
    return np.clip(array, 0, (2**bits) - 1)


def tensor_to_bytes(tensor: torch.Tensor) -> np.ndarray:
    return tensor_to_int(tensor, 8).astype(np.uint8)


def tensor_to_shorts(tensor: torch.Tensor) -> np.ndarray:
    return tensor_to_int(tensor, 16).astype(np.uint16)


def to_pingpong(frames):
    if not hasattr(frames, "__getitem__"):
        frames = list(frames)
    yield from frames
    for index in range(len(frames) - 2, 0, -1):
        yield frames[index]


def _escape_ffmpeg_metadata(key: str, value: Any) -> str:
    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace("#", "\\#")
    text = text.replace("=", "\\=")
    text = text.replace("\n", "\\\n")
    return f"{key}={text}"


def ffmpeg_process(args, video_format, video_metadata, file_path, env):
    res = None
    frame_data = yield
    total_frames_output = 0
    save_metadata = video_format.get("save_metadata", False)
    if str(save_metadata).lower() not in ("false", "0", "none"):
        os.makedirs(folder_paths.get_temp_directory(), exist_ok=True)
        metadata_path = os.path.join(folder_paths.get_temp_directory(), "smart_save_video_metadata.txt")
        with open(metadata_path, "w", encoding="utf-8") as handle:
            handle.write(";FFMETADATA1\n")
            if "prompt" in video_metadata:
                handle.write(
                    _escape_ffmpeg_metadata("prompt", json.dumps(video_metadata["prompt"])) + "\n"
                )
            if "workflow" in video_metadata:
                handle.write(
                    _escape_ffmpeg_metadata("workflow", json.dumps(video_metadata["workflow"]))
                    + "\n"
                )
            for key, value in video_metadata.items():
                if key not in ("prompt", "workflow"):
                    handle.write(_escape_ffmpeg_metadata(key, json.dumps(value)) + "\n")
        meta_args = (
            args[:1]
            + ["-i", metadata_path]
            + args[1:]
            + ["-metadata", "creation_time=now", "-movflags", "use_metadata_tags"]
        )
        cmd = [str(part) for part in (meta_args + [file_path])]
        try:
            proc_cm = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "SmartSaveVideo: failed to launch ffmpeg at "
                f"{cmd[0]!r}. Reinstall imageio-ffmpeg or add ffmpeg to PATH."
            ) from exc
        with proc_cm as proc:
            try:
                while frame_data is not None:
                    proc.stdin.write(frame_data)
                    frame_data = yield
                    total_frames_output += 1
                proc.stdin.flush()
                proc.stdin.close()
                res = proc.stderr.read()
            except BrokenPipeError:
                err = proc.stderr.read()
                if os.path.exists(file_path):
                    raise RuntimeError(
                        "SmartSaveVideo: ffmpeg metadata encode failed:\n"
                        + err.decode(*_ENCODE_ARGS)
                    ) from None
                print(err.decode(*_ENCODE_ARGS), end="", file=sys.stderr)
                logger.warning("SmartSaveVideo: metadata encode failed; retrying without metadata.")

    if not os.path.exists(file_path):
        total_frames_output = 0
        cmd = [str(part) for part in (args + [file_path])]
        try:
            proc_cm = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "SmartSaveVideo: failed to launch ffmpeg at "
                f"{cmd[0]!r}. Reinstall imageio-ffmpeg or add ffmpeg to PATH."
            ) from exc
        with proc_cm as proc:
            try:
                while frame_data is not None:
                    proc.stdin.write(frame_data)
                    frame_data = yield
                    total_frames_output += 1
                proc.stdin.flush()
                proc.stdin.close()
                res = proc.stderr.read()
            except BrokenPipeError:
                res = proc.stderr.read()
                raise RuntimeError(
                    "SmartSaveVideo: ffmpeg encode failed:\n" + res.decode(*_ENCODE_ARGS)
                ) from None
    yield total_frames_output
    if res:
        print(res.decode(*_ENCODE_ARGS), end="", file=sys.stderr)


def _is_within_base(path: str, base: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(base)]) == os.path.abspath(
            base
        )
    except Exception:
        return False


def encode_video(
    images: torch.Tensor,
    frame_rate: float,
    loop_count: int,
    filename_prefix: str,
    format_name: str,
    pingpong: bool,
    output_dir: str,
    audio: dict[str, Any] | None,
    inc_audio: bool,
    prompt=None,
    extra_pnginfo=None,
    **format_kwargs,
) -> tuple[str, str]:
    """Encode frames to one video file. Returns (absolute_path, filename)."""
    if images is None or (isinstance(images, torch.Tensor) and images.size(0) == 0):
        raise ValueError("SmartSaveVideo: no frames to encode.")

    ffmpeg_path = _resolve_ffmpeg()
    num_frames = int(images.shape[0])
    first_image = images[0]
    has_alpha = first_image.shape[-1] == 4
    format_kwargs = dict(format_kwargs)
    format_kwargs["has_alpha"] = has_alpha
    video_format = apply_format_widgets(format_name, format_kwargs)

    dim_alignment = int(video_format.get("dim_alignment", 2))
    height = int(first_image.shape[0])
    width = int(first_image.shape[1])
    frame_iter = iter(images)
    if (width % dim_alignment) or (height % dim_alignment):
        to_pad = (-width % dim_alignment, -height % dim_alignment)
        padding = (
            to_pad[0] // 2,
            to_pad[0] - to_pad[0] // 2,
            to_pad[1] // 2,
            to_pad[1] - to_pad[1] // 2,
        )
        padfunc = torch.nn.ReplicationPad2d(padding)

        def pad(image):
            image = image.permute((2, 0, 1))
            padded = padfunc(image.to(dtype=torch.float32))
            return padded.permute((1, 2, 0))

        frame_iter = map(pad, frame_iter)
        width = width + (-width % dim_alignment)
        height = height + (-height % dim_alignment)
        logger.warning("SmartSaveVideo: padded frames to a codec-aligned resolution.")

    if pingpong:
        materialised = list(frame_iter)
        frame_iter = to_pingpong(materialised)
        if num_frames > 2:
            num_frames += num_frames - 2

    loop_args = (
        ["-vf", f"loop=loop={int(loop_count)}:size={num_frames}"] if int(loop_count) > 0 else []
    )
    input_depth = video_format.get("input_color_depth", "8bit")
    if isinstance(input_depth, list):
        input_depth = format_kwargs.get("input_color_depth", input_depth[0])
    if input_depth == "16bit":
        frame_iter = map(tensor_to_shorts, frame_iter)
        i_pix_fmt = "rgba64" if has_alpha else "rgb48"
    else:
        frame_iter = map(tensor_to_bytes, frame_iter)
        i_pix_fmt = "rgba" if has_alpha else "rgb24"

    os.makedirs(output_dir, exist_ok=True)
    counter = 1
    matcher_prefix = os.path.basename(filename_prefix.rstrip("/\\")) or "SmartVideo"
    while True:
        file = f"{matcher_prefix}_{counter:05}.{video_format['extension']}"
        file_path = os.path.join(output_dir, file)
        if not os.path.exists(file_path):
            break
        counter += 1

    bitrate_arg: list[str] = []
    bitrate = video_format.get("bitrate")
    if bitrate is not None:
        unit = "M" if str(video_format.get("megabit", False)) in ("True", "true", "1") else "K"
        bitrate_arg = ["-b:v", f"{bitrate}{unit}"]

    args = [
        ffmpeg_path,
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        i_pix_fmt,
        "-color_range",
        "pc",
        "-colorspace",
        "rgb",
        "-color_primaries",
        "bt709",
        "-color_trc",
        video_format.get("fake_trc", "iec61966-2-1"),
        "-s",
        f"{width}x{height}",
        "-r",
        str(frame_rate),
        "-i",
        "-",
    ] + loop_args

    env = os.environ.copy()
    if "environment" in video_format:
        env.update(video_format["environment"])

    args += video_format["main_pass"] + bitrate_arg
    merge_filter_args(args)

    video_metadata: dict[str, Any] = {}
    if not getattr(comfy_args, "disable_metadata", False):
        if prompt is not None:
            video_metadata["prompt"] = prompt
        if extra_pnginfo is not None:
            for key, value in extra_pnginfo.items():
                video_metadata[key] = value

    output_process = ffmpeg_process(args, video_format, video_metadata, file_path, env)
    output_process.send(None)
    pbar = ProgressBar(num_frames)
    for frame in frame_iter:
        pbar.update(1)
        output_process.send(frame.tobytes())
    try:
        total_frames_output = output_process.send(None)
        output_process.send(None)
    except StopIteration:
        total_frames_output = num_frames

    final_path = file_path
    a_waveform = None
    if inc_audio and audio is not None:
        try:
            a_waveform = audio["waveform"]
        except Exception:
            a_waveform = None

    if a_waveform is not None:
        muxed_name = f"{matcher_prefix}_{counter:05}-audio.{video_format['extension']}"
        muxed_path = os.path.join(output_dir, muxed_name)
        audio_pass = video_format.get("audio_pass") or ["-c:a", "libopus"]
        channels = int(audio["waveform"].size(1))
        min_audio_dur = float(total_frames_output) / float(frame_rate) + 1.0
        trim = str(video_format.get("trim_to_audio", False)).lower() not in ("false", "0", "none")
        apad = [] if trim else ["-af", f"apad=whole_dur={min_audio_dur}"]
        mux_args = [
            ffmpeg_path,
            "-v",
            "error",
            "-y",
            "-i",
            file_path,
            "-ar",
            str(audio["sample_rate"]),
            "-ac",
            str(channels),
            "-f",
            "f32le",
            "-i",
            "-",
            "-c:v",
            "copy",
        ] + audio_pass + apad + ["-shortest", muxed_path]
        merge_filter_args(mux_args, "-af")
        audio_data = (
            audio["waveform"].squeeze(0).transpose(0, 1).detach().cpu().numpy().astype(np.float32).tobytes()
        )
        try:
            result = subprocess.run(
                mux_args, input=audio_data, env=env, capture_output=True, check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "SmartSaveVideo: ffmpeg audio mux failed:\n" + exc.stderr.decode(*_ENCODE_ARGS)
            ) from exc
        if result.stderr:
            print(result.stderr.decode(*_ENCODE_ARGS), end="", file=sys.stderr)
        try:
            os.remove(file_path)
        except OSError:
            pass
        final_path = muxed_path
        file = muxed_name
    else:
        file = os.path.basename(final_path)

    return final_path, file


@PromptServer.instance.routes.post("/smart_tools/save_video/save")
async def save_video_handler(request):
    try:
        data = await request.json()
        filename = data.get("filename")
        subfolder = data.get("subfolder", "")
        file_type = data.get("type", "temp")
        filename_prefix = data.get("filename_prefix", "SmartVideo")
        use_timestamp = bool(data.get("use_timestamp", False))
        if not filename:
            return web.Response(status=400, text="Missing filename")

        src_dir = (
            folder_paths.get_temp_directory()
            if file_type == "temp"
            else folder_paths.get_output_directory()
        )
        src_path = (
            os.path.join(src_dir, subfolder, filename)
            if subfolder
            else os.path.join(src_dir, filename)
        )
        if not os.path.exists(src_path):
            return web.Response(status=404, text=f"File not found: {src_path}")

        ext = os.path.splitext(filename)[1] or ".mp4"
        out_base_dir = folder_paths.get_output_directory()
        out_dir, base_name, _ = resolve_output_dir(filename_prefix, out_base_dir)
        dst_path, new_filename = next_available_path(
            out_dir, base_name, use_timestamp=use_timestamp, ext=ext
        )
        shutil.copy2(src_path, dst_path)
        return web.Response(status=200, text=f"Saved to {dst_path}")
    except Exception as exc:
        return web.Response(status=500, text=str(exc))


class SmartSaveVideo:
    def __init__(self):
        self.prefix_append = "_sv_" + "".join(random.choice(string.ascii_lowercase) for _ in range(5))
        self.last_prompt_id = None

    @classmethod
    def INPUT_TYPES(cls):
        formats = _list_formats()
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "Frame batch to encode as video."}),
                "autosave": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "label_on": "AutoSave ON",
                        "label_off": "AutoSave OFF",
                        "tooltip": "When ON, the selected video is written automatically.",
                    },
                ),
                "inc_audio": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "inc. Audio ON",
                        "label_off": "inc. Audio OFF",
                        "tooltip": "When ON and AUDIO is connected, mux audio into the single saved video.",
                    },
                ),
                "frame_rate": (
                    "FLOAT",
                    {"default": 24.0, "min": 1.0, "max": 120.0, "step": 0.01},
                ),
                "loop_count": ("INT", {"default": 0, "min": 0, "max": 100, "step": 1}),
                "filename_prefix": (
                    "STRING",
                    {
                        "default": "SmartVideo",
                        "tooltip": "Prefix or absolute/relative output folder path.",
                    },
                ),
                "format": (
                    formats,
                    {
                        "default": formats[0],
                        "format_widgets": _format_widget_map(),
                        "tooltip": "Bundled FFmpeg preset. Format-specific options appear below.",
                    },
                ),
                "pingpong": ("BOOLEAN", {"default": False}),
                "crf": ("INT", {"default": 19, "min": 0, "max": 100, "step": 1}),
                "pix_fmt": (
                    ["yuv420p", "yuv420p10le", "yuva420p", "yuv444p10le", "p010le", "rgba64le"],
                    {"default": "yuv420p"},
                ),
                "bitrate": ("INT", {"default": 10, "min": 1, "max": 999, "step": 1}),
                "megabit": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "Megabit ON",
                        "label_off": "Megabit OFF",
                        "tooltip": "NVENC only. ON = Mbps, OFF = Kbps for the bitrate value.",
                    },
                ),
                "save_metadata": ("BOOLEAN", {"default": True}),
                "trim_to_audio": ("BOOLEAN", {"default": False}),
                "profile": (
                    ["lt", "standard", "hq", "4444", "4444xq"],
                    {"default": "hq", "tooltip": "ProRes profile."},
                ),
                "use_timestamp": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "label_on": "Timestamp ON",
                        "label_off": "Timestamp OFF",
                    },
                ),
                "save_trigger": (
                    "INT",
                    {"default": 0, "min": 0, "max": 99999, "step": 1},
                ),
            },
            "optional": {
                "audio": ("AUDIO",),
                "original": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "Optional original frames. When connected, preview shows "
                            "New | Original side-by-side and synced."
                        ),
                    },
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return kwargs.get("save_trigger", 0)

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("video_path",)
    FUNCTION = "save_video"
    OUTPUT_NODE = True
    CATEGORY = "slikvik"
    DISPLAY_NAME = "Smart Save Video"
    DESCRIPTION = (
        "Encodes IMAGE frames to a single video with optional audio, using bundled "
        "FFmpeg presets and Smart Save-style autosave / manual save controls."
    )

    def save_video(
        self,
        images,
        autosave=False,
        inc_audio=True,
        frame_rate=24.0,
        loop_count=0,
        filename_prefix="SmartVideo",
        format="h264-mp4",
        pingpong=False,
        crf=19,
        pix_fmt="yuv420p",
        bitrate=10,
        megabit=True,
        save_metadata=True,
        trim_to_audio=False,
        profile="hq",
        use_timestamp=False,
        save_trigger=0,
        audio=None,
        original=None,
        prompt=None,
        extra_pnginfo=None,
    ):
        if images is None or (isinstance(images, torch.Tensor) and images.numel() == 0):
            return {"ui": {"gifs": []}, "result": ("",)}

        temp_dir = folder_paths.get_temp_directory()
        temp_prefix = "smart_save_video_preview" + self.prefix_append
        encode_dir = os.path.join(temp_dir, "SmartSaveVideo")
        os.makedirs(encode_dir, exist_ok=True)
        encode_kwargs = dict(
            frame_rate=float(frame_rate),
            loop_count=int(loop_count),
            format_name=format,
            pingpong=bool(pingpong),
            output_dir=encode_dir,
            prompt=prompt,
            extra_pnginfo=extra_pnginfo,
            crf=int(crf),
            pix_fmt=pix_fmt,
            bitrate=int(bitrate),
            megabit=bool(megabit),
            save_metadata=bool(save_metadata),
            trim_to_audio=bool(trim_to_audio),
            profile=profile,
            input_color_depth="8bit",
            gop_size=1,
        )
        encoded_path, encoded_name = encode_video(
            images=images,
            filename_prefix=temp_prefix,
            audio=audio,
            inc_audio=bool(inc_audio),
            **encode_kwargs,
        )

        extension = os.path.splitext(encoded_name)[1].lstrip(".").lower() or "mp4"
        mime = {
            "mp4": "video/mp4",
            "webm": "video/webm",
            "mov": "video/quicktime",
            "mkv": "video/x-matroska",
        }.get(extension, f"video/{extension}")
        video_ui = {
            "filename": encoded_name,
            "subfolder": "SmartSaveVideo",
            "type": "temp",
            "format": mime,
            "frame_rate": float(frame_rate),
            "fullpath": encoded_path,
        }
        final_path = encoded_path

        if autosave:
            current_prompt_id = id(prompt) if prompt is not None else None
            if current_prompt_id != self.last_prompt_id:
                self.last_prompt_id = current_prompt_id
                out_base_dir = folder_paths.get_output_directory()
                out_dir, base_name, out_subfolder = resolve_output_dir(
                    filename_prefix, out_base_dir
                )
                ext = os.path.splitext(encoded_name)[1] or ".mp4"
                dst_path, new_filename = next_available_path(
                    out_dir, base_name, use_timestamp=bool(use_timestamp), ext=ext
                )
                shutil.copy2(encoded_path, dst_path)
                final_path = dst_path
                if _is_within_base(out_dir, out_base_dir):
                    # Preview the saved output copy when it is viewable via /view.
                    video_ui = {
                        "filename": new_filename,
                        "subfolder": out_subfolder,
                        "type": "output",
                        "format": mime,
                        "frame_rate": float(frame_rate),
                        "fullpath": dst_path,
                    }
                else:
                    # Keep temp preview entry for UI loading when saving outside output/.
                    video_ui["fullpath"] = dst_path

        gifs = [video_ui]
        has_original = original is not None and not (
            isinstance(original, torch.Tensor) and original.numel() == 0
        )
        if has_original:
            orig_prefix = "smart_save_video_original" + self.prefix_append
            orig_path, orig_name = encode_video(
                images=original,
                filename_prefix=orig_prefix,
                audio=None,
                inc_audio=False,
                **encode_kwargs,
            )
            orig_ext = os.path.splitext(orig_name)[1].lstrip(".").lower() or "mp4"
            orig_mime = {
                "mp4": "video/mp4",
                "webm": "video/webm",
                "mov": "video/quicktime",
                "mkv": "video/x-matroska",
            }.get(orig_ext, f"video/{orig_ext}")
            # Always temp: compare preview is never rewritten by autosave.
            gifs.append(
                {
                    "filename": orig_name,
                    "subfolder": "SmartSaveVideo",
                    "type": "temp",
                    "format": orig_mime,
                    "frame_rate": float(frame_rate),
                    "fullpath": orig_path,
                }
            )

        # Use `gifs` for Comfy/VHS-compatible animated preview payload naming.
        # gifs[0] is always the saveable/new video; gifs[1] is original compare-only.
        return {
            "ui": {"gifs": gifs},
            "result": (final_path,),
        }


NODE_CLASS_MAPPINGS = {"SmartSaveVideo": SmartSaveVideo}
NODE_DISPLAY_NAME_MAPPINGS = {"SmartSaveVideo": "Smart Save Video"}
