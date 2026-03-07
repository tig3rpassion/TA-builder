/**
 * TA Builder — 프론트엔드 로직
 * Step 1: PDF 업로드 → Step 2: 에이전트 편집 → Step 3: 채팅
 */

// ─── 상태 ────────────────────────────────────────────────────────────────────
let sessionId = null;
let agents = [];
let selectedAgentId = null;
let isStreaming = false;
let currentAgentInChat = null;
let currentCourseName = "";

// ─── 세션 로컬 저장/복원 ──────────────────────────────────────────────────────
function saveSessionLocally() {
  try {
    localStorage.setItem("ta_session_id", sessionId);
    localStorage.setItem("ta_agents", JSON.stringify(agents));
    localStorage.setItem("ta_course_name", currentCourseName || "");
  } catch (_) {}
}

async function tryRecoverSession() {
  /** 세션이 소멸된 경우 저장된 에이전트로 새 세션을 자동 재생성 */
  const savedAgents = localStorage.getItem("ta_agents");
  if (!savedAgents) return false;
  try {
    const parsed = JSON.parse(savedAgents);
    const res = await fetch("/agents/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agents: parsed }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    sessionId = data.session_id;
    agents = data.agents;
    currentCourseName = localStorage.getItem("ta_course_name") || currentCourseName;
    saveSessionLocally();
    showToast("세션이 자동 재연결되었습니다. (강의 자료는 다시 업로드가 필요합니다)");
    return true;
  } catch (_) {
    return false;
  }
}

function showToast(msg) {
  const t = document.createElement("div");
  t.textContent = msg;
  t.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);"
    + "background:#333;color:#fff;padding:10px 20px;border-radius:8px;"
    + "font-size:13px;z-index:9999;opacity:0;transition:opacity 0.3s;max-width:90%";
  document.body.appendChild(t);
  requestAnimationFrame(() => { t.style.opacity = "1"; });
  setTimeout(() => {
    t.style.opacity = "0";
    setTimeout(() => t.remove(), 300);
  }, 4000);
}

function inferCourseNameFromFiles(files) {
  if (!Array.isArray(files) || files.length === 0) return "";
  const names = files.map((f) => (f && f.name ? f.name : ""))
    .filter(Boolean)
    .map((name) => name.replace(/\.pdf$/i, ""));

  if (!names.length) return "";

  const preferred = names.find((name) => /syllabus|강의계획|강의계획서/i.test(name)) || names[0];
  return preferred
    .replace(/syllabus/ig, "")
    .replace(/spring|summer|fall|winter/ig, "")
    .replace(/20\d{2}/g, "")
    .replace(/[A-Z]{2,}\d{3,5}/g, "")
    .replace(/[()[\]{}]/g, " ")
    .replace(/[_\-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 40);
}

function inferCourseNameFromAgents() {
  if (!Array.isArray(agents) || !agents.length) return "";
  const first = agents[0];
  const seed = (first.name || first.role || "").trim();
  return seed.slice(0, 30);
}

function sanitizeFilename(text) {
  return (text || "")
    .normalize("NFKD")
    .replace(/[^\w\s.-가-힣]/g, "")
    .replace(/\s+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

function buildAgentsFilename() {
  const base = sanitizeFilename(currentCourseName || inferCourseNameFromAgents() || "course");
  return `${base}_ta-agents.json`;
}

// ─── 화면 전환 ────────────────────────────────────────────────────────────────
function showStep(step) {
  document.getElementById("step-upload").classList.add("hidden");
  document.getElementById("step-preview").classList.add("hidden");
  const chatEl = document.getElementById("step-chat");
  chatEl.classList.add("hidden");
  chatEl.style.display = "none";

  if (step === "upload") {
    document.getElementById("step-upload").classList.remove("hidden");
  } else if (step === "preview") {
    document.getElementById("step-preview").classList.remove("hidden");
  } else if (step === "chat") {
    chatEl.classList.remove("hidden");
    chatEl.style.display = "flex";
  }
}

// ─── STEP 1: 업로드 ───────────────────────────────────────────────────────────
let selectedFiles = [];

function initUpload() {
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("file-input");
  const generateBtn = document.getElementById("generate-btn");

  dropZone.addEventListener("click", () => fileInput.click());

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });

  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    addFiles(Array.from(e.dataTransfer.files));
  });

  fileInput.addEventListener("change", () => {
    addFiles(Array.from(fileInput.files));
    fileInput.value = "";
  });

  generateBtn.addEventListener("click", runGenerate);
}

