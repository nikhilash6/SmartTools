from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import torch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_smarttools_save_video_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)


def _ensure_comfy_stubs():
    if "folder_paths" not in sys.modules:
        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_temp_directory = lambda: tempfile.gettempdir()
        folder_paths.get_output_directory = lambda: tempfile.gettempdir()
        folder_paths.get_save_image_path = lambda prefix, output_dir, width, height: (
            output_dir,
            prefix,
            1,
            "",
            prefix,
        )
        sys.modules["folder_paths"] = folder_paths

    if "server" not in sys.modules:
        server = types.ModuleType("server")

        class _Routes:
            def post(self, _path):
                return lambda fn: fn

            def get(self, _path):
                return lambda fn: fn

        class _PromptServer:
            instance = types.SimpleNamespace(routes=_Routes())

        server.PromptServer = _PromptServer
        sys.modules["server"] = server

    if "aiohttp" not in sys.modules:
        aiohttp = types.ModuleType("aiohttp")
        aiohttp.web = types.SimpleNamespace(
            Response=lambda **kwargs: kwargs,
            json_response=lambda data: data,
        )
        sys.modules["aiohttp"] = aiohttp

    if "comfy.cli_args" not in sys.modules:
        comfy = types.ModuleType("comfy")
        cli_args = types.ModuleType("comfy.cli_args")
        cli_args.args = types.SimpleNamespace(disable_metadata=False)
        utils = types.ModuleType("comfy.utils")

        class ProgressBar:
            def __init__(self, total):
                self.total = total

            def update(self, n=1):
                pass

        utils.ProgressBar = ProgressBar
        sys.modules["comfy"] = comfy
        sys.modules["comfy.cli_args"] = cli_args
        sys.modules["comfy.utils"] = utils
        comfy.cli_args = cli_args
        comfy.utils = utils


def _load(name: str):
    _ensure_comfy_stubs()
    full_name = f"{PACKAGE}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_load("SmartSave")
ssv = _load("SmartSaveVideo")


class FormatTests(unittest.TestCase):
    def test_bundled_formats_are_discovered(self):
        names = ssv._list_formats()
        self.assertIn("h264-mp4", names)
        self.assertIn("webm", names)
        self.assertIn("ProRes", names)

    def test_apply_format_widgets_resolves_h264(self):
        fmt = ssv.apply_format_widgets(
            "h264-mp4",
            {
                "crf": 18,
                "pix_fmt": "yuv420p",
                "save_metadata": True,
                "trim_to_audio": False,
            },
        )
        self.assertEqual(fmt["extension"], "mp4")
        self.assertIn("libx264", fmt["main_pass"])
        self.assertEqual(str(fmt["save_metadata"]).lower(), "true")

    def test_format_widget_map_hides_megabit_for_h264(self):
        widget_map = ssv._format_widget_map()
        self.assertNotIn("megabit", widget_map["h264-mp4"])
        self.assertNotIn("bitrate", widget_map["h264-mp4"])
        self.assertIn("crf", widget_map["h264-mp4"])
        self.assertIn("megabit", widget_map["nvenc_h264-mp4"])
        self.assertIn("bitrate", widget_map["nvenc_h264-mp4"])
        self.assertIn("profile", widget_map["ProRes"])
        required = ssv.SmartSaveVideo.INPUT_TYPES()["required"]
        self.assertEqual(
            required["format"][1]["format_widgets"]["h264-mp4"],
            widget_map["h264-mp4"],
        )


class EncodeBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.images = torch.zeros(2, 8, 8, 3)

    def test_missing_ffmpeg_raises_clear_error(self):
        previous = ssv.smart_ffmpeg._FFMPEG_PATH
        try:
            ssv.smart_ffmpeg._FFMPEG_PATH = None
            with self.assertRaisesRegex(RuntimeError, "ffmpeg is required"):
                ssv._resolve_ffmpeg()
        finally:
            ssv.smart_ffmpeg._FFMPEG_PATH = previous

    def test_ffmpeg_unset_sentinel_is_not_used_as_path(self):
        previous = ssv.smart_ffmpeg._FFMPEG_PATH
        try:
            ssv.smart_ffmpeg._FFMPEG_PATH = ssv.smart_ffmpeg.FFMPEG_UNSET
            resolved = ssv._resolve_ffmpeg()
            self.assertTrue(os.path.isfile(resolved))
            self.assertNotIn("object object", resolved)
        finally:
            ssv.smart_ffmpeg._FFMPEG_PATH = previous

    def test_silent_encode_keeps_single_file(self):
        def fake_process(args, video_format, video_metadata, file_path, env):
            frame_data = yield
            count = 0
            while frame_data is not None:
                count += 1
                frame_data = yield
            Path(file_path).write_bytes(b"video")
            yield count

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(ssv, "_resolve_ffmpeg", return_value="ffmpeg"),
                mock.patch.object(ssv, "ffmpeg_process", side_effect=fake_process),
            ):
                path, name = ssv.encode_video(
                    images=self.images,
                    frame_rate=8.0,
                    loop_count=0,
                    filename_prefix="clip",
                    format_name="h264-mp4",
                    pingpong=False,
                    output_dir=tmp,
                    audio=None,
                    inc_audio=True,
                    crf=19,
                    pix_fmt="yuv420p",
                    save_metadata=False,
                )
            self.assertTrue(Path(path).is_file())
            self.assertFalse(name.endswith("-audio.mp4"))
            self.assertEqual(len(list(Path(tmp).glob("*"))), 1)

    def test_audio_mux_removes_silent_intermediate(self):
        def fake_process(args, video_format, video_metadata, file_path, env):
            frame_data = yield
            while frame_data is not None:
                frame_data = yield
            Path(file_path).write_bytes(b"silent")
            yield 2

        def fake_run(mux_args, input=None, env=None, capture_output=True, check=True):
            Path(mux_args[-1]).write_bytes(b"muxed")
            return types.SimpleNamespace(stderr=b"")

        audio = {"waveform": torch.zeros(1, 1, 16), "sample_rate": 16000}
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(ssv, "_resolve_ffmpeg", return_value="ffmpeg"),
                mock.patch.object(ssv, "ffmpeg_process", side_effect=fake_process),
                mock.patch.object(ssv.subprocess, "run", side_effect=fake_run),
            ):
                path, name = ssv.encode_video(
                    images=self.images,
                    frame_rate=8.0,
                    loop_count=0,
                    filename_prefix="clip",
                    format_name="h264-mp4",
                    pingpong=False,
                    output_dir=tmp,
                    audio=audio,
                    inc_audio=True,
                    crf=19,
                    pix_fmt="yuv420p",
                    save_metadata=False,
                    trim_to_audio=False,
                )
            files = list(Path(tmp).iterdir())
            self.assertEqual(len(files), 1)
            self.assertTrue(name.endswith("-audio.mp4"))
            self.assertEqual(Path(path).read_bytes(), b"muxed")

    def test_inc_audio_off_ignores_connected_audio(self):
        def fake_process(args, video_format, video_metadata, file_path, env):
            frame_data = yield
            while frame_data is not None:
                frame_data = yield
            Path(file_path).write_bytes(b"silent")
            yield 2

        audio = {"waveform": torch.zeros(1, 1, 8), "sample_rate": 16000}
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(ssv, "_resolve_ffmpeg", return_value="ffmpeg"),
                mock.patch.object(ssv, "ffmpeg_process", side_effect=fake_process),
                mock.patch.object(ssv.subprocess, "run") as run_mock,
            ):
                path, name = ssv.encode_video(
                    images=self.images,
                    frame_rate=8.0,
                    loop_count=0,
                    filename_prefix="clip",
                    format_name="h264-mp4",
                    pingpong=False,
                    output_dir=tmp,
                    audio=audio,
                    inc_audio=False,
                    crf=19,
                    pix_fmt="yuv420p",
                    save_metadata=False,
                )
            run_mock.assert_not_called()
            self.assertFalse(name.endswith("-audio.mp4"))
            self.assertEqual(Path(path).read_bytes(), b"silent")


