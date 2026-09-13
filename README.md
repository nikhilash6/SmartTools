# SmartTools

Custom nodes for [ComfyUI](https://github.com/comfyanonymous/ComfyUI). Category: **slikvik** / **slikvik/Image** / **slikvik/LLM** / **slikvik/Prompt**.

## Installation

1. Copy this folder into ComfyUI’s `custom_nodes` directory (or clone the repo there), e.g.  
   `ComfyUI/custom_nodes/SmartTools`
2. Restart ComfyUI.

Dependencies match a typical ComfyUI install: **PyTorch**, **NumPy**, **Pillow**. **Smart Save** also uses ComfyUI’s `folder_paths`, `comfy.cli_args`, and the Comfy server for the save route.

**Smart LLM** is optional: install Transformers-related packages from [`requirements.txt`](requirements.txt) into the same Python environment as ComfyUI (see that file for versions):
C:\APPS\AI\ComfyEasyInstall\ComfyUI-Easy-Install\python_embeded\python.exe -m pip install -r C:\APPS\AI\ComfyEasyInstall\ComfyUI-Easy-Install\ComfyUI\custom_nodes\SmartTools\requirements.txt

## Nodes

### Smart Resizer

**Display name:** Smart Resizer  
**Category:** `slikvik/Image`

Resizes a batch of images to a target resolution with optional **letterboxing** (pad) or **crop to fit**.

| Input | Notes |
|--------|--------|
| `image` | Image batch `(B, H, W, C)` |
| `use_presets` | **ON** — target size from `VIDEO_preset`; **OFF** — from `Megapixels` + `Multiple` |
| `resampling` | **Lanczos**, **Bilinear**, or **Nearest-Exact** (Pillow nearest-neighbor) |
| `Megapixels` | When presets off: target size in MP (0.10–5.00) |
| `Multiple` | When presets off: both sides snapped to be divisible by this (e.g. 16) |
| `VIDEO_preset` | **480p**, **720p**, **1080p**, or **1024px** (when presets on) |
| `pad_image` | On: pad (letterbox); off: crop to target aspect |
| Outpaint | `pad_left` / `pad_top` / `pad_right` / `pad_bottom`, `feathering`, optional `mask`, `overlay_mask` — applied after resize in **both** preset and megapixel modes |

**Use Presets ON:** Chooses **square-ish** vs **wide** (16:9 / 9:16 style) from input aspect ratio, then applies the selected preset dimensions.

**Use Presets OFF:** Ignores `VIDEO_preset`. Chooses width/height that preserve aspect ratio, approximate total pixels `Megapixels × 1_000_000`, and satisfy `Multiple`.

**Outputs:** `image`, `width`, `height`, `mask` (includes outpaint / feathering when used).

---

### Smart Resizer v2

**Display name:** Smart Resizer v2  
**Category:** `slikvik/Image`

Separate from **Smart Resizer** (v1) for testing. Sizing is driven by **Size Mode**, **Aspect Ratio**, and **Multiple** (applied last).

| Input | Notes |
|--------|--------|
| `image` | Image batch `(B, H, W, C)` |
| `size_mode` | **Dimensions**, **Megapixels**, **Shortest**, or **Longest** (shown first in the UI) |
| `width` / `height` | Used when Size Mode is Dimensions. `0` means derive from other settings |
| `megapixels` | Used when Size Mode is Megapixels. Target area in MP (0.10–100.00, default 1.0) |
| `shortest` / `longest` | Used in Shortest / Longest mode. Sets that edge after Aspect Ratio (default 1024) |
| `aspect_ratio` | **Input**, **Smart**, **1:1 (Square)**, **16:9 (Widescreen)**, **9:16 (Portrait Widescreen)** |
| `multiple` | Final W/H snapped to this multiple (default 1) |
| `pad_image` | Pad (letterbox) or crop to fit |
| `pad_colour` | Letterbox colour: Black (default), Grey, Red, Green, or White |
| `resampling` | Lanczos / Bilinear / Nearest-Exact |
| `feathering` | Feathers letterbox pad edges; combined with optional `mask` via `max` |
| `overlay_mask` | Applies the selected Pad Colour where the output mask is bright |
| `mask` | Optional; resized into the content region |

**Dimensions + Aspect Input:** both dims 0 → keep source size. Both non-zero → use that exact target canvas and pad/crop into it. Exactly one dim set → derive the other from the source aspect.

**Megapixels:** target area is `megapixels × 1_000_000`. Aspect Input preserves source aspect (v1-style). Smart / fixed ratios use that aspect while matching the area.

**Shortest / Longest:** set the named edge, then apply Aspect Ratio. For 16:9, Shortest is the 9-side and Longest is the 16-side. Aspect Input keeps the source aspect while resizing that edge. Smart picks 1:1 / 16:9 / 9:16 first, then applies the edge.

**Smart / fixed ratio (Dimensions):** both dims 0 → build a target frame of that aspect from the input (pad = contain, crop = cover). One dim set → that dim forces the frame at the target aspect. Both dims set → expand the proposed box to the aspect, then pad/crop. Multiple snaps last (keeping the locked aspect).

The node shows **Input W×H**, **Output W×H**, and a preview of the result (`web/smart_resizer_v2.js`).

**Outputs:** `image`, `width`, `height`, `mask`.

---

### Smart Save

**Display name:** Smart Save  
**Category:** `slikvik`

Same PNG output and metadata behaviour as ComfyUI’s built-in **Save Image** (`folder_paths.get_save_image_path`, `%batch_num%`, optional workflow metadata in PNG), but files are written to the **output** folder only when you click **Save Image** on the node.

**Formats**
- **PNG / WebP / AVIF:** embed `prompt` + `workflow` so drag-and-drop onto ComfyUI restores the graph (WebP/AVIF use the same EXIF `prompt:` / `workflow:` tags ComfyUI reads).
- **JPEG:** EXIF stores the CLIP **prompt** (`ImageDescription` / `UserComment`) and **seed** (`Artist` / `UserComment`). JPEG cannot restore a workflow on drop.

1. **Queue the workflow** at least once so the node can cache the current image batch (keyed by the graph node id).
2. The node shows a **temp** preview so you can review the image without writing to the final output path.
3. Click **Save Image** to write the selected format to the output directory.

Requires the included **web** extension (`web/smart_save.js`); restart ComfyUI after installing or updating this pack.

---

### Smart Save Video

**Display name:** Smart Save Video  
**Category:** `slikvik`

Self-contained FFmpeg video encoder with the same manual-save workflow as **Smart Save**. It does **not** depend on VideoHelperSuite. Bundled presets live in [`video_formats/`](video_formats/) (h264/h265 MP4, WebM, AV1, NVENC variants, ProRes, FFV1).

| Input | Notes |
|--------|--------|
| `images` | Frame batch `(B, H, W, C)` to encode. |
| `autosave` | Default **OFF**. When ON, the final video is written during execution; the Save Video button is disabled. |
| `inc_audio` | Default **ON**. When ON and `audio` is connected, audio is muxed into the single saved file. When OFF (or no audio), exactly one silent video is produced. Never writes both silent and muxed copies. |
| `frame_rate`, `loop_count`, `pingpong` | Encoding timing controls. |
| `filename_prefix` | Relative `subfolder/name`, plain prefix, or absolute folder path. |
| `format` | Bundled FFmpeg preset name. |
| `crf`, `pix_fmt`, `bitrate`, `megabit`, `profile` | Quality / codec options applied when the selected preset uses them. |
| `save_metadata` | Embed workflow/prompt metadata into the container when the preset supports it. No first-frame PNG sidecar is written. |
| `audio` | Optional ComfyUI `AUDIO` dict (`waveform`, `sample_rate`). |
| `original` | Optional frame batch. When connected, the node encodes a second silent temp preview and the UI shows New and Original side-by-side, time-synced from the first frame. Save / autosave still target only the primary (`images`) video. |
| `use_timestamp` | Counter vs timestamp filenames on final save. |

**ffmpeg:** Required for encoding. Resolved from `SMART_SAVE_VIDEO_FFMPEG` / `VHS_FORCE_FFMPEG_PATH`, `imageio-ffmpeg`, system `PATH`, or a local `ffmpeg` / `ffmpeg.exe`.

1. Queue the workflow so the node encodes a temp preview video. The node UI shows an interactive video player (play / pause / mute / timeline). When `original` is connected, both videos play side-by-side and stay synced.
2. With autosave **OFF**, click **Save Video** to copy that encoded file to the chosen output path without re-encoding.
3. Buttons match Smart Save: browse path, open folder, favorites, and save history (`web/smart_save_video.js`).

**Outputs:** `video_path` is the absolute path of the autosaved file, or the temp encode when autosave is off.

---

### Smart Load Video

**Display name:** Smart Load Video  
**Category:** `slikvik`

Self-contained FFmpeg video loader. It does **not** depend on VideoHelperSuite. Soft-modeled on VHS **Load Video FFmpeg (Upload)** for the core load widgets.

| Input | Notes |
|--------|--------|
| `video` | Combo of videos already in the ComfyUI input folder. Drag-and-drop can add a **small** file here; ComfyUI rejects uploads larger than `--max-upload-size` (default 100 MB). |
| `video_path` | Absolute path (or `~`) to a video on disk. When set, this is used instead of the combo — FFmpeg reads the file in place, so size does not matter. Use **browse video path**. |
| `force_rate` | `0` keeps the source fps. The widget shows the source rate (`59.93↩`) when at `0`. Disable (slashed circle) returns to `0`; reset writes the source fps as an explicit value. |
| `custom_width` / `custom_height` | `0` keeps or derives that edge from the other. Both set crops to the new aspect, then scales. Format presets add reset sizes (H3: 1344×768). |
| `multiple` | After the target size is chosen, both edges snap **up** to this multiple. Default `1` is a no-op. Selecting a format other than **None** sets this to the preset grid (H3 → 32). You can override it afterward. |
| `format` | VHS-style model preset (`None`, AnimateDiff, Mochi, LTXV, Hunyuan, Cosmos, Wan, H3). Only updates reset/step targets until you click reset. **H3:** reset 24 fps and 1344×768, `multiple` 32. Requested caps snap **up** to `5 + 17n` (48 → 56); leftover loaded frames still truncate down. |
| `cap_seconds` | `0` leaves `frame_load_cap` alone. Otherwise live-sets `frame_load_cap` from loaded fps × seconds (`force_rate` if set, else source fps), then snaps **up** to the format frame rule (H3: `2s` at 24 fps → 56). |
| `frame_load_cap` | `0` loads all remaining frames from the effective start. The widget shows the source (or format-legal) frame count when at `0`. Disable returns to `0`; reset writes that count. Overwritten while `cap_seconds` is `> 0`. |
| `start_time` | Start offset in seconds (index `0`). |
| `slice_index` | `0` starts at `start_time`. Each next index jumps forward by `frame_load_cap` frames (`start_time + slice_index * frame_load_cap / fps`). `slice_index > 0` requires `frame_load_cap > 0`. |

**Large clips:** use `video_path` (browse or paste), leave `start_time` at `0`, set `cap_seconds` (e.g. `10`) so `frame_load_cap` is filled and format-snapped, and increment `slice_index` each run (a 2-minute clip in 10-second chunks is indices `0`–`11`).

The node shows a preview of the selected combo file or disk path and annotates `force_rate` / `frame_load_cap` with the source fps and total frames (`web/smart_load_video.js`). Restart ComfyUI after installing or updating this pack so the browse/view/query routes register.

**Outputs:** `IMAGE` (frame batch), `mask` (inverted alpha, or ones when there is no alpha), `audio` (Comfy `AUDIO` aligned to the loaded slice; silence if the file has no audio), `framerate` (loaded fps: `force_rate` when set, otherwise source fps).

**ffmpeg:** Same resolve order as Smart Save Video (`SMART_SAVE_VIDEO_FFMPEG` / `VHS_FORCE_FFMPEG_PATH`, `imageio-ffmpeg`, system `PATH`, or a local `ffmpeg` / `ffmpeg.exe`).

---

### Smart Lora

**Display name:** Smart Lora  
**Category:** `slikvik`

Applies two **independent** lists of **model-only** LoRAs in one node: **high** LoRAs to the high-noise model and **low** LoRAs to the low-noise model (e.g. for split high/low-noise model setups). No CLIP is touched.

| Input / Output | Notes |
|--------|--------|
| `model_high` (in/out) | Optional. High-noise diffusion model; **high** LoRAs are applied to it. If left unconnected, the output is `None`. |
| `model_low` (in/out) | Optional. Low-noise diffusion model; **low** LoRAs are applied to it. If left unconnected, the output is `None`. |
| `prompt` (in) | Optional string from another node; if connected it is prepended to the prompt text with a line break. |
| `prompt` (out) | Combined prompt: optional `prompt` input, line break, then `prompt_text`. |

**LoRA lists (custom UI):** Use **Add Lora (High)** / **Add Lora (Low)** to add rows to each group. Each row has:

- a **name** field (click to open a searchable LoRA picker),
- a **strength** box (click to type a value; negative values allowed),
- an **info** button (`i`) that, when a sidecar JSON exists next to the LoRA file (same name, `.json` extension), opens a modal showing the **link**, **trigger words**, and **description**. Description may be a string or `{ "model": "...", "version": "..." }`; both HTML fields are shown in the same box, and basic tags such as `<p>`, `<ul>`, `<li>` are rendered. Each field is copyable to the clipboard,
- an **enable** toggle, and
- a **delete** button (`✕`).

High LoRAs apply to `model_high` and low LoRAs to `model_low`, in list order, only when their toggle is on and strength is non-zero.

**Profiles:** Above the prompt text box, a **Profile** selector with **Save Profile As...**, **Update Profile**, and **Delete Profile** buttons lets you store and recall setups. A profile captures both LoRA lists (each LoRA's name, strength and enable toggle) plus the prompt text. Selecting a profile loads it (replacing the current lists and prompt text), **Update** overwrites the selected profile with the current state, and **Delete** removes it. Profiles are stored globally on the server in `smart_lora_profiles.json`, so they are shared across every Smart Lora node and all workflows, and persist across restarts.

**Resizing:** Drag the node wider/narrower and the rows reflow horizontally. Drag it taller/shorter and only the `prompt_text` box grows or shrinks; the LoRA rows and buttons stay fixed.

**Persistence:** The full LoRA configuration is stored as JSON on the node (in `node.properties` / the hidden `lora_config` input) and is sent to the backend, so workflows reload exactly as saved.

Requires the included **web** extension (`web/smart_lora.js`); restart ComfyUI after installing or updating this pack.

---

### Smart LLM

**Display name:** Smart LLM  
**Category:** `slikvik/LLM`

Runs **local Hugging Face vision-language** instruction checkpoints from a **model folder** (full snapshot: `config.json`, tokenizer / processor files, and `*.safetensors` or a sharded `model.safetensors.index.json`). Inference uses **Transformers** (`AutoModelForImageTextToText` + `AutoProcessor`); there is no GGUF or llama.cpp dependency.

Works with any checkpoint those Auto classes load — for example **Qwen3-VL** (including fine-tunes such as Huihui abliterated builds) when `AutoProcessor` succeeds, and **Gemma 4** (with a Gemma-specific processor fallback if AutoProcessor fails). There is no separate “model family” toggle.

| Input | Notes |
|--------|--------|
| `model_folder` | Absolute or `~` path to the directory you downloaded (e.g. with `huggingface-cli download ... --local-dir ...`). Must contain `config.json` and safetensors weights. |
| `system_prompt` | Optional multiline system message. |
| `prompt` | Multiline user prompt. |
| `max_tokens` | Cap on **new** tokens decoded after the prompt (same idea as `max_new_tokens` in Transformers). Increase if output looks truncated. |
| `attn_implementation` | Transformers attention: **`sdpa`** (default), **`eager`**, or **`flash_attention_2`** (requires `flash-attn` + CUDA; falls back to SDPA with a warning if unavailable). |
| `device_placement` | **`cuda`** (default) frees Comfy-cached models and forces the complete HF model onto GPU, avoiding unpredictable CPU offload. **`auto`** lets Accelerate split/offload the model when VRAM is insufficient, which can make token generation dramatically slower. |
| `enable_thinking` | Enables reasoning in chat templates that support it. Default **OFF** for direct responses; reasoning can consume the full token budget before the final answer. |
| `unload_model` | **ON** — after generation, drop the model and processor from memory and call CUDA cache cleanup so later nodes get more VRAM. **OFF** — keep the model loaded for the next run (same `model_folder` path). |
| `video_fps` | Frame rate of the optional `video` batch (default **30**). Match VideoHelperSuite `force_rate` / loaded fps so temporal grounding is correct. Ignored when `video` is disconnected. |
| `max_video_frames` | Cap on frames taken from `video` (even subsampling). Default **32**. **0** = use all frames, but batches larger than **64** are auto-capped (avoids multi‑minute hangs / VRAM blowups). Prefer VHS `frame_load_cap` for long clips. Ignored when `video` is disconnected. |
| `image` | Optional. First batch frame as RGB PIL inside the HF processor. |
| `image_2` | Optional. Second batch frame, after `image`, passed as PIL to the processor. |
| `video` | Optional. Video as an **`IMAGE` frame batch** `(B, H, W, C)` — same type as [VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite) Load Video **IMAGE** output. All frames are sent as native HF video (not as still images). |

**Sage Attention vs Smart LLM:** ComfyUI’s **Sage Attention** (the `sageattention` package and flags such as `--use-sage-attention`) plugs into **diffusion** sampling (`comfy` attention). **Smart LLM does not use Sage**; the VL model runs inside Hugging Face and only supports the backends above—not `sageattention`.

**VRAM:** Full VL checkpoints are large; use a size and dtype your GPU can hold, or explore quantization in Transformers separately. Video adds frame tokens — prefer VHS `frame_load_cap` and/or `max_video_frames` if generation OOMs. CUDA uses `device_map="auto"` (requires **`accelerate`**). With **Unload OFF**, the same `model_folder` + `attn_implementation` pair reuses one in-memory model (no reload each run). Changing **`model_folder`** drops any cached weights for other folders first. Changing **attention backend** replaces the previous cached copy for that folder so VRAM does not stack. (If you use several Smart LLM nodes with different folders in one workflow, the cache holds one folder at a time—whichever loads last—so the other path may reload on its next run.)

**Transformers version:** See [`requirements.txt`](requirements.txt). Gemma 4 needs **`transformers` 5.5.0 or newer**; Qwen3-VL needs a Transformers build that registers its processor/model classes.

### Smart H3 LLM

**Display name:** Smart H3 LLM

**Category:** `slikvik/LLM`

Uses the same local Hugging Face model cache as Smart LLM to analyze references and produce a duration-aware prompt that follows the bundled [MiniMax H3 prompt-writing guides](h3_references/). It runs a factual media-analysis pass, generates the H3 prompt deterministically, validates its structure and timing, and makes up to two text-only repair attempts if needed.

| Input | Notes |
|--------|-------|
| `skill` | **base** for T2VA/I2VA/FL2VA/L2VA, or **ref2VA** for full-reference six-section output. |
| `base_workflow` | Explicit base workflow. Ignored by ref2VA. |
| `ref_image_1_role`…`ref_image_4_role` | ref2VA only: assign each image independently as Auto, subject/reference, first frame, intermediate keyframe, last frame, or storyboard/composition. A disconnected image's role is ignored. |
| `ref_video_role` | ref2VA only: infer from the prompt, use the video for `reference generation`, treat it as a `video editing` source, or perform `video continuation`. |
| `prompt` | Creative intent and desired use of references. The base workflow does not need to be repeated here. |
| `verbatim_dialogue` | Optional dialogue/lyrics to preserve exactly inside H3 `<d>` blocks. |
| `video_duration` | Exact target duration in seconds (default **15.00**); controls final-frame alignment and valid cut range. |
| `visual_style` | **Auto** or a specific H3-compatible visual style hint. |
| `shot_count` | **0** lets the model choose; a positive value is validated as an exact shot count. |
| `audio_usage` | Infer from prompt, copy/reuse, reference only, or ignore connected audio. |
| `max_tokens` | Generation budget, default **4096** to accommodate detailed ref2VA output. |
| `device_placement` | **cuda** by default to prevent intermittent CPU-offloaded runs; use **auto** only when the checkpoint cannot fit fully in VRAM. |
| `enable_thinking` | Default **OFF** and normally should remain off for H3. Thinking can exhaust `max_tokens` before required fields such as `detailed_description`. |
| `image_1`…`image_4` | Optional still references. Base workflows use only their prescribed sockets; ref2VA numbers connected images densely. |
| `video` | Optional VHS-style `IMAGE` frame batch, with `video_fps` and `max_video_frames` matching Smart LLM. |
| `audio` | Optional standard `AUDIO` dictionary, directly connectable from VideoHelperSuite **Load Audio**. |

Base image mapping:

- **T2VA:** text-driven; connected still images are not used or labeled.
- **I2VA:** requires `image_1`, mapped to `<Picture 1>` at 0.00 seconds.
- **FL2VA:** requires `image_1` as Picture 1 at 0.00 and `image_2` as Picture 2 at the exact final duration.
- **L2VA:** requires `image_1`, mapped to `<Picture 1>` at the exact final duration.

ref2VA uses all connected images in socket order, skipping gaps, then exposes the video and audio as `<Video 1>` and `<Audio 1>` when active. Each connected image keeps the role selected for its original socket: if only `image_1` and `image_3` are connected, they become `<Picture 1>` and `<Picture 2>` while using `ref_image_1_role` and `ref_image_3_role`. Reusable people, objects, scenes, styles, or actions are defined separately as `<Subject N>` by the model.

`base_workflow` remains separate from ref2VA task types because base workflows are mutually exclusive, while a ref2VA summary may combine several relationships. First/intermediate/last-frame roles require `keyframe completion`; subject/reference and storyboard/composition roles require `reference generation`. These can combine with the selected video and audio tasks—for example, a character-reference image + a last-frame image + an edited source video + reference-only audio requires `[keyframe completion + reference generation + video editing + audio reference]`. Any role left on **Auto from prompt** is inferred from the user prompt and media analysis.

The audio socket accepts `{"waveform": Tensor[B,C,T], "sample_rate": int}`, the same payload returned by VideoHelperSuite `LoadAudio`. Audio is mixed to mono and resampled to 16 kHz for inference. The selected checkpoint must support every connected modality; Gemma 4 E2B, E4B, and 12B variants support native audio, while many vision-language checkpoints do not. If source-audio and target-video durations differ, the prompt is instructed to describe partial reuse, trimming, continuation, or padding rather than claiming impossible 1:1 reuse.

**Outputs:** `h3_prompt` is a clean paste-ready H3 prompt with no analysis or markdown wrapper. `analysis` contains the model's factual notes about the connected references and can be left unconnected.

Because this node normally performs two model generations (analysis and prompt writing), with up to two additional generations when repair is required, it takes longer than a single Smart LLM call. The model remains loaded between passes and follows the `unload_model` setting after completion.

### Smart H3 Prompt

**Display name:** Smart H3 Prompt

**Category:** `slikvik/Prompt`

Builds a paste-ready MiniMax H3 prompt entirely with deterministic Python logic. It does not load an LLM or inspect media. Instead, describe the future reference assets in the multiline `<Picture 1>`…`<Picture 4>`, `<Video 1>`, and `<Audio 1>` widgets.

| Input | Notes |
|--------|-------|
| `skill` / `base_workflow` | Select base T2VA/I2VA/FL2VA/L2VA output or the six-section ref2VA format. |
| Reference-role selectors | Assign each described picture and video its H3 role. In ref2VA, **Auto from prompt** deterministically falls back to subject/reference for pictures and reference generation for video. |
| `Shot 1` | Required first-shot description. Do not type a `[Shot 1]` header. |
| `Shot 2`, `Shot 3` | Optional later-shot descriptions. Shot 3 requires Shot 2. The node adds sequential H3 headers. |
| Shot start times | Numeric seconds for Shots 2 and 3. The node formats them as `MM:SS.mmm`; populated times must increase and remain before `video_duration`. |
| `verbatim_dialogue` | Exact dialogue/lyrics to verify. Place each line in the appropriate shot inside `<d>[Language] ...</d>`; the node cannot infer its speaker or timing. |
| H3 section details | Optional `subject_definitions`, `summary`, `retention_analysis`, `overall_soundscape`, and `non_diegetic_music` bodies. Omit field headings. Missing content receives conservative generic defaults. |

Base workflows require the prescribed picture descriptions: none for T2VA, Picture 1 for I2VA/L2VA, and Pictures 1–2 for FL2VA. Base output keeps video/audio descriptions as ordinary prose because `<Video N>`, `<Audio N>`, and `<Subject N>` labels belong only to ref2VA.

ref2VA picture descriptions must be populated contiguously from Picture 1. The node creates task prefixes from the selected roles, definitions for omitted active labels, and conservative retention markers. Custom section text remains authoritative, but it must use consistent H3 labels. Generic defaults provide valid structure; for production-quality detail, explicitly fill the section fields and all shot descriptions.

The output is rejected with a focused error if references are missing, cuts are invalid, dialogue tags are malformed, or the assembled result violates the bundled H3 guides. There is no LLM repair pass.

## Author

slivik (Smart Resizer header: v1.0.0 initial release).
