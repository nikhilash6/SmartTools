from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import torch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_smarttools_load_video_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)


def _ensure_comfy_stubs():
    if "folder_paths" not in sys.modules:
        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_input_directory = lambda: tempfile.gettempdir()
        folder_paths.get_annotated_filepath = lambda name: (
            name if os.path.isabs(str(name)) else os.path.join(tempfile.gettempdir(), str(name))
        )
        folder_paths.exists_annotated_filepath = lambda name: os.path.isfile(
            folder_paths.get_annotated_filepath(name)
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
            FileResponse=lambda path: {"file": path},
        )
        sys.modules["aiohttp"] = aiohttp

    if "comfy.utils" not in sys.modules:
        comfy = sys.modules.get("comfy") or types.ModuleType("comfy")
        utils = types.ModuleType("comfy.utils")

        class ProgressBar:
            def __init__(self, total):
                self.total = total

            def update(self, n=1):
                pass

        utils.ProgressBar = ProgressBar
        sys.modules["comfy"] = comfy
        sys.modules["comfy.utils"] = utils
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


slv = _load("SmartLoadVideo")


class HelperTests(unittest.TestCase):
    def test_snap_up_1080_to_1088(self):
        self.assertEqual(slv.snap_up(1080, 32), 1088)
        self.assertEqual(slv.snap_up(1920, 32), 1920)
        self.assertEqual(slv.snap_up(1080, 1), 1080)

    def test_target_size_native_then_multiple(self):
        self.assertEqual(slv.target_size(1920, 1080, 0, 0, 1), (1920, 1080))
        self.assertEqual(slv.target_size(1920, 1080, 0, 0, 32), (1920, 1088))

    def test_target_size_one_dim_preserves_aspect(self):
        width, height = slv.target_size(1920, 1080, 1280, 0, 1)
        self.assertEqual(width, 1280)
        self.assertEqual(height, 720)

    def test_target_size_both_dims(self):
        self.assertEqual(slv.target_size(1920, 1080, 512, 512, 1), (512, 512))
        self.assertEqual(slv.target_size(1920, 1080, 510, 510, 32), (512, 512))

    def test_effective_start_contiguous(self):
        self.assertAlmostEqual(slv.effective_start_seconds(1.5, 0, 430, 30.0), 1.5)
        self.assertAlmostEqual(slv.effective_start_seconds(1.5, 1, 430, 30.0), 1.5 + 430 / 30.0)
        self.assertAlmostEqual(slv.effective_start_seconds(1.5, 2, 430, 30.0), 1.5 + 860 / 30.0)

    def test_slice_requires_cap(self):
        with self.assertRaisesRegex(ValueError, "frame_load_cap"):
            slv.effective_start_seconds(0.0, 1, 0, 24.0)

    def test_loaded_framerate(self):
        self.assertEqual(slv.loaded_framerate(0, 29.97), 29.97)
        self.assertEqual(slv.loaded_framerate(24, 29.97), 24.0)
        with self.assertRaisesRegex(ValueError, "framerate"):
            slv.loaded_framerate(0, 0)


class NodeContractTests(unittest.TestCase):
    def test_registration_and_widgets(self):
        self.assertIs(slv.NODE_CLASS_MAPPINGS["SmartLoadVideo"], slv.SmartLoadVideo)
        self.assertEqual(slv.NODE_DISPLAY_NAME_MAPPINGS["SmartLoadVideo"], "Smart Load Video")
        required = slv.SmartLoadVideo.INPUT_TYPES()["required"]
        for name in (
            "video",
            "video_path",
            "force_rate",
            "custom_width",
            "custom_height",
            "multiple",
            "frame_load_cap",
            "start_time",
            "slice_index",
        ):
            self.assertIn(name, required)
        self.assertEqual(required["video_path"][1]["default"], "")
        self.assertEqual(required["multiple"][1]["default"], 1)
        self.assertEqual(required["slice_index"][1]["default"], 0)
        self.assertNotIn("format", required)
        self.assertNotIn("meta_batch", required)
        self.assertEqual(slv.SmartLoadVideo.RETURN_TYPES, ("IMAGE", "MASK", "AUDIO", "FLOAT"))
        self.assertEqual(slv.SmartLoadVideo.RETURN_NAMES, ("IMAGE", "mask", "audio", "framerate"))
        self.assertNotIn("video_info", slv.SmartLoadVideo.RETURN_NAMES)
        self.assertIn("SmartLoadVideo", (ROOT / "__init__.py").read_text(encoding="utf-8"))


