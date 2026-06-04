// ============================================================================
// Thursday dashboard frontend
// ----------------------------------------------------------------------------
// This file owns the browser-side behavior for the dashboard:
// - keeps local UI state
// - talks to the Python server through /api/* endpoints
// - renders sessions, chat messages, timeline events, logs, tools, and settings
// - stores purely visual preferences such as panel widths and accent color
//
// When modifying the UI, start by finding the matching section header below.
// ============================================================================

// Central browser state. This is not persisted directly; the server persists
// sessions/preferences, while this object remembers the current UI view.
const state = {
  sessionId: null,
  sessions: [],
  enabledTools: [],
  requiredTools: [],
  allTools: [],
  workspace: null,
  workspaceResetting: false,
  maintenanceBusy: false,
  pollTimer: null,
  config: null,
  preferences: null,
  saveTimer: null,
  settingsStatusTimer: null,
  forceScrollOnNextRender: false,
  lastRenderedSessionId: null,
  lastRenderedMessageSignature: "",
  logsOpen: false,
  logView: "trace",
  logsPollInFlight: false,
  rawOpenPanels: new Set(),
  settingsOpen: false,
  selectedTimelineEvent: null,
  pendingImages: [],
  themeColor: "#9b63ff",
  guiForLlm: {
    displays: [],
    activeRegion: null,
    previewPath: "",
  },
};

// Tiny DOM helper. Example: $("chatLog") means document.getElementById("chatLog").
const $ = (id) => document.getElementById(id);

// Browser-local key for saved panel widths. This only affects this browser.
const PANEL_WIDTH_STORAGE_KEY = "thursday.panelWidths";

// Used when preferences have no saved accent color or the typed color is invalid.
const DEFAULT_THEME_COLOR = "#9b63ff";

// Default side/drawer widths before the user drags resize handles.
const PANEL_WIDTH_DEFAULTS = {
  left: 300,
  right: 360,
  logs: 920,
  settings: 440,
};

// CSS custom properties controlled by the resize code.
const PANEL_WIDTH_VARS = {
  left: "--left-panel-width",
  right: "--right-panel-width",
  logs: "--logs-drawer-width",
  settings: "--settings-drawer-width",
};

// Read the current settings drawer controls and convert them into the payload
// shape expected by the backend.
function settingsFromUi() {
  ensureRequiredToolsEnabled();
  return {
    endpoint: $("endpointInput").value.trim(),
    model: $("modelInput").value.trim(),
    temperature: Number($("temperatureInput").value),
    top_p: Number($("topPInput").value),
    max_tokens: Number($("maxTokensInput").value),
    context_window: Number($("contextInput").value),
    max_steps: Number($("maxStepsInput").value),
    stream: false,
    enabled_tools: state.enabledTools.slice(),
  };
}

// Read just the appearance/theme settings from the UI.
function themeFromUi() {
  return {
    accent_color: normalizeHexColor($("themeColorTextInput")?.value || state.themeColor || DEFAULT_THEME_COLOR),
  };
}

// Small JSON API helper. All dashboard server endpoints return JSON; failed
// requests throw so callers can show a status message.
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

// ============================================================================
// Sessions, Chat, Files, and Timeline
// ============================================================================

// Render the left sidebar list of conversations. Each row has a main button for
// selecting the session and a separate delete button.
function renderSessions() {
  const list = $("sessionList");
  list.innerHTML = "";
  if (!state.sessions.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No sessions yet";
    list.appendChild(empty);
    return;
  }
  state.sessions
    .slice()
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .forEach((session) => {
      const row = document.createElement("div");
      row.className = `session-row ${session.id === state.sessionId ? "active" : ""}`;

      const button = document.createElement("button");
      button.className = "session-item";
      button.type = "button";
      button.innerHTML = `<strong>${escapeHtml(session.title)}</strong><span>${session.status} · ${new Date(session.updated_at).toLocaleTimeString()}</span>`;
      button.addEventListener("click", () => {
        state.sessionId = session.id;
        state.forceScrollOnNextRender = true;
        state.lastRenderedMessageSignature = "";
        renderSession(session);
        pollSession();
      });

      const deleteButton = document.createElement("button");
      deleteButton.className = "session-delete";
      deleteButton.type = "button";
      deleteButton.title = `Delete ${session.title}`;
      deleteButton.setAttribute("aria-label", `Delete ${session.title}`);
      deleteButton.textContent = "×";
      deleteButton.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteSession(session);
      });

      row.append(button, deleteButton);
      list.appendChild(row);
    });
}

// Render the active chat session into the middle column. The signature check
// prevents rewriting the chat DOM while nothing meaningful changed, which also
// protects text selection while the polling loop is running.
function renderSession(session) {
  if (!session) return;
  const chat = $("chatLog");
  const isSameSession = state.lastRenderedSessionId === session.id;
  const messageSignature = getMessageSignature(session);
  const chatHasSelection = hasSelectionInside(chat);
  const distanceFromBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight;
  const wasNearBottom = distanceFromBottom < 80;
  const shouldStickToBottom = state.forceScrollOnNextRender || !isSameSession || wasNearBottom;

  state.sessionId = session.id;
  state.lastRenderedSessionId = session.id;
  $("runStatus").textContent = session.status;
  $("tokenMetric").textContent = String(session.token_estimate || 0);
  const usage = Math.round((session.context_usage || 0) * 100);
  $("contextMetric").textContent = `${usage}%`;
  $("contextBar").style.width = `${Math.min(100, usage)}%`;

  if (messageSignature !== state.lastRenderedMessageSignature && (!chatHasSelection || !isSameSession)) {
    chat.innerHTML = "";
    if (!session.visible_messages.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Start with a build request or ask the agent to inspect the workspace.";
      chat.appendChild(empty);
    }
    session.visible_messages.forEach((message) => {
      const node = document.createElement("article");
      node.className = `message ${message.role}`;
      node.innerHTML = `<span class="role">${escapeHtml(message.role)}</span><div class="message-content">${renderMarkdown(message.content)}</div>`;
      const gallery = renderMessageImages(message.images || []);
      if (gallery) node.appendChild(gallery);
      chat.appendChild(node);
    });
    state.lastRenderedMessageSignature = messageSignature;
    if (shouldStickToBottom) {
      chat.scrollTop = chat.scrollHeight;
    }
  }
  state.forceScrollOnNextRender = false;

  renderFiles(session);
  renderTimeline(session);
}

// Render the "Files" panel in the right sidebar from the session's modified
// file list.
function renderFiles(session) {
  const files = $("fileList");
  files.innerHTML = "";
  if (!session.modified_files.length) {
    files.className = "file-list empty";
    files.textContent = "No files yet";
    return;
  }
  files.className = "file-list";
  session.modified_files.forEach((file) => {
    const item = document.createElement("div");
    item.className = "file-item";
    item.textContent = file;
    files.appendChild(item);
  });
}

// Render recent orchestrator events in reverse chronological order. Only the
// latest 36 are shown in the sidebar; full details are still available in the
// modal opened by clicking an event.
function renderTimeline(session) {
  const timeline = $("timeline");
  const allEvents = session.events || [];
  const events = allEvents.slice().reverse().slice(0, 36);
  $("timelineCount").textContent = events.length ? `latest ${events.length}/${allEvents.length}` : "0 events";
  timeline.innerHTML = "";
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty timeline-empty";
    empty.textContent = "No orchestrator activity yet";
    timeline.appendChild(empty);
    return;
  }
  events.forEach((event) => {
    const type = event.type || "event";
    const details = timelineDetails(event);
    const item = document.createElement("div");
    item.className = `event-item ${escapeClass(type)}`;
    item.tabIndex = 0;
    item.role = "button";
    item.title = "Open full timeline event";
    item.setAttribute("aria-label", `Open timeline event ${timelineTypeLabel(type)}`);
    item.innerHTML = `
      <div class="event-dot" aria-hidden="true"></div>
      <div class="event-main">
        <div class="event-line">
          <strong>${escapeHtml(timelineTypeLabel(type))}</strong>
          <span>${escapeHtml(clampInline(event.message || type, 150))}</span>
          <time>${formatEventTime(event.timestamp)}</time>
        </div>
        ${details.length ? `<div class="event-details">${details.map((detail) => `<span class="event-chip">${escapeHtml(detail)}</span>`).join("")}</div>` : ""}
      </div>
    `;
    item.addEventListener("click", () => openTimelineDetail(event));
    item.addEventListener("keydown", (keyboardEvent) => {
      if (keyboardEvent.key !== "Enter" && keyboardEvent.key !== " ") return;
      keyboardEvent.preventDefault();
      openTimelineDetail(event);
    });
    timeline.appendChild(item);
  });
}

