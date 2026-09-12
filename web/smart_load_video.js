import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const VIDEO_ACCEPT = [
    "video/webm",
    "video/mp4",
    "video/x-matroska",
    "video/quicktime",
    "video/x-msvideo",
    "image/gif",
    "image/webp",
].join(",");

const IMAGE_PREVIEW_EXT = new Set(["gif", "webp", "avif"]);
const DEFAULT_MAX_UPLOAD = 100 * 1024 * 1024;
const PATH_HINT =
    "This file is larger than ComfyUI's upload limit. Use Browse video path so FFmpeg can read it from disk.";

function extensionOf(filename) {
    const i = String(filename || "").lastIndexOf(".");
    return i >= 0 ? filename.slice(i + 1).toLowerCase() : "";
}

function maxUploadBytes() {
    const flags = api.serverFeatureFlags || api.featureFlags || {};
    const n = Number(flags.max_upload_size);
    return Number.isFinite(n) && n > 0 ? n : DEFAULT_MAX_UPLOAD;
}

function isTooLargeFailure(status, text) {
    const body = String(text || "");
    return status === 413 || /too large|request entity too large|413/i.test(body);
}

function inputViewUrl(filename) {
    const ext = extensionOf(filename);
    const kind = IMAGE_PREVIEW_EXT.has(ext) ? "image" : "video";
    const params = new URLSearchParams({
        filename,
        type: "input",
        format: `${kind}/${ext || "mp4"}`,
        t: String(Date.now()),
    });
    return api.apiURL("/view?" + params);
}

function pathViewUrl(path) {
    const params = new URLSearchParams({
        path,
        t: String(Date.now()),
    });
    return api.apiURL("/smart_tools/load_video/view?" + params);
}

const SOURCE_ARROW = "\u21FD";

function roundToPrecision(num, precision) {
    const strnum = Number(num).toFixed(precision);
    const deci = strnum.indexOf(".");
    if (deci > 0) {
        let i = strnum.length - 1;
        while (i > deci && strnum[i] === "0") i--;
        if (i === deci) i--;
        return strnum.slice(0, i + 1);
    }
    return strnum;
}

function fitText(ctx, text, maxLength) {
    text = String(text);
    if (maxLength <= 0) return ["", 0];
    const fullLength = ctx.measureText(text).width;
    if (fullLength < maxLength) return [text, fullLength];
    const cutoff = ((maxLength / fullLength) * text.length) | 0;
    const shortened = text.slice(0, Math.max(0, cutoff - 2)) + "…";
    return [shortened, ctx.measureText(shortened).width];
}

function widgetReset(widget) {
    if (widget?.options?.reset !== undefined) return widget.options.reset;
    if (widget?.slvReset !== undefined) return widget.slvReset;
    return undefined;
}

function widgetDisable(widget) {
    return widget?.options?.disable ?? 0;
}

function buttonAction(widget) {
    const reset = widgetReset(widget);
    const disable = widgetDisable(widget);
    if (reset === undefined && disable === undefined) {
        return "None";
    }
    if (reset !== undefined && widget.value != reset) {
        return "Reset";
    }
    if (disable !== undefined && widget.value != disable) {
        return "Disable";
    }
    if (reset !== undefined) {
        return "No Reset";
    }
    return "No Disable";
}

