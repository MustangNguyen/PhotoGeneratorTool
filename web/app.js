"use strict";

const POLL_MS = 3000;
const STALE_AFTER_MS = POLL_MS * 3;
const ACTIVE_STATUSES = new Set(["planning", "queued", "generating", "running", "starting", "pausing"]);
const ACTIVE_ITEM_STAGES = new Set(["preparing", "generating", "saving", "checking"]);
const STATUS_META = {
  draft: ["Bản nháp", "draft"],
  planning: ["Đang lên context", "generating"],
  planned: ["Đã lên context", "planned"],
  queued: ["Đang chờ", "queued"],
  generating: ["Đang tạo", "generating"],
  running: ["Đang tạo", "generating"],
  completed: ["Đã tạo", "completed"],
  complete: ["Đã tạo", "completed"],
  approved: ["Đã duyệt", "approved"],
  rejected: ["Đã loại", "rejected"],
  failed: ["Lỗi", "failed"],
  blocked: ["Cần xử lý", "failed"],
  pausing: ["Đang tạm dừng", "generating"],
  paused: ["Tạm dừng", "paused"],
  pending: ["Chờ duyệt", "pending"],
};
const STAGE_META = {
  queued: ["Đang chờ", "queued"],
  preparing: ["Đang chuẩn bị", "generating"],
  generating: ["Đang tạo · chờ dịch vụ trả ảnh", "generating"],
  saving: ["Đang lưu ảnh", "generating"],
  checking: ["Đang kiểm tra gần trùng", "generating"],
  completed: ["Đã tạo", "completed"],
  failed: ["Lỗi", "failed"],
};

const state = {
  status: null,
  batches: [],
  activeBatch: null,
  items: [],
  events: [],
  selectedItem: null,
  filter: "all",
  pollTimer: null,
  liveTimer: null,
  lastBatchFetchAt: 0,
  batchFeedHealthy: true,
  previewImage: null,
  shuffleOrder: [],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const elements = {
  providerPill: $("#providerPill"),
  providerLabel: $("#providerLabel"),
  batchList: $("#batchList"),
  batchCount: $("#batchCount"),
  imageCount: $("#imageCount"),
  conceptCount: $("#conceptCount"),
  createForm: $("#createForm"),
  countInput: $("#countInput"),
  createButton: $("#createButton"),
  emptyState: $("#emptyState"),
  batchView: $("#batchView"),
  batchDate: $("#batchDate"),
  batchTitle: $("#batchTitle"),
  batchSummary: $("#batchSummary"),
  pauseButton: $("#pauseButton"),
  exportButton: $("#exportButton"),
  exportAllButton: $("#exportAllButton"),
  progressText: $("#progressText"),
  progressPercent: $("#progressPercent"),
  progressBar: $("#progressBar"),
  progressTrack: $(".progress-track"),
  progressStages: $("#progressStages"),
  batchError: $("#batchError"),
  liveActivity: $("#liveActivity"),
  liveActivitySummary: $("#liveActivitySummary"),
  liveActivityList: $("#liveActivityList"),
  liveFeedNote: $("#liveFeedNote"),
  imageGallery: $("#imageGallery"),
  galleryCount: $("#galleryCount"),
  eventLog: $("#eventLog"),
  eventLogToggle: $("#eventLogToggle"),
  eventList: $("#eventList"),
  imageModal: $("#imageModal"),
  previewCanvas: $("#previewCanvas"),
  modalImagePlaceholder: $("#modalImagePlaceholder"),
  modalLiveProgress: $("#modalLiveProgress"),
  modalPhase: $("#modalPhase"),
  modalProgressMessage: $("#modalProgressMessage"),
  modalElapsed: $("#modalElapsed"),
  modalFeedNote: $("#modalFeedNote"),
  gridSize: $("#gridSize"),
  gridOutput: $("#gridOutput"),
  settingsModal: $("#settingsModal"),
  settingsForm: $("#settingsForm"),
  settingsStatus: $("#settingsStatus"),
  providerSelect: $("#providerSelect"),
  textModelInput: $("#textModelInput"),
  imageModelInput: $("#imageModelInput"),
  concurrencyInput: $("#concurrencyInput"),
  apiKeyInput: $("#apiKeyInput"),
  apiKeyHint: $("#apiKeyHint"),
  apiKeyField: $("#apiKeyField"),
  toastRegion: $("#toastRegion"),
};

async function api(path, options = {}) {
  const config = { ...options, headers: { ...options.headers } };
  if (["POST", "PUT"].includes(String(config.method).toUpperCase()) && config.body == null) config.body = {};
  if (config.body && typeof config.body !== "string") {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(path, config);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    throw new Error(payload?.error || payload?.message || `Yêu cầu thất bại (${response.status})`);
  }
  return payload;
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value == null ? "" : String(value);
  return node.innerHTML;
}

function formatNumber(value) {
  return new Intl.NumberFormat("vi-VN").format(Number(value) || 0);
}

function formatDate(value, options = {}) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", ...options }).format(date);
}