// Convert backend event types into compact UI labels.
function timelineTypeLabel(type) {
  const labels = {
    user: "USER",
    llm: "LLM",
    thinking: "THINK",
    retry: "RETRY",
    fallback: "FALLBACK",
    done: "DONE",
    tool_call: "TOOL",
    tool_result: "RESULT",
    context: "CONTEXT",
    stopped: "STOP",
    error: "ERROR",
    restored: "RESTORE",
  };
  return labels[type] || String(type).replaceAll("_", " ").toUpperCase();
}

// Pick a few useful details to show inline under a timeline event. The complete
// event JSON is still shown in the detail modal.
function timelineDetails(event) {
  const data = event.data || {};
  const details = [];
  if (data.tool) details.push(`tool: ${data.tool}`);
  if (data.model) details.push(`model: ${data.model}`);
  if (data.token_estimate !== undefined) details.push(`tokens: ${data.token_estimate}`);
  if (data.before_tokens !== undefined && data.after_tokens !== undefined) details.push(`${data.before_tokens} -> ${data.after_tokens} tokens`);
  if (data.summary_chars !== undefined) details.push(`summary: ${data.summary_chars} chars`);
  if (data.session_summary_file) details.push(`session summary: ${data.session_summary_file}`);
  if (data.message_count !== undefined) details.push(`messages: ${data.message_count}`);
  if (data.max_steps !== undefined) details.push(`steps: ${data.max_steps}`);
  if (data.ok !== undefined) details.push(data.ok ? "ok" : "failed");
  if (data.result?.exit_code !== undefined) details.push(`exit: ${data.result.exit_code}`);
  if (data.result?.path) details.push(`path: ${data.result.path}`);
  if (data.result?.file_path) details.push(`file: ${data.result.file_path}`);
  if (data.args) details.push(`args: ${compactJson(data.args, 120)}`);
  if (data.error) details.push(`error: ${clampInline(data.error, 120)}`);
  return details.slice(0, 4);
}

// Make JSON safe for short timeline chips.
function compactJson(value, limit = 120) {
  try {
    return clampInline(JSON.stringify(value), limit);
  } catch {
    return clampInline(String(value), limit);
  }
}