function drawAnnotated(ctx, node, widget_width, y, H) {
    const litegraph_base = globalThis.LiteGraph || {};
    const show_text =
        litegraph_base.vueNodesMode ||
        app.canvas.ds.scale >= (app.canvas.low_quality_zoom_threshold ?? 0.5);
    const margin = 15;
    ctx.strokeStyle = litegraph_base.WIDGET_OUTLINE_COLOR || "#666";
    ctx.fillStyle = litegraph_base.WIDGET_BGCOLOR || "#222";
    ctx.beginPath();
    if (show_text) ctx.roundRect(margin, y, widget_width - margin * 2, H, [H * 0.5]);
    else ctx.rect(margin, y, widget_width - margin * 2, H);
    ctx.fill();
    if (!show_text) return;

    if (!this.disabled) ctx.stroke();
    const button = buttonAction(this);
    if (button !== "None") {
        ctx.save();
        const active = !button.startsWith("No ");
        ctx.fillStyle = active
            ? litegraph_base.WIDGET_TEXT_COLOR || "#ddd"
            : litegraph_base.WIDGET_OUTLINE_COLOR || "#666";
        ctx.strokeStyle = ctx.fillStyle;
        ctx.beginPath();
        if (button.endsWith("Reset")) {
            ctx.arc(widget_width - margin - 26, y + H / 2, 4, Math.PI * 1.5, Math.PI);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(widget_width - margin - 26, y + H / 2 - 1.5);
            ctx.lineTo(widget_width - margin - 26, y + H / 2 - 6.5);
            ctx.lineTo(widget_width - margin - 30, y + H / 2 - 3.5);
            ctx.fill();
        } else {
            ctx.arc(widget_width - margin - 26, y + H / 2, 4, (Math.PI * 2) / 3, (Math.PI * 8) / 3);
            ctx.moveTo(widget_width - margin - 26 - 8 ** 0.5, y + H / 2 + 8 ** 0.5);
            ctx.lineTo(widget_width - margin - 26 + 8 ** 0.5, y + H / 2 - 8 ** 0.5);
            ctx.stroke();
        }
        ctx.restore();
    }

    const textColor = litegraph_base.WIDGET_TEXT_COLOR || "#ddd";
    const secondary = litegraph_base.WIDGET_SECONDARY_TEXT_COLOR || "#999";
    ctx.fillStyle = textColor;
    if (!this.disabled) {
        ctx.beginPath();
        ctx.moveTo(margin + 16, y + 5);
        ctx.lineTo(margin + 6, y + H * 0.5);
        ctx.lineTo(margin + 16, y + H - 5);
        ctx.fill();
        ctx.beginPath();
        ctx.moveTo(widget_width - margin - 16, y + 5);
        ctx.lineTo(widget_width - margin - 6, y + H * 0.5);
        ctx.lineTo(widget_width - margin - 16, y + H - 5);
        ctx.fill();
    }

    let freeWidth = widget_width - (40 + margin * 2 + 20);
    const [valueText, valueWidth] = fitText(ctx, this.displayValue?.() ?? "", freeWidth);
    freeWidth -= valueWidth;

    ctx.textAlign = "left";
    ctx.fillStyle = secondary;
    if (freeWidth > 20) {
        const [name, nameWidth] = fitText(ctx, this.label || this.name, freeWidth);
        freeWidth -= nameWidth;
        ctx.fillText(name, margin * 2 + 5, y + H * 0.7);
    }

    let value_offset = margin * 2 + 20;
    ctx.textAlign = "right";
    ctx.fillStyle = textColor;
    ctx.fillText(valueText, widget_width - value_offset, y + H * 0.7);

    let annotation = "";
    if (this.annotation) annotation = this.annotation(this.value, freeWidth) || "";
    if (annotation) {
        ctx.fillStyle = litegraph_base.WIDGET_OUTLINE_COLOR || "#666";
        const [annoDisplay] = fitText(ctx, annotation, freeWidth);
        ctx.fillText(annoDisplay, widget_width - 5 - valueWidth - value_offset, y + H * 0.7);
    }
}

function widgetHit(widget, x, node) {
    const widget_width = widget.width || node.size[0];
    const margin = 15;
    if (x > margin + 6 && x < margin + 16) return -1;
    if (x > widget_width - margin - 16 && x < widget_width - margin - 6) return 1;
    if (x > widget_width - margin - 34 && x < widget_width - margin - 18) return 2;
    return 0;
}

function applyResetOrDisable(widget) {
    const action = buttonAction(widget);
    if (action === "Reset") {
        widget.value = Number(widgetReset(widget));
        return true;
    }
    if (action === "Disable") {
        widget.value = Number(widgetDisable(widget));
        return true;
    }
    return false;
}

