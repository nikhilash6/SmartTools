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

app.registerExtension({
    name: "SmartTools.SmartLoadVideo",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "SmartLoadVideo") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);

            const self = this;
            const getWidget = (name) => self.widgets?.find((w) => w.name === name);
            const videoWidget = getWidget("video");
            const pathWidget = getWidget("video_path");

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
