#
# SmartResizerV2.py
#
# Smart Resizer v2 — Width/Height/Aspect Ratio controls with Multiple applied last.
# Separate from Smart Resizer v1 for side-by-side testing.
#

from __future__ import annotations

import os
import random
import string

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

import folder_paths

MAX_RESOLUTION = 16384

ASPECT_INPUT = "Input"
ASPECT_SMART = "Smart"
ASPECT_SQUARE = "1:1 (Square)"
ASPECT_WIDE = "16:9 (Widescreen)"
ASPECT_PORTRAIT = "9:16 (Portrait Widescreen)"

ASPECT_CHOICES = [
    ASPECT_INPUT,
    ASPECT_SMART,
    ASPECT_SQUARE,
    ASPECT_WIDE,
    ASPECT_PORTRAIT,
]


class SmartResizerV2:
    """Resize with Width/Height/Aspect Ratio; Multiple snaps final resolution."""

    RESAMPLING_METHODS = ["Lanczos", "Bilinear", "Nearest-Exact"]

    def __init__(self):
        self.prefix_append = "_rsz2_" + "".join(
            random.choice(string.ascii_lowercase) for _ in range(5)
        )

    @staticmethod
    def _pil_resample(resampling: str):
        return {
            "Lanczos": Image.Resampling.LANCZOS,
            "Bilinear": Image.Resampling.BILINEAR,
            "Nearest-Exact": Image.Resampling.NEAREST,
        }.get(resampling, Image.Resampling.LANCZOS)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "width": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": MAX_RESOLUTION,
                        "step": 1,
                        "tooltip": "Proposed width. 0 means derive from other settings.",
                    },
                ),
                "height": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": MAX_RESOLUTION,
                        "step": 1,
                        "tooltip": "Proposed height. 0 means derive from other settings.",
                    },
                ),
                "aspect_ratio": (
                    ASPECT_CHOICES,
                    {
                        "default": ASPECT_INPUT,
                        "tooltip": (
                            "Input: keep source aspect (Width/Height only when exactly one "
                            "is set). Smart: nearest of 1:1 / 16:9 / 9:16. Fixed ratios "
                            "force that aspect via pad or crop."
                        ),
                    },
                ),
                "multiple": (
                    "INT",
                    {
                        "default": 1,
                        "min": 1,
                        "max": 112,
                        "step": 1,
                        "tooltip": "Final width and height are adjusted to this multiple.",
                    },
                ),
                "pad_image": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "Pad (Letterbox)",
                        "label_off": "Crop to Fit",
                    },
                ),
                "resampling": (
                    cls.RESAMPLING_METHODS,
                    {"default": "Lanczos"},
                ),
                "feathering": (
                    "INT",
                    {
                        "default": 40,
                        "min": 0,
                        "max": MAX_RESOLUTION,
                        "step": 1,
                        "advanced": True,
                        "tooltip": (
                            "Feathers letterbox pad edges into the content. Combined with "
                            "the optional mask via max()."
                        ),
                    },
                ),
                "overlay_mask": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "label": "Overlay Mask",
                        "label_on": "On",
                        "label_off": "Off",
                        "tooltip": (
                            "Full-opacity white where the output mask is bright; unchanged "
                            "where mask is 0."
                        ),
                    },
                ),
            },
            "optional": {
                "mask": (
                    "MASK",
                    {
                        "tooltip": (
                            "Optional mask. Resized to the content region and combined with "
                            "letterbox feathering via max(). Letterbox bars stay 1."
                        ),
                    },
                ),
            },
        }

    RETURN_TYPES = ("IMAGE", "INT", "INT", "MASK")
    RETURN_NAMES = ("image", "width", "height", "mask")
    FUNCTION = "process"
    CATEGORY = "slikvik/Image"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Smart Resizer v2: Width/Height/Aspect Ratio sizing with Multiple applied last, "
        "pad or crop, mask feathering, and an on-node preview."
    )

    @staticmethod
    def _fixed_aspect_value(aspect_ratio: str) -> float | None:
        return {
            ASPECT_SQUARE: 1.0,
            ASPECT_WIDE: 16 / 9,
            ASPECT_PORTRAIT: 9 / 16,
        }.get(aspect_ratio)

    @staticmethod
    def _smart_aspect(src_w: int, src_h: int) -> float:
        ar = src_w / src_h if src_h else 1.0
        candidates = (1.0, 16 / 9, 9 / 16)
        return min(candidates, key=lambda c: abs(ar - c))

    @classmethod
    def _resolve_target_aspect(
        cls, aspect_ratio: str, src_w: int, src_h: int
    ) -> float | None:
        """None means Aspect Input (preserve source aspect)."""
        if aspect_ratio == ASPECT_INPUT:
            return None
        if aspect_ratio == ASPECT_SMART:
            return cls._smart_aspect(src_w, src_h)
        return cls._fixed_aspect_value(aspect_ratio)

    @staticmethod
    def _expand_box_to_aspect(width: int, height: int, target_ar: float) -> tuple[int, int]:
        """Expand proposed WxH until aspect matches (never shrink)."""
        width = max(1, int(width))
        height = max(1, int(height))
        current_ar = width / height
        if current_ar > target_ar:
            # Too wide → increase height.
            height = max(1, int(round(width / target_ar)))
        elif current_ar < target_ar:
            width = max(1, int(round(height * target_ar)))
        return width, height

    @staticmethod
    def _fit_canvas_from_input(
        src_w: int, src_h: int, target_ar: float, pad_image: bool
    ) -> tuple[int, int]:
        """
        Build a target frame of `target_ar` from the input size.
        Pad (contain): canvas fully contains the image.
        Crop (cover): canvas fits inside the image.
        """
        src_w = max(1, int(src_w))
        src_h = max(1, int(src_h))
        src_ar = src_w / src_h
        if pad_image:
            if src_ar > target_ar:
                tw = src_w
                th = max(1, int(round(tw / target_ar)))
            else:
                th = src_h
                tw = max(1, int(round(th * target_ar)))
        else:
            if src_ar > target_ar:
                th = src_h
                tw = max(1, int(round(th * target_ar)))
            else:
                tw = src_w
                th = max(1, int(round(tw / target_ar)))
        return tw, th

    @staticmethod
    def _snap_independent(width: int, height: int, multiple: int) -> tuple[int, int]:
        m = max(1, int(multiple))
        tw = max(m, int(round(width / m) * m))
        th = max(m, int(round(height / m) * m))
        return tw, th

    @staticmethod
    def _snap_keep_aspect(width: int, height: int, target_ar: float, multiple: int) -> tuple[int, int]:
        m = max(1, int(multiple))
        tw = max(m, int(round(width / m) * m))
        th = max(m, int(round((tw / target_ar) / m) * m))
        return tw, th

    @classmethod
    def _compute_target_size(
        cls,
        src_w: int,
        src_h: int,
        width: int,
        height: int,
        aspect_ratio: str,
        pad_image: bool,
    ) -> tuple[int, int, float | None]:
        """
        Returns (target_w, target_h, locked_ar).
        locked_ar is None for Aspect Input (independent Multiple snap).
        """
        w = max(0, int(width))
        h = max(0, int(height))
        locked_ar = cls._resolve_target_aspect(aspect_ratio, src_w, src_h)
        src_ar = src_w / src_h if src_h else 1.0

        both_zero = w == 0 and h == 0
        both_set = w > 0 and h > 0
        only_w = w > 0 and h == 0
        only_h = h > 0 and w == 0

        if locked_ar is None:
            # Aspect Input
            if both_zero or both_set:
                return src_w, src_h, None
            if only_w:
                tw = w
                th = max(1, int(round(w / src_ar)))
                return tw, th, None
            # only_h
            th = h
            tw = max(1, int(round(h * src_ar)))
            return tw, th, None

        # Smart or fixed aspect
        if both_zero:
            tw, th = cls._fit_canvas_from_input(src_w, src_h, locked_ar, pad_image)
            return tw, th, locked_ar

        if only_w:
            tw = w
            th = max(1, int(round(w / locked_ar)))
            return tw, th, locked_ar

        if only_h:
            th = h
            tw = max(1, int(round(h * locked_ar)))
            return tw, th, locked_ar

        # both_set
        tw, th = cls._expand_box_to_aspect(w, h, locked_ar)
        return tw, th, locked_ar

    @staticmethod
    def _letterbox_or_crop(
        pil_img: Image.Image,
        target_width: int,
        target_height: int,
        pad_image: bool,
        pil_resample,
    ) -> tuple[Image.Image, int, int, int, int]:
        """
        Returns (final_image, paste_x, paste_y, content_w, content_h).
        For crop, paste offsets are 0 and content fills the canvas.
        """
        img_width, img_height = pil_img.size
        target_width = max(1, int(target_width))
        target_height = max(1, int(target_height))

        if pad_image:
            original_ar = img_width / img_height if img_height else 1.0
            target_ar = target_width / target_height if target_height else 1.0
            if original_ar > target_ar:
                scaled_width = target_width
                scaled_height = max(1, int(target_width / original_ar))
            else:
                scaled_height = target_height
                scaled_width = max(1, int(target_height * original_ar))
            resized_img = pil_img.resize((scaled_width, scaled_height), pil_resample)
            background = Image.new("RGB", (target_width, target_height), (0, 0, 0))
            paste_x = (target_width - scaled_width) // 2
            paste_y = (target_height - scaled_height) // 2
            background.paste(resized_img, (paste_x, paste_y))
            return background, paste_x, paste_y, scaled_width, scaled_height

        # Cover + center crop
        target_ar = target_width / target_height
        input_ar = img_width / img_height if img_height else 1.0
        if input_ar > target_ar:
            new_height = target_height
            new_width = max(1, int(img_width * (target_height / img_height)))
        else:
            new_width = target_width
            new_height = max(1, int(img_height * (target_width / img_width)))
        resized_img = pil_img.resize((new_width, new_height), pil_resample)
        left = (new_width - target_width) // 2
        top = (new_height - target_height) // 2
        cropped = resized_img.crop((left, top, left + target_width, top + target_height))
        return cropped, 0, 0, target_width, target_height

    @staticmethod
    def _inner_feather_mask(
        d2: int, d3: int, left: int, top: int, right: int, bottom: int, feathering: int
    ):
        t = torch.zeros((d2, d3), dtype=torch.float32)
        if feathering > 0 and feathering * 2 < d2 and feathering * 2 < d3:
            for i in range(d2):
                for j in range(d3):
                    dt = i if top != 0 else d2
                    db = d2 - i if bottom != 0 else d2
                    dl = j if left != 0 else d3
                    dr = d3 - j if right != 0 else d3
                    d_edge = min(dt, db, dl, dr)
                    if d_edge >= feathering:
                        continue
                    v = (feathering - d_edge) / feathering
                    t[i, j] = v * v
        return t

    @staticmethod
    def _prepare_source_mask(
        source_mask: torch.Tensor,
        batch: int,
        height: int,
        width: int,
        device: torch.device,
    ) -> torch.Tensor:
        sm = source_mask.float()
        if sm.dim() == 2:
            sm = sm.unsqueeze(0)
        if sm.shape[0] == 1 and batch > 1:
            sm = sm.expand(batch, -1, -1)
        elif sm.shape[0] != batch:
            sm = sm[0:1].expand(batch, -1, -1)
        if sm.shape[1] != height or sm.shape[2] != width:
            sm = F.interpolate(
                sm.unsqueeze(1),
                size=(height, width),
                mode="bilinear",
                align_corners=False,
            ).squeeze(1)
        return sm.clamp(0.0, 1.0).to(device)

    @classmethod
    def _build_letterbox_mask(
        cls,
        batch: int,
        canvas_h: int,
        canvas_w: int,
        paste_x: int,
        paste_y: int,
        content_w: int,
        content_h: int,
        feathering: int,
        source_mask: torch.Tensor | None,
        device: torch.device,
    ) -> torch.Tensor:
        """Bars = 1; content = max(source_mask, edge feather)."""
        left = paste_x
        top = paste_y
        right = max(0, canvas_w - paste_x - content_w)
        bottom = max(0, canvas_h - paste_y - content_h)

        mask = torch.ones((batch, canvas_h, canvas_w), dtype=torch.float32, device=device)
        if content_w <= 0 or content_h <= 0:
            return mask

        feather = cls._inner_feather_mask(
            content_h, content_w, left, top, right, bottom, feathering
        )
        feather_b = feather.to(device).unsqueeze(0).expand(batch, -1, -1)
        if source_mask is not None:
            sm = cls._prepare_source_mask(source_mask, batch, content_h, content_w, device)
            inner = torch.maximum(sm, feather_b)
        else:
            inner = feather_b
        mask[:, top : top + content_h, left : left + content_w] = inner
        return mask

    @staticmethod
    def _blend_mask_as_white_overlay(
        image: torch.Tensor,
        mask: torch.Tensor,
        strength: float = 1.0,
    ) -> torch.Tensor:
        image = image.to(dtype=torch.float32)
        m = mask.to(dtype=torch.float32).clamp(0.0, 1.0)
        if m.dim() == 2:
            m = m.unsqueeze(0)
        b, h, w, c = image.shape
        if m.shape[0] == 1 and b > 1:
            m = m.expand(b, -1, -1)
        elif m.shape[0] != b:
            m = m[0:1].expand(b, -1, -1)
        if m.shape[1] != h or m.shape[2] != w:
            m = F.interpolate(
                m.unsqueeze(1),
                size=(h, w),
                mode="bilinear",
                align_corners=False,
            ).squeeze(1)
        m = m.to(device=image.device)
        m4 = m.unsqueeze(-1).expand(b, h, w, c)
        white = torch.ones_like(image)
        out = image * (1.0 - strength * m4) + white * (strength * m4)
        return out.clamp(0.0, 1.0)

    @staticmethod
    def _resize_batch(
        image: torch.Tensor,
        mask: torch.Tensor,
        out_w: int,
        out_h: int,
        pil_resample,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Resize image batch + mask to out_w x out_h when Multiple snaps size."""
        b, h, w, c = image.shape
        if w == out_w and h == out_h:
            return image, mask

        processed = []
        for img_tensor in image:
            pil_img = Image.fromarray(
                np.clip(255.0 * img_tensor.detach().cpu().numpy(), 0, 255).astype(np.uint8)
            )
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            resized = pil_img.resize((out_w, out_h), pil_resample)
            arr = np.array(resized).astype(np.float32) / 255.0
            processed.append(torch.from_numpy(arr))
        new_image = torch.stack(processed).to(device=image.device, dtype=torch.float32)

        m = mask.to(dtype=torch.float32)
        if m.dim() == 2:
            m = m.unsqueeze(0)
        m = F.interpolate(
            m.unsqueeze(1),
            size=(out_h, out_w),
            mode="bilinear",
            align_corners=False,
        ).squeeze(1)
        return new_image, m.clamp(0.0, 1.0)

    def _save_preview(self, image: torch.Tensor) -> list[dict]:
        """Save first frame to temp for /view preview."""
        if image is None or (isinstance(image, torch.Tensor) and image.numel() == 0):
            return []
        tensor = image[0]
        arr = tensor.detach().cpu().numpy()
        if arr.ndim == 3 and arr.shape[0] in (1, 3, 4):
            arr = np.transpose(arr, (1, 2, 0))
        arr = (255.0 * arr).clip(0, 255).astype(np.uint8)
        if arr.shape[-1] == 4:
            img = Image.fromarray(arr, mode="RGBA")
        else:
            img = Image.fromarray(arr[..., :3], mode="RGB")

        height, width = arr.shape[0], arr.shape[1]
        output_dir = folder_paths.get_temp_directory()
        prefix = "smart_resizer_v2" + self.prefix_append
        full_output_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            prefix, output_dir, width, height
        )
        file = f"{filename}_{counter:05}_.png"
        img.save(os.path.join(full_output_folder, file), compress_level=4)
        return [{"filename": file, "subfolder": subfolder, "type": "temp"}]

    def process(
        self,
        image: torch.Tensor,
        width: int = 0,
        height: int = 0,
        aspect_ratio: str = ASPECT_INPUT,
        multiple: int = 1,
        pad_image: bool = True,
        resampling: str = "Lanczos",
        feathering: int = 40,
        overlay_mask: bool = False,
        mask: torch.Tensor | None = None,
    ):
        pil_resample = self._pil_resample(resampling)
        b, src_h, src_w, _ = image.shape

        if src_h <= 0 or src_w <= 0:
            empty_mask = torch.zeros(
                (b, max(0, src_h), max(0, src_w)),
                dtype=torch.float32,
                device=image.device,
            )
            return {
                "ui": {
                    "rsz2_images": [],
                    "input_width": [src_w],
                    "input_height": [src_h],
                    "output_width": [src_w],
                    "output_height": [src_h],
                },
                "result": (image, src_w, src_h, empty_mask),
            }

        target_w, target_h, locked_ar = self._compute_target_size(
            src_w, src_h, width, height, aspect_ratio, pad_image
        )

        processed_images = []
        paste_meta = None  # shared geometry from first image (batch assumed same size)

        for img_tensor in image:
            pil_img = Image.fromarray(
                np.clip(255.0 * img_tensor.detach().cpu().numpy(), 0, 255).astype(np.uint8)
            )
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            final_pil, px, py, cw, ch = self._letterbox_or_crop(
                pil_img, target_w, target_h, pad_image, pil_resample
            )
            if paste_meta is None:
                paste_meta = (px, py, cw, ch)
            arr = np.array(final_pil).astype(np.float32) / 255.0
            processed_images.append(torch.from_numpy(arr))

        final_batch = torch.stack(processed_images).to(
            device=image.device, dtype=torch.float32
        )
        canvas_h, canvas_w = final_batch.shape[1], final_batch.shape[2]
        px, py, cw, ch = paste_meta if paste_meta else (0, 0, canvas_w, canvas_h)

        out_mask = self._build_letterbox_mask(
            batch=b,
            canvas_h=canvas_h,
            canvas_w=canvas_w,
            paste_x=px,
            paste_y=py,
            content_w=cw,
            content_h=ch,
            feathering=int(feathering),
            source_mask=mask,
            device=image.device,
        )

        # Multiple applied last.
        if locked_ar is None:
            final_w, final_h = self._snap_independent(canvas_w, canvas_h, multiple)
        else:
            final_w, final_h = self._snap_keep_aspect(canvas_w, canvas_h, locked_ar, multiple)

        if final_w != canvas_w or final_h != canvas_h:
            final_batch, out_mask = self._resize_batch(
                final_batch, out_mask, final_w, final_h, pil_resample
            )

        if overlay_mask:
            final_batch = self._blend_mask_as_white_overlay(final_batch, out_mask)

        preview = self._save_preview(final_batch)
        return {
            "ui": {
                # Custom key so ComfyUI does not also draw the default OUTPUT_NODE preview.
                "rsz2_images": preview,
                "input_width": [src_w],
                "input_height": [src_h],
                "output_width": [final_w],
                "output_height": [final_h],
            },
            "result": (final_batch, final_w, final_h, out_mask),
        }


NODE_CLASS_MAPPINGS = {
    "SmartResizerV2Node": SmartResizerV2,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "SmartResizerV2Node": "Smart Resizer v2",
}