function mouseAnnotated(event, [x, y], node) {
    const old_value = this.value;
    const isButton = widgetHit(this, x, node);

    const applyBounds = () => {
        if (this.options.min != null && this.value < this.options.min) this.value = this.options.min;
        if (this.options.max != null && this.value > this.options.max) this.value = this.options.max;
    };

    if (event.type === "pointerup") {
        this._slvIgnoreDrag = false;
    }

    if (event.type === "pointermove") {
        if (!this._slvIgnoreDrag && event.deltaX) {
            this.value += event.deltaX * (this.options.step || 1);
            applyBounds();
        }
    } else if (event.type === "pointerdown") {
        if (isButton === 2) {
            this._slvIgnoreDrag = true;
            applyResetOrDisable(this);
        } else {
            this._slvIgnoreDrag = false;
            this.value += isButton * (this.options.step || 1);
            applyBounds();
        }
    } else if (event.type === "pointerup" && event.click_time < 200 && !isButton) {
        const d_callback = (v) => {
            this.value = Number(v);
            this.callback?.(this.value, app.canvas, node, event);
        };
        app.canvas.prompt("Value", this.value, d_callback, event);
    }

    if (old_value != this.value) {
        setTimeout(() => this.callback?.(this.value, app.canvas, node, event), 20);
    }
    return true;
}

function legalFrameCount(n, frames) {
    if (!n || !frames) return n;
    const div = Number(frames[0]) || 1;
    const mod = Number(frames[1]) || 0;
    let k = n - ((((n - mod) % div) + div) % div);
    if (k > n) k -= div;
    return Math.max(0, k);
}

function makeAnnotated(widget, { integer = false } = {}) {
    if (!widget) return;
    widget.options = widget.options || {};
    widget.options.disable = 0;
    widget.computeSize = (width) => [width, 20];
    widget.displayValue = integer
        ? function () {
              return this.value | 0;
          }
        : function () {
              return roundToPrecision(this.value, this.options.precision ?? 3);
          };
    widget.draw = drawAnnotated;
    widget.mouse = mouseAnnotated;
    widget.type = "SLV.ANNOTATED";
    widget.options.canvasOnly = true;
    widget.onPointerDown = function (pointer, node, canvas) {
        const down = pointer?.eDown;
        const pos = pointerPos(down, node, canvas);
        if (widgetHit(this, pos[0], node) !== 2) return false;
        if (!applyResetOrDisable(this)) return false;
        this._slvIgnoreDrag = true;
        pointer.onDrag = undefined;
        pointer.onClick = undefined;
        setTimeout(() => this.callback?.(this.value, app.canvas, node, down), 20);
        app.graph?.setDirtyCanvas?.(true, true);
        return true;
    };
}

function pointerPos(event, node, canvas) {
    if (event && event.offsetX != null && event.offsetY != null) {
        return [event.offsetX, event.offsetY];
    }
    const mouse = canvas?.graph_mouse;
    if (mouse && node?.pos) {
        return [mouse[0] - node.pos[0], mouse[1] - node.pos[1]];
    }
    return [0, 0];
}

function setWidgetReset(widget, value) {
    if (!widget) return;
    widget.options = widget.options || {};
    if (value == null) {
        delete widget.options.reset;
        delete widget.slvReset;
        return;
    }
    widget.options.reset = value;
    widget.slvReset = value;
}

function createSlvWidget(node, inputName, inputData, integer) {
    const opts = Object.assign({}, inputData?.[1] || {});
    const widget = {
        name: inputName,
        type: "SLV.ANNOTATED",
        value: opts.default ?? 0,
        options: opts,
        config: inputData,
        callback(v) {
            if (this.options.max != null && v > this.options.max) v = this.options.max;
            if (this.options.min != null && v < this.options.min) v = this.options.min;
            this.value = integer ? Math.round(v) : v;
        },
    };
    makeAnnotated(widget, { integer });
    if (!node.widgets) node.widgets = [];
    node.widgets.push(widget);
    return widget;
}

function replaceNumberWidget(node, widget, integer) {
    if (!widget) return widget;
    if (widget.type === "SLV.ANNOTATED" && widget.mouse === mouseAnnotated) {
        makeAnnotated(widget, { integer });
        return widget;
    }
    const replacement = {
        name: widget.name,
        type: "SLV.ANNOTATED",
        value: widget.value,
        options: Object.assign({}, widget.options),
        config: widget.config,
        callback: widget.callback,
        label: widget.label,
    };
    makeAnnotated(replacement, { integer });
    const idx = node.widgets?.indexOf(widget) ?? -1;
    if (idx >= 0) node.widgets[idx] = replacement;
    return replacement;
}

