import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
    name: "SmartTools.SmartResizerV2",

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== "SmartResizerV2Node") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);

            const self = this;
            const getWidget = (name) => self.widgets?.find((w) => w.name === name);

            function setWidgetHidden(widget, hidden) {
                if (!widget) return;
                if (hidden) {
                    if (widget.__rsz2Hidden) return;
                    widget.__rsz2Hidden = true;
                    widget.__rsz2OrigType = widget.type;
                    widget.__rsz2OrigComputeSize = widget.computeSize;
                    widget.type = "hidden";
                    widget.hidden = true;
                    widget.computeSize = () => [0, -4];
                } else if (widget.__rsz2Hidden) {
                    widget.type = widget.__rsz2OrigType;
                    widget.computeSize = widget.__rsz2OrigComputeSize;
                    widget.hidden = false;
                    delete widget.__rsz2Hidden;
                    delete widget.__rsz2OrigType;
                    delete widget.__rsz2OrigComputeSize;
                }
            }

            function updateSizeModeVisibility() {
                const mode = getWidget("size_mode")?.value || "Dimensions";
                setWidgetHidden(getWidget("width"), mode !== "Dimensions");
                setWidgetHidden(getWidget("height"), mode !== "Dimensions");
                setWidgetHidden(getWidget("megapixels"), mode !== "Megapixels");
                setWidgetHidden(getWidget("shortest"), mode !== "Shortest");
                setWidgetHidden(getWidget("longest"), mode !== "Longest");
                try {
                    if (typeof self.computeSize === "function") {
                        const size = self.computeSize(self.size);
                        if (Array.isArray(size)) self.setSize?.(size);
                    }
                    app.graph?.setDirtyCanvas?.(true, true);
                } catch (_) {}
            }

            // Show Size Mode first without changing saved widget order in Python.
            const modeWidget = getWidget("size_mode");
            const widthWidget = getWidget("width");
            if (modeWidget && widthWidget && Array.isArray(this.widgets)) {
                const modeIdx = this.widgets.indexOf(modeWidget);
                const widthIdx = this.widgets.indexOf(widthWidget);
                if (modeIdx > -1 && widthIdx > -1 && modeIdx !== widthIdx) {
                    this.widgets.splice(modeIdx, 1);
                    this.widgets.splice(widthIdx, 0, modeWidget);
                }
            }
            if (modeWidget) {
                const originalModeCallback = modeWidget.callback;
                modeWidget.callback = function (value) {
                    const result = originalModeCallback?.call(this, value);
                    updateSizeModeVisibility();
                    return result;
                };
            }
            setTimeout(updateSizeModeVisibility, 0);

            const onConfigure = this.onConfigure;
            this.onConfigure = function () {
                onConfigure?.apply(this, arguments);
                updateSizeModeVisibility();
            };

            const root = document.createElement("div");
            root.className = "smart_resizer_v2_preview";
            root.style.cssText = "width:100%;display:flex;flex-direction:column;gap:4px;";

            const dimsEl = document.createElement("div");
            dimsEl.style.cssText =
                "font-size:11px;opacity:0.85;line-height:1.35;user-select:none;padding:2px 0;";
            dimsEl.textContent = "Input: —  |  Output: —";

            const imgEl = document.createElement("img");
            imgEl.style.cssText =
                "width:100%;display:none;background:#111;object-fit:contain;max-height:512px;";
            imgEl.alt = "Smart Resizer v2 preview";

            root.appendChild(dimsEl);
            root.appendChild(imgEl);

            const previewWidget = this.addDOMWidget("rsz2preview", "preview", root, {
                serialize: false,
                hideOnZoom: false,
            });
            previewWidget.aspectRatio = null;
            previewWidget.computeSize = function (width) {
                if (this.aspectRatio && imgEl.src && imgEl.style.display !== "none") {
                    const height = (self.size[0] - 20) / this.aspectRatio + 28;
                    this.computedHeight = Math.max(48, height);
                    return [width, this.computedHeight];
                }
                // Dims-only row when no image yet.
                this.computedHeight = 28;
                return [width, this.computedHeight];
            };

            function refreshSize() {
                try {
                    const size = self.computeSize?.(self.size);
                    if (Array.isArray(size)) self.setSize?.(size);
                    app.graph?.setDirtyCanvas?.(true, true);
                } catch (_) {}
            }

            imgEl.addEventListener("load", () => {
                if (imgEl.naturalWidth > 0 && imgEl.naturalHeight > 0) {
                    previewWidget.aspectRatio = imgEl.naturalWidth / imgEl.naturalHeight;
                }
                refreshSize();
            });
            imgEl.addEventListener("error", () => {
                previewWidget.aspectRatio = null;
                imgEl.style.display = "none";
                refreshSize();
            });

            function firstNum(val) {
                if (Array.isArray(val)) return val[0];
                return val;
            }

            function setDims(inputW, inputH, outputW, outputH) {
                const iw = firstNum(inputW);
                const ih = firstNum(inputH);
                const ow = firstNum(outputW);
                const oh = firstNum(outputH);
                const inTxt =
                    iw != null && ih != null ? `${iw}×${ih}` : "—";
                const outTxt =
                    ow != null && oh != null ? `${ow}×${oh}` : "—";
                dimsEl.textContent = `Input: ${inTxt}  |  Output: ${outTxt}`;
            }

            function setPreviewImage(info) {
                if (!info?.filename) {
                    imgEl.removeAttribute("src");
                    imgEl.style.display = "none";
                    previewWidget.aspectRatio = null;
                    refreshSize();
                    return;
                }
                const params = new URLSearchParams({
                    filename: info.filename,
                    type: info.type || "temp",
                    subfolder: info.subfolder || "",
                    t: String(Date.now()),
                });
                imgEl.src = api.apiURL(`/view?${params.toString()}`);
                imgEl.style.display = "block";
            }

            const _origOnExecuted = this.onExecuted;
            this.onExecuted = function (output) {
                // Suppress ComfyUI's default OUTPUT_NODE image preview; we use our own widget.
                this.imgs = undefined;
                this.imageIndex = null;
                _origOnExecuted?.call(this, output);
                this.imgs = undefined;
                this.imageIndex = null;
                // Extra: some frontends keep a media preview from ui.images — we never send that key.

                setDims(
                    output?.input_width,
                    output?.input_height,
                    output?.output_width,
                    output?.output_height
                );

                const images = output?.rsz2_images;
                const info = Array.isArray(images) ? images[0] : null;
                setPreviewImage(info);
            };

            // Prevent default canvas image drawing if imgs ever get set.
            const _origOnDrawBackground = this.onDrawBackground;
            this.onDrawBackground = function (ctx) {
                this.imgs = undefined;
                this.imageIndex = null;
                return _origOnDrawBackground?.call(this, ctx);
            };
        };
    },
});