class NodeTests(unittest.TestCase):
    def test_node_registration_and_defaults(self):
        self.assertIs(ssv.NODE_CLASS_MAPPINGS["SmartSaveVideo"], ssv.SmartSaveVideo)
        required = ssv.SmartSaveVideo.INPUT_TYPES()["required"]
        self.assertFalse(required["autosave"][1]["default"])
        self.assertTrue(required["inc_audio"][1]["default"])
        self.assertIn("h264-mp4", required["format"][0])
        optional = ssv.SmartSaveVideo.INPUT_TYPES()["optional"]
        self.assertIn("original", optional)
        self.assertEqual(optional["original"][0], "IMAGE")

    def test_ui_gifs_single_without_original(self):
        node = ssv.SmartSaveVideo()
        frames = torch.zeros((2, 8, 8, 3), dtype=torch.float32)

        def fake_encode(**kwargs):
            name = "preview_new.mp4"
            path = os.path.join(kwargs["output_dir"], name)
            Path(path).write_bytes(b"new")
            return path, name

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(ssv.folder_paths, "get_temp_directory", return_value=tmp),
            mock.patch.object(ssv, "encode_video", side_effect=fake_encode) as enc,
        ):
            out = node.save_video(images=frames, format="h264-mp4", autosave=False)
            self.assertEqual(enc.call_count, 1)
            self.assertEqual(len(out["ui"]["ssv_gifs"]), 1)
            self.assertEqual(out["ui"]["ssv_gifs"][0]["filename"], "preview_new.mp4")
            self.assertEqual(out["ui"]["ssv_gifs"][0]["subfolder"], "SmartSaveVideo")
            self.assertEqual(out["ui"]["ssv_gifs"][0]["type"], "temp")
            self.assertTrue(out["result"][0].endswith("preview_new.mp4"))

    def test_ui_gifs_dual_with_original(self):
        node = ssv.SmartSaveVideo()
        frames = torch.zeros((2, 8, 8, 3), dtype=torch.float32)
        original = torch.ones((2, 8, 8, 3), dtype=torch.float32)
        calls = []

        def fake_encode(**kwargs):
            calls.append(kwargs)
            is_orig = "original" in kwargs["filename_prefix"]
            name = "orig.mp4" if is_orig else "new.mp4"
            path = os.path.join(kwargs["output_dir"], name)
            Path(path).write_bytes(b"orig" if is_orig else b"new")
            return path, name

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(ssv.folder_paths, "get_temp_directory", return_value=tmp),
            mock.patch.object(ssv, "encode_video", side_effect=fake_encode) as enc,
        ):
            out = node.save_video(
                images=frames,
                original=original,
                format="h264-mp4",
                autosave=False,
                audio={"waveform": torch.zeros(1, 1, 8), "sample_rate": 44100},
                inc_audio=True,
            )
            self.assertEqual(enc.call_count, 2)
            self.assertEqual(len(out["ui"]["ssv_gifs"]), 2)
            self.assertEqual(out["ui"]["ssv_gifs"][0]["filename"], "new.mp4")
            self.assertEqual(out["ui"]["ssv_gifs"][1]["filename"], "orig.mp4")
            self.assertEqual(out["ui"]["ssv_gifs"][1]["type"], "temp")
            self.assertTrue(out["result"][0].endswith("new.mp4"))
            # Primary may mux audio; original compare encode is always silent.
            self.assertTrue(calls[0].get("inc_audio"))
            self.assertIsNotNone(calls[0].get("audio"))
            self.assertFalse(calls[1].get("inc_audio"))
            self.assertIsNone(calls[1].get("audio"))

    def test_manual_copy_preserves_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "preview.mp4"
            src.write_bytes(b"abc")
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()

            async def run():
                request = types.SimpleNamespace(
                    json=mock.AsyncMock(
                        return_value={
                            "filename": "preview.mp4",
                            "subfolder": "",
                            "type": "temp",
                            "filename_prefix": "Final",
                            "use_timestamp": False,
                        }
                    )
                )
                with (
                    mock.patch.object(
                        ssv.folder_paths, "get_temp_directory", return_value=tmp
                    ),
                    mock.patch.object(
                        ssv.folder_paths,
                        "get_output_directory",
                        return_value=str(out_dir),
                    ),
                ):
                    return await ssv.save_video_handler(request)

            response = asyncio.run(run())
            self.assertEqual(response.get("status"), 200)
            saved = list(out_dir.glob("Final_*.mp4"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_bytes(), b"abc")

    def test_counter_and_timestamp_naming(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1, n1 = ssv.next_available_path(tmp, "vid", use_timestamp=False, ext=".mp4")
            Path(p1).write_bytes(b"1")
            _p2, n2 = ssv.next_available_path(tmp, "vid", use_timestamp=False, ext=".mp4")
            self.assertNotEqual(n1, n2)
            self.assertTrue(n1.endswith(".mp4"))
            _ts_path, ts_name = ssv.next_available_path(
                tmp, "vid", use_timestamp=True, ext=".webm"
            )
            self.assertTrue(ts_name.endswith(".webm"))


if __name__ == "__main__":
    unittest.main()
