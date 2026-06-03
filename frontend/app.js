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
};

const $ = (id) => document.getElementById(id);
const PANEL_WIDTH_STORAGE_KEY = "thursday.panelWidths";
const DEFAULT_THEME_COLOR = "#9b63ff";

const PANEL_WIDTH_DEFAULTS = {
  left: 300,
  right: 360,
  logs: 920,
  settings: 440,
};

const PANEL_WIDTH_VARS = {
  left: "--left-panel-width",
  right: "--right-panel-width",
  logs: "--logs-drawer-width",
  settings: "--settings-drawer-width",
};

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

function themeFromUi() {
  return {
    accent_color: normalizeHexColor($("themeColorTextInput")?.value || state.themeColor || DEFAULT_THEME_COLOR),
  };
}

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
      deleteButton.textContent = "Delete";
      deleteButton.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteSession(session);
      });

      row.append(button, deleteButton);
      list.appendChild(row);
    });
}

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

function timelineDetails(event) {
  const data = event.data || {};
  const details = [];
  if (data.tool) details.push(`tool: ${data.tool}`);
  if (data.model) details.push(`model: ${data.model}`);
  if (data.token_estimate !== undefined) details.push(`tokens: ${data.token_estimate}`);
  if (data.max_steps !== undefined) details.push(`steps: ${data.max_steps}`);
  if (data.ok !== undefined) details.push(data.ok ? "ok" : "failed");
  if (data.result?.exit_code !== undefined) details.push(`exit: ${data.result.exit_code}`);
  if (data.result?.path) details.push(`path: ${data.result.path}`);
  if (data.result?.file_path) details.push(`file: ${data.result.file_path}`);
  if (data.args) details.push(`args: ${compactJson(data.args, 120)}`);
  if (data.error) details.push(`error: ${clampInline(data.error, 120)}`);
  return details.slice(0, 4);
}

function compactJson(value, limit = 120) {
  try {
    return clampInline(JSON.stringify(value), limit);
  } catch {
    return clampInline(String(value), limit);
  }
}