// Collapse whitespace and truncate text that needs to fit on one row.
function clampInline(value, limit = 120) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 1))}...`;
}

// Format event timestamps for compact timeline display.
function formatEventTime(timestamp) {
  const date = timestamp ? new Date(timestamp) : null;
  if (!date || Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Open the full event modal for a timeline item.
function openTimelineDetail(event) {
  state.selectedTimelineEvent = event;
  const type = event.type || "event";
  const timestamp = event.timestamp ? new Date(event.timestamp) : null;
  const fullTime = timestamp && !Number.isNaN(timestamp.getTime()) ? timestamp.toLocaleString() : "Unknown time";
  $("timelineDetailTitle").textContent = `${timelineTypeLabel(type)} event`;
  $("timelineDetailMeta").textContent = `${fullTime} · ${type}`;
  $("timelineDetailBody").innerHTML = `
    ${timelineDetailSection("Message", event.message || "(empty)")}
    ${timelineDetailSection("Data", prettyJson(event.data || {}), true)}
    ${timelineDetailSection("Complete Event", prettyJson(event), true)}
  `;
  $("timelineDetailModal").classList.add("open");
  $("timelineDetailModal").setAttribute("aria-hidden", "false");
  $("timelineDetailCloseBtn").focus();
}

// Close the full event modal and forget the selected event.
function closeTimelineDetail() {
  state.selectedTimelineEvent = null;
  $("timelineDetailModal").classList.remove("open");
  $("timelineDetailModal").setAttribute("aria-hidden", "true");
}

// Build one section inside the timeline detail modal. Code sections are escaped
// and wrapped in <pre> for readable JSON.
function timelineDetailSection(title, value, code = false) {
  return `
    <section class="timeline-detail-section">
      <h3>${escapeHtml(title)}</h3>
      ${code ? `<pre>${escapeHtml(value)}</pre>` : `<p>${escapeHtml(value)}</p>`}
    </section>
  `;
}

// Fetch the session list from the server and keep the currently selected
// session if possible.
async function refreshSessions() {
  const data = await api("/api/sessions");
  state.sessions = data.sessions || [];
  if (!state.sessionId && state.sessions.length) {
    state.sessionId = state.sessions
      .slice()
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0].id;
  }
  renderSessions();
  if (state.sessionId) {
    const current = state.sessions.find((session) => session.id === state.sessionId);
    if (current) renderSession(current);
    if (state.logsOpen && state.logView === "trace") await refreshLogs();
  }
}

// Fetch the active session. This is used after sending messages and by the
// polling loop to keep chat/status/timeline fresh.
async function pollSession() {
  if (!state.sessionId) return;
  const session = await api(`/api/sessions/${state.sessionId}`);
  const index = state.sessions.findIndex((item) => item.id === session.id);
  if (index >= 0) state.sessions[index] = session;
  else state.sessions.push(session);
  renderSessions();
  renderSession(session);
  if (state.logsOpen && state.logView === "trace") await refreshLogs();
}

// Create a fresh server-side session and switch the UI to it immediately.
async function createSession() {
  const session = await api("/api/sessions", { method: "POST", body: "{}" });
  state.sessionId = session.id;
  state.forceScrollOnNextRender = true;
  state.lastRenderedMessageSignature = "";
  state.sessions.push(session);
  renderSessions();
  renderSession(session);
  if (state.logsOpen && state.logView === "trace") await refreshLogs();
}

// Delete one dashboard session after confirmation. If it was selected, choose
// the newest remaining session or clear the main view.
async function deleteSession(session) {
  const title = session.title || "this session";
  if (!window.confirm(`Delete "${title}"? This only removes it from the dashboard history.`)) return;
  await api(`/api/sessions/${session.id}`, { method: "DELETE" });
  state.sessions = state.sessions.filter((item) => item.id !== session.id);

  if (state.sessionId === session.id) {
    const nextSession = state.sessions
      .slice()
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
    state.sessionId = nextSession ? nextSession.id : null;
    state.forceScrollOnNextRender = true;
    state.lastRenderedMessageSignature = "";
    state.lastRenderedSessionId = null;
    if (nextSession) renderSession(nextSession);
    else {
      $("chatLog").innerHTML = '<div class="empty">Start with a build request or ask the agent to inspect the workspace.</div>';
      resetSessionMetrics();
    }
  }

  renderSessions();
  if (state.logsOpen) await refreshLogs();
}

// Submit a user message plus any uploaded images to the backend. Preferences are
// saved first so the turn uses the current model/tool/theme settings.
async function sendMessage(event) {
  event.preventDefault();
  await savePreferencesNow();
  const input = $("messageInput");
  const message = input.value.trim() || (state.pendingImages.length ? "Please analyze the attached image(s)." : "");
  if (!message && !state.pendingImages.length) return;
  const images = state.pendingImages.map(({ preview_url, ...image }) => image);
  input.value = "";
  state.pendingImages.forEach((image) => {
    if (image.preview_url) URL.revokeObjectURL(image.preview_url);
  });
  state.pendingImages = [];
  renderImagePreview();
  const payload = {
    session_id: state.sessionId,
    message,
    settings: settingsFromUi(),
    images,
  };
  const result = await api("/api/chat", { method: "POST", body: JSON.stringify(payload) });
  state.sessionId = result.session_id;
  state.forceScrollOnNextRender = true;
  await pollSession();
}

// ============================================================================
// LM Studio Status and Workspace State
// ============================================================================

// Ask the backend whether LM Studio is reachable for the selected endpoint/model
// and update the status pill.
async function checkStatus() {
  const endpoint = encodeURIComponent($("endpointInput").value.trim());
  const model = encodeURIComponent($("modelInput").value.trim());
  const status = $("serverStatus");
  try {
    const data = await api(`/api/status?endpoint=${endpoint}&model=${model}`);
    status.className = `pill ${data.ok ? "ok" : "error"}`;
    status.textContent = data.ok ? statusText(data) : "LM Studio offline";
    applyModelContextFromStatus(data);
  } catch {
    status.className = "pill error";
    status.textContent = "LM Studio offline";
  }
}

// Build the short top-right LM Studio status text.
function statusText(data) {
  const context = data.selected_model_context || {};
  const tokens = Number(context.context_window || 0);
  if (!tokens) return data.cached ? "LM Studio online · cached" : "LM Studio online";
  const suffix = data.cached ? " · cached" : "";
  return `LM Studio online · ctx ${tokens.toLocaleString()}${suffix}`;
}

// If the backend can see the selected model's context window, copy it into the
// settings form so the summarizer threshold matches the model.
function applyModelContextFromStatus(data) {
  if (!data.ok || !data.selected_model_context) return;
  const context = Number(data.selected_model_context.context_window || 0);
  if (!context) return;
  const contextInput = $("contextInput");
  contextInput.max = String(Math.max(context, Number(contextInput.max || 0)));
  contextInput.value = String(context);
  const maxTokensInput = $("maxTokensInput");
  if (Number(maxTokensInput.value) > context) {
    maxTokensInput.value = String(context);
  }
}

// Load Docker workspace health/configuration shown in the left sidebar.
async function loadWorkspace() {
  const data = await api("/api/workspace");
  state.workspace = data;
  renderWorkspace(data);
}

// Paint the workspace panel from the server's workspace status response.
function renderWorkspace(workspace) {
  $("workspacePath").textContent = workspace.workspace || "docker://Thursday/workspace";
  $("workspaceContainer").textContent = workspace.container || "Thursday";
  $("workspaceImage").textContent = workspace.image || workspace.configured_image || "ubuntu:24.04";
  $("workspaceWorkdir").textContent = workspace.workdir || "/workspace";
  $("workspaceStatusBadge").textContent = workspace.running ? "running" : workspace.exists ? "stopped" : "missing";
  $("workspaceStatusBadge").className = `mini-meta workspace-badge ${workspace.running ? "ok" : "error"}`;
  setMaintenanceButtons();
}

// Load the user-controlled GUI region used by the gui_for_llm tool. This tells
// the model where it is allowed to look/click without making the model choose a
// monitor region itself.
async function loadGuiForLlm() {
  try {
    const data = await api("/api/gui-for-llm");
    state.guiForLlm.displays = data.displays || [];
    state.guiForLlm.activeRegion = data.active_region || null;
    renderGuiForLlmControls();
  } catch (error) {
    $("guiRegionStatus").textContent = "unavailable";
    console.warn(error);
  }
}

// Render display options and region fields. If no active region exists yet, the
// first display is used as a sensible default.
function renderGuiForLlmControls() {
  const displays = state.guiForLlm.displays || [];
  const select = $("guiDisplaySelect");
  select.innerHTML = "";
  displays.forEach((display) => {
    const option = document.createElement("option");
    option.value = String(display.id);
    option.textContent = `${display.primary ? "Primary" : "Display"} ${display.id} · ${display.width}×${display.height}`;
    select.appendChild(option);
  });

  const fallback = displays[0] ? {
    display: displays[0].id,
    x: 0,
    y: 0,
    width: displays[0].width,
    height: displays[0].height,
  } : null;
  const region = state.guiForLlm.activeRegion || fallback;
  if (!region) {
    $("guiRegionStatus").textContent = "no screen";
    return;
  }

  select.value = String(region.display || 0);
  $("guiRegionX").value = String(region.x || 0);
  $("guiRegionY").value = String(region.y || 0);
  $("guiRegionWidth").value = String(region.width || 800);
  $("guiRegionHeight").value = String(region.height || 600);
  $("guiRegionStatus").textContent = state.guiForLlm.activeRegion ? "ready" : "not set";
}

// Read the region form and clamp it to the selected display dimensions.
function guiRegionFromUi() {
  const displayId = Number($("guiDisplaySelect").value || 0);
  const display = (state.guiForLlm.displays || []).find((item) => Number(item.id) === displayId);
  const maxWidth = Number(display?.width || 800);
  const maxHeight = Number(display?.height || 600);
  const x = clampNumber(Number($("guiRegionX").value || 0), 0, Math.max(0, maxWidth - 20));
  const y = clampNumber(Number($("guiRegionY").value || 0), 0, Math.max(0, maxHeight - 20));
  const width = clampNumber(Number($("guiRegionWidth").value || maxWidth), 20, Math.max(20, maxWidth - x));
  const height = clampNumber(Number($("guiRegionHeight").value || maxHeight), 20, Math.max(20, maxHeight - y));
  return { display: displayId, x, y, width, height };
}

// Save the selected GUI region on the backend. The gui_for_llm tool will use
// this active region by default for screenshots and input actions.
async function saveGuiRegion() {
  const region = guiRegionFromUi();
  $("guiRegionStatus").textContent = "saving";
  const result = await api("/api/gui-for-llm/region", { method: "POST", body: JSON.stringify(region) });
  state.guiForLlm.activeRegion = result.output?.active_region || region;
  $("guiRegionStatus").textContent = result.ok ? "ready" : "failed";
  renderGuiForLlmControls();
}

// Capture a screenshot of the saved GUI region and show a small preview. This
// is only a dashboard preview; the model gets screenshots through tool results.
async function previewGuiRegion() {
  $("guiRegionStatus").textContent = "capturing";
  const result = await api("/api/gui-for-llm/screenshot", { method: "POST", body: "{}" });
  const image = result.output?.llm_images?.[0];
  const preview = $("guiRegionPreview");
  if (image?.path) {
    preview.src = imageUrlForPath(image.path);
    preview.hidden = false;
    state.guiForLlm.previewPath = image.path;
  }
  $("guiRegionStatus").textContent = result.ok ? "previewed" : "failed";
}

function imageUrlForPath(path) {
  return `/api/images?path=${encodeURIComponent(path)}`;
}

function clampNumber(value, minimum, maximum) {
  if (!Number.isFinite(value)) return minimum;
  return Math.max(minimum, Math.min(Math.round(value), maximum));
}

// Recreate the Docker workspace container after confirmation. This does not
// delete host files or dashboard sessions.
async function resetWorkspace() {
  if (state.maintenanceBusy || state.workspaceResetting) return;
  const container = state.workspace?.container || "Thursday";
  const image = state.workspace?.configured_image || state.workspace?.image || "ubuntu:24.04";
  const confirmed = window.confirm(
    `Fresh start Docker workspace?\n\nThis will stop and delete container "${container}", then recreate an empty "${image}" container with the same name and workdir.\n\nHost project files and dashboard sessions are not deleted.`
  );
  if (!confirmed) return;

  state.workspaceResetting = true;
  state.maintenanceBusy = true;
  setMaintenanceButtons();
  setMaintenanceStatus("Clearing workspace...");
  try {
    const result = await api("/api/workspace/reset", { method: "POST", body: "{}" });
    renderWorkspace(result.status || result);
    $("workspaceResetStatus").textContent = result.ok ? "Fresh Docker workspace is ready." : result.error || "Workspace reset failed.";
    setMaintenanceStatus(result.ok ? "Workspace cleared" : result.error || "Workspace failed");
    await loadTools();
  } catch (error) {
    $("workspaceResetStatus").textContent = error.message || "Workspace reset failed.";
    setMaintenanceStatus("Workspace failed");
    await loadWorkspace().catch(() => {});
  } finally {
    state.workspaceResetting = false;
    state.maintenanceBusy = false;
    setMaintenanceButtons();
  }
}

// Remove all dashboard sessions after confirmation, then reset the visible chat,
// metrics, files, and timeline panels.
async function clearAllSessions() {
  if (state.maintenanceBusy) return;
  const confirmed = window.confirm(
    "Clear all sessions?\n\nThis removes every dashboard conversation and trace from the session list. Logs, reminders, preferences, and Docker workspace files are not deleted."
  );
  if (!confirmed) return;
  state.maintenanceBusy = true;
  setMaintenanceButtons();
  setMaintenanceStatus("Clearing sessions...");
  try {
    const result = await api("/api/sessions/clear", { method: "POST", body: "{}" });
    state.sessions = [];
    state.sessionId = null;
    state.lastRenderedSessionId = null;
    state.lastRenderedMessageSignature = "";
    renderSessions();
    clearSessionViews();
    if (state.logsOpen) await refreshLogs();
    setMaintenanceStatus(`Cleared ${result.cleared || 0} sessions`);
  } catch (error) {
    setMaintenanceStatus(error.message || "Session clear failed");
    await refreshSessions().catch(() => {});
  } finally {
    state.maintenanceBusy = false;
    setMaintenanceButtons();
  }
}

// Reset the center/right session-dependent panels when there is no active
// session to render.
function clearSessionViews() {
  const chat = $("chatLog");
  chat.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = "No sessions yet. Start a new conversation when you're ready.";
  chat.appendChild(empty);
  $("runStatus").textContent = "idle";
  $("tokenMetric").textContent = "0";
  $("contextMetric").textContent = "0%";
  $("contextBar").style.width = "0%";
  renderFiles({ modified_files: [] });
  renderTimeline({ events: [] });
}

// Clear logs without touching sessions, preferences, reminders, or workspace
// files.
async function clearAllLogs() {
  if (state.maintenanceBusy) return;
  const confirmed = window.confirm(
    "Clear all logs?\n\nThis empties app, HTTP, raw LM Studio logs, legacy raw log files, and visual-check screenshots. Sessions, reminders, preferences, and workspace files are not deleted."
  );
  if (!confirmed) return;
  state.maintenanceBusy = true;
  setMaintenanceButtons();
  setMaintenanceStatus("Clearing logs...");
  try {
    const result = await api("/api/logs/clear", { method: "POST", body: "{}" });
    if (state.logsOpen) await refreshLogs();
    setMaintenanceStatus(result.ok ? "Logs cleared" : result.error || "Log clear failed");
  } catch (error) {
    setMaintenanceStatus(error.message || "Log clear failed");
  } finally {
    state.maintenanceBusy = false;
    setMaintenanceButtons();
  }
}

// Disable maintenance buttons while a destructive/long-running maintenance
// action is active.
function setMaintenanceButtons() {
  ["clearSessionsBtn", "clearLogsBtn", "resetWorkspaceBtn"].forEach((id) => {
    const button = $(id);
    if (button) button.disabled = state.maintenanceBusy || (id === "resetWorkspaceBtn" && state.workspaceResetting);
  });
}

// Short status text beside the Maintenance heading.
function setMaintenanceStatus(message) {
  $("maintenanceStatus").textContent = message || "ready";
}

// ============================================================================
// Tool List and Preferences
// ============================================================================

// Load all tool definitions and enabled/required state from the backend.
async function loadTools() {
  const data = await api("/api/tools");
  $("workspacePath").textContent = data.workspace;
  state.allTools = data.all_tools || data.tools || [];
  state.requiredTools = data.required_tools || state.requiredTools || [];
  if (!state.enabledTools.length) state.enabledTools = data.enabled_tools || state.allTools.map(toolName);
  ensureRequiredToolsEnabled();
  renderToolControls();
}

// Render the left sidebar tool toggles. Required tools are shown checked and
// disabled because the system needs them to function.
function renderToolControls() {
  const list = $("toolList");
  list.innerHTML = "";
  ensureRequiredToolsEnabled();
  const enabled = new Set(state.enabledTools);
  const required = new Set(state.requiredTools);
  $("toolCount").textContent = `${enabled.size}/${state.allTools.length} enabled`;
  state.allTools.forEach((tool) => {
    const name = toolName(tool);
    const isRequired = required.has(name);
    const item = document.createElement("label");
    item.className = `tool-toggle ${enabled.has(name) ? "enabled" : ""} ${isRequired ? "required" : ""}`;
    item.innerHTML = `
      <input type="checkbox" ${enabled.has(name) ? "checked" : ""} ${isRequired ? "disabled" : ""} />
      <span>
        <strong>${escapeHtml(name)}${isRequired ? " · required" : ""}</strong>
        <small>${escapeHtml(tool.function.description)}</small>
      </span>
    `;
    item.querySelector("input").addEventListener("change", (event) => {
      const checked = event.target.checked;
      const set = new Set(state.enabledTools);
      if (checked) set.add(name);
      else set.delete(name);
      state.enabledTools = Array.from(set).filter((itemName) => state.allTools.some((toolDef) => toolName(toolDef) === itemName));
      renderToolControls();
      schedulePreferencesSave();
    });
    list.appendChild(item);
  });
}

// Load immutable/default app config from the backend.
async function loadConfig() {
  const config = await api("/api/config");
  state.config = config;
}

// Load saved user preferences, then apply them to the settings UI.
async function loadPreferences() {
  const preferences = await api("/api/preferences");
  applyPreferences(preferences);
}

// Copy backend preferences into UI controls and local state.
function applyPreferences(preferences) {
  state.preferences = preferences;
  const settings = preferences.settings || state.config.default_settings;
  const theme = preferences.theme || preferences.defaults?.theme || {};
  state.requiredTools = (preferences.required_tools || []).slice();
  state.enabledTools = (preferences.enabled_tools || preferences.defaults?.enabled_tools || []).slice();
  ensureRequiredToolsEnabled();
  applyTheme(theme.accent_color || DEFAULT_THEME_COLOR);
  $("endpointInput").value = settings.endpoint;
  $("modelInput").value = settings.model;
  $("temperatureInput").value = settings.temperature;
  $("temperatureValue").textContent = String(settings.temperature);
  $("topPInput").value = settings.top_p;
  $("topPValue").textContent = String(settings.top_p);
  $("maxTokensInput").value = settings.max_tokens;
  $("contextInput").value = settings.context_window;
  $("maxStepsInput").value = settings.max_steps;
  if (state.allTools.length) renderToolControls();
}

// Required tools must stay enabled even if the user changes tool selections.
function ensureRequiredToolsEnabled() {
  const known = new Set(state.allTools.map(toolName));
  const enabled = new Set(state.enabledTools);
  state.requiredTools.forEach((name) => {
    if (!known.size || known.has(name)) enabled.add(name);
  });
  state.enabledTools = Array.from(enabled).filter((name) => !known.size || known.has(name));
}

// ============================================================================
// Event Binding and Resizable Panels
// ============================================================================

// Attach every DOM event listener used by the dashboard. If a button/control
// needs new behavior, this is the first place to look.
function bindUi() {
  $("chatForm").addEventListener("submit", sendMessage);
  $("attachImageBtn").addEventListener("click", () => $("imageInput").click());
  $("imageInput").addEventListener("change", handleImageSelection);
  $("messageInput").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    $("chatForm").requestSubmit();
  });
  $("newSessionBtn").addEventListener("click", createSession);
  $("clearSessionsBtn").addEventListener("click", clearAllSessions);
  $("clearLogsBtn").addEventListener("click", clearAllLogs);
  $("resetWorkspaceBtn").addEventListener("click", resetWorkspace);
  $("guiDisplaySelect").addEventListener("change", () => {
    const displayId = Number($("guiDisplaySelect").value || 0);
    const display = (state.guiForLlm.displays || []).find((item) => Number(item.id) === displayId);
    if (!display) return;
    $("guiRegionX").value = "0";
    $("guiRegionY").value = "0";
    $("guiRegionWidth").value = String(display.width);
    $("guiRegionHeight").value = String(display.height);
    $("guiRegionStatus").textContent = "edited";
  });
  ["guiRegionX", "guiRegionY", "guiRegionWidth", "guiRegionHeight"].forEach((id) => {
    $(id).addEventListener("change", () => {
      const region = guiRegionFromUi();
      $("guiRegionX").value = String(region.x);
      $("guiRegionY").value = String(region.y);
      $("guiRegionWidth").value = String(region.width);
      $("guiRegionHeight").value = String(region.height);
      $("guiRegionStatus").textContent = "edited";
    });
  });
  $("saveGuiRegionBtn").addEventListener("click", saveGuiRegion);
  $("previewGuiRegionBtn").addEventListener("click", previewGuiRegion);
  $("temperatureInput").addEventListener("input", () => {
    $("temperatureValue").textContent = $("temperatureInput").value;
    schedulePreferencesSave();
  });
  $("topPInput").addEventListener("input", () => {
    $("topPValue").textContent = $("topPInput").value;
    schedulePreferencesSave();
  });
  ["endpointInput", "modelInput", "maxTokensInput", "contextInput", "maxStepsInput"].forEach((id) => {
    $(id).addEventListener("change", () => {
      schedulePreferencesSave();
      if (id === "endpointInput" || id === "modelInput") checkStatus();
    });
  });
  $("themeColorInput").addEventListener("input", () => {
    applyTheme($("themeColorInput").value);
    schedulePreferencesSave();
  });
  $("themeColorTextInput").addEventListener("change", () => {
    applyTheme($("themeColorTextInput").value);
    schedulePreferencesSave();
  });
  $("logsTab").addEventListener("click", toggleLogsDrawer);
  $("logsCloseBtn").addEventListener("click", closeLogsDrawer);
  $("settingsTab").addEventListener("click", toggleSettingsDrawer);
  $("settingsCloseBtn").addEventListener("click", closeSettingsDrawer);
  bindResizablePanels();
  $("timelineDetailCloseBtn").addEventListener("click", closeTimelineDetail);
  $("timelineDetailModal").addEventListener("click", (event) => {
    if (event.target === $("timelineDetailModal")) closeTimelineDetail();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("timelineDetailModal").classList.contains("open")) {
      closeTimelineDetail();
    }
  });
  window.addEventListener("resize", () => {
    Object.keys(PANEL_WIDTH_DEFAULTS).forEach((key) => {
      setPanelWidth(key, currentPanelWidth(key), false);
    });
  });
  $("saveSettingsBtn").addEventListener("click", saveSettingsClicked);
  $("restoreDefaultsBtn").addEventListener("click", restoreDefaults);
  document.querySelectorAll("[data-log-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.logView = button.dataset.logView;
      renderLogTabs();
      await refreshLogs();
    });
  });
}

// Restore saved sidebar/drawer widths from localStorage.
function loadPanelWidths() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY) || "{}");
  } catch {
    saved = {};
  }
  Object.keys(PANEL_WIDTH_DEFAULTS).forEach((key) => {
    const value = Number(saved[key] || PANEL_WIDTH_DEFAULTS[key]);
    setPanelWidth(key, value, false);
  });
}

// Persist one resized panel width to localStorage.
function savePanelWidth(key, value) {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY) || "{}");
  } catch {
    saved = {};
  }
  saved[key] = Math.round(value);
  localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, JSON.stringify(saved));
}

// Apply one width to the matching CSS variable and optionally persist it.
function setPanelWidth(key, value, persist = true) {
  const cssVar = PANEL_WIDTH_VARS[key];
  if (!cssVar) return;
  const clamped = clampPanelWidth(key, value);
  document.documentElement.style.setProperty(cssVar, `${clamped}px`);
  if (persist) savePanelWidth(key, clamped);
}

// Keep resizable panels usable by clamping them to the current viewport.
function clampPanelWidth(key, value) {
  const viewport = Math.max(320, window.innerWidth || 1200);
  const handleSpace = viewport > 1050 ? 12 : 6;
  const minChat = viewport > 1050 ? 360 : 280;
  const otherSideWidth = key === "left" ? currentPanelWidth("right") : key === "right" ? currentPanelWidth("left") : 0;
  const availableSideWidth = Math.max(220, viewport - otherSideWidth - minChat - handleSpace);
  const limits = {
    left: [220, Math.min(520, Math.round(viewport * 0.38), availableSideWidth)],
    right: [260, Math.min(620, Math.round(viewport * 0.42), availableSideWidth)],
    logs: [420, Math.max(420, viewport - 28)],
    settings: [340, Math.min(620, Math.max(340, viewport - 28))],
  }[key] || [220, viewport - 28];
  return Math.min(limits[1], Math.max(limits[0], Number(value) || PANEL_WIDTH_DEFAULTS[key] || limits[0]));
}

// Wire all resize handles. Sidebars resize from their nearest edge; drawers
// resize from the right side of the viewport.
function bindResizablePanels() {
  bindResizeHandle("leftResizeHandle", "left", (clientX) => {
    const layout = document.querySelector(".layout");
    const rect = layout.getBoundingClientRect();
    return clientX - rect.left;
  }, 1);

  bindResizeHandle("rightResizeHandle", "right", (clientX) => window.innerWidth - clientX, -1);
  bindResizeHandle("logsDrawerResizeHandle", "logs", (clientX) => window.innerWidth - clientX, -1);
  bindResizeHandle("settingsDrawerResizeHandle", "settings", (clientX) => window.innerWidth - clientX, -1);
}

// Add pointer and keyboard resizing behavior to one handle.
function bindResizeHandle(handleId, key, valueFromPointer, keyboardDirection) {
  const handle = $(handleId);
  if (!handle) return;
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    document.body.classList.add("resizing");
    handle.setPointerCapture?.(event.pointerId);

    const onMove = (moveEvent) => {
      setPanelWidth(key, valueFromPointer(moveEvent.clientX));
    };
    const onEnd = () => {
      document.body.classList.remove("resizing");
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onEnd);
      document.removeEventListener("pointercancel", onEnd);
    };

    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onEnd);
    document.addEventListener("pointercancel", onEnd);
    onMove(event);
  });

  handle.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const current = currentPanelWidth(key);
    const delta = event.key === "ArrowRight" ? 24 : -24;
    setPanelWidth(key, current + delta * keyboardDirection);
  });
}

// Read the current pixel width from the CSS variable for a panel/drawer.
function currentPanelWidth(key) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(PANEL_WIDTH_VARS[key]);
  const parsed = Number(String(value).replace("px", "").trim());
  return Number.isFinite(parsed) ? parsed : PANEL_WIDTH_DEFAULTS[key];
}

// ============================================================================
// Theme and Color Utilities
// ============================================================================

// Apply a selected accent color by updating CSS custom properties. The CSS file
// uses these variables for borders, glows, buttons, and status accents.
function applyTheme(value) {
  const color = normalizeHexColor(value);
  const rgb = hexToRgb(color);
  const light = mixRgb(rgb, { r: 255, g: 255, b: 255 }, 0.58);
  const muted = mixRgb(rgb, { r: 190, g: 184, b: 205 }, 0.48);
  const deep = mixRgb(rgb, { r: 0, g: 0, b: 0 }, 0.78);
  const root = document.documentElement;
  state.themeColor = color;
  root.style.setProperty("--accent", color);
  root.style.setProperty("--accent-rgb", `${rgb.r}, ${rgb.g}, ${rgb.b}`);
  root.style.setProperty("--accent-2", rgbToHex(light));
  root.style.setProperty("--accent-muted", rgbToHex(muted));
  root.style.setProperty("--accent-deep", rgbToHex(deep));
  $("themeColorInput").value = color;
  $("themeColorTextInput").value = color;
}

// Accept only full 6-digit hex colors. Invalid input falls back to default.
function normalizeHexColor(value) {
  const text = String(value || DEFAULT_THEME_COLOR).trim();
  if (/^#[0-9a-fA-F]{6}$/.test(text)) return text.toLowerCase();
  return DEFAULT_THEME_COLOR;
}

// Convert #rrggbb into numeric RGB components.
function hexToRgb(hex) {
  const value = normalizeHexColor(hex).slice(1);
  return {
    r: Number.parseInt(value.slice(0, 2), 16),
    g: Number.parseInt(value.slice(2, 4), 16),
    b: Number.parseInt(value.slice(4, 6), 16),
  };
}

// Blend two RGB colors. Used to produce lighter/muted/deeper variants of the
// selected accent color.
function mixRgb(first, second, amount) {
  const ratio = Math.max(0, Math.min(1, Number(amount) || 0));
  return {
    r: Math.round(first.r * (1 - ratio) + second.r * ratio),
    g: Math.round(first.g * (1 - ratio) + second.g * ratio),
    b: Math.round(first.b * (1 - ratio) + second.b * ratio),
  };
}

// Convert numeric RGB components back into #rrggbb.
function rgbToHex(rgb) {
  return `#${[rgb.r, rgb.g, rgb.b].map((part) => Math.max(0, Math.min(255, part)).toString(16).padStart(2, "0")).join("")}`;
}

