from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import torch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_smarttools_resizer_v2_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)


def _ensure_comfy_stubs():
    if "folder_paths" not in sys.modules:
        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_temp_directory = lambda: tempfile.gettempdir()

        def get_save_image_path(prefix, output_dir, width, height):
            return output_dir, prefix, 1, "", prefix

        folder_paths.get_save_image_path = get_save_image_path
        sys.modules["folder_paths"] = folder_paths


def _load(name: str):
    _ensure_comfy_stubs()
    full_name = f"{PACKAGE}.{name}"
    if full_name in sys.modules:
        # Reload so edits are picked up during iterative runs.
        del sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


rsz = _load("SmartResizerV2")


def _rgb(h, w, value=0.25):
    return torch.full((1, h, w, 3), float(value), dtype=torch.float32)


def _run(node, image, **kwargs):
    with mock.patch.object(rsz.folder_paths, "get_temp_directory", return_value=tempfile.mkdtemp()):
        return node.process(image=image, **kwargs)


class RegistrationTests(unittest.TestCase):
    def test_registration(self):
        self.assertIs(rsz.NODE_CLASS_MAPPINGS["SmartResizerV2Node"], rsz.SmartResizerV2)
        self.assertEqual(rsz.NODE_DISPLAY_NAME_MAPPINGS["SmartResizerV2Node"], "Smart Resizer v2")
        required = rsz.SmartResizerV2.INPUT_TYPES()["required"]
        self.assertEqual(required["width"][1]["default"], 0)
        self.assertEqual(required["height"][1]["default"], 0)
        self.assertEqual(required["multiple"][1]["default"], 1)
        self.assertEqual(required["aspect_ratio"][1]["default"], rsz.ASPECT_INPUT)
        self.assertEqual(required["pad_colour"][1]["default"], "Black")
        self.assertEqual(
            required["pad_colour"][0],
            ["Black", "Grey", "Red", "Green", "White"],
        )