function clampInline(value, limit = 120) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 1))}...`;
}

function formatEventTime(timestamp) {
  const date = timestamp ? new Date(timestamp) : null;
  if (!date || Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

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

function closeTimelineDetail() {
  state.selectedTimelineEvent = null;
  $("timelineDetailModal").classList.remove("open");
  $("timelineDetailModal").setAttribute("aria-hidden", "true");
}

function timelineDetailSection(title, value, code = false) {
  return `
    <section class="timeline-detail-section">
      <h3>${escapeHtml(title)}</h3>
      ${code ? `<pre>${escapeHtml(value)}</pre>` : `<p>${escapeHtml(value)}</p>`}
    </section>
  `;
}

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

function statusText(data) {
  const context = data.selected_model_context || {};
  const tokens = Number(context.context_window || 0);
  if (!tokens) return data.cached ? "LM Studio online · cached" : "LM Studio online";
  const suffix = data.cached ? " · cached" : "";
  return `LM Studio online · ctx ${tokens.toLocaleString()}${suffix}`;
}

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

async function loadWorkspace() {
  const data = await api("/api/workspace");
  state.workspace = data;
  renderWorkspace(data);
}

function renderWorkspace(workspace) {
  $("workspacePath").textContent = workspace.workspace || "docker://Thursday/workspace";
  $("workspaceContainer").textContent = workspace.container || "Thursday";
  $("workspaceImage").textContent = workspace.image || workspace.configured_image || "ubuntu:24.04";
  $("workspaceWorkdir").textContent = workspace.workdir || "/workspace";
  $("workspaceStatusBadge").textContent = workspace.running ? "running" : workspace.exists ? "stopped" : "missing";
  $("workspaceStatusBadge").className = `mini-meta workspace-badge ${workspace.running ? "ok" : "error"}`;
  setMaintenanceButtons();
}

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

function setMaintenanceButtons() {
  ["clearSessionsBtn", "clearLogsBtn", "resetWorkspaceBtn"].forEach((id) => {
    const button = $(id);
    if (button) button.disabled = state.maintenanceBusy || (id === "resetWorkspaceBtn" && state.workspaceResetting);
  });
}

function setMaintenanceStatus(message) {
  $("maintenanceStatus").textContent = message || "ready";
}

async function loadTools() {
  const data = await api("/api/tools");
  $("workspacePath").textContent = data.workspace;
  state.allTools = data.all_tools || data.tools || [];
  state.requiredTools = data.required_tools || state.requiredTools || [];
  if (!state.enabledTools.length) state.enabledTools = data.enabled_tools || state.allTools.map(toolName);
  ensureRequiredToolsEnabled();
  renderToolControls();
}

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

async function loadConfig() {
  const config = await api("/api/config");
  state.config = config;
}

async function loadPreferences() {
  const preferences = await api("/api/preferences");
  applyPreferences(preferences);
}

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

function ensureRequiredToolsEnabled() {
  const known = new Set(state.allTools.map(toolName));
  const enabled = new Set(state.enabledTools);
  state.requiredTools.forEach((name) => {
    if (!known.size || known.has(name)) enabled.add(name);
  });
  state.enabledTools = Array.from(enabled).filter((name) => !known.size || known.has(name));
}

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

function setPanelWidth(key, value, persist = true) {
  const cssVar = PANEL_WIDTH_VARS[key];
  if (!cssVar) return;
  const clamped = clampPanelWidth(key, value);
  document.documentElement.style.setProperty(cssVar, `${clamped}px`);
  if (persist) savePanelWidth(key, clamped);
}

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

function currentPanelWidth(key) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(PANEL_WIDTH_VARS[key]);
  const parsed = Number(String(value).replace("px", "").trim());
  return Number.isFinite(parsed) ? parsed : PANEL_WIDTH_DEFAULTS[key];
}

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

function normalizeHexColor(value) {
  const text = String(value || DEFAULT_THEME_COLOR).trim();
  if (/^#[0-9a-fA-F]{6}$/.test(text)) return text.toLowerCase();
  return DEFAULT_THEME_COLOR;
}

function hexToRgb(hex) {
  const value = normalizeHexColor(hex).slice(1);
  return {
    r: Number.parseInt(value.slice(0, 2), 16),
    g: Number.parseInt(value.slice(2, 4), 16),
    b: Number.parseInt(value.slice(4, 6), 16),
  };
}

function mixRgb(first, second, amount) {
  const ratio = Math.max(0, Math.min(1, Number(amount) || 0));
  return {
    r: Math.round(first.r * (1 - ratio) + second.r * ratio),
    g: Math.round(first.g * (1 - ratio) + second.g * ratio),
    b: Math.round(first.b * (1 - ratio) + second.b * ratio),
  };
}

function rgbToHex(rgb) {
  return `#${[rgb.r, rgb.g, rgb.b].map((part) => Math.max(0, Math.min(255, part)).toString(16).padStart(2, "0")).join("")}`;
}

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

function schedulePreferencesSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(savePreferencesNow, 350);
}

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

async function restoreDefaults() {
  const preferences = await api("/api/preferences", {
    method: "POST",
    body: JSON.stringify({ restore_defaults: true }),
  });
  applyPreferences(preferences);
  await checkStatus();
}

async function toggleLogsDrawer() {
  if (state.logsOpen) {
    closeLogsDrawer();
    return;
  }
  await openLogsDrawer();
}

async function openLogsDrawer() {
  closeSettingsDrawer();
  state.logsOpen = true;
  document.body.classList.add("logs-open");
  $("logsDrawer").setAttribute("aria-hidden", "false");
  $("logsTab").setAttribute("aria-expanded", "true");
  renderLogTabs();
  await refreshLogs();
}