// ============================================================================
// Image Attachments
// ============================================================================

// Read selected image files, upload them to the backend, and keep temporary
// object URLs for local preview chips.
async function handleImageSelection(event) {
  const files = Array.from(event.target.files || []).filter((file) => file.type.startsWith("image/"));
  if (!files.length) return;
  const availableSlots = Math.max(0, 6 - state.pendingImages.length);
  const selected = files.slice(0, availableSlots);
  const loaded = await Promise.all(selected.map(uploadImageFile));
  state.pendingImages.push(...loaded.filter(Boolean));
  event.target.value = "";
  renderImagePreview();
}

// Upload one image file. The backend stores the file and returns a path/URL that
// can later be passed to the model request.
async function uploadImageFile(file) {
  const formData = new FormData();
  formData.append("image", file, file.name);
  try {
    const response = await fetch("/api/images", { method: "POST", body: formData });
    if (!response.ok) throw new Error(await response.text());
    const uploaded = await response.json();
    return {
      name: uploaded.name || file.name,
      mime_type: uploaded.mime_type || file.type || "image/png",
      size: uploaded.size || file.size,
      path: uploaded.path,
      url: uploaded.url,
      preview_url: URL.createObjectURL(file),
    };
  } catch (error) {
    console.warn("Image upload failed", error);
    return null;
  }
}