class SizingHelperTests(unittest.TestCase):
    def test_smart_aspect_picks_nearest(self):
        self.assertAlmostEqual(rsz.SmartResizerV2._smart_aspect(100, 100), 1.0)
        self.assertAlmostEqual(rsz.SmartResizerV2._smart_aspect(1920, 1080), 16 / 9)
        self.assertAlmostEqual(rsz.SmartResizerV2._smart_aspect(1080, 1920), 9 / 16)

    def test_expand_box_never_shrinks(self):
        tw, th = rsz.SmartResizerV2._expand_box_to_aspect(800, 800, 16 / 9)
        self.assertGreaterEqual(tw, 800)
        self.assertGreaterEqual(th, 800)
        self.assertAlmostEqual(tw / th, 16 / 9, places=2)

    def test_input_both_zero_keeps_source(self):
        tw, th, ar = rsz.SmartResizerV2._compute_target_size(
            640, 480, 0, 0, rsz.ASPECT_INPUT, True
        )
        self.assertEqual((tw, th, ar), (640, 480, None))

    def test_input_both_set_uses_proposed_canvas(self):
        tw, th, ar = rsz.SmartResizerV2._compute_target_size(
            640, 480, 1024, 768, rsz.ASPECT_INPUT, True
        )
        self.assertEqual((tw, th, ar), (1024, 768, None))

    def test_input_only_width_scales(self):
        tw, th, ar = rsz.SmartResizerV2._compute_target_size(
            640, 480, 320, 0, rsz.ASPECT_INPUT, True
        )
        self.assertEqual(tw, 320)
        self.assertEqual(th, 240)
        self.assertIsNone(ar)

    def test_smart_both_zero_pad_contain(self):
        # 640x480 (4:3) → nearest Smart is 1:1; pad contain → 640x640
        tw, th, ar = rsz.SmartResizerV2._compute_target_size(
            640, 480, 0, 0, rsz.ASPECT_SMART, True
        )
        self.assertEqual(ar, 1.0)
        self.assertEqual((tw, th), (640, 640))

    def test_fixed_both_dims_expands(self):
        tw, th, ar = rsz.SmartResizerV2._compute_target_size(
            100, 100, 800, 800, rsz.ASPECT_WIDE, True
        )
        self.assertAlmostEqual(ar, 16 / 9)
        self.assertAlmostEqual(tw / th, 16 / 9, places=2)
        self.assertGreaterEqual(tw, 800)
        self.assertGreaterEqual(th, 800)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.node = rsz.SmartResizerV2()

    def test_input_both_zero_multiple_only(self):
        img = _rgb(100, 100)
        out = _run(self.node, img, width=0, height=0, aspect_ratio=rsz.ASPECT_INPUT, multiple=16)
        image, w, h, mask = out["result"]
        self.assertEqual((w, h), (96, 96))  # nearest multiple of 16
        self.assertEqual(tuple(image.shape[1:3]), (96, 96))
        self.assertEqual(out["ui"]["input_width"], [100])
        self.assertEqual(out["ui"]["input_height"], [100])
        self.assertEqual(out["ui"]["output_width"], [96])
        self.assertEqual(out["ui"]["output_height"], [96])
        self.assertEqual(len(out["ui"]["rsz2_images"]), 1)

    def test_input_one_dim_scales(self):
        img = _rgb(200, 100)  # H=200 W=100 → portrait 1:2
        out = _run(
            self.node,
            img,
            width=50,
            height=0,
            aspect_ratio=rsz.ASPECT_INPUT,
            multiple=1,
        )
        _image, w, h, _mask = out["result"]
        self.assertEqual((w, h), (50, 100))

    def test_input_both_dims_pad_to_exact_canvas(self):
        img = _rgb(160, 119)
        out = _run(
            self.node,
            img,
            width=70,
            height=108,
            aspect_ratio=rsz.ASPECT_INPUT,
            pad_image=True,
            multiple=1,
        )
        image, w, h, _mask = out["result"]
        self.assertEqual((w, h), (70, 108))
        self.assertEqual(tuple(image.shape[1:3]), (108, 70))

    def test_input_both_dims_crop_to_exact_canvas(self):
        img = _rgb(160, 119)
        out = _run(
            self.node,
            img,
            width=70,
            height=108,
            aspect_ratio=rsz.ASPECT_INPUT,
            pad_image=False,
            multiple=1,
        )
        image, w, h, _mask = out["result"]
        self.assertEqual((w, h), (70, 108))
        self.assertEqual(tuple(image.shape[1:3]), (108, 70))

    def test_pad_colours_fill_letterbox_bars(self):
        img = _rgb(40, 40, value=0.25)
        expected = {
            "Black": (0.0, 0.0, 0.0),
            "Grey": (128 / 255, 128 / 255, 128 / 255),
            "Red": (1.0, 0.0, 0.0),
            "Green": (0.0, 1.0, 0.0),
            "White": (1.0, 1.0, 1.0),
        }
        for colour, rgb in expected.items():
            with self.subTest(colour=colour):
                out = _run(
                    self.node,
                    img,
                    width=80,
                    height=40,
                    aspect_ratio=rsz.ASPECT_INPUT,
                    pad_image=True,
                    pad_colour=colour,
                    feathering=0,
                    multiple=1,
                )
                image, w, h, _mask = out["result"]
                self.assertEqual((w, h), (80, 40))
                actual = tuple(float(v) for v in image[0, 20, 0])
                for got, want in zip(actual, rgb):
                    self.assertAlmostEqual(got, want, places=5)

    def test_pad_colour_applies_to_mask_overlay(self):
        img = _rgb(20, 20, value=0.25)
        src_mask = torch.zeros((1, 20, 20), dtype=torch.float32)
        src_mask[:, 5:15, 5:15] = 1.0
        out = _run(
            self.node,
            img,
            width=0,
            height=0,
            aspect_ratio=rsz.ASPECT_INPUT,
            pad_image=True,
            pad_colour="Red",
            feathering=0,
            overlay_mask=True,
            multiple=1,
            mask=src_mask,
        )
        image, _w, _h, out_mask = out["result"]
        self.assertGreater(float(out_mask[0, 10, 10]), 0.99)
        self.assertEqual(tuple(float(v) for v in image[0, 10, 10]), (1.0, 0.0, 0.0))
        # Unmasked content remains unchanged.
        for value in image[0, 0, 0]:
            self.assertAlmostEqual(float(value), 0.25, places=2)

    def test_smart_zero_dims_pad(self):
        img = _rgb(48, 64)  # 64x48 landscape-ish → nearest square
        out = _run(
            self.node,
            img,
            width=0,
            height=0,
            aspect_ratio=rsz.ASPECT_SMART,
            pad_image=True,
            multiple=1,
        )
        image, w, h, mask = out["result"]
        self.assertEqual(w, h)
        self.assertEqual(tuple(image.shape[1:3]), (h, w))
        # Letterbox bars should mark mask as 1 somewhere if AR changed.
        self.assertEqual(mask.shape[-2:], (h, w))

    def test_fixed_aspect_both_dims_and_multiple(self):
        img = _rgb(64, 64)
        out = _run(
            self.node,
            img,
            width=100,
            height=100,
            aspect_ratio=rsz.ASPECT_SQUARE,
            pad_image=True,
            multiple=16,
        )
        _image, w, h, _mask = out["result"]
        self.assertEqual(w % 16, 0)
        self.assertEqual(h % 16, 0)
        self.assertEqual(w, h)

    def test_wide_aspect_only_width(self):
        img = _rgb(90, 160)
        out = _run(
            self.node,
            img,
            width=320,
            height=0,
            aspect_ratio=rsz.ASPECT_WIDE,
            pad_image=True,
            multiple=1,
        )
        _image, w, h, _mask = out["result"]
        self.assertEqual(w, 320)
        self.assertEqual(h, 180)

    def test_mask_and_letterbox_feather(self):
        img = _rgb(40, 80)  # wide → square pad will add vertical bars conceptually... 80x40 → 80x80
        src_mask = torch.zeros((1, 40, 80), dtype=torch.float32)
        src_mask[:, 10:30, 20:60] = 1.0
        out = _run(
            self.node,
            img,
            width=0,
            height=0,
            aspect_ratio=rsz.ASPECT_SQUARE,
            pad_image=True,
            feathering=8,
            multiple=1,
            mask=src_mask,
        )
        _image, w, h, mask = out["result"]
        self.assertEqual((w, h), (80, 80))
        # Top/bottom letterbox bars (pad contain for 2:1 → 80x80) should be 1.
        self.assertGreater(float(mask[0, 0, 40].item()), 0.99)
        self.assertGreater(float(mask[0, 79, 40].item()), 0.99)
        # Content region should retain some of the source mask signal.
        self.assertGreater(float(mask.max().item()), 0.5)


if __name__ == "__main__":
    unittest.main()