function addFiles(files) {
  const pdfs = files.filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  for (const f of pdfs) {
    if (selectedFiles.length >= 5) break;
    if (!selectedFiles.find((sf) => sf.name === f.name)) {
      selectedFiles.push(f);
    }
  }
  renderFileList();
}

function renderFileList() {
  const list = document.getElementById("file-list");
  const btn = document.getElementById("generate-btn");

  if (selectedFiles.length === 0) {
    list.classList.add("hidden");
    list.innerHTML = "";
    btn.disabled = true;
    return;
  }

  list.classList.remove("hidden");
  list.innerHTML = selectedFiles
    .map(
      (f, i) => `
    <div class="file-item">
      <span class="file-item-icon">📄</span>
      <span class="file-item-name">${escapeHtml(f.name)}</span>
      <button class="file-item-remove" data-index="${i}" title="제거">✕</button>
    </div>
  `
    )
    .join("");

  list.querySelectorAll(".file-item-remove").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const idx = parseInt(e.currentTarget.dataset.index);
      selectedFiles.splice(idx, 1);
      renderFileList();
    });
  });

  btn.disabled = false;
}

async function runGenerate() {
  if (selectedFiles.length === 0) return;

  const btn = document.getElementById("generate-btn");
  const status = document.getElementById("generate-status");
  const statusText = document.getElementById("generate-status-text");

  btn.disabled = true;
  status.classList.remove("hidden");
  statusText.textContent = "PDF 분석 중...";

  const formData = new FormData();
  for (const f of selectedFiles) {
    formData.append("files", f);
  }

  try {
    statusText.textContent = "AI가 에이전트를 설계하는 중...";
    const res = await fetch("/generate", { method: "POST", body: formData });

    if (!res.ok) {
      let errMsg = "생성 실패";
      try { const err = await res.json(); errMsg = err.detail || errMsg; } catch { errMsg = await res.text().catch(() => errMsg); }
      throw new Error(errMsg);
    }

    const data = await res.json();
    sessionId = data.session_id;
    agents = data.agents;
    currentCourseName = inferCourseNameFromFiles(selectedFiles);
    saveSessionLocally();

    showPreview();
  } catch (err) {
    alert(`오류: ${err.message}`);
    btn.disabled = false;
  } finally {
    status.classList.add("hidden");
  }
}

// ─── STEP 2: 에이전트 프리뷰/편집 ────────────────────────────────────────────
function showPreview() {
  renderPreviewCards();
  showStep("preview");

  document.getElementById("back-btn").addEventListener("click", () => {
    showStep("upload");
  }, { once: true });

  document.getElementById("save-agents-btn").addEventListener("click", saveAgents);

  document.getElementById("start-btn").addEventListener("click", async () => {
    await saveAgentEdits();
    initChat();
    showStep("chat");
  }, { once: true });
}

function renderPreviewCards() {
  const container = document.getElementById("preview-agents");
  container.innerHTML = "";

  agents.forEach((agent, i) => {
    const card = document.createElement("div");
    card.className = "preview-card";
    card.style.setProperty("--card-color", agent.color);
    card.dataset.agentIndex = i;
    card.innerHTML = `
      <div class="preview-card-header">
        <div class="preview-avatar">${agent.avatar}</div>
        <div class="preview-card-fields">
          <div class="preview-field-label">이름</div>
          <input class="preview-field-input" data-field="name" value="${escapeHtml(agent.name)}" maxlength="20" />
          <div class="preview-field-label" style="margin-top:6px">역할</div>
          <input class="preview-field-input" data-field="role" value="${escapeHtml(agent.role)}" maxlength="30" />
        </div>
      </div>
      <div class="preview-desc">${escapeHtml(agent.description)}</div>
    `;
    container.appendChild(card);
  });
}