// Render removable preview chips above the composer for pending image uploads.
function renderImagePreview() {
  const preview = $("imagePreview");
  preview.innerHTML = "";
  preview.classList.toggle("empty", !state.pendingImages.length);
  state.pendingImages.forEach((image, index) => {
    const chip = document.createElement("div");
    chip.className = "image-chip";
    chip.innerHTML = `
      <img src="${escapeAttribute(image.preview_url || image.url || image.data_url)}" alt="${escapeAttribute(image.name)}">
      <span>${escapeHtml(image.name)}</span>
      <button type="button" aria-label="Remove ${escapeAttribute(image.name)}">×</button>
    `;
    chip.querySelector("button").addEventListener("click", () => {
      if (image.preview_url) URL.revokeObjectURL(image.preview_url);
      state.pendingImages.splice(index, 1);
      renderImagePreview();
    });
    preview.appendChild(chip);
  });
}

// Render images that are already part of a chat message.
function renderMessageImages(images) {
  if (!images.length) return null;
  const gallery = document.createElement("div");
  gallery.className = "message-images";
  images.forEach((image) => {
    const figure = document.createElement("figure");
    figure.innerHTML = `
      <img src="${escapeAttribute(image.url || image.data_url)}" alt="${escapeAttribute(image.name || "attached image")}">
      <figcaption>${escapeHtml(image.name || "attached image")}</figcaption>
    `;
    gallery.appendChild(figure);
  });
  return gallery;
}

