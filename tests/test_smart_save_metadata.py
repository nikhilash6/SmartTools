from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_smarttools_save_metadata_tests"
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
        sys.modules["comfy"] = comfy
        sys.modules["comfy.cli_args"] = cli_args
        comfy.cli_args = cli_args


def _load():
    _ensure_comfy_stubs()
    full_name = f"{PACKAGE}.SmartSave"
    if full_name in sys.modules:
        del sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, ROOT / "SmartSave.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ss = _load()

SAMPLE_PROMPT = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 4242, "steps": 20},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a red bicycle"},
    },
}
SAMPLE_EXTRA = {"workflow": {"nodes": [{"id": 1}], "version": 0.4}}


class MetadataHelperTests(unittest.TestCase):
    def test_extract_prompt_and_seed(self):
        text, seed = ss.extract_prompt_and_seed(SAMPLE_PROMPT)
        self.assertEqual(text, "a red bicycle")
        self.assertEqual(seed, 4242)

    def test_format_list_includes_avif(self):
        formats = ss.SmartSave.INPUT_TYPES()["required"]["format"][0]
        self.assertEqual(formats, ["PNG", "JPEG", "WEBP", "AVIF"])
        self.assertEqual(ss.get_pil_format_and_ext("AVIF"), ("AVIF", ".avif"))


class SaveMetadataTests(unittest.TestCase):
    def test_webp_embeds_prompt_and_workflow_exif(self):
        img = Image.new("RGB", (8, 8), (10, 20, 30))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.webp"
            ss.save_pil_image(
                img,
                str(path),
                "WEBP",
                90,
                prompt=SAMPLE_PROMPT,
                extra_pnginfo=SAMPLE_EXTRA,
            )
            raw = path.read_bytes()
            self.assertIn(b"prompt:", raw)
            self.assertIn(b"workflow:", raw)
            self.assertIn(b"a red bicycle", raw)

    def test_jpeg_embeds_prompt_and_seed_exif(self):
        img = Image.new("RGB", (8, 8), (10, 20, 30))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.jpg"
            ss.save_pil_image(
                img,
                str(path),
                "JPEG",
                90,
                prompt=SAMPLE_PROMPT,
                extra_pnginfo=SAMPLE_EXTRA,
            )
            saved = Image.open(path)
            try:
                exif = saved.getexif()
                self.assertEqual(exif.get(0x010E), "a red bicycle")
                self.assertEqual(exif.get(0x013B), "Seed: 4242")
                comment = exif.get(0x9286) or b""
                if isinstance(comment, bytes):
                    comment = comment.decode("utf-8", "replace")
                self.assertIn("Seed: 4242", comment)
                self.assertIn("Prompt: a red bicycle", comment)
            finally:
                saved.close()


if __name__ == "__main__":
    unittest.main()
