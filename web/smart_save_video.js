import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
    name: "SmartTools.SmartSaveVideo",

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== "SmartSaveVideo") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);

            const self = this;
            const triggerWidget = this.widgets?.find((w) => w.name === "save_trigger");
            if (triggerWidget) {
                triggerWidget.type = "hidden";
                triggerWidget.computeSize = () => [0, -4];
            }

            const getWidget = (name) => self.widgets?.find((w) => w.name === name);
            const isAutoSave = () => getWidget("autosave")?.value ?? false;
            const getPrefix = () => getWidget("filename_prefix")?.value ?? "SmartVideo";
            const getTimestamp = () => getWidget("use_timestamp")?.value ?? false;

            const FORMAT_OPTION_WIDGETS = [
                "crf",
                "pix_fmt",
                "bitrate",
                "megabit",
                "save_metadata",
                "trim_to_audio",
                "profile",
                "input_color_depth",
                "gop_size",
            ];
            const formatWidgetMeta =
                nodeData?.input?.required?.format?.[1]?.format_widgets || {};

            function setWidgetHidden(widget, hidden) {
                if (!widget) return;
                if (hidden) {
                    if (widget.__ssvHidden) return;
                    widget.__ssvHidden = true;
                    widget.__ssvOrigType = widget.type;
                    widget.__ssvOrigComputeSize = widget.computeSize;
                    widget.type = "hidden";
                    widget.computeSize = () => [0, -4];
                } else if (widget.__ssvHidden) {
                    widget.type = widget.__ssvOrigType;
                    widget.computeSize = widget.__ssvOrigComputeSize;
                    delete widget.__ssvHidden;
                    delete widget.__ssvOrigType;
                    delete widget.__ssvOrigComputeSize;
                }
            }

            function updateFormatOptionVisibility() {
                const formatWidget = getWidget("format");
                const selected = formatWidget?.value;
                const needed = new Set(formatWidgetMeta[selected] || []);
                for (const name of FORMAT_OPTION_WIDGETS) {
                    setWidgetHidden(getWidget(name), !needed.has(name));
                }
                try {
                    if (typeof self.computeSize === "function") {
                        const size = self.computeSize(self.size);
                        if (Array.isArray(size)) self.setSize?.(size);
                    }
                    app.graph?.setDirtyCanvas?.(true, true);
                } catch (_) {}
            }

            const formatWidget = getWidget("format");
            if (formatWidget) {
                const originalFormatCallback = formatWidget.callback;
                formatWidget.callback = function (value) {
                    const result = originalFormatCallback?.call(this, value);
                    updateFormatOptionVisibility();
                    return result;
                };
            }
            // Apply after widgets exist.
            setTimeout(updateFormatOptionVisibility, 0);

            function showToast(message, isError = false) {
                const existing = document.getElementById("smart_save_video_toast");
                if (existing) existing.remove();
                const toast = document.createElement("div");
                toast.id = "smart_save_video_toast";
                toast.style.cssText = `
                    position: fixed; bottom: 30px; right: 30px;
                    background: ${isError ? "#c0392b" : "#1a6b4a"};
                    color: white; padding: 12px 20px; border-radius: 8px;
                    font-size: 14px; font-family: sans-serif; z-index: 99999;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.4); max-width: 420px;
                    word-break: break-all; transition: opacity 0.4s ease;
                `;
                toast.textContent = message;
                document.body.appendChild(toast);
                setTimeout(() => {
                    toast.style.opacity = "0";
                    setTimeout(() => toast.remove(), 400);
                }, 3500);
            }

            const HISTORY_KEY = "smart_save_video_history";
            const MAX_HISTORY = 50;

            function loadHistory() {
                try {
                    return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
                } catch {
                    return [];
                }
            }

            function addToHistory(entry) {
                const history = loadHistory();
                history.unshift(entry);
                if (history.length > MAX_HISTORY) history.pop();
                localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
            }

            async function loadFavorites() {
                try {
                    const res = await api.fetchApi("/smart_tools/save_it/favorites");
                    return res.ok ? await res.json() : [];
                } catch {
                    return [];
                }
            }

            async function saveFavorites(folders) {
                await api.fetchApi("/smart_tools/save_it/favorites", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ folders }),
                });
            }

            function showFavoritesDialog() {
                const existing = document.getElementById("smart_save_video_fav_dialog");
                if (existing) {
                    existing.remove();
                    return;
                }
                const overlay = document.createElement("div");
                overlay.id = "smart_save_video_fav_dialog";
                overlay.style.cssText = `
                    position: fixed; inset: 0; background: rgba(0,0,0,0.6);
                    z-index: 99998; display: flex; align-items: center; justify-content: center;
                `;
                const dialog = document.createElement("div");
                dialog.style.cssText = `
                    background: #1e2a2a; border: 1px solid #2a9d8f; border-radius: 10px;
                    padding: 20px; width: 460px; max-width: 95vw; max-height: 90vh;
                    display: flex; flex-direction: column; color: white; font-family: sans-serif;
                `;
                dialog.innerHTML = `
                    <h3 style="margin:0 0 10px;color:#2a9d8f;">⭐ Favorite Folders</h3>
                    <div id="ssv_fav_list" style="flex:1;overflow-y:auto;margin-bottom:12px;min-height:60px;"></div>
                    <div style="display:flex;gap:8px;margin-bottom:14px;">
                        <input id="ssv_fav_input" type="text" placeholder="e.g. Projects/Videos/_"
                            style="flex:1;padding:7px 10px;border-radius:6px;border:1px solid #2a9d8f;
                                   background:#0d1f1f;color:white;font-size:13px;outline:none;" />
                        <button id="ssv_fav_add"
                            style="padding:7px 14px;background:#2a9d8f;color:white;border:none;
                                   border-radius:6px;cursor:pointer;font-size:13px;">Add</button>
                    </div>
                    <div style="display:flex;justify-content:flex-end;">
                        <button id="ssv_fav_close"
                            style="padding:5px 12px;background:#555;color:white;border:none;
                                   border-radius:6px;cursor:pointer;">Close</button>
                    </div>
                `;
                overlay.appendChild(dialog);
                document.body.appendChild(overlay);

                async function renderFavorites() {
                    const favs = await loadFavorites();
                    const listDiv = dialog.querySelector("#ssv_fav_list");
                    if (!favs.length) {
                        listDiv.innerHTML = `<div style="text-align:center;color:#666;padding:20px;">No favorite folders yet</div>`;
                        return;
                    }
                    listDiv.innerHTML = "";
                    favs.forEach((folder) => {
                        const row = document.createElement("div");
                        row.style.cssText = `
                            display:flex;align-items:center;gap:8px;padding:6px 8px;
                            background:#0d1f1f;border-radius:6px;margin-bottom:6px;border:1px solid #2a5d54;
                        `;
                        row.innerHTML = `
                            <span style="flex:1;font-size:13px;color:#ccc;word-break:break-all;">${folder}</span>
                            <button class="ssv_fav_set" data-folder="${folder}"
                                style="padding:4px 10px;background:#2a9d8f;color:white;border:none;
                                       border-radius:4px;cursor:pointer;font-size:12px;">Set</button>
                            <button class="ssv_fav_del" data-folder="${folder}"
                                style="padding:4px 8px;background:#c0392b;color:white;border:none;
                                       border-radius:4px;cursor:pointer;font-size:12px;">✕</button>
                        `;
                        listDiv.appendChild(row);
                    });
                    listDiv.querySelectorAll(".ssv_fav_set").forEach((btn) => {
                        btn.addEventListener("click", () => {
                            const folder = btn.getAttribute("data-folder");
                            const pw = getWidget("filename_prefix");
                            if (pw) pw.value = folder;
                            showToast(`📁 Path set: ${folder}`);
                            overlay.remove();
                        });
                    });
                    listDiv.querySelectorAll(".ssv_fav_del").forEach((btn) => {
                        btn.addEventListener("click", async () => {
                            const folder = btn.getAttribute("data-folder");
                            const next = (await loadFavorites()).filter((f) => f !== folder);
                            await saveFavorites(next);
                            renderFavorites();
                        });
                    });
                }

                renderFavorites();
                dialog.querySelector("#ssv_fav_add").addEventListener("click", async () => {
                    const input = dialog.querySelector("#ssv_fav_input");
                    const val = input.value.trim();
                    if (!val) return;
                    const favs = await loadFavorites();
                    if (favs.includes(val)) {
                        showToast("Already in favorites.");
                    } else {
                        favs.push(val);
                        await saveFavorites(favs);
                        showToast(`⭐ Added: ${val}`);
                        input.value = "";
                        renderFavorites();
                    }
                });
                dialog.querySelector("#ssv_fav_close").addEventListener("click", () => overlay.remove());
                overlay.addEventListener("click", (e) => {
                    if (e.target === overlay) overlay.remove();
                });
            }

            function showHistoryDialog() {
                const existing = document.getElementById("smart_save_video_hist_dialog");
                if (existing) {
                    existing.remove();
                    return;
                }
                const overlay = document.createElement("div");
                overlay.id = "smart_save_video_hist_dialog";
                overlay.style.cssText = `
                    position: fixed; inset: 0; background: rgba(0,0,0,0.6);
                    z-index: 99998; display: flex; align-items: center; justify-content: center;
                `;
                const history = loadHistory();
                const items = history.length
                    ? history
                          .map(
                              (h) => `
                        <div style="padding:8px;background:#0d1f1f;border-radius:6px;margin-bottom:6px;">
                            <div style="font-size:13px;color:#2a9d8f;font-weight:bold;">${h.filename}</div>
                            <div style="font-size:11px;color:#888;margin-top:2px;">${h.path}</div>
                            <div style="font-size:10px;color:#555;margin-top:2px;">${h.time}</div>
                        </div>`
                          )
                          .join("")
                    : `<div style="text-align:center;color:#666;padding:20px;">No save history yet.</div>`;
                const dialog = document.createElement("div");
                dialog.style.cssText = `
                    background: #1e2a2a; border: 1px solid #2a9d8f; border-radius: 10px;
                    padding: 20px; width: 500px; max-width: 95vw; max-height: 80vh;
                    display: flex; flex-direction: column; color: white; font-family: sans-serif;
                `;
                dialog.innerHTML = `
                    <h3 style="margin:0 0 10px;color:#2a9d8f;">📋 Save History</h3>
                    <div style="flex:1;overflow-y:auto;margin-bottom:12px;">${items}</div>
                    <div style="display:flex;justify-content:space-between;gap:8px;">
                        <button id="ssv_hist_clear"
                            style="padding:5px 12px;background:#c0392b;color:white;border:none;
                                   border-radius:6px;cursor:pointer;">Clear History</button>
                        <button id="ssv_hist_close"
                            style="padding:5px 12px;background:#555;color:white;border:none;
                                   border-radius:6px;cursor:pointer;">Close</button>
                    </div>
                `;
                overlay.appendChild(dialog);
                document.body.appendChild(overlay);
                dialog.querySelector("#ssv_hist_clear").addEventListener("click", () => {
                    localStorage.removeItem(HISTORY_KEY);
                    showToast("History cleared.");
                    overlay.remove();
                });
                dialog.querySelector("#ssv_hist_close").addEventListener("click", () => overlay.remove());
                overlay.addEventListener("click", (e) => {
                    if (e.target === overlay) overlay.remove();
                });
            }

            function bringAppToFront() {
                try {
                    window.focus();
                } catch (_) {}
            }

            function showAddToFavoritesPrompt(path) {
                const existing = document.getElementById("ssv_addfav_prompt");
                if (existing) existing.remove();
                const prompt = document.createElement("div");
                prompt.id = "ssv_addfav_prompt";
                prompt.style.cssText = `
                    position: fixed; bottom: 80px; right: 30px; background: #1e2a2a;
                    border: 1px solid #2a9d8f; border-radius: 8px; padding: 14px 18px;
                    font-size: 13px; font-family: sans-serif; color: white; z-index: 99999;
                    box-shadow: 0 4px 16px rgba(0,0,0,0.5); max-width: 420px; word-break: break-all;
                `;
                prompt.innerHTML = `
                    <div style="margin-bottom:10px;">
                        <span style="color:#2a9d8f;font-weight:bold;">⭐ Add to Favorites?</span><br>
                        <span style="color:#ccc;font-size:12px;">${path}</span>
                    </div>
                    <div style="display:flex;gap:8px;">
                        <button id="ssv_addfav_yes"
                            style="flex:1;padding:6px;background:#2a9d8f;color:white;border:none;
                                   border-radius:6px;cursor:pointer;font-size:13px;">⭐ Add to Favorites</button>
                        <button id="ssv_addfav_no"
                            style="padding:6px 12px;background:#555;color:white;border:none;
                                   border-radius:6px;cursor:pointer;font-size:13px;">Not now</button>
                    </div>
                `;
                document.body.appendChild(prompt);
                const dismiss = () => prompt.remove();
                prompt.querySelector("#ssv_addfav_yes").addEventListener("click", async () => {
                    const favs = await loadFavorites();
                    if (!favs.includes(path)) {
                        favs.push(path);
                        await saveFavorites(favs);
                        showToast(`⭐ Added to favorites: ${path}`);
                    } else {
                        showToast("Already in favorites.");
                    }
                    dismiss();
                });
                prompt.querySelector("#ssv_addfav_no").addEventListener("click", dismiss);
                setTimeout(dismiss, 15000);
            }

            async function doSave() {
                const video = self.currentVideo;
                if (!video?.filename) {
                    showToast("No video to save. Please run the workflow first.", true);
                    return;
                }
                try {
                    const response = await api.fetchApi("/smart_tools/save_video/save", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            filename: video.filename,
                            subfolder: video.subfolder || "",
                            type: video.type || "temp",
                            filename_prefix: getPrefix(),
                            use_timestamp: getTimestamp(),
                        }),
                    });
                    if (response.ok) {
                        const msg = await response.text();
                        const savedPath = msg.replace("Saved to ", "");
                        const savedFilename = savedPath.split(/[\\/]/).pop();
                        addToHistory({
                            filename: savedFilename,
                            path: savedPath,
                            time: new Date().toLocaleString(),
                        });
                        showToast(`✅ Saved: ${savedFilename}`);
                    } else {
                        showToast(`❌ Save failed: ${await response.text()}`, true);
                    }
                } catch (e) {
                    showToast(`❌ Error: ${e.message}`, true);
                }
            }

            const browseBtn = this.addWidget("button", "📁  Browse & Set Save Path", null, async () => {
                bringAppToFront();
                await new Promise((resolve) => setTimeout(resolve, 150));
                try {
                    const response = await api.fetchApi("/smart_tools/save_it/browse_folder", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({}),
                    });
                    bringAppToFront();
                    if (response.status === 204) return;
                    if (!response.ok) {
                        showToast(`❌ Browse failed: ${await response.text()}`, true);
                        return;
                    }
                    const data = await response.json();
                    const selectedPath = data?.path;
                    if (!selectedPath) return;
                    const pw = getWidget("filename_prefix");
                    if (pw) {
                        pw.value = selectedPath;
                        try {
                            pw.callback?.(selectedPath);
                        } catch (_) {}
                        try {
                            app.graph?.setDirtyCanvas?.(true, true);
                        } catch (_) {}
                    }
                    showToast(`📁 Path set: ${selectedPath}`);
                    showAddToFavoritesPrompt(selectedPath);
                } catch (e) {
                    showToast(`❌ Error: ${e.message}`, true);
                }
            });
            browseBtn.serialize = false;

            const saveBtn = this.addWidget("button", "💾  Save Video", null, async () => {
                if (isAutoSave()) return;
                await doSave();
            });
            saveBtn.serialize = false;

            const folderBtn = this.addWidget("button", "📂  Open Output Folder", null, async () => {
                bringAppToFront();
                try {
                    const response = await api.fetchApi("/smart_tools/save_it/open_folder", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ filename_prefix: getPrefix() }),
                    });
                    if (!response.ok) {
                        showToast(`❌ Could not open folder: ${await response.text()}`, true);
                    }
                } catch (e) {
                    showToast(`❌ Error: ${e.message}`, true);
                }
            });
            folderBtn.serialize = false;

            const historyBtn = this.addWidget("button", "📋  Save History", null, () => {
                showHistoryDialog();
            });
            historyBtn.serialize = false;

            const favBtn = this.addWidget("button", "⭐  Favorite Folders", null, () => {
                showFavoritesDialog();
            });
            favBtn.serialize = false;

            const autosaveWidget = getWidget("autosave");
            if (autosaveWidget) {
                const originalCallback = autosaveWidget.callback;
                autosaveWidget.callback = function (value) {
                    originalCallback?.call(this, value);
                    saveBtn.disabled = !!value;
                };
                saveBtn.disabled = !!autosaveWidget.value;
            }

            // Interactive video preview: single player, or New | Original side-by-side synced.
            const previewRoot = document.createElement("div");
            previewRoot.className = "smart_save_video_preview";
            previewRoot.style.cssText = "width:100%;";

            const row = document.createElement("div");
            row.style.cssText = "display:flex;gap:6px;width:100%;align-items:flex-start;";

            function makePanel(labelText) {
                const panel = document.createElement("div");
                panel.style.cssText = "flex:1 1 0;min-width:0;display:flex;flex-direction:column;";
                const label = document.createElement("div");
                label.textContent = labelText;
                label.style.cssText =
                    "font-size:11px;opacity:0.75;padding:2px 0;user-select:none;";
                const video = document.createElement("video");
                video.controls = true;
                video.loop = true;
                video.playsInline = true;
                video.preload = "metadata";
                video.style.cssText = "width:100%;display:block;background:#111;";
                panel.appendChild(label);
                panel.appendChild(video);
                return { panel, label, video };
            }

            const newPanel = makePanel("New");
            const origPanel = makePanel("Original");
            origPanel.panel.style.display = "none";
            newPanel.label.style.display = "none";
            row.appendChild(newPanel.panel);
            row.appendChild(origPanel.panel);
            previewRoot.appendChild(row);

            const videoEl = newPanel.video;
            const origVideoEl = origPanel.video;
            let compareMode = false;
            let syncing = false;
            const SYNC_EPS = 0.05;

            const previewWidget = this.addDOMWidget("videopreview", "preview", previewRoot, {
                serialize: false,
                hideOnZoom: false,
            });
            previewWidget.videoEl = videoEl;
            previewWidget.origVideoEl = origVideoEl;
            previewWidget.aspectRatio = null;
            previewWidget.computeSize = function (width) {
                if (this.aspectRatio && videoEl.src && !videoEl.hidden) {
                    const usable = Math.max(40, self.size[0] - 20);
                    const colW = compareMode ? usable / 2 : usable;
                    const height = colW / this.aspectRatio + (compareMode ? 24 : 10);
                    this.computedHeight = Math.max(40, height);
                    return [width, this.computedHeight];
                }
                return [width, -4];
            };

            function refreshPreviewSize() {
                let ar = null;
                if (videoEl.videoWidth > 0 && videoEl.videoHeight > 0) {
                    ar = videoEl.videoWidth / videoEl.videoHeight;
                }
                if (
                    compareMode &&
                    origVideoEl.videoWidth > 0 &&
                    origVideoEl.videoHeight > 0
                ) {
                    const origAr = origVideoEl.videoWidth / origVideoEl.videoHeight;
                    // Use the taller relative aspect so neither column is clipped short.
                    ar = ar == null ? origAr : Math.min(ar, origAr);
                }
                previewWidget.aspectRatio = ar;
                try {
                    const size = self.computeSize?.(self.size);
                    if (Array.isArray(size)) self.setSize?.(size);
                    app.graph?.setDirtyCanvas?.(true, true);
                } catch (_) {}
            }

            videoEl.addEventListener("loadedmetadata", refreshPreviewSize);
            origVideoEl.addEventListener("loadedmetadata", refreshPreviewSize);
            videoEl.addEventListener("error", () => {
                if (!compareMode) previewWidget.aspectRatio = null;
                refreshPreviewSize();
            });
            origVideoEl.addEventListener("error", refreshPreviewSize);

            function setVideoSrc(el, info) {
                if (!info?.filename) {
                    el.removeAttribute("src");
                    el.load();
                    return;
                }
                const params = new URLSearchParams({
                    filename: info.filename,
                    type: info.type || "temp",
                    subfolder: info.subfolder || "",
                    t: String(Date.now()),
                });
                el.src = api.apiURL(`/view?${params.toString()}`);
                el.hidden = false;
            }

            function syncFollowerTime() {
                if (!compareMode || syncing || !origVideoEl.src) return;
                const masterT = videoEl.currentTime || 0;
                let target = masterT;
                if (Number.isFinite(origVideoEl.duration) && origVideoEl.duration > 0) {
                    target = Math.min(masterT, Math.max(0, origVideoEl.duration - 0.001));
                }
                if (Math.abs((origVideoEl.currentTime || 0) - target) > SYNC_EPS) {
                    syncing = true;
                    try {
                        origVideoEl.currentTime = target;
                    } catch (_) {}
                    syncing = false;
                }
            }

            function wireSync() {
                videoEl.addEventListener("play", () => {
                    if (!compareMode || syncing) return;
                    syncFollowerTime();
                    const p = origVideoEl.play?.();
                    if (p && typeof p.catch === "function") p.catch(() => {});
                });
                videoEl.addEventListener("pause", () => {
                    if (!compareMode || syncing) return;
                    origVideoEl.pause?.();
                });
                videoEl.addEventListener("seeked", () => {
                    if (!compareMode || syncing) return;
                    syncFollowerTime();
                });
                videoEl.addEventListener("ratechange", () => {
                    if (!compareMode || syncing) return;
                    try {
                        origVideoEl.playbackRate = videoEl.playbackRate;
                    } catch (_) {}
                });
                videoEl.addEventListener("timeupdate", syncFollowerTime);
                // Keep follower from drifting when user scrubs original controls.
                origVideoEl.addEventListener("seeked", () => {
                    if (!compareMode || syncing) return;
                    syncing = true;
                    try {
                        videoEl.currentTime = origVideoEl.currentTime;
                    } catch (_) {}
                    syncing = false;
                });
                origVideoEl.addEventListener("play", () => {
                    if (!compareMode || syncing) return;
                    const p = videoEl.play?.();
                    if (p && typeof p.catch === "function") p.catch(() => {});
                });
                origVideoEl.addEventListener("pause", () => {
                    if (!compareMode || syncing) return;
                    videoEl.pause?.();
                });
            }
            wireSync();

            function setPreviewSource(info, originalInfo) {
                compareMode = !!(originalInfo?.filename);
                newPanel.label.style.display = compareMode ? "" : "none";
                origPanel.panel.style.display = compareMode ? "" : "none";

                if (!info?.filename) {
                    setVideoSrc(videoEl, null);
                    setVideoSrc(origVideoEl, null);
                    previewWidget.aspectRatio = null;
                    return;
                }
                setVideoSrc(videoEl, info);
                if (compareMode) {
                    setVideoSrc(origVideoEl, originalInfo);
                    // Start both at frame 0 once metadata is ready.
                    const seekBothToStart = () => {
                        syncing = true;
                        try {
                            videoEl.currentTime = 0;
                            origVideoEl.currentTime = 0;
                        } catch (_) {}
                        syncing = false;
                    };
                    let pending = 2;
                    const onReady = () => {
                        pending -= 1;
                        if (pending <= 0) seekBothToStart();
                    };
                    videoEl.addEventListener("loadedmetadata", onReady, { once: true });
                    origVideoEl.addEventListener("loadedmetadata", onReady, { once: true });
                } else {
                    setVideoSrc(origVideoEl, null);
                }
            }

            this.currentVideo = null;
            const _origOnExecuted = this.onExecuted;
            this.onExecuted = function (output) {
                // Prefer animated video payload; ignore still-image node previews.
                this.imgs = undefined;
                this.imageIndex = null;
                _origOnExecuted?.call(this, output);
                this.imgs = undefined;
                this.imageIndex = null;

                const gifs = output?.gifs || output?.videos || [];
                const info = gifs[0] || null;
                const originalInfo = gifs[1] || null;
                this.currentVideo = info;
                setPreviewSource(info, originalInfo);
            };
        };
    },
});