class DecodeTests(unittest.TestCase):
    def test_load_video_missing_file(self):
        node = slv.SmartLoadVideo()
        with self.assertRaises(FileNotFoundError):
            node.load_video(video="__missing_smart_load_video__.mp4")

    def test_ffmpeg_roundtrip_slice_and_snap(self):
        try:
            ffmpeg = slv._resolve_ffmpeg()
        except RuntimeError:
            self.skipTest("ffmpeg is not available")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clip.mp4")
            cmd = [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=64x48:d=1",
                "-r",
                "10",
                "-frames:v",
                "10",
                "-pix_fmt",
                "yuv420p",
                path,
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                self.skipTest(f"could not generate test clip: {exc}")

            node = slv.SmartLoadVideo()
            with mock.patch.object(slv, "resolve_source", return_value=path):
                images, mask, audio, fps = node.load_video(
                    video="clip.mp4",
                    force_rate=10,
                    custom_width=0,
                    custom_height=0,
                    multiple=32,
                    frame_load_cap=4,
                    start_time=0.0,
                    slice_index=1,
                )

            self.assertEqual(tuple(images.shape[1:]), (64, 64, 3))
            self.assertEqual(images.shape[0], 4)
            self.assertEqual(tuple(mask.shape), (4, 64, 64))
            self.assertTrue(torch.all(mask == 1))
            self.assertAlmostEqual(float(fps), 10.0)
            self.assertIn("waveform", audio)
            self.assertIn("sample_rate", audio)

            with mock.patch.object(slv, "resolve_source", return_value=path):
                images0, _, _, _ = node.load_video(
                    video="clip.mp4",
                    frame_load_cap=4,
                    slice_index=0,
                    multiple=1,
                )
            self.assertEqual(images0.shape[0], 4)
            self.assertEqual(tuple(images0.shape[1:3]), (48, 64))


class PathResolveTests(unittest.TestCase):
    def test_path_wins_over_combo(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.mp4"
            combo = Path(tmp) / "combo.mp4"
            real.write_bytes(b"x")
            combo.write_bytes(b"y")
            with mock.patch.object(
                slv.folder_paths, "get_annotated_filepath", return_value=str(combo)
            ):
                self.assertEqual(
                    slv.resolve_source("combo.mp4", str(real)),
                    os.path.abspath(str(real)),
                )

    def test_tilde_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "home_clip.mp4"
            clip.write_bytes(b"x")
            with mock.patch.object(slv.os.path, "expanduser", return_value=str(clip)):
                self.assertEqual(
                    slv.resolve_source("", "~/home_clip.mp4"),
                    os.path.abspath(str(clip)),
                )

    def test_empty_combo_with_path_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "ok.mp4"
            clip.write_bytes(b"x")
            self.assertTrue(
                slv.SmartLoadVideo.VALIDATE_INPUTS("", video_path=str(clip))
            )

    def test_missing_path_errors(self):
        with self.assertRaisesRegex(FileNotFoundError, "invalid video path"):
            slv.resolve_source("", r"D:\missing_smart_load_video.mp4")
        message = slv.SmartLoadVideo.VALIDATE_INPUTS(
            "", video_path=r"D:\missing_smart_load_video.mp4"
        )
        self.assertIsInstance(message, str)
        self.assertIn("invalid video path", message)

    def test_empty_combo_and_path_errors(self):
        with self.assertRaisesRegex(FileNotFoundError, "no video selected"):
            slv.resolve_source("", "")


class BrowseViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_browse_cancel_returns_204(self):
        with mock.patch.object(slv, "_pick_video_file", return_value=""):
            resp = await slv.browse_file_handler(None)
        self.assertEqual(resp["status"], 204)

    async def test_browse_returns_path(self):
        with mock.patch.object(slv, "_pick_video_file", return_value=r"D:\clip.mp4"):
            resp = await slv.browse_file_handler(None)
        self.assertEqual(resp, {"path": r"D:\clip.mp4"})

    async def test_view_rejects_bad_path(self):
        request = types.SimpleNamespace(
            rel_url=types.SimpleNamespace(query={"path": r"D:\nope.txt"})
        )
        resp = await slv.view_video_handler(request)
        self.assertEqual(resp["status"], 404)

    async def test_view_serves_video_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "preview.mp4"
            clip.write_bytes(b"mp4")
            request = types.SimpleNamespace(
                rel_url=types.SimpleNamespace(query={"path": str(clip)})
            )
            resp = await slv.view_video_handler(request)
        self.assertEqual(resp["file"], os.path.realpath(str(clip)))


if __name__ == "__main__":
    unittest.main()