// ============================================================================
// Saving Preferences and Drawers
// ============================================================================

// Debounce preference saves so sliders/text inputs do not send a request on
// every tiny intermediate edit.
function schedulePreferencesSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(savePreferencesNow, 350);
}

// Persist settings/theme/tool preferences to the backend immediately.
async function savePreferencesNow() {
  clearTimeout(state.saveTimer);
  const preferences = await api("/api/preferences", {
    method: "POST",
    body: JSON.stringify({
      settings: settingsFromUi(),
      theme: themeFromUi(),
      enabled_tools: state.enabledTools,
    }),
  });
  applyPreferences(preferences);
  setSettingsSaveStatus("Saved");
}

// Explicit Save button handler with user-facing save status.
async function saveSettingsClicked() {
  setSettingsSaveStatus("Saving...");
  try {
    await savePreferencesNow();
    await checkStatus();
  } catch (error) {
    setSettingsSaveStatus("Save failed");
    console.warn(error);
  }
}

// Brief status message inside the settings drawer.
function setSettingsSaveStatus(message) {
  const status = $("settingsSaveStatus");
  if (!status) return;
  status.textContent = message;
  if (message) {
    clearTimeout(state.settingsStatusTimer);
    state.settingsStatusTimer = setTimeout(() => {
      status.textContent = "";
    }, 1800);
  }
}

// Restore backend default preferences and refresh model status.
async function restoreDefaults() {
  const preferences = await api("/api/preferences", {
    method: "POST",
    body: JSON.stringify({ restore_defaults: true }),
  });
  applyPreferences(preferences);
  await checkStatus();
}

// Open or close the Logs drawer.
async function toggleLogsDrawer() {
  if (state.logsOpen) {
    closeLogsDrawer();
    return;
  }
  await openLogsDrawer();
}

// Open Logs and close Settings so only one drawer overlays the dashboard.
async function openLogsDrawer() {
  closeSettingsDrawer();
  state.logsOpen = true;
  document.body.classList.add("logs-open");
  $("logsDrawer").setAttribute("aria-hidden", "false");
  $("logsTab").setAttribute("aria-expanded", "true");
  renderLogTabs();
  await refreshLogs();
}

// Close the Logs drawer without changing the selected log view.
function closeLogsDrawer() {
  state.logsOpen = false;
  document.body.classList.remove("logs-open");
  $("logsDrawer").setAttribute("aria-hidden", "true");
  $("logsTab").setAttribute("aria-expanded", "false");
}

// Open or close the Settings drawer.
function toggleSettingsDrawer() {
  if (state.settingsOpen) {
    closeSettingsDrawer();
    return;
  }
  openSettingsDrawer();
}

// Open Settings and close Logs so the drawers do not stack.
function openSettingsDrawer() {
  closeLogsDrawer();
  state.settingsOpen = true;
  document.body.classList.add("settings-open");
  $("settingsDrawer").setAttribute("aria-hidden", "false");
  $("settingsTab").setAttribute("aria-expanded", "true");
}

// Close the Settings drawer.
function closeSettingsDrawer() {
  state.settingsOpen = false;
  document.body.classList.remove("settings-open");
  $("settingsDrawer").setAttribute("aria-hidden", "true");
  $("settingsTab").setAttribute("aria-expanded", "false");
}

// Highlight the active log tab.
function renderLogTabs() {
  document.querySelectorAll("[data-log-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.logView === state.logView);
  });
}

// Fetch and render whichever log view is selected. The in-flight guard prevents
// overlapping poll responses from racing each other.
async function refreshLogs() {
  if (state.logsPollInFlight) return;
  state.logsPollInFlight = true;
  try {
    if (state.logView === "trace") {
      if (!state.sessionId) renderTrace({ messages: [], status: "idle", token_estimate: 0, context_window: 0, message_count: 0 });
      else renderTrace(await api(`/api/sessions/${state.sessionId}/trace`));
      return;
    }
    if (state.logView === "raw") {
      renderRawLog(await api("/api/lmstudio/raw-log?limit=40"));
      return;
    }
    renderDatabaseLogs(await api(`/api/logs?source=${encodeURIComponent(state.logView)}&limit=120`), state.logView);
  } finally {
    state.logsPollInFlight = false;
  }
}