function closeLogsDrawer() {
  state.logsOpen = false;
  document.body.classList.remove("logs-open");
  $("logsDrawer").setAttribute("aria-hidden", "true");
  $("logsTab").setAttribute("aria-expanded", "false");
}

function toggleSettingsDrawer() {
  if (state.settingsOpen) {
    closeSettingsDrawer();
    return;
  }
  openSettingsDrawer();
}

function openSettingsDrawer() {
  closeLogsDrawer();
  state.settingsOpen = true;
  document.body.classList.add("settings-open");
  $("settingsDrawer").setAttribute("aria-hidden", "false");
  $("settingsTab").setAttribute("aria-expanded", "true");
}

function closeSettingsDrawer() {
  state.settingsOpen = false;
  document.body.classList.remove("settings-open");
  $("settingsDrawer").setAttribute("aria-hidden", "true");
  $("settingsTab").setAttribute("aria-expanded", "false");
}

function renderLogTabs() {
  document.querySelectorAll("[data-log-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.logView === state.logView);
  });
}

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

function rawPanelKey(entry) {
  return String(entry.id || entry.request_id || entry._line || "record");
}

function formatRawTimestamp(timestamp) {
  const date = timestamp ? new Date(timestamp) : null;
  if (!date || Number.isNaN(date.getTime())) return "unknown time";
  return date.toLocaleString();
}

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

function formatReadableResponseBody(response) {
  if (response.text) return formatReadableJsonText(response.text);
  if (response.json !== undefined && response.json !== null) return formatReadableValue(response.json);
  if (response.json_error) return `JSON parse error: ${response.json_error}`;
  return "(empty response body)";
}

function formatReadableJsonText(text) {
  try {
    return formatReadableValue(JSON.parse(text));
  } catch {
    return String(text || "");
  }
}

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

function indentBlock(value, spaces = 2) {
  const prefix = " ".repeat(spaces);
  return String(value || "").split("\n").map((line) => `${prefix}${line}`).join("\n");
}

function rawPre(value) {
  return `<pre class="raw-pre">${escapeHtml(value)}</pre>`;
}

function prettyJson(value) {
  return JSON.stringify(value, null, 2);
}

function stringifyContent(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  return prettyJson(value);
}

function renderTrace(trace) {
  $("logsMeta").textContent = `${trace.message_count || 0} messages · ${trace.status} · ${trace.token_estimate || 0}/${trace.context_window || 0} tokens est.`;
  const list = $("logsList");
  if (hasSelectionInside(list)) return;
  list.innerHTML = "";
  if (!trace.messages || !trace.messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No LLM messages yet.";
    list.appendChild(empty);
    return;
  }
  trace.messages.forEach((message, index) => {
    list.appendChild(renderTraceItem(message, index));
  });
}

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

function traceSection(label, value, variant = "") {
  return `
    <section class="trace-section ${escapeClass(variant || label)}">
      <div class="trace-section-label">${escapeHtml(label)}</div>
      <pre>${escapeHtml(value)}</pre>
    </section>
  `;
}

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

function getMessageSignature(session) {
  return JSON.stringify((session.visible_messages || []).map((message) => [
    message.role,
    message.content,
    message.timestamp,
    (message.images || []).map((image) => [image.name, image.mime_type, image.path || image.url || image.data_url?.length || ""]),
  ]));
}

function hasSelectionInside(element) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return false;
  const range = selection.getRangeAt(0);
  return element.contains(range.commonAncestorContainer);
}

function toolName(tool) {
  return tool?.function?.name || "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

function escapeClass(value) {
  return String(value).toLowerCase().replace(/[^a-z0-9_-]/g, "-");
}

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

async function init() {
  loadPanelWidths();
  bindUi();
  await loadConfig();
  await loadPreferences();
  await Promise.all([loadTools(), loadWorkspace(), refreshSessions(), checkStatus()]);
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

init().catch((error) => {
  console.error(error);
  $("chatLog").textContent = error.message;
});