async function saveAgentEdits() {
  const container = document.getElementById("preview-agents");
  const cards = container.querySelectorAll(".preview-card");

  cards.forEach((card) => {
    const idx = parseInt(card.dataset.agentIndex);
    const nameInput = card.querySelector('[data-field="name"]');
    const roleInput = card.querySelector('[data-field="role"]');
    if (nameInput) agents[idx].name = nameInput.value.trim() || agents[idx].name;
    if (roleInput) agents[idx].role = roleInput.value.trim() || agents[idx].role;
  });

  // 서버에 업데이트
  await fetch(`/agents/${sessionId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agents }),
  });
}

// ─── STEP 3: 채팅 ─────────────────────────────────────────────────────────────
function initChat() {
  selectedAgentId = null;
  currentAgentInChat = null;
  isStreaming = false;

  // 제목 업데이트
  document.getElementById("chat-title").textContent = "수업 조교";
  document.getElementById("chat-subtitle").textContent = `${agents.length}개 에이전트 · 아래에서 선택하세요`;

  renderAgentSelector();
  renderAgentHints();

  // 채팅 메시지 초기화
  const chatMessages = document.getElementById("chat-messages");
  chatMessages.innerHTML = `
    <div class="welcome-screen" id="welcome-screen">
      <div class="welcome-icon">💬</div>
      <h2>조교에게 질문해 보세요</h2>
      <p>위에서 에이전트를 선택하면 채팅을 시작할 수 있습니다</p>
      <div class="agent-hints" id="agent-hints"></div>
    </div>
  `;
  renderAgentHints();

  document.getElementById("send-btn").onclick = sendMessage;
  document.getElementById("clear-btn").onclick = clearConversation;
  document.getElementById("reset-btn").onclick = resetToUpload;
  initAddMaterial();

  const input = document.getElementById("message-input");
  input.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };
  input.oninput = function () { autoResize.call(this); };
}

function resetToUpload() {
  if (isStreaming) return;
  sessionId = null;
  agents = [];
  currentCourseName = "";
  selectedFiles = [];
  renderFileList();
  document.getElementById("generate-btn").disabled = true;
  showStep("upload");
}

function renderAgentSelector() {
  const selector = document.getElementById("agent-selector");
  selector.innerHTML = "";

  agents.forEach((agent) => {
    const card = document.createElement("div");
    card.className = "agent-card";
    card.id = `agent-card-${agent.id}`;
    card.style.setProperty("--card-color", agent.color);
    card.innerHTML = `
      <div class="agent-card-avatar">${agent.avatar}</div>
      <div class="agent-card-info">
        <div class="agent-card-name" style="color:${agent.color}">${escapeHtml(agent.name)}</div>
        <div class="agent-card-role">${escapeHtml(agent.role)}</div>
      </div>
    `;
    card.addEventListener("click", () => selectAgent(agent.id));
    selector.appendChild(card);
  });
}

function renderAgentHints() {
  const container = document.getElementById("agent-hints");
  if (!container) return;
  container.innerHTML = "";

  agents.forEach((agent) => {
    const card = document.createElement("div");
    card.className = "hint-card";
    card.style.setProperty("--hint-color", agent.color);
    card.innerHTML = `
      <div class="hint-card-header">
        <span>${agent.avatar}</span>
        <span class="hint-card-name" style="color:${agent.color}">${escapeHtml(agent.name)}</span>
      </div>
      <div class="hint-card-desc">${escapeHtml(agent.description)}</div>
    `;
    card.addEventListener("click", () => {
      selectAgent(agent.id);
      const input = document.getElementById("message-input");
      input.value = agent.description;
      autoResize.call(input);
      input.focus();
    });
    container.appendChild(card);
  });
}

function selectAgent(agentId) {
  if (isStreaming) return;

  document.querySelectorAll(".agent-card").forEach((c) => c.classList.remove("active"));

  selectedAgentId = agentId;
  const card = document.getElementById(`agent-card-${agentId}`);
  if (card) card.classList.add("active");

  const input = document.getElementById("message-input");
  const sendBtn = document.getElementById("send-btn");
  input.disabled = false;
  sendBtn.disabled = false;
  input.focus();

  const agent = agents.find((a) => a.id === agentId);
  input.placeholder = `${agent.name}에게 질문하세요...`;

  if (currentAgentInChat && currentAgentInChat !== agentId) {
    appendAgentSwitch(agent);
  }
  currentAgentInChat = agentId;

  const welcome = document.getElementById("welcome-screen");
  if (welcome) welcome.style.display = "none";

  document.getElementById("clear-btn").classList.remove("hidden");
}

async function sendMessage() {
  if (isStreaming || !selectedAgentId) return;

  const input = document.getElementById("message-input");
  const message = input.value.trim();
  if (!message) return;

  input.value = "";
  autoResize.call(input);
  isStreaming = true;
  document.getElementById("send-btn").disabled = true;
  input.disabled = true;

  appendUserMessage(message);

  const agent = agents.find((a) => a.id === selectedAgentId);
  const { bubbleEl } = appendAgentMessage(agent);

  const cursor = document.createElement("span");
  cursor.className = "cursor";
  bubbleEl.appendChild(cursor);
  let assistantRawText = "";

  try {
    let response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        agent_id: selectedAgentId,
        message,
      }),
    });

    if (!response.ok) {
      if (response.status === 404) {
        assistantRawText = "세션 재연결 중...";
        renderAgentMessageContent(bubbleEl, assistantRawText, cursor);
        const recovered = await tryRecoverSession();
        if (recovered) {
          // 새 세션으로 같은 메시지 재전송
          assistantRawText = "";
          renderAgentMessageContent(bubbleEl, assistantRawText, cursor);
          const retryRes = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId, agent_id: selectedAgentId, message }),
          });
          if (retryRes.ok) {
            response = retryRes;
          } else {
            assistantRawText = "세션 재연결에 실패했습니다. PDF를 다시 업로드해 주세요.";
            renderAgentMessageContent(bubbleEl, assistantRawText, cursor);
            return;
          }
        } else {
          assistantRawText = "세션이 만료되었습니다. PDF를 다시 업로드해 주세요.";
          renderAgentMessageContent(bubbleEl, assistantRawText, cursor);
          return;
        }
      } else {
        let errMsg = "알 수 없는 오류";
        try { const err = await response.json(); errMsg = err.detail || errMsg; } catch { errMsg = await response.text().catch(() => errMsg); }
        assistantRawText = `오류: ${errMsg}`;
        renderAgentMessageContent(bubbleEl, assistantRawText, cursor);
        return;
      }
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.type === "chunk") {
            assistantRawText += data.text;
            renderAgentMessageContent(bubbleEl, assistantRawText, cursor);
            scrollToBottom();
          } else if (data.type === "error") {
            assistantRawText += `\n\n[오류: ${data.message}]`;
            renderAgentMessageContent(bubbleEl, assistantRawText, cursor);
          }
        } catch (_) {}
      }
    }
  } catch (err) {
    assistantRawText += `[연결 오류: ${err.message}]`;
    renderAgentMessageContent(bubbleEl, assistantRawText, cursor);
  } finally {
    const c = bubbleEl.querySelector(".cursor");
    if (c) c.remove();
    renderAgentMessageContent(bubbleEl, assistantRawText, null);

    isStreaming = false;
    input.disabled = false;
    document.getElementById("send-btn").disabled = false;
    input.focus();
  }
}

async function clearConversation() {
  if (isStreaming || !selectedAgentId) return;

  await fetch(`/chat/clear/${sessionId}/${selectedAgentId}`, { method: "POST" });

  const container = document.getElementById("chat-messages");
  container.innerHTML = "";

  const welcome = document.createElement("div");
  welcome.className = "welcome-screen";
  welcome.id = "welcome-screen";
  welcome.innerHTML = `
    <div class="welcome-icon">💬</div>
    <h2>대화가 초기화되었습니다</h2>
    <p>새로운 질문을 입력해 주세요.</p>
  `;
  container.appendChild(welcome);

  currentAgentInChat = null;
  selectAgent(selectedAgentId);
}

// ─── DOM 헬퍼 ─────────────────────────────────────────────────────────────────
function appendUserMessage(text) {
  const container = document.getElementById("chat-messages");
  const msg = document.createElement("div");
  msg.className = "message user-message";
  msg.innerHTML = `
    <div class="message-avatar" style="background:#1e2d4a">👤</div>
    <div class="message-body">
      <div class="message-header" style="justify-content:flex-end">
        <span class="message-name" style="color:#8b90a8">나</span>
      </div>
      <div class="message-bubble">${escapeHtml(text)}</div>
    </div>
  `;
  container.appendChild(msg);
  scrollToBottom();
}

function appendAgentMessage(agent) {
  const container = document.getElementById("chat-messages");
  const msg = document.createElement("div");
  msg.className = "message";
  msg.innerHTML = `
    <div class="message-avatar" style="background:color-mix(in srgb, ${agent.color} 12%, #1a1d27)">
      ${agent.avatar}
    </div>
    <div class="message-body">
      <div class="message-header">
        <span class="message-name" style="color:${agent.color}">${escapeHtml(agent.name)}</span>
        <span class="message-role">${escapeHtml(agent.role)}</span>
      </div>
      <div class="message-bubble agent-bubble" style="--bubble-color:${agent.color}"></div>
    </div>
  `;
  container.appendChild(msg);
  scrollToBottom();
  return { messageEl: msg, bubbleEl: msg.querySelector(".message-bubble") };
}

function appendAgentSwitch(agent) {
  const container = document.getElementById("chat-messages");
  if (!container.children.length) return;
  const divider = document.createElement("div");
  divider.className = "agent-switch-divider";
  divider.innerHTML = `<span class="agent-switch-label">${agent.avatar} ${escapeHtml(agent.name)}으로 전환</span>`;
  container.appendChild(divider);
}

function scrollToBottom() {
  const main = document.querySelector("main");
  if (main) main.scrollTop = main.scrollHeight;
}

function autoResize() {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 120) + "px";
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatAssistantText(rawText) {
  let safe = escapeHtml(rawText || "");
  safe = safe.replace(/\*\*(.+?)\*\*/gs, "<strong>$1</strong>");
  safe = safe.replace(/__(.+?)__/gs, "<strong>$1</strong>");
  const parts = safe.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return "";
  return parts.map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}

function renderAgentMessageContent(bubbleEl, rawText, cursorEl) {
  bubbleEl.innerHTML = formatAssistantText(rawText);
  if (cursorEl) bubbleEl.appendChild(cursorEl);
}

// ─── 에이전트 저장 ─────────────────────────────────────────────────────────────
function saveAgents() {
  const payload = {
    course_name: currentCourseName || inferCourseNameFromAgents() || "",
    agents,
  };
  const data = JSON.stringify(payload, null, 2);
  const blob = new Blob([data], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = buildAgentsFilename();
  a.click();
  URL.revokeObjectURL(url);
}

// ─── 에이전트 불러오기 ─────────────────────────────────────────────────────────
function initLoadAgents() {
  const btn = document.getElementById("load-agents-btn");
  const input = document.getElementById("load-agents-input");

  btn.addEventListener("click", () => input.click());

  input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;
    input.value = "";

    try {
      const text = await file.text();
      const data = JSON.parse(text);
      if (!data.agents || !Array.isArray(data.agents)) throw new Error("올바른 형식이 아닙니다.");

      const res = await fetch("/agents/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agents: data.agents }),
      });
      if (!res.ok) {
        let errMsg = "불러오기 실패";
        try { const err = await res.json(); errMsg = err.detail || errMsg; } catch { errMsg = await res.text().catch(() => errMsg); }
        throw new Error(errMsg);
      }
      const result = await res.json();
      sessionId = result.session_id;
      agents = result.agents;
      currentCourseName = (data.course_name || inferCourseNameFromAgents() || "").trim();
      saveSessionLocally();
      showPreview();
    } catch (err) {
      alert(`불러오기 오류: ${err.message}`);
    }
  });
}

// ─── 자료 추가 ─────────────────────────────────────────────────────────────────
function initAddMaterial() {
  const btn = document.getElementById("add-material-btn");
  const input = document.getElementById("add-material-input");

  btn.addEventListener("click", () => input.click());

  input.addEventListener("change", async () => {
    const files = Array.from(input.files);
    if (!files.length) return;
    input.value = "";

    btn.disabled = true;
    btn.textContent = "자료 추가 중...";

    const formData = new FormData();
    for (const f of files) formData.append("files", f);

    try {
      const res = await fetch(`/add-material/${sessionId}`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "자료 추가 실패");
      }
      const result = await res.json();
      agents = result.agents;

      // 에이전트 셀렉터 갱신, 대화 초기화
      selectedAgentId = null;
      currentAgentInChat = null;
      renderAgentSelector();
      const chatMessages = document.getElementById("chat-messages");
      chatMessages.innerHTML = `
        <div class="welcome-screen" id="welcome-screen">
          <div class="welcome-icon">📚</div>
          <h2>자료가 추가되었습니다</h2>
          <p>에이전트가 새 자료를 반영하여 재생성되었습니다.<br>에이전트를 다시 선택해 주세요.</p>
          <div class="agent-hints" id="agent-hints"></div>
        </div>
      `;
      renderAgentHints();
      document.getElementById("clear-btn").classList.add("hidden");
      document.getElementById("message-input").disabled = true;
      document.getElementById("send-btn").disabled = true;
    } catch (err) {
      alert(`오류: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "자료 추가";
    }
  });
}

// ─── 시작 ─────────────────────────────────────────────────────────────────────
showStep("upload");
initUpload();
initLoadAgents();