// Render app/http database logs as simple rows.
function renderDatabaseLogs(payload, source) {
  $("logsMeta").textContent = `${payload.logs?.length || 0} ${source.toUpperCase()} rows · ${payload.file || ""}`;
  const list = $("logsList");
  if (hasSelectionInside(list)) return;
  list.innerHTML = "";
  if (!payload.logs || !payload.logs.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = `No ${source} logs yet.`;
    list.appendChild(empty);
    return;
  }
  payload.logs.forEach((entry) => {
    const item = document.createElement("article");
    item.className = `log-row ${escapeClass(entry.level || "info")}`;
    item.innerHTML = `
      <div class="log-row-head">
        <strong>${escapeHtml(entry.level)}</strong>
        <span>${escapeHtml(entry.source)} / ${escapeHtml(entry.logger)}</span>
        <time>${new Date(entry.created_at).toLocaleString()}</time>
      </div>
      <pre>${escapeHtml(entry.message)}</pre>
    `;
    list.appendChild(item);
  });
}

// Render raw LM Studio request/response records. Open detail panels stay open
// across refreshes by storing their keys in state.rawOpenPanels.
function renderRawLog(rawLog) {
  $("logsMeta").textContent = `${rawLog.entries?.length || 0}/${rawLog.line_count || 0} raw LM Studio rows · ${rawLog.table || "sqlite"} · ${rawLog.file || ""}`;
  const list = $("logsList");
  if (hasSelectionInside(list)) return;
  const previousScrollTop = list.scrollTop;
  list.innerHTML = "";
  if (!rawLog.entries || !rawLog.entries.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No raw LM Studio records yet.";
    list.appendChild(empty);
    return;
  }
  rawLog.entries.slice().reverse().forEach((entry) => {
    list.appendChild(renderRawLogItem(entry));
  });
  list.scrollTop = previousScrollTop;
}

// Build one expandable raw LM Studio record.
function renderRawLogItem(entry) {
  const item = document.createElement("details");
  const status = entry.response?.status_code || (entry.error ? "error" : "pending");
  const failed = entry.error || Number(status) >= 400;
  item.className = `raw-record ${failed ? "error" : "ok"}`;
  const key = rawPanelKey(entry);
  if (state.rawOpenPanels.has(key)) item.open = true;
  item.innerHTML = `
    <summary class="raw-record-head">
      <strong>${escapeHtml(`record ${entry._line || entry.id || "?"}`)}</strong>
      <span>${escapeHtml(rawRecordLabel(entry, status))}</span>
      <time>${escapeHtml(formatRawTimestamp(entry.timestamp))}</time>
    </summary>
    <div class="raw-io-list">${renderRawInputOutput(entry)}</div>
  `;
  item.addEventListener("toggle", () => {
    if (item.open) state.rawOpenPanels.add(key);
    else state.rawOpenPanels.delete(key);
  });
  return item;
}

// Compose the short summary shown in a raw log record header.
function rawRecordLabel(entry, status) {
  const request = entry.request?.json || {};
  const response = entry.response || {};
  const responseJson = response.json || {};
  const usage = responseJson.usage || {};
  const choices = responseJson.choices || [];
  return [
    entry.model || request.model || "unknown model",
    `${request.messages?.length || 0} messages`,
    `${request.tools?.length || 0} tools`,
    `status ${status}`,
    entry.finish_reason || choices[0]?.finish_reason ? `finish ${entry.finish_reason || choices[0]?.finish_reason}` : "",
    usage.total_tokens ? `${usage.total_tokens} tokens` : "",
  ].filter(Boolean).join(" · ");
}

// Render the two main blocks inside a raw record: request to LM Studio and
// response/error from LM Studio.
function renderRawInputOutput(entry) {
  return `
    <section class="raw-io-block">
      <h3>Input to LM Studio</h3>
      ${rawPre(formatRawInput(entry))}
    </section>
    <section class="raw-io-block">
      <h3>Output from LM Studio</h3>
      ${rawPre(formatRawOutput(entry))}
    </section>
  `;
}

// Stable key used to remember whether a raw log panel is open.
function rawPanelKey(entry) {
  return String(entry.id || entry.request_id || entry._line || "record");
}

// Format raw record timestamps while tolerating missing/invalid data.
function formatRawTimestamp(timestamp) {
  const date = timestamp ? new Date(timestamp) : null;
  if (!date || Number.isNaN(date.getTime())) return "unknown time";
  return date.toLocaleString();
}

// Convert the stored raw request object into readable text.
function formatRawInput(entry) {
  const request = entry.request?.json || {};
  const lines = [
    `${entry.method || "POST"} ${entry.url || ""}`.trim(),
    "",
    "Headers:",
    formatReadableValue(entry.request?.headers || {}),
    "",
    "Body:",
    formatReadableRequestBody(request),
  ];
  return lines.join("\n");
}

// Convert the stored raw response/error object into readable text.
function formatRawOutput(entry) {
  if (entry.error) {
    return [
      "Request failed before a complete LM Studio response was returned.",
      "",
      "Error:",
      formatReadableValue(entry.error),
      "",
      "Stored record:",
      formatReadableValue(entry.stored_record || entry),
    ].join("\n");
  }
  const response = entry.response || {};
  const lines = [
    `HTTP ${response.status_code || entry.status_code || "unknown"}`,
    "",
    "Headers:",
    formatReadableValue(response.headers || {}),
    "",
    "Body:",
    formatReadableResponseBody(response),
  ];
  return lines.join("\n");
}

// Turn the JSON request body into a human-readable outline, especially messages
// and tool definitions.
function formatReadableRequestBody(request) {
  const body = [];
  const { messages = [], tools = [], ...settings } = request || {};
  body.push(formatReadableValue(settings));
  if (messages.length) {
    body.push("");
    body.push("messages:");
    messages.forEach((message, index) => {
      body.push("");
      body.push(`#${index + 1} ${String(message.role || "unknown").toUpperCase()}${message.name ? ` · ${message.name}` : ""}${message.tool_call_id ? ` · ${message.tool_call_id}` : ""}`);
      if (message.content !== undefined && message.content !== null) {
        body.push(formatReadableValue(message.content));
      }
      if (message.tool_calls) {
        body.push("tool_calls:");
        body.push(formatReadableValue(message.tool_calls));
      }
    });
  }
  if (tools.length) {
    body.push("");
    body.push("tools:");
    body.push(formatReadableValue(tools));
  }
  return body.join("\n");
}

// Turn raw response body data into readable text, parsing JSON strings when
// possible.
function formatReadableResponseBody(response) {
  if (response.text) return formatReadableJsonText(response.text);
  if (response.json !== undefined && response.json !== null) return formatReadableValue(response.json);
  if (response.json_error) return `JSON parse error: ${response.json_error}`;
  return "(empty response body)";
}

// Pretty-print JSON stored as text; fall back to the raw text if parsing fails.
function formatReadableJsonText(text) {
  try {
    return formatReadableValue(JSON.parse(text));
  } catch {
    return String(text || "");
  }
}

// Recursive object/array/string formatter used by the raw log viewer.
function formatReadableValue(value, indent = 0) {
  const pad = " ".repeat(indent);
  if (value === undefined) return "";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value)) {
    if (!value.length) return "[]";
    return value.map((item, index) => {
      const rendered = formatReadableValue(item, indent + 2);
      return `${pad}- [${index}]\n${indentBlock(rendered, indent + 2)}`;
    }).join("\n");
  }
  const entries = Object.entries(value);
  if (!entries.length) return "{}";
  return entries.map(([key, item]) => {
    if (item && typeof item === "object") {
      return `${pad}${key}:\n${indentBlock(formatReadableValue(item, indent + 2), indent + 2)}`;
    }
    const rendered = formatReadableValue(item, indent + 2);
    if (typeof item === "string" && rendered.includes("\n")) {
      return `${pad}${key}:\n${indentBlock(rendered, indent + 2)}`;
    }
    return `${pad}${key}: ${rendered}`;
  }).join("\n");
}

// Indent every line in a preformatted block.
function indentBlock(value, spaces = 2) {
  const prefix = " ".repeat(spaces);
  return String(value || "").split("\n").map((line) => `${prefix}${line}`).join("\n");
}

// Escape raw text before inserting it into a <pre>.
function rawPre(value) {
  return `<pre class="raw-pre">${escapeHtml(value)}</pre>`;
}

// Standard pretty JSON helper.
function prettyJson(value) {
  return JSON.stringify(value, null, 2);
}