function statusInfo(status) {
  const key = String(status || "pending").toLowerCase();
  return { key, label: STATUS_META[key]?.[0] || key, css: STATUS_META[key]?.[1] || "pending" };
}

function itemStage(item) {
  const stage = String(item?.stage || "").toLowerCase();
  if (STAGE_META[stage]) return stage;
  const status = String(item?.status || "planned").toLowerCase();
  if (["completed", "complete"].includes(status)) return "completed";
  if (status === "failed") return "failed";
  if (["generating", "running"].includes(status)) return "generating";
  return "queued";
}

function stageInfo(item) {
  const key = itemStage(item);
  return { key, label: STAGE_META[key]?.[0] || key, css: STAGE_META[key]?.[1] || "pending" };
}

function isItemActive(item) {
  return ACTIVE_ITEM_STAGES.has(itemStage(item));
}

function parseTimestamp(value) {
  const timestamp = value ? new Date(value).getTime() : NaN;
  return Number.isFinite(timestamp) ? timestamp : null;
}

function formatElapsed(value, now = Date.now()) {
  const started = parseTimestamp(value);
  if (started == null) return "";
  const seconds = Math.max(0, Math.floor((now - started) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function isBatchFeedStale() {
  if (!state.activeBatch || !ACTIVE_STATUSES.has(String(state.activeBatch.status).toLowerCase())) return false;
  return !state.batchFeedHealthy || !state.lastBatchFetchAt || Date.now() - state.lastBatchFetchAt > STALE_AFTER_MS;
}

function showToast(message, type = "") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`.trim();
  toast.textContent = message;
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function setButtonBusy(button, busy, label) {
  if (!button.dataset.defaultLabel) button.dataset.defaultLabel = button.textContent.trim();
  button.disabled = busy;
  if (label) button.textContent = busy ? label : button.dataset.defaultLabel;
}

async function loadStatus() {
  try {
    const data = await api("/api/status");
    state.status = data;
    renderStatus();
    if (!state.activeBatch && data.activeBatchId) await selectBatch(data.activeBatchId, { quiet: true });
  } catch (error) {
    state.status = null;
    elements.providerPill.className = "provider-pill error";
    elements.providerLabel.textContent = "Không kết nối được máy chủ";
  }
}

function renderStatus() {
  const provider = state.status?.provider || {};
  const ready = Boolean(provider.ready);
  const textReady = provider.text_ready == null ? ready : Boolean(provider.text_ready);
  elements.providerPill.className = `provider-pill ${ready ? "ready" : "error"}`;
  elements.providerLabel.textContent = provider.message || (ready ? `${provider.name || "Dịch vụ"} sẵn sàng` : `${provider.name || "Dịch vụ"} cần thiết lập`);
  elements.providerPill.title = elements.providerLabel.textContent;
  elements.imageCount.textContent = formatNumber(state.status?.counts?.images);
  elements.conceptCount.textContent = formatNumber(state.status?.counts?.concepts);
  elements.createButton.title = textReady ? "" : (provider.message || "Chưa thể lên context");
  if (state.activeBatch) renderBatch();
}

async function loadBatches() {
  try {
    state.batches = (await api("/api/batches")) || [];
    renderBatchList();
  } catch (error) {
    elements.batchList.innerHTML = `<div class="sidebar-empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderBatchList() {
  elements.batchCount.textContent = formatNumber(state.batches.length);
  if (!state.batches.length) {
    elements.batchList.innerHTML = '<div class="sidebar-empty">Chưa có lô ảnh nào.</div>';
    return;
  }
  elements.batchList.innerHTML = state.batches.map((batch) => {
    const meta = statusInfo(batch.status);
    const active = String(state.activeBatch?.id) === String(batch.id);
    return `<button type="button" class="batch-list-item${active ? " active" : ""}" data-batch-id="${escapeHtml(batch.id)}" aria-current="${active ? "page" : "false"}">
      <span class="status-dot status-${meta.css}" aria-hidden="true"></span>
      <span class="batch-list-copy"><strong>Lô ${escapeHtml(shortId(batch.id))} · ${formatNumber(batch.count)} ảnh</strong><small>${escapeHtml(formatDate(batch.created_at) || meta.label)}</small></span>
      <span class="batch-list-progress">${formatNumber(batch.completed)}/${formatNumber(batch.count)}</span>
    </button>`;
  }).join("");
}

function shortId(id) {
  const text = String(id ?? "");
  return text.length > 8 ? text.slice(-6).toUpperCase() : text.toUpperCase();
}

async function selectBatch(id, { quiet = false } = {}) {
  try {
    const data = await api(`/api/batches/${encodeURIComponent(id)}`);
    state.activeBatch = data.batch;
    state.items = data.items || [];
    state.events = data.events || [];
    state.lastBatchFetchAt = Date.now();
    state.batchFeedHealthy = true;
    renderBatchList();
    renderBatch();
    schedulePoll();
  } catch (error) {
    if (String(state.activeBatch?.id) === String(id)) {
      state.batchFeedHealthy = false;
      renderLiveActivity();
      syncOpenModal();
      schedulePoll();
    }
    if (!quiet) showToast(error.message, "error");
  }
}

function renderBatch() {
  const batch = state.activeBatch;
  if (!batch) {
    elements.emptyState.hidden = false;
    elements.batchView.hidden = true;
    return;
  }
  elements.emptyState.hidden = true;
  elements.batchView.hidden = false;
  const meta = statusInfo(batch.status);
  const total = Number(batch.count) || 0;
  const completed = Number(batch.completed) || 0;
  const failed = Number(batch.failed) || 0;
  const planned = Number(batch.planned) || 0;
  const statusBatchId = state.status?.activeBatchId;
  const statusMatchesBatch = statusBatchId == null || String(statusBatchId) === String(batch.id);
  const activeItemCount = state.items.filter(isItemActive).length;
  const inFlight = Number.isFinite(Number(batch.generating))
    ? Number(batch.generating)
    : (statusMatchesBatch ? (Number(state.status?.inFlight) || activeItemCount) : activeItemCount);
  const waiting = Number.isFinite(Number(batch.waiting))
    ? Number(batch.waiting)
    : state.items.filter((item) => itemStage(item) === "queued").length;
  const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;

  elements.batchDate.textContent = formatDate(batch.created_at, { hour: "2-digit", minute: "2-digit" }) || "Lô ảnh";
  elements.batchTitle.textContent = `Lô ${shortId(batch.id)} · ${formatNumber(total)} ảnh`;
  elements.batchSummary.textContent = batchSummary(batch, meta.label);
  elements.progressText.textContent = `${formatNumber(completed)} / ${formatNumber(total)} ảnh hoàn thành`;
  elements.progressPercent.textContent = `${percent}%`;
  elements.progressBar.style.width = `${percent}%`;
  elements.progressTrack.setAttribute("aria-valuenow", String(percent));
  elements.progressStages.innerHTML = [
    `<span><i></i>${formatNumber(planned)} context đã lên</span>`,
    `<span><i></i>${formatNumber(completed)} ảnh đã tạo</span>`,
    inFlight ? `<span><i></i>${formatNumber(inFlight)} ảnh đang tạo đồng thời</span>` : "",
    waiting ? `<span><i></i>${formatNumber(waiting)} ảnh đang chờ</span>` : "",
    failed ? `<span><i></i>${formatNumber(failed)} ảnh lỗi</span>` : "",
    `<span><i></i>${escapeHtml(meta.label)}</span>`,
  ].join("");

  const canPause = ACTIVE_STATUSES.has(meta.key) && meta.key !== "pausing";
  const canResume = meta.key === "paused" || meta.key === "blocked";
  elements.pauseButton.hidden = !(canPause || canResume);
  elements.pauseButton.textContent = canResume ? "Tiếp tục" : "Tạm dừng";
  elements.pauseButton.title = canPause ? "Các ảnh đang tạo sẽ hoàn tất trước khi lô tạm dừng." : "";
  elements.pauseButton.dataset.action = canResume ? "start" : "pause";
  const approvedCount = state.items.filter((item) => item.status === "completed" && item.review === "approved").length;
  setExportLink(elements.exportButton, approvedCount > 0, `/api/batches/${encodeURIComponent(batch.id)}/export?approved=1`, "Chưa có ảnh đã duyệt để tải.");
  setExportLink(elements.exportAllButton, completed > 0, `/api/batches/${encodeURIComponent(batch.id)}/export`, "Chưa có ảnh hoàn thành để tải.");
  elements.batchError.hidden = !batch.error;
  elements.batchError.textContent = batch.error || "";
  renderGallery();
  renderLiveActivity();
  renderEvents();
  syncOpenModal();
}

function batchSummary(batch, statusLabel) {
  const approved = state.items.filter((item) => item.review === "approved").length;
  const parts = [statusLabel];
  if (batch.failed) parts.push(`${formatNumber(batch.failed)} lỗi`);
  if (approved) parts.push(`${formatNumber(approved)} đã duyệt`);
  return parts.join(" · ");
}

function setExportLink(link, enabled, href, disabledMessage) {
  link.setAttribute("aria-disabled", String(!enabled));
  link.title = enabled ? "" : disabledMessage;
  if (enabled) link.href = href;
  else link.removeAttribute("href");
}

function itemMatchesFilter(item) {
  if (state.filter === "all") return true;
  if (state.filter === "approved") return item.review === "approved";
  if (state.filter === "completed") return ["completed", "complete"].includes(item.status);
  if (state.filter === "attention") return item.status === "failed" || item.review === "rejected";
  return true;
}

function renderGallery() {
  const items = state.items.filter(itemMatchesFilter);
  elements.galleryCount.textContent = `${formatNumber(items.length)} ảnh`;
  if (!items.length) {
    const text = state.items.length ? "Không có ảnh ở trạng thái này." : "Context sẽ xuất hiện tại đây khi hệ thống lên kế hoạch.";
    elements.imageGallery.innerHTML = `<div class="empty-gallery">${text}</div>`;
    return;
  }
  elements.imageGallery.innerHTML = items.map((item) => {
    const reviewed = item.review && item.review !== "pending";
    const meta = reviewed ? statusInfo(item.review) : stageInfo(item);
    const stage = itemStage(item);
    const active = isItemActive(item);
    const startedAt = item.started_at || item.stage_changed_at || "";
    const message = item.progress_message || meta.label;
    const image = item.image_url
      ? `<img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.title || "Ảnh puzzle")}" loading="lazy">`
      : `<div class="image-placeholder">${active ? `<div><span class="spinner"></span>${escapeHtml(message)}</div>` : escapeHtml(stage === "queued" ? "Đang chờ đến lượt…" : meta.label)}</div>`;
    const activity = active
      ? `<div class="card-progress"><span>${escapeHtml(message)}</span><time class="live-elapsed" data-elapsed-start="${escapeHtml(startedAt)}" data-elapsed-prefix="Đã chạy "></time></div>`
      : stage === "queued" ? '<div class="card-progress waiting"><span>Đang chờ đến lượt</span></div>' : "";
    return `<article class="image-card">
      <button class="image-card-button" type="button" data-item-id="${escapeHtml(item.id)}" aria-label="Xem ${escapeHtml(item.title || "chi tiết context")}">
        <div class="image-wrap">${image}<span class="card-state status-${meta.css}">${escapeHtml(meta.label)}</span></div>
        <div class="card-copy"><small>${escapeHtml(item.category || "Context")}</small><strong>${escapeHtml(item.title || "Đang lên ý tưởng")}</strong><p>${escapeHtml(item.scene || item.story || "Chi tiết đang được chuẩn bị.")}</p>${activity}</div>
      </button>
    </article>`;
  }).join("");
  updateLiveElapsed();
}

function renderLiveActivity() {
  const batch = state.activeBatch;
  if (!batch) {
    elements.liveActivity.hidden = true;
    return;
  }
  const activeItems = state.items.filter(isItemActive).slice(0, 8);
  const batchActive = ACTIVE_STATUSES.has(String(batch.status).toLowerCase());
  const generating = batch.generating == null ? activeItems.length : Number(batch.generating) || 0;
  const waiting = batch.waiting == null
    ? state.items.filter((item) => itemStage(item) === "queued").length
    : Number(batch.waiting) || 0;
  elements.liveActivity.hidden = !batchActive && !activeItems.length;
  if (elements.liveActivity.hidden) return;
  const summary = [];
  if (generating) summary.push(`${formatNumber(generating)} đang xử lý`);
  if (waiting) summary.push(`${formatNumber(waiting)} đang chờ`);
  elements.liveActivitySummary.textContent = summary.join(" · ") || "Đang chuẩn bị tác vụ tiếp theo";
  elements.liveActivityList.innerHTML = activeItems.length ? activeItems.map((item) => {
    const meta = stageInfo(item);
    const message = item.progress_message || meta.label;
    const startedAt = item.started_at || item.stage_changed_at || "";
    return `<article class="live-activity-item">
      <span class="activity-pulse" aria-hidden="true"></span>
      <div><strong>${escapeHtml(item.title || "Ảnh đang xử lý")}</strong><span class="activity-phase">${escapeHtml(meta.label)}</span><p>${escapeHtml(message)}</p></div>
      <time class="live-elapsed" data-elapsed-start="${escapeHtml(startedAt)}" data-elapsed-prefix="Đã chạy "></time>
    </article>`;
  }).join("") : `<div class="live-activity-empty">${batch.status === "planning" ? "Đang lập và kiểm tra context trước khi tạo ảnh." : "Đang chờ trạng thái ảnh tiếp theo."}</div>`;
  updateLiveElapsed();
}

function updateLiveElapsed() {
  const stale = isBatchFeedStale();
  const clock = stale && state.lastBatchFetchAt ? state.lastBatchFetchAt : Date.now();
  $$('[data-elapsed-start]').forEach((node) => {
    const elapsed = formatElapsed(node.dataset.elapsedStart, clock);
    node.textContent = elapsed ? `${node.dataset.elapsedPrefix || ""}${elapsed}` : "";
  });
  elements.liveActivity.classList.toggle("stale", stale);
  elements.liveFeedNote.hidden = !stale;
  elements.liveFeedNote.textContent = stale ? "Mất kết nối cập nhật. Trạng thái dưới đây được giữ tại lần nhận dữ liệu cuối." : "";
  const modalShowsProgress = elements.imageModal.open && !elements.modalLiveProgress.hidden;
  elements.modalFeedNote.hidden = !(stale && modalShowsProgress);
  elements.modalFeedNote.textContent = stale && modalShowsProgress ? "Mất kết nối cập nhật; tiến trình này có thể đã thay đổi." : "";
}

function renderEvents() {
  elements.eventLog.hidden = !state.events.length;
  elements.eventList.innerHTML = state.events.map((event) => `<li>${escapeHtml(event.message)}${event.created_at ? ` · ${escapeHtml(formatDate(event.created_at, { hour: "2-digit", minute: "2-digit" }))}` : ""}</li>`).join("");
}

function schedulePoll() {
  window.clearTimeout(state.pollTimer);
  const active = state.activeBatch && ACTIVE_STATUSES.has(String(state.activeBatch.status).toLowerCase());
  if (!active) return;
  state.pollTimer = window.setTimeout(async () => {
    await Promise.all([selectBatch(state.activeBatch.id, { quiet: true }), loadStatus(), loadBatches()]);
  }, POLL_MS);
}

async function createBatch(event) {
  event.preventDefault();
  const count = Number(elements.countInput.value);
  if (!Number.isInteger(count) || count < 1 || count > 1000) {
    elements.countInput.setCustomValidity("Nhập số nguyên từ 1 đến 1.000.");
    elements.countInput.reportValidity();
    return;
  }
  elements.countInput.setCustomValidity("");
  setButtonBusy(elements.createButton, true, "Đang tạo lô…");
  try {
    const batch = await api("/api/batches", { method: "POST", body: { count } });
    const id = batch.id || batch.batch?.id;
    if (!id) throw new Error("Máy chủ không trả về mã lô ảnh.");
    await selectBatch(id);
    await Promise.all([loadBatches(), loadStatus(), selectBatch(id, { quiet: true })]);
    showToast(`Đã tạo lô ${formatNumber(count)} ảnh và bắt đầu lên context.`);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(elements.createButton, false);
  }
}

async function toggleBatch() {
  if (!state.activeBatch) return;
  const action = elements.pauseButton.dataset.action;
  setButtonBusy(elements.pauseButton, true, action === "pause" ? "Đang yêu cầu dừng…" : "Đang tiếp tục…");
  try {
    await api(`/api/batches/${encodeURIComponent(state.activeBatch.id)}/${action}`, { method: "POST" });
    await selectBatch(state.activeBatch.id);
    if (action === "pause") showToast("Đã yêu cầu tạm dừng. Các ảnh đang tạo sẽ hoàn tất trước khi lô dừng.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(elements.pauseButton, false);
  }
}

function openItem(id) {
  const item = state.items.find((candidate) => String(candidate.id) === String(id));
  if (!item) return;
  updateModalDetails(item);
  loadPreview(item.image_url);
  elements.imageModal.showModal();
  updateLiveElapsed();
}

function updateModalDetails(item) {
  state.selectedItem = item;
  $("#modalCategory").textContent = item.category || "Context";
  $("#modalTitle").textContent = item.title || "Chi tiết ảnh";
  $("#modalScene").textContent = item.scene || "—";
  $("#modalStory").textContent = item.story || "—";
  $("#modalComposition").textContent = item.composition || "—";
  $("#modalPalette").textContent = item.palette || "—";
  $("#modalSimilarityRow").hidden = !item.similarity;
  $("#modalSimilarity").textContent = item.similarity || "—";
  $("#modalError").hidden = !item.error;
  $("#modalError").textContent = item.error || "";
  $("#modalPrompt").textContent = item.prompt || "Prompt chưa được tạo.";
  const reviewed = item.review && item.review !== "pending";
  const meta = reviewed ? statusInfo(item.review) : stageInfo(item);
  const modalStatus = $("#modalStatus");
  modalStatus.textContent = meta.label;
  modalStatus.className = `status-badge status-${meta.css}`;
  const stage = itemStage(item);
  const showsProgress = isItemActive(item) || stage === "queued";
  elements.modalLiveProgress.hidden = !showsProgress;
  if (showsProgress) {
    const startedAt = item.started_at || item.stage_changed_at || "";
    elements.modalPhase.textContent = meta.label;
    elements.modalProgressMessage.textContent = item.progress_message || (stage === "queued" ? "Đang chờ đến lượt tạo." : meta.label);
    elements.modalElapsed.dataset.elapsedStart = startedAt;
    elements.modalElapsed.dataset.elapsedPrefix = "Đã chạy ";
  } else {
    elements.modalElapsed.removeAttribute("data-elapsed-start");
    elements.modalElapsed.textContent = "";
  }
  $("#approveButton").disabled = item.review === "approved" || !item.image_url;
  $("#rejectButton").disabled = item.review === "rejected" || !item.image_url;
  $("#retryButton").hidden = item.status !== "failed";
  const download = $("#downloadButton");
  download.hidden = !item.image_url;
  download.href = item.image_url || "#";
  download.download = `${safeFilename(item.title || `puzzle-${item.id}`)}.jpg`;
}

function syncOpenModal() {
  if (!elements.imageModal.open || !state.selectedItem) return;
  const updated = state.items.find((candidate) => String(candidate.id) === String(state.selectedItem.id));
  if (!updated) return;
  const previousImageUrl = state.selectedItem.image_url;
  updateModalDetails(updated);
  if (updated.image_url && updated.image_url !== previousImageUrl) loadPreview(updated.image_url);
  updateLiveElapsed();
}

function safeFilename(value) {
  return String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase() || "puzzle-image";
}

function loadPreview(url) {
  state.previewImage = null;
  state.shuffleOrder = [];
  const context = elements.previewCanvas.getContext("2d");
  context.clearRect(0, 0, elements.previewCanvas.width, elements.previewCanvas.height);
  elements.modalImagePlaceholder.hidden = false;
  elements.modalImagePlaceholder.textContent = url ? "Đang tải ảnh…" : "Ảnh chưa sẵn sàng";
  if (!url) return;
  const image = new Image();
  image.onload = () => {
    state.previewImage = image;
    elements.modalImagePlaceholder.hidden = true;
    resetShuffleOrder();
    drawPuzzlePreview();
  };
  image.onerror = () => { elements.modalImagePlaceholder.textContent = "Không tải được ảnh xem trước"; };
  image.src = url;
}

function resetShuffleOrder() {
  const size = Number(elements.gridSize.value);
  state.shuffleOrder = Array.from({ length: size * size }, (_, index) => index);
}

function shufflePreview() {
  if (!state.previewImage) return;
  if (state.shuffleOrder.length !== Number(elements.gridSize.value) ** 2) resetShuffleOrder();
  for (let i = state.shuffleOrder.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [state.shuffleOrder[i], state.shuffleOrder[j]] = [state.shuffleOrder[j], state.shuffleOrder[i]];
  }
  drawPuzzlePreview();
}

function drawPuzzlePreview() {
  const image = state.previewImage;
  if (!image) return;
  const size = Number(elements.gridSize.value);
  const canvas = elements.previewCanvas;
  const width = 600;
  const height = 900;
  canvas.width = width;
  canvas.height = height;
  if (state.shuffleOrder.length !== size * size) resetShuffleOrder();
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, width, height);
  const sourceCellWidth = image.naturalWidth / size;
  const sourceCellHeight = image.naturalHeight / size;
  const cellWidth = width / size;
  const cellHeight = height / size;
  state.shuffleOrder.forEach((sourceIndex, targetIndex) => {
    const sx = (sourceIndex % size) * sourceCellWidth;
    const sy = Math.floor(sourceIndex / size) * sourceCellHeight;
    const dx = (targetIndex % size) * cellWidth;
    const dy = Math.floor(targetIndex / size) * cellHeight;
    context.drawImage(image, sx, sy, sourceCellWidth, sourceCellHeight, dx, dy, cellWidth, cellHeight);
    context.strokeStyle = "rgba(255,255,255,.72)";
    context.lineWidth = 2;
    context.strokeRect(dx, dy, cellWidth, cellHeight);
  });
}

async function reviewItem(review) {
  const item = state.selectedItem;
  if (!item) return;
  try {
    await api(`/api/items/${encodeURIComponent(item.id)}/review`, { method: "POST", body: { review } });
    await selectBatch(state.activeBatch.id, { quiet: true });
    const updated = state.items.find((candidate) => String(candidate.id) === String(item.id));
    if (updated) openItemRefresh(updated);
    showToast(review === "approved" ? "Đã duyệt ảnh." : "Đã loại ảnh.");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function openItemRefresh(item) {
  updateModalDetails(item);
  updateLiveElapsed();
}

async function retryItem() {
  const item = state.selectedItem;
  if (!item) return;
  const button = $("#retryButton");
  setButtonBusy(button, true, "Đang xếp hàng…");
  try {
    await api(`/api/items/${encodeURIComponent(item.id)}/retry`, { method: "POST" });
    elements.imageModal.close();
    await selectBatch(state.activeBatch.id);
    showToast("Ảnh đã được xếp hàng tạo lại.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function openSettings() {
  try {
    const settings = await api("/api/settings");
    elements.providerSelect.value = settings.provider || "codex";
    elements.textModelInput.value = settings.text_model || "";
    elements.imageModelInput.value = settings.image_model || "";
    elements.concurrencyInput.value = String(settings.concurrency ?? 4);
    elements.apiKeyInput.value = "";
    elements.apiKeyHint.textContent = settings.has_api_key ? "Đã có khóa API. Để trống nếu không thay đổi." : "Khóa hiện tại chưa được thiết lập.";
    elements.settingsModal.dataset.codexAvailable = String(Boolean(settings.codex_available));
    updateSettingsFields();
    elements.settingsStatus.className = `settings-status ${state.status?.provider?.ready ? "" : "error"}`;
    elements.settingsStatus.textContent = state.status?.provider?.message || "Kiểm tra cấu hình trước khi tạo ảnh.";
    elements.settingsModal.showModal();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function updateSettingsFields() {
  const openai = elements.providerSelect.value === "openai";
  elements.apiKeyField.hidden = !openai;
  if (!openai && elements.settingsModal.dataset.codexAvailable === "false") {
    elements.settingsStatus.className = "settings-status error";
    elements.settingsStatus.textContent = "Không tìm thấy Codex cục bộ trên máy này.";
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const button = $("#saveSettingsButton");
  const concurrency = Number(elements.concurrencyInput.value);
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 8) {
    elements.concurrencyInput.setCustomValidity("Nhập số nguyên từ 1 đến 8.");
    elements.concurrencyInput.reportValidity();
    return;
  }
  elements.concurrencyInput.setCustomValidity("");
  const body = {
    provider: elements.providerSelect.value,
    text_model: elements.textModelInput.value.trim(),
    image_model: elements.imageModelInput.value.trim(),
    concurrency,
  };
  if (elements.providerSelect.value === "openai" && elements.apiKeyInput.value.trim()) body.api_key = elements.apiKeyInput.value.trim();
  setButtonBusy(button, true, "Đang lưu…");
  try {
    await api("/api/settings", { method: "PUT", body });
    elements.apiKeyInput.value = "";
    await loadStatus();
    elements.settingsModal.close();
    showToast("Đã lưu cài đặt dịch vụ.");
  } catch (error) {
    elements.settingsStatus.className = "settings-status error";
    elements.settingsStatus.textContent = error.message;
  } finally {
    setButtonBusy(button, false);
  }
}

function wireEvents() {
  elements.createForm.addEventListener("submit", createBatch);
  elements.countInput.addEventListener("input", () => elements.countInput.setCustomValidity(""));
  $$('[data-step]').forEach((button) => button.addEventListener("click", () => {
    const next = Math.max(1, Math.min(1000, (Number(elements.countInput.value) || 0) + Number(button.dataset.step)));
    elements.countInput.value = String(next);
  }));
  elements.batchList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-batch-id]");
    if (button) selectBatch(button.dataset.batchId);
  });
  elements.pauseButton.addEventListener("click", toggleBatch);
  $("#mobileBackButton").addEventListener("click", () => $(".sidebar").scrollIntoView({ behavior: "smooth" }));
  $("#filterTabs").addEventListener("click", (event) => {
    const tab = event.target.closest("[data-filter]");
    if (!tab) return;
    state.filter = tab.dataset.filter;
    $$(".filter-tab").forEach((button) => button.classList.toggle("active", button === tab));
    renderGallery();
  });
  elements.imageGallery.addEventListener("click", (event) => {
    const button = event.target.closest("[data-item-id]");
    if (button) openItem(button.dataset.itemId);
  });
  elements.eventLogToggle.addEventListener("click", () => {
    const expanded = elements.eventLogToggle.getAttribute("aria-expanded") === "true";
    elements.eventLogToggle.setAttribute("aria-expanded", String(!expanded));
    elements.eventList.hidden = expanded;
  });
  $("#settingsButton").addEventListener("click", openSettings);
  elements.providerSelect.addEventListener("change", updateSettingsFields);
  elements.concurrencyInput.addEventListener("input", () => elements.concurrencyInput.setCustomValidity(""));
  elements.settingsForm.addEventListener("submit", saveSettings);
  $("#approveButton").addEventListener("click", () => reviewItem("approved"));
  $("#rejectButton").addEventListener("click", () => reviewItem("rejected"));
  $("#retryButton").addEventListener("click", retryItem);
  elements.gridSize.addEventListener("input", () => {
    elements.gridOutput.textContent = `${elements.gridSize.value} × ${elements.gridSize.value}`;
    resetShuffleOrder();
    drawPuzzlePreview();
  });
  $("#shuffleButton").addEventListener("click", shufflePreview);
  $$('[data-close-modal]').forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
  $$('dialog').forEach((dialog) => dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  }));
}

async function init() {
  wireEvents();
  const reference = $(".reference-frame img");
  const hideMissingReference = () => {
    reference.closest(".reference-frame").hidden = true;
    elements.emptyState.classList.add("without-reference");
  };
  reference.addEventListener("error", hideMissingReference);
  if (reference.complete && !reference.naturalWidth) hideMissingReference();
  state.liveTimer = window.setInterval(updateLiveElapsed, 1000);
  await Promise.all([loadStatus(), loadBatches()]);
  if (!state.activeBatch) {
    const initialId = state.status?.activeBatchId || state.batches[0]?.id;
    if (initialId) await selectBatch(initialId, { quiet: true });
  }
}

init();