async function uploadVideo(file) {
    if (file.size > maxUploadBytes()) {
        throw new Error(PATH_HINT);
    }
    const body = new FormData();
    body.append("image", file);
    body.append("overwrite", "true");
    const resp = await api.fetchApi("/upload/image", { method: "POST", body });
    const text = await resp.text();
    if (!resp.ok) {
        if (isTooLargeFailure(resp.status, text)) {
            throw new Error(PATH_HINT);
        }
        throw new Error(`Upload failed (${resp.status}): ${text}`);
    }
    const data = JSON.parse(text);
    return data.name || file.name;
}

const ANNOTATED_WIDGETS = new Set([
    "force_rate",
    "custom_width",
    "custom_height",
    "frame_load_cap",
]);

app.registerExtension({
    name: "SmartTools.SmartLoadVideo",

    getCustomWidgets() {
        return {
            SLVFLOAT(node, inputName, inputData) {
                return createSlvWidget(node, inputName, inputData, false);
            },
            SLVINT(node, inputName, inputData) {
                return createSlvWidget(node, inputName, inputData, true);
            },
        };
    },

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "SmartLoadVideo") return;

        for (const [name, inp] of Object.entries({
            ...(nodeData.input?.required || {}),
            ...(nodeData.input?.optional || {}),
        })) {
            if (!ANNOTATED_WIDGETS.has(name) || !inp) continue;
            if (!inp[1]) inp[1] = {};
            inp[1].widgetType = inp[0] === "FLOAT" ? "SLVFLOAT" : "SLVINT";
        }

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);

            const self = this;
            const getWidget = (name) => self.widgets?.find((w) => w.name === name);
            const videoWidget = getWidget("video");
            const pathWidget = getWidget("video_path");
            const rateWidget = replaceNumberWidget(this, getWidget("force_rate"), false);
            const widthWidget = replaceNumberWidget(this, getWidget("custom_width"), true);
            const heightWidget = replaceNumberWidget(this, getWidget("custom_height"), true);
            const multipleWidget = getWidget("multiple");
            const formatWidget = getWidget("format");
            const capWidget = replaceNumberWidget(this, getWidget("frame_load_cap"), true);
            const formatMap =
                nodeData?.input?.required?.format?.[1]?.formats || {};

            const widgetBases = {};
            for (const widget of [rateWidget, widthWidget, heightWidget, capWidget]) {
                if (widget) widgetBases[widget.name] = { ...widget.options };
            }

            function currentFormat() {
                return formatMap[formatWidget?.value] || {};
            }

            function applyFormat() {
                const format = currentFormat();
                const source = self.video_query?.source;
                if (rateWidget) {
                    const wasDefault = widgetReset(rateWidget) == rateWidget.value;
                    rateWidget.options = { ...widgetBases.force_rate, canvasOnly: true };
                    rateWidget.options.disable = 0;
                    if (format.target_rate) setWidgetReset(rateWidget, format.target_rate);
                    else if (source?.fps) setWidgetReset(rateWidget, source.fps);
                    else setWidgetReset(rateWidget, undefined);
                    if (wasDefault && widgetReset(rateWidget) != null) {
                        rateWidget.value = widgetReset(rateWidget);
                    }
                }
                if (widthWidget || heightWidget) {
                    const dim = format.dim;
                    for (const widget of [widthWidget, heightWidget]) {
                        if (!widget) continue;
                        const wasDefault = widgetReset(widget) == widget.value;
                        widget.options = { ...widgetBases[widget.name], canvasOnly: true };
                        widget.options.disable = 0;
                        if (dim) {
                            widget.options.step = dim[0] || widget.options.step;
                            if (dim[1] != null) widget.options.mod = dim[1];
                        }
                        if (widget === widthWidget) {
                            setWidgetReset(widget, dim?.[2] || undefined);
                        }
                        if (widget === heightWidget) {
                            setWidgetReset(widget, dim?.[3] || undefined);
                        }
                        if (wasDefault && widgetReset(widget) != null) {
                            widget.value = widgetReset(widget);
                        }
                    }
                    if (dim?.[0] && multipleWidget) {
                        multipleWidget.value = dim[0];
                    }
                }
                if (capWidget) {
                    capWidget.options = { ...widgetBases.frame_load_cap, canvasOnly: true };
                    capWidget.options.disable = 0;
                    if (format.frames) {
                        capWidget.options.step = format.frames[0];
                        capWidget.options.mod = format.frames[1];
                    }
                    const rawFrames = source?.frames;
                    if (rawFrames) {
                        setWidgetReset(
                            capWidget,
                            format.frames ? legalFrameCount(rawFrames, format.frames) : rawFrames
                        );
                    } else {
                        setWidgetReset(capWidget, undefined);
                    }
                }
                app.graph?.setDirtyCanvas?.(true, true);
            }

            if (rateWidget) {
                rateWidget.annotation = function (value) {
                    if (value != 0) return;
                    const reset = widgetReset(this);
                    if (reset != null && reset != 0) {
                        return roundToPrecision(reset, 2) + SOURCE_ARROW;
                    }
                    const fps = self.video_query?.source?.fps;
                    if (fps != null) return roundToPrecision(fps, 2) + SOURCE_ARROW;
                };
            }
            if (widthWidget) {
                widthWidget.annotation = function (value) {
                    const reset = widgetReset(this);
                    if (value == 0 && reset) return reset + SOURCE_ARROW;
                };
            }
            if (heightWidget) {
                heightWidget.annotation = function (value) {
                    const reset = widgetReset(this);
                    if (value == 0 && reset) return reset + SOURCE_ARROW;
                };
            }
            if (capWidget) {
                capWidget.annotation = function (value) {
                    const reset = widgetReset(this);
                    if (reset != null && (!value || value >= reset)) {
                        return reset + SOURCE_ARROW;
                    }
                    const maxFrames = self.video_query?.source?.frames;
                    if (!maxFrames) return;
                    const legal = legalFrameCount(maxFrames, currentFormat().frames);
                    if (value && value < legal) return;
                    return legal + SOURCE_ARROW;
                };
            }

            const fileInput = document.createElement("input");
            Object.assign(fileInput, {
                type: "file",
                accept: VIDEO_ACCEPT,
                style: "display: none",
            });
            fileInput.addEventListener("change", async () => {
                const file = fileInput.files?.[0];
                fileInput.value = "";
                if (!file) return;
                try {
                    const filename = await uploadVideo(file);
                    const values = videoWidget?.options?.values;
                    if (Array.isArray(values) && !values.includes(filename)) {
                        values.push(filename);
                    }
                    if (pathWidget) {
                        pathWidget.value = "";
                    }
                    if (videoWidget) {
                        videoWidget.value = filename;
                        videoWidget.callback?.(filename);
                    }
                    self.updatePreview?.();
                } catch (err) {
                    alert(err?.message || String(err));
                }
            });
            document.body.append(fileInput);
            const prevOnRemoved = this.onRemoved;
            this.onRemoved = function () {
                fileInput.remove();
                return prevOnRemoved?.apply(this, arguments);
            };

            const uploadWidget = this.addWidget("button", "choose video to upload", "image", () => {
                app.canvas.node_widget = null;
                fileInput.click();
            });
            uploadWidget.options.serialize = false;

            const browseWidget = this.addWidget("button", "browse video path", "image", async () => {
                app.canvas.node_widget = null;
                try {
                    const resp = await api.fetchApi("/smart_tools/load_video/browse_file", {
                        method: "POST",
                    });
                    if (resp.status === 204) return;
                    if (!resp.ok) {
                        throw new Error(`Browse failed (${resp.status}): ${await resp.text()}`);
                    }
                    const data = await resp.json();
                    if (!data?.path || !pathWidget) return;
                    pathWidget.value = data.path;
                    pathWidget.callback?.(data.path);
                    self.updatePreview?.();
                } catch (err) {
                    alert(err?.message || String(err));
                }
            });
            browseWidget.options.serialize = false;

            const previewRoot = document.createElement("div");
            previewRoot.style.width = "100%";
            const videoEl = document.createElement("video");
            videoEl.controls = true;
            videoEl.loop = true;
            videoEl.muted = true;
            videoEl.playsInline = true;
            videoEl.style.width = "100%";
            videoEl.style.display = "block";
            const imgEl = document.createElement("img");
            imgEl.style.width = "100%";
            imgEl.style.display = "none";
            previewRoot.appendChild(videoEl);
            previewRoot.appendChild(imgEl);

            let fittingHeight = false;
            function fitHeight() {
                if (fittingHeight) return;
                fittingHeight = true;
                try {
                    const w = self.size?.[0];
                    if (!(w > 0) || typeof self.computeSize !== "function") return;
                    const next = self.computeSize([w, self.size[1]]);
                    if (Array.isArray(next) && next[1] > 0) {
                        self.setSize([w, next[1]]);
                    }
                    app.graph?.setDirtyCanvas?.(true, true);
                } catch (_) {
                } finally {
                    fittingHeight = false;
                }
            }

            const previewWidget = this.addDOMWidget("videopreview", "preview", previewRoot, {
                serialize: false,
                hideOnZoom: false,
            });
            previewWidget.aspectRatio = 0;
            previewWidget.computeSize = function (width) {
                const w = width || self.size?.[0] || 210;
                if (!this.aspectRatio) {
                    return [w, 0];
                }
                const height = (w - 20) / this.aspectRatio + 10;
                return [w, Math.max(80, height)];
            };

            videoEl.addEventListener("loadedmetadata", () => {
                if (videoEl.videoWidth > 0 && videoEl.videoHeight > 0) {
                    previewWidget.aspectRatio = videoEl.videoWidth / videoEl.videoHeight;
                    fitHeight();
                }
            });
            imgEl.addEventListener("load", () => {
                if (imgEl.naturalWidth > 0 && imgEl.naturalHeight > 0) {
                    previewWidget.aspectRatio = imgEl.naturalWidth / imgEl.naturalHeight;
                    fitHeight();
                }
            });

            this.updatePreview = function () {
                const diskPath = String(pathWidget?.value || "").trim();
                const name = diskPath || videoWidget?.value;
                if (!name) {
                    videoEl.removeAttribute("src");
                    imgEl.removeAttribute("src");
                    videoEl.style.display = "none";
                    imgEl.style.display = "none";
                    previewWidget.aspectRatio = 0;
                    self.video_query = null;
                    applyFormat();
                    fitHeight();
                    return;
                }
                const url = diskPath ? pathViewUrl(diskPath) : inputViewUrl(name);
                const ext = extensionOf(name);
                if (IMAGE_PREVIEW_EXT.has(ext)) {
                    videoEl.pause();
                    videoEl.removeAttribute("src");
                    videoEl.style.display = "none";
                    imgEl.style.display = "block";
                    imgEl.src = url;
                } else {
                    imgEl.removeAttribute("src");
                    imgEl.style.display = "none";
                    videoEl.style.display = "block";
                    videoEl.src = url;
                }
                self.refreshVideoQuery?.();
            };

            this.refreshVideoQuery = async function () {
                const diskPath = String(pathWidget?.value || "").trim();
                const filename = videoWidget?.value;
                if (!diskPath && !filename) {
                    self.video_query = null;
                    return;
                }
                const params = diskPath
                    ? { path: diskPath }
                    : { filename, type: "input" };
                try {
                    const resp = await api.fetchApi(
                        "/smart_tools/load_video/query?" + new URLSearchParams(params)
                    );
                    const data = resp.ok ? await resp.json() : {};
                    self.video_query = data?.source ? data : null;
                    applyFormat();
                } catch (_) {
                    self.video_query = null;
                }
            };

            if (videoWidget) {
                const original = videoWidget.callback;
                videoWidget.callback = function (value) {
                    const result = original?.call(this, value);
                    self.updatePreview();
                    return result;
                };
            }
            if (pathWidget) {
                const original = pathWidget.callback;
                pathWidget.callback = function (value) {
                    const result = original?.call(this, value);
                    self.updatePreview();
                    return result;
                };
            }
            if (formatWidget) {
                const original = formatWidget.callback;
                formatWidget.callback = function (value) {
                    const result = original?.call(this, value);
                    applyFormat();
                    return result;
                };
            }

            this.onDragOver = (e) => !!e?.dataTransfer?.types?.includes?.("Files");
            this.onDragDrop = async function (e) {
                const file = e?.dataTransfer?.files?.[0];
                if (!file) return false;
                try {
                    const filename = await uploadVideo(file);
                    const values = videoWidget?.options?.values;
                    if (Array.isArray(values) && !values.includes(filename)) {
                        values.push(filename);
                    }
                    if (pathWidget) {
                        pathWidget.value = "";
                    }
                    if (videoWidget) {
                        videoWidget.value = filename;
                        videoWidget.callback?.(filename);
                    }
                    return true;
                } catch (err) {
                    alert(err?.message || String(err));
                    return false;
                }
            };

            applyFormat();
            this.updatePreview();
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            this.updatePreview?.();
            return result;
        };
    },
});