// Convert message content objects/arrays into displayable text.
function stringifyContent(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  return prettyJson(value);
}

// ============================================================================
// Trace Log View
// ============================================================================

// Render the active session's LLM transcript/summary in the Logs drawer.
function renderTrace(trace) {
  $("logsMeta").textContent = `${trace.message_count || 0} messages · ${trace.status} · ${trace.token_estimate || 0}/${trace.context_window || 0} tokens est.`;
  const list = $("logsList");
  if (hasSelectionInside(list)) return;
  list.innerHTML = "";
  if (trace.summary) {
    list.appendChild(renderTraceSummary(trace));
  }
  if (!trace.messages || !trace.messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = trace.summary ? "No active LLM messages beyond the summary yet." : "No LLM messages yet.";
    list.appendChild(empty);
    return;
  }
  trace.messages.forEach((message, index) => {
    list.appendChild(renderTraceItem(message, index));
  });
}

// Render one message inside the trace transcript.
function renderTraceItem(message, index) {
  const item = document.createElement("article");
  item.className = `trace-item ${escapeClass(message.role || "unknown")}`;
  const labelParts = [`#${index + 1}`, message.role || "unknown"];
  if (message.name) labelParts.push(message.name);
  if (message.tool_call_id) labelParts.push(message.tool_call_id);
  item.innerHTML = `
    <div class="trace-role">${labelParts.map(escapeHtml).join(" · ")}</div>
    <div class="trace-content">${renderTraceSections(message)}</div>
  `;
  return item;
}

// Render the active context summary banner that appears after summarization.
function renderTraceSummary(trace) {
  const item = document.createElement("article");
  item.className = "trace-item context-summary";
  item.innerHTML = `
    <div class="trace-role">Active context summary · ${Number(trace.summary_chars || 0).toLocaleString()} chars</div>
    <div class="trace-content">${traceSection("summary", trace.summary)}</div>
  `;
  return item;
}

// Split a trace message into content/thinking/tool_calls/usage sections.
function renderTraceSections(message) {
  const sections = [];
  const content = message.content;
  if (typeof content === "string" && content.trim()) sections.push(traceSection("content", content));
  else if (content !== undefined && content !== null) sections.push(traceSection("content", JSON.stringify(content, null, 2)));
  if (message.thinking) sections.push(traceSection("thinking", message.thinking, "thinking"));
  if (message.tool_calls) sections.push(traceSection("tool_calls", JSON.stringify(message.tool_calls, null, 2)));
  if (message.usage) sections.push(traceSection("usage", JSON.stringify(message.usage, null, 2), "usage"));
  if (!sections.length) sections.push(traceSection("content", "(empty)"));
  return sections.join("");
}

// Build one labeled trace section.
function traceSection(label, value, variant = "") {
  return `
    <section class="trace-section ${escapeClass(variant || label)}">
      <div class="trace-section-label">${escapeHtml(label)}</div>
      <pre>${escapeHtml(value)}</pre>
    </section>
  `;
}

// Clear run metrics, files, and timeline when there is no session selected.
function resetSessionMetrics() {
  $("runStatus").textContent = "idle";
  $("tokenMetric").textContent = "0";
  $("contextMetric").textContent = "0%";
  $("contextBar").style.width = "0%";
  $("fileList").className = "file-list empty";
  $("fileList").textContent = "No files yet";
  $("timeline").innerHTML = "";
  $("timelineCount").textContent = "0 events";
}

// Build a lightweight signature for visible messages. If this does not change,
// renderSession can avoid rewriting the chat DOM.
function getMessageSignature(session) {
  return JSON.stringify((session.visible_messages || []).map((message) => [
    message.role,
    message.content,
    message.timestamp,
    (message.images || []).map((image) => [image.name, image.mime_type, image.path || image.url || image.data_url?.length || ""]),
  ]));
}

// Keep polling refreshes from replacing DOM while the user is selecting text.
function hasSelectionInside(element) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return false;
  const range = selection.getRangeAt(0);
  return element.contains(range.commonAncestorContainer);
}

// Extract the OpenAI-style tool name from a tool definition.
function toolName(tool) {
  return tool?.function?.name || "";
}

// ============================================================================
// Escaping and Markdown Rendering
// ============================================================================

// Escape text before inserting it as HTML.
function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

// Escape text for use inside HTML attributes.
function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

// Make arbitrary event/tool names safe to use as CSS class fragments.
function escapeClass(value) {
  return String(value).toLowerCase().replace(/[^a-z0-9_-]/g, "-");
}

// Minimal markdown renderer for assistant/user messages. It supports fenced
// code blocks, headings, lists, links, bold, italic, and inline code.
function renderMarkdown(value) {
  const text = String(value || "").replace(/\r\n/g, "\n");
  const parts = [];
  const fencePattern = /```([a-zA-Z0-9_-]+)?\n?([\s\S]*?)```/g;
  let cursor = 0;
  let match;

  while ((match = fencePattern.exec(text)) !== null) {
    if (match.index > cursor) parts.push(renderMarkdownBlocks(text.slice(cursor, match.index)));
    const language = match[1] ? `<span class="code-language">${escapeHtml(match[1])}</span>` : "";
    parts.push(`<pre>${language}<code>${escapeHtml(match[2].trimEnd())}</code></pre>`);
    cursor = fencePattern.lastIndex;
  }

  if (cursor < text.length) parts.push(renderMarkdownBlocks(text.slice(cursor)));
  return parts.join("");
}

// Render markdown block-level structures outside fenced code blocks.
function renderMarkdownBlocks(text) {
  const lines = text.split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 4);
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(`<li>${renderInlineMarkdown(lines[index].replace(/^\s*[-*]\s+/, ""))}</li>`);
        index += 1;
      }
      blocks.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
        items.push(`<li>${renderInlineMarkdown(lines[index].replace(/^\s*\d+\.\s+/, ""))}</li>`);
        index += 1;
      }
      blocks.push(`<ol>${items.join("")}</ol>`);
      continue;
    }

    const paragraph = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^(#{1,4})\s+/.test(lines[index]) &&
      !/^\s*[-*]\s+/.test(lines[index]) &&
      !/^\s*\d+\.\s+/.test(lines[index])
    ) {
      paragraph.push(renderInlineMarkdown(lines[index]));
      index += 1;
    }
    blocks.push(`<p>${paragraph.join("<br>")}</p>`);
  }

  return blocks.join("");
}

// Render inline markdown after protecting inline code spans.
function renderInlineMarkdown(value) {
  const codeSpans = [];
  let text = String(value).replace(/`([^`]+)`/g, (_, code) => {
    const token = `@@CODE_${codeSpans.length}@@`;
    codeSpans.push(`<code>${escapeHtml(code)}</code>`);
    return token;
  });

  text = escapeHtml(text);
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/\*([^*]+)\*/g, "<em>$1</em>");

  codeSpans.forEach((html, index) => {
    text = text.replace(`@@CODE_${index}@@`, html);
  });
  return text;
}

// ============================================================================
// Application Boot
// ============================================================================

// Initialize the dashboard in dependency order:
// 1. restore local widths and bind DOM events
// 2. load backend config/preferences
// 3. load tools/workspace/sessions/status in parallel
// 4. start a polling loop for live updates
async function init() {
  loadPanelWidths();
  bindUi();
  await loadConfig();
  await loadPreferences();
  await Promise.all([loadTools(), loadWorkspace(), loadGuiForLlm(), refreshSessions(), checkStatus()]);
  const requestedLogView = new URLSearchParams(window.location.search).get("logs");
  if (["trace", "raw", "http", "app"].includes(requestedLogView)) {
    state.logView = requestedLogView;
    await openLogsDrawer();
  }
  state.pollTimer = setInterval(async () => {
    try {
      await refreshSessions();
      if (state.logsOpen) await refreshLogs();
    } catch (error) {
      console.warn(error);
    }
  }, 1500);
}

// Surface boot errors in the chat area so failures are visible without opening
// devtools.
init().catch((error) => {
  console.error(error);
  $("chatLog").textContent = error.message;
});
