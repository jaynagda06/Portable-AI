/* Portable AI - front-end (vanilla JS, no CDNs). */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- auth token (LAN mode) ---------------- */
(function initToken() {
  const u = new URL(location.href);
  const t = u.searchParams.get("t");
  if (t) {
    localStorage.setItem("pai.token", t);
    u.searchParams.delete("t");
    history.replaceState(null, "", u.pathname + u.search + u.hash);
  }
})();
const TOKEN = () => localStorage.getItem("pai.token") || "";

function headers(extra = {}) {
  const h = Object.assign({ "X-Requested-With": "PortableAI" }, extra);
  if (TOKEN()) h["X-Auth-Token"] = TOKEN();
  return h;
}

async function api(path, opts = {}) {
  const o = Object.assign({}, opts);
  o.headers = headers(o.headers || {});
  if (o.body && typeof o.body === "object" && !(o.body instanceof Blob)) {
    o.headers["Content-Type"] = "application/json";
    o.body = JSON.stringify(o.body);
  }
  const res = await fetch(path, o);
  const txt = await res.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch { data = null; }
  if (!res.ok) throw new Error((data && data.error) || txt.slice(0, 300) || `HTTP ${res.status}`);
  return data;
}

function upload(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-Requested-With", "PortableAI");
    if (TOKEN()) xhr.setRequestHeader("X-Auth-Token", TOKEN());
    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    }
    xhr.onload = () => {
      let j = null;
      try { j = JSON.parse(xhr.responseText); } catch { /* noop */ }
      if (xhr.status < 400 && j) resolve(j);
      else reject(new Error((j && j.error) || xhr.responseText.slice(0, 300) || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("network error during upload"));
    xhr.send(file);
  });
}

/* ---------------- toast ---------------- */
let toastTimer = null;
function toast(msg, bad = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 3200);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied");
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); toast("Copied"); } catch { toast("Copy failed", true); }
    ta.remove();
  }
}
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-copy]");
  if (el) { e.preventDefault(); copyText(el.getAttribute("data-copy")); }
});

/* ---------------- markdown (small, safe subset) ---------------- */
function inlineMd(t) {
  let s = esc(t);
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, "$1<i>$2</i>");
  s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s;
}

function codeBlock(c) {
  return `<div class="code-block"><button class="copy-code" data-copy="${esc(c.code)}">Copy</button>` +
    `<pre><code>${esc(c.code.replace(/\n$/, ""))}</code></pre></div>`;
}

function md(src) {
  if (!src) return "";
  const codes = [];
  let text = String(src).replace(/```([\w+#.-]*)[ \t]*\n?([\s\S]*?)```/g, (_m, lang, code) => {
    codes.push({ lang, code });
    return `\u0000CODE${codes.length - 1}\u0000`;
  });
  const lines = text.split("\n");
  let html = "", list = null, para = [];
  const flush = () => { if (para.length) { html += `<p>${inlineMd(para.join(" "))}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    const cm = line.trim().match(/^\u0000CODE(\d+)\u0000$/);
    if (cm) { flush(); closeList(); html += codeBlock(codes[+cm[1]]); continue; }
    if (!line.trim()) { flush(); closeList(); continue; }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flush(); closeList();
      const lv = Math.min(3, m[1].length);
      html += `<h${lv}>${inlineMd(m[2])}</h${lv}>`;
      continue;
    }
    if ((m = line.match(/^[-*+]\s+(.*)$/))) {
      flush();
      if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${inlineMd(m[1])}</li>`;
      continue;
    }
    if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {
      flush();
      if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${inlineMd(m[1])}</li>`;
      continue;
    }
    if ((m = line.match(/^&gt;\s?(.*)$/)) || (m = line.match(/^>\s?(.*)$/))) {
      flush(); closeList(); html += `<blockquote>${inlineMd(m[1])}</blockquote>`; continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { flush(); closeList(); html += "<hr>"; continue; }
    para.push(line.trim());
  }
  flush(); closeList();
  // any code placeholder left inline
  html = html.replace(/\u0000CODE(\d+)\u0000/g, (_m, i) => codeBlock(codes[+i]));
  return html;
}

/* ---------------- state ---------------- */
const state = {
  view: "chat",
  chats: [],
  activeId: null,
  streaming: false,
  abort: null,
  report: null,
  status: {},
  attachReport: null,
};
try { state.chats = JSON.parse(localStorage.getItem("pai.chats") || "[]") || []; } catch { state.chats = []; }
state.activeId = localStorage.getItem("pai.active") || null;

function persist() {
  const keep = state.chats.filter((c) => c.messages && c.messages.length).slice(0, 40);
  state.chats = keep;
  localStorage.setItem("pai.chats", JSON.stringify(keep));
  if (state.activeId) localStorage.setItem("pai.active", state.activeId);
}
function activeChat() {
  let c = state.chats.find((x) => x.id === state.activeId);
  if (!c) { c = { id: "c" + Date.now(), title: "New chat", messages: [], t: Date.now() }; state.chats.unshift(c); state.activeId = c.id; }
  return c;
}
function newChat() {
  if (state.streaming) { toast("Stop the current answer first", true); return; }
  const c = { id: "c" + Date.now(), title: "New chat", messages: [], t: Date.now() };
  state.chats.unshift(c);
  state.activeId = c.id;
  persist();
  renderChatList();
  renderMessages();
  switchView("chat");
  $("#input").focus();
}

/* ---------------- view switching ---------------- */
const VIEW_META = {
  chat: ["Chat", "Ask about a binary, your documents, or Windows internals"],
  lab: ["Binary Lab", "Static, read-only inspection of PE files · nothing is executed"],
  library: ["Library", "Search your own PDFs, notes and documentation offline"],
  settings: ["Settings", "Runtime, model, LAN and storage"],
};

function switchView(v) {
  state.view = v;
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach((s) => s.classList.toggle("active", s.id === "view-" + v));
  const [t, sub] = VIEW_META[v];
  $("#viewTitle").textContent = t;
  $("#viewSub").textContent = sub;
  if (v === "library") loadDocs();
  if (v === "settings") loadSettings();
}
$$(".nav-item").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));

/* ---------------- chat rendering ---------------- */
function renderChatList() {
  const wrap = $("#chatList");
  wrap.innerHTML = "";
  const list = state.chats.filter((c) => c.messages.length).slice(0, 20);
  for (const c of list) {
    const row = document.createElement("div");
    row.className = "chat-row" + (c.id === state.activeId ? " active" : "");
    row.innerHTML = `<button class="chat-btn">${esc(c.title || "Untitled")}</button>
      <button class="chat-del" title="Delete">✕</button>`;
    row.querySelector(".chat-btn").onclick = () => {
      if (state.streaming) { toast("Stop the current answer first", true); return; }
      state.activeId = c.id; persist(); renderChatList(); renderMessages(); switchView("chat");
    };
    row.querySelector(".chat-del").onclick = (e) => {
      e.stopPropagation();
      state.chats = state.chats.filter((x) => x.id !== c.id);
      if (state.activeId === c.id) state.activeId = (state.chats[0] || {}).id || null;
      persist(); renderChatList(); renderMessages();
    };
    wrap.appendChild(row);
  }
}

function emptyState() {
  return `<div class="empty">
    <h2>Offline analyst, ready when you are</h2>
    <p>Everything runs on this machine. Nothing is sent anywhere.</p>
    <div class="cards">
      <button class="card-btn" data-action="lab"><span class="ico">▤</span><b>Analyze a binary</b>
        <span>Drop an EXE or DLL and get headers, imports, strings and indicators.</span></button>
      <button class="card-btn" data-action="prompt" data-q="Explain the difference between a PE section's virtual size and raw size, and why it matters when reverse engineering."><span class="ico">⛭</span><b>Learn PE internals</b>
        <span>Ask about headers, sections, imports and how loaders map files.</span></button>
      <button class="card-btn" data-action="prompt" data-q="Summarize what is in my document library and suggest what I should read first."><span class="ico">▥</span><b>Search my documents</b>
        <span>Index PDFs and notes, then ask questions with citations.</span></button>
    </div></div>`;
}

function msgHTML(m) {
  if (m.role === "user") {
    return `<div class="msg user"><div class="avatar">YOU</div><div class="bubble">
      <div class="msg-name">You</div><div class="content">${esc(m.content)}</div></div></div>`;
  }
  const sources = (m.sources || []).length ? `<div class="sources">${m.sources.map((s) =>
    `<div class="source"><span class="sn">S${s.n}</span><span><b>${esc(s.name)}</b>${s.page ? " · p." + s.page : ""}
      — ${esc((s.snippet || "").slice(0, 170))}</span></div>`).join("")}</div>` : "";
  const meta = m.stats ? `<div class="msg-meta">
      ${m.stats.completion_tokens ? `<span>${m.stats.completion_tokens} tok</span>` : ""}
      ${m.stats.tokens_per_second ? `<span>${m.stats.tokens_per_second} tok/s</span>` : ""}
      ${m.meta && m.meta.backend ? `<span>${esc(m.meta.backend)}</span>` : ""}
    </div>` : "";
  const body = m.error
    ? `<div class="error-box">${esc(m.error)}</div>`
    : (m.content ? md(m.content) : `<div class="typing"><i></i><i></i><i></i></div>`);
  return `<div class="msg assistant"><div class="avatar">AI</div><div class="bubble">
    <div class="msg-name">Portable AI</div>${sources}<div class="content">${body}</div>${meta}</div></div>`;
}

function renderMessages() {
  const chat = state.chats.find((c) => c.id === state.activeId);
  const box = $("#messages");
  if (!chat || !chat.messages.length) { box.innerHTML = emptyState(); return; }
  box.innerHTML = chat.messages.map(msgHTML).join("");
  scrollBottom(true);
}

function scrollBottom(force = false) {
  const box = $("#messages");
  const near = box.scrollHeight - box.scrollTop - box.clientHeight < 160;
  if (force || near) box.scrollTop = box.scrollHeight;
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-action]");
  if (!b) return;
  if (b.dataset.action === "lab") { switchView("lab"); return; }
  if (b.dataset.action === "prompt") {
    switchView("chat");
    $("#input").value = b.dataset.q || "";
    autosize();
    $("#input").focus();
  }
});

/* ---------------- chat send ---------------- */
function autosize() {
  const ta = $("#input");
  ta.style.height = "auto";
  ta.style.height = Math.min(200, ta.scrollHeight) + "px";
}
$("#input").addEventListener("input", autosize);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

function setStreaming(on) {
  state.streaming = on;
  $("#btnSend").disabled = on;
  $("#btnSend").textContent = on ? "…" : "Send";
  $("#btnStop").hidden = !on;
}

async function sendMessage(textOverride) {
  if (state.streaming) return;
  const text = (textOverride ?? $("#input").value).trim();
  if (!text) return;
  if (!textOverride) { $("#input").value = ""; autosize(); }

  const chat = activeChat();
  if (!chat.messages.length) { chat.title = text.slice(0, 48); }
  chat.messages.push({ role: "user", content: text });
  const assistant = { role: "assistant", content: "", sources: [], stats: null, meta: null };
  chat.messages.push(assistant);
  persist(); renderChatList(); renderMessages();
  switchView("chat");

  const reportId = state.attachReport;
  state.attachReport = null;

  setStreaming(true);
  state.abort = new AbortController();
  let raf = null;
  const paint = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = null;
      const chatNow = state.chats.find((c) => c.id === state.activeId);
      if (!chatNow) return;
      const idx = chatNow.messages.indexOf(assistant);
      if (idx < 0) return;
      const nodes = $$("#messages .msg.assistant");
      const node = nodes[nodes.length - 1];
      if (!node) return;
      node.outerHTML = msgHTML(assistant);
      scrollBottom();
    });
  };

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        messages: chat.messages.slice(0, -1).map((m) => ({ role: m.role, content: m.content })),
        use_library: $("#useLib").checked,
        report_id: reportId || undefined,
        temperature: undefined,
      }),
      signal: state.abort.signal,
    });
    if (!res.ok || !res.body) {
      const t = await res.text();
      let msg = t;
      try { msg = JSON.parse(t).error || t; } catch { /* noop */ }
      throw new Error(msg || `HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
        if (ev.type === "token") { assistant.content += ev.text; paint(); }
        else if (ev.type === "reasoning") { /* keep reasoning out of the answer body */ }
        else if (ev.type === "sources") { assistant.sources = ev.sources || []; paint(); }
        else if (ev.type === "meta") { assistant.meta = ev; }
        else if (ev.type === "done") { assistant.stats = ev.stats || null; paint(); }
        else if (ev.type === "error") { assistant.error = ev.message; paint(); }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      assistant.content += "\n\n_(stopped)_";
    } else {
      assistant.error = err.message || String(err);
    }
  } finally {
    setStreaming(false);
    state.abort = null;
    if (!assistant.content && !assistant.error) assistant.error = "No response from the model.";
    persist();
    const chatNow = state.chats.find((c) => c.id === state.activeId);
    if (chatNow) renderMessages();
    refreshStatus();
  }
}

$("#btnSend").onclick = () => sendMessage();
$("#btnStop").onclick = () => { if (state.abort) state.abort.abort(); };
$("#btnNewChat").onclick = newChat;

/* ---------------- status ---------------- */
function shortModel(name) {
  if (!name) return "model";
  return String(name).split(/[\\/]/).pop().replace(/\.gguf$/i, "").replace(/-Q\d.*$/i, "").slice(0, 34);
}

async function refreshStatus() {
  try {
    const st = await api("/api/status");
    state.status = st;
    const pill = $("#statusPill");
    const txt = $("#statusText");
    const sub = $("#statusSub");
    pill.className = "status-pill";
    if (st.status === "ready") {
      pill.classList.add("ready");
      txt.textContent = shortModel(st.model);
      sub.textContent = `${st.backend || "local"} · ${st.mode === "external" ? "existing server" : "on this stick"}`;
      $("#modelChip").hidden = false;
      $("#modelChip").textContent = `${shortModel(st.model)} · ${st.backend || ""}`.trim();
    } else if (st.status === "starting" || st.status === "idle") {
      pill.classList.add("starting");
      txt.textContent = st.status === "idle" ? "starting model…" : "loading model…";
      sub.textContent = st.backend ? `${st.backend} backend` : "first load can take a few seconds";
      $("#modelChip").hidden = true;
    } else if (st.status === "error") {
      pill.classList.add("error");
      txt.textContent = "model error";
      sub.textContent = (st.error || "").slice(0, 70);
      $("#modelChip").hidden = true;
    } else {
      txt.textContent = st.status || "idle";
      sub.textContent = "local · offline";
    }
    if (state.view === "settings") fillRuntime(st);
  } catch (e) {
    const pill = $("#statusPill");
    pill.className = "status-pill error";
    $("#statusText").textContent = "server offline";
    $("#statusSub").textContent = "check the launcher window";
  }
}

function fillRuntime(st) {
  $("#rtChip").textContent = st.status;
  $("#rtChip").className = "chip " + (st.status === "ready" ? "ok" : st.status === "error" ? "bad" : "warn");
  const rows = [
    ["Mode", st.mode === "external" ? "existing OpenAI server" : "bundled llama.cpp"],
    ["Endpoint", st.endpoint || "—"],
    ["Model", st.model || "—"],
    ["Backend", st.backend || "—"],
    ["Load time", st.load_seconds != null ? st.load_seconds + " s" : "—"],
    ["Embeddings", st.embed && st.embed.status === "ready" ? "ready (semantic search on)" : (st.embed ? st.embed.status : "—")],
  ];
  if (st.error) rows.push(["Error", st.error]);
  $("#rtInfo").innerHTML = rows.map(([k, v]) =>
    `<div class="k"><span>${k}</span><span>${esc(v)}</span></div>`).join("");
  const g = (st.gpu || [])[0];
  const hw = [];
  if (g) hw.push(["GPU", `${g.name} · ${g.vram_used_mb}/${g.vram_total_mb} MB VRAM`]);
  if (st.ram && st.ram.total) hw.push(["RAM", `${st.ram.free} free of ${st.ram.total} (${st.ram.used_pct}% used)`]);
  if (st.storage && st.storage.free_h) hw.push(["Drive", `${st.storage.free_h} free of ${st.storage.total_h}`]);
  if (st.library) hw.push(["Library", `${st.library.documents} docs · ${st.library.chunks} chunks · ${st.library.db_size_h}`]);
  $("#hwInfo").innerHTML = hw.map(([k, v]) =>
    `<div class="k"><span>${k}</span><span>${esc(v)}</span></div>`).join("");
  if (st.storage) $("#storageInfo").textContent =
    `Total USB volume: ${st.storage.total_h} · used ${st.storage.used_h} · free ${st.storage.free_h}`;
  const lan = st.lan || {};
  $("#lanChip").textContent = lan.enabled ? "on" : "off";
  $("#lanChip").className = "chip " + (lan.enabled ? "ok" : "quiet");
  $("#lanInfo").innerHTML = lan.enabled
    ? `<div class="k"><span>URL</span><span>${esc(lan.url)}/?t=${esc(lan.token)}</span></div>
       <div class="k"><span>Token</span><span>${esc(lan.token)}</span></div>`
    : `<div class="k"><span>Status</span><span>loopback only (127.0.0.1)</span></div>`;
}

/* ---------------- settings ---------------- */
async function loadSettings() {
  try {
    const st = await api("/api/status");
    state.status = st;
    fillRuntime(st);
    const { config } = await api("/api/config");
    const models = (st.models || []).filter((m) => !m.embedding);
    const sel = $("#cfgModel");
    sel.innerHTML = models.length
      ? models.map((m) => `<option value="${esc(m.path)}" ${m.path === config.llm.model_file ? "selected" : ""}>
          ${esc(m.name)} · ${esc(m.size_h)}</option>`).join("")
      : `<option value="${esc(config.llm.model_file)}">${esc(config.llm.model_file)} (not found)</option>`;
    $("#cfgCtx").value = config.llm.ctx;
    $("#cfgGpu").value = config.llm.gpu_layers;
    $("#cfgBackend").value = config.llm.backend;
    $("#cfgMode").value = config.llm.mode;
    $("#cfgTemp").value = config.llm.temperature;
    $("#tempVal").textContent = Number(config.llm.temperature).toFixed(2);
    $("#aboutInfo").innerHTML = [
      ["Runtime", "llama.cpp (bundled) + Python 3.13 (bundled)"],
      ["App port", config.server.port],
      ["Folder", location.host],
      ["Model file", config.llm.model_file],
      ["Platform", navigator.platform],
    ].map(([k, v]) => `<div class="k"><span>${k}</span><span>${esc(v)}</span></div>`).join("");
  } catch (e) {
    toast("Could not load settings: " + e.message, true);
  }
}
$("#cfgTemp").addEventListener("input", (e) => { $("#tempVal").textContent = Number(e.target.value).toFixed(2); });

$("#btnSaveCfg").onclick = async () => {
  try {
    const body = {
      llm: {
        model_file: $("#cfgModel").value,
        ctx: Number($("#cfgCtx").value),
        gpu_layers: Number($("#cfgGpu").value),
        backend: $("#cfgBackend").value,
        mode: $("#cfgMode").value,
        temperature: Number($("#cfgTemp").value),
      },
    };
    await api("/api/config", { method: "POST", body });
    toast("Saved. Restarting the model…");
    setTimeout(refreshStatus, 1200);
  } catch (e) { toast("Save failed: " + e.message, true); }
};

$("#btnRestartModel").onclick = async () => {
  try { await api("/api/runtime/restart", { method: "POST", body: {} }); toast("Restarting model…"); }
  catch (e) { toast(e.message, true); }
};
$("#btnStopModel").onclick = async () => {
  try { await api("/api/runtime/stop", { method: "POST", body: {} }); toast("Model stopped"); refreshStatus(); }
  catch (e) { toast(e.message, true); }
};
$("#btnLanToggle").onclick = async () => {
  const on = !!(state.status.lan && state.status.lan.enabled);
  try {
    await api("/api/config", { method: "POST", body: { server: { lan: !on } } });
    toast(!on ? "LAN enabled in config. Restart the app (Stop-AI then Start-AI) to listen on the network."
              : "LAN disabled in config. Restart the app to return to loopback only.");
    loadSettings();
  } catch (e) { toast(e.message, true); }
};
$("#btnCopyLan").onclick = () => {
  const lan = state.status.lan || {};
  if (!lan.enabled) return toast("LAN mode is off", true);
  copyText(`${lan.url}/?t=${lan.token}`);
};

/* ---------------- binary lab ---------------- */
const drop = $("#drop");
drop.onclick = () => $("#fileInput").click();
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) analyzeUpload(f);
});
$("#fileInput").onchange = (e) => { const f = e.target.files[0]; if (f) analyzeUpload(f); e.target.value = ""; };
$("#btnPath").onclick = () => analyzePath($("#pathInput").value.trim());
$("#pathInput").addEventListener("keydown", (e) => { if (e.key === "Enter") analyzePath($("#pathInput").value.trim()); });

function reportLoading(name) {
  state.report = null;
  $("#reportArea").hidden = false;
  $("#reportArea").innerHTML = `<div class="card"><div class="card-head">
    <h2>Analyzing ${esc(name)}</h2><span class="chip warn" id="anPct">working…</span></div>
    <div class="tiny muted">Reading headers, sections, imports, resources and strings. The file is never executed.</div>
    <div id="anErr"></div></div>`;
  $("#reportArea").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function analyzeUpload(file) {
  reportLoading(file.name);
  try {
    const res = await upload(`/api/analyze?name=${encodeURIComponent(file.name)}`, file,
      (p) => { const el = $("#anPct"); if (el) el.textContent = `uploading ${Math.round(p * 100)}%`; });
    if (!res.ok) throw new Error(res.error || "analysis failed");
    renderReport(res.report);
  } catch (e) {
    const el = $("#anErr");
    if (el) el.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    else toast(e.message, true);
  }
}

async function analyzePath(path) {
  if (!path) { toast("Enter a file path first", true); return; }
  reportLoading(path.split(/[\\/]/).pop());
  try {
    const res = await api("/api/analyze", { method: "POST", body: { path } });
    renderReport(res.report);
  } catch (e) {
    const el = $("#anErr");
    if (el) el.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

const TABS = [
  ["overview", "Overview"], ["sections", "Sections"], ["imports", "Imports"],
  ["exports", "Exports"], ["resources", "Resources"], ["strings", "Strings"], ["disasm", "Disassembly"],
];

function renderReport(r) {
  state.report = r;
  const s = r.summary || {};
  const p = r.pe || {};
  const tabs = TABS.map(([id, label], i) =>
    `<button class="tab${i === 0 ? " active" : ""}" data-tab="${id}">${label}</button>`).join("");
  const area = $("#reportArea");
  area.hidden = false;
  area.innerHTML = `<div class="card">
    <div class="rep-head">
      <div class="rep-title">${esc(s.name || "report")}</div>
      ${s.kind ? `<span class="chip">${esc(s.kind)}</span>` : ""}
      ${p.bits ? `<span class="chip quiet">${p.bits}-bit ${esc(p.machine || "")}</span>` : ""}
      ${p.is_dll ? `<span class="chip quiet">DLL</span>` : ""}
      ${p.is_dotnet ? `<span class="chip quiet">.NET</span>` : ""}
      <span class="chip ${s.signed ? "ok" : "quiet"}">${s.signed ? "signed" : "unsigned"}</span>
      ${s.attention ? `<span class="chip bad">${s.attention} indicator${s.attention > 1 ? "s" : ""}</span>` : ""}
      <span class="spacer"></span>
      <span class="rep-sub">${esc(s.size_h || "")} · ${r.duration_ms} ms</span>
    </div>
    <div class="hash" data-copy="${esc(r.file.sha256)}">SHA-256 · ${esc(r.file.sha256)}</div>
    <div class="tabs">${tabs}</div>
    <div id="tabBody"></div>
    <div class="rep-actions">
      <button class="primary" id="btnExplain">Explain with AI</button>
      <button class="ghost" id="btnCopyDossier">Copy dossier</button>
    </div>
  </div>`;
  $$("#reportArea .tab").forEach((b) => b.onclick = () => {
    $$("#reportArea .tab").forEach((x) => x.classList.toggle("active", x === b));
    renderTab(b.dataset.tab);
  });
  $("#btnExplain").onclick = () => explainReport(r);
  $("#btnCopyDossier").onclick = () => copyText(r.dossier || "");
  renderTab("overview");
  area.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderTab(id) {
  const r = state.report;
  const body = $("#tabBody");
  if (!r) return;
  const p = r.pe || {};
  if (id === "overview") {
    const obs = (r.observations || []).map((o) => `<div class="obs ${o.severity}">
      <span class="sev">${esc(o.severity)}</span>
      <div><b>${esc(o.title)}</b><p>${esc(o.detail)}</p></div></div>`).join("");
    const meta = [
      ["Type", `${r.kind}${r.file.file_kind ? " · " + r.file.file_kind : ""}`],
      ["Size", r.file.size_h],
      ["MD5", r.file.md5], ["SHA-1", r.file.sha1],
      ["Compiled", p.timestamp || "—"],
      ["Linker", p.linker || "—"],
      ["Subsystem", p.subsystem || "—"],
      ["Entry point", p.entry_point ? `${p.entry_point} (${p.entry_section || "?"})` : "—"],
      ["Image base", p.image_base || "—"],
      ["Imphash", p.imphash || "—"],
      ["Flags", (p.dll_characteristics || []).join(" ") || "—"],
      ["Overlay", r.overlay && r.overlay.present ? `${r.overlay.size} bytes` : "none"],
      ["Imports / exports", `${r.import_count || 0} / ${(r.exports || []).length}`],
    ];
    body.innerHTML = obs + `<div class="kv" style="margin-top:14px">${meta.map(([k, v]) =>
      `<div class="k"><span>${k}</span><span>${esc(v)}</span></div>`).join("")}</div>` +
      (r.version_info && Object.keys(r.version_info).length ? `<h3 style="margin:16px 0 8px;font-size:13px">Version resources</h3>
        <div class="kv">${Object.entries(r.version_info).slice(0, 16).map(([k, v]) =>
          `<div class="k"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}</div>` : "") +
      (r.manifest && r.manifest.present ? `<h3 style="margin:16px 0 8px;font-size:13px">Manifest</h3>
        <div class="tiny muted">requestedExecutionLevel: <b>${esc(r.manifest.requested_execution_level || "?")}</b>
        · uiAccess: ${r.manifest.ui_access} · dpiAware: ${r.manifest.dpi_aware}</div>` : "") +
      ((r.debug || []).some((d) => d.pdb) ? `<h3 style="margin:16px 0 8px;font-size:13px">PDB path</h3>
        <div class="hash" data-copy="${esc((r.debug.find((d) => d.pdb) || {}).pdb || "")}">${esc((r.debug.find((d) => d.pdb) || {}).pdb || "")}</div>` : "") +
      ((r.warnings || []).length ? `<div class="error-box" style="margin-top:12px">${r.warnings.map(esc).join("<br>")}</div>` : "") +
      `<p class="tiny muted" style="margin-top:12px">${esc(r.note || "")}</p>`;
  } else if (id === "sections") {
    const rows = (r.sections || []).map((s) => {
      const cls = s.entropy >= 7.2 ? "vhi" : s.entropy >= 6.5 ? "hi" : "";
      const w = Math.max(2, Math.round(s.entropy / 8 * 46));
      return `<tr><td class="mono">${esc(s.name)}</td><td class="mono">${esc(s.va)}</td>
        <td class="mono">${s.vsize.toLocaleString()}</td><td class="mono">${s.rawsize.toLocaleString()}</td>
        <td><span class="ent ${cls}" style="width:${w}px"></span>${s.entropy}</td>
        <td>${(s.flags || "").split(" ").map((f) =>
          `<span class="badge ${f === "WRITE" && s.exec ? "rwx" : ""}">${esc(f)}</span>`).join(" ")}</td></tr>`;
    }).join("");
    body.innerHTML = `<div class="table-wrap"><table class="data"><thead><tr>
      <th>Name</th><th>VA</th><th>VSize</th><th>Raw</th><th>Entropy</th><th>Flags</th>
      </tr></thead><tbody>${rows}</tbody></table></div>
      <p class="tiny muted" style="margin-top:8px">Entropy above ~7.2 bits/byte usually means compressed or encrypted content.</p>`;
  } else if (id === "imports") {
    const entries = Object.entries(r.imports || {});
    body.innerHTML = (entries.length ? entries.map(([dll, fns]) =>
      `<details class="dll"><summary>${esc(dll)} <span class="badge">${fns.length}</span></summary>
        <div class="fns">${fns.map(esc).join(", ")}</div></details>`).join("")
      : `<div class="empty-note">No imports parsed.</div>`) +
      (r.ordinal_imports ? `<p class="tiny muted">${r.ordinal_imports} imports by ordinal.</p>` : "");
  } else if (id === "exports") {
    const ex = r.exports || [];
    body.innerHTML = ex.length ? `<div class="table-wrap"><table class="data"><thead>
      <tr><th>Name</th><th>Ordinal</th><th>RVA</th></tr></thead><tbody>
      ${ex.slice(0, 500).map((e) => `<tr><td class="mono">${esc(e.name)}</td>
        <td class="mono">${e.ordinal}</td><td class="mono">${esc(e.rva)}</td></tr>`).join("")}
      </tbody></table></div>${ex.length > 500 ? `<p class="tiny muted">Showing 500 of ${ex.length}.</p>` : ""}`
      : `<div class="empty-note">No export table.</div>`;
  } else if (id === "resources") {
    const rs = r.resources || [];
    body.innerHTML = rs.length ? `<div class="table-wrap"><table class="data"><thead>
      <tr><th>Type</th><th>Entries</th><th>Total size</th></tr></thead><tbody>
      ${rs.map((x) => `<tr><td>${esc(x.type)}</td><td class="mono">${x.count}</td>
        <td class="mono">${x.total_size.toLocaleString()} B</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty-note">No resources.</div>`;
    if (r.resource_flags && (r.resource_flags.embedded_pe || []).length) {
      body.innerHTML = `<div class="error-box" style="margin-bottom:10px">Embedded PE detected in resources:
        ${r.resource_flags.embedded_pe.map((x) => `${esc(x.type)}/${esc(x.name)} (${x.size} B)`).join(", ")}</div>` + body.innerHTML;
    }
  } else if (id === "strings") {
    const b = (r.strings && r.strings.buckets) || {};
    const groups = ["urls", "domains", "ips", "emails", "registry", "paths", "keywords"]
      .filter((k) => (b[k] || []).length);
    body.innerHTML = `<p class="tiny muted" style="margin-bottom:10px">
      ${r.strings.total} strings extracted, ${r.strings.unique} unique</p>` +
      groups.map((k) => `<h3 style="margin:12px 0 6px;font-size:12.5px">${k}
        <span class="badge">${b[k].length}</span></h3>
        <div class="strlist">${b[k].map(esc).join("\n")}</div>`).join("") +
      ((r.strings.notable || []).length ? `<h3 style="margin:12px 0 6px;font-size:12.5px">notable</h3>
        <div class="strlist">${r.strings.notable.map(esc).join("\n")}</div>` : "") +
      ((r.strings.sample || []).length ? `<details style="margin-top:12px"><summary class="tiny muted"
        style="cursor:pointer">Show sample of all strings (${r.strings.sample.length})</summary>
        <div class="strlist" style="margin-top:8px">${r.strings.sample.map(esc).join("\n")}</div></details>` : "");
  } else if (id === "disasm") {
    const blocks = r.disasm || [];
    body.innerHTML = blocks.length ? blocks.map((blk) => `<pre class="disasm"><span class="lbl">${esc(blk.label)}</span>
      ${blk.lines.map(esc).join("\n")}</pre>`).join("")
      : `<div class="empty-note">No disassembly produced (capstone missing or entry point unreadable).</div>`;
  }
}

function explainReport(r) {
  state.attachReport = r.id;
  switchView("chat");
  sendMessage(`Explain this binary based on the static analysis: what it likely is, the most important ` +
    `evidence in the dossier, what looks ordinary, and what you would check next in an isolated VM. ` +
    `Be explicit about uncertainty.`);
}

/* ---------------- library ---------------- */
const docDrop = $("#docDrop");
docDrop.onclick = () => $("#docInput").click();
["dragenter", "dragover"].forEach((ev) => docDrop.addEventListener(ev, (e) => {
  e.preventDefault(); docDrop.classList.add("over");
}));
["dragleave", "drop"].forEach((ev) => docDrop.addEventListener(ev, (e) => {
  e.preventDefault(); docDrop.classList.remove("over");
}));
docDrop.addEventListener("drop", (e) => {
  const files = Array.from(e.dataTransfer.files || []);
  if (files.length) uploadDocs(files);
});
$("#docInput").onchange = (e) => { uploadDocs(Array.from(e.target.files || [])); e.target.value = ""; };

async function uploadDocs(files) {
  const prog = $("#docProgress");
  let ok = 0, fail = 0;
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    prog.textContent = `(${i + 1}/${files.length}) ${f.name}`;
    try {
      await upload(`/api/library/upload?name=${encodeURIComponent(f.name)}`, f,
        (p) => { prog.textContent = `(${i + 1}/${files.length}) ${f.name} — ${Math.round(p * 100)}%`; });
      ok++;
    } catch (e) {
      fail++;
      toast(`${f.name}: ${e.message}`, true);
    }
  }
  prog.textContent = `Done: ${ok} indexed${fail ? `, ${fail} failed` : ""}.`;
  await loadDocs();
}

$("#btnFolder").onclick = async () => {
  const path = $("#folderInput").value.trim();
  if (!path) return toast("Enter a folder path", true);
  $("#docProgress").textContent = "Indexing folder… (large folders take a while)";
  try {
    const r = await api("/api/library/index", { method: "POST", body: { path } });
    $("#docProgress").textContent = `Added ${r.added}, skipped ${r.skipped}, failed ${r.failed}.`;
    if (r.errors && r.errors.length) toast(`${r.errors.length} file(s) failed — see launcher log`, true);
    await loadDocs();
  } catch (e) {
    $("#docProgress").textContent = "";
    toast(e.message, true);
  }
};

$("#btnSearch").onclick = doSearch;
$("#libQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

async function doSearch() {
  const q = $("#libQuery").value.trim();
  if (!q) return;
  const box = $("#searchResults");
  box.innerHTML = `<div class="tiny muted">Searching…</div>`;
  try {
    const r = await api(`/api/library/search?q=${encodeURIComponent(q)}&k=8`);
    if (!r.results.length) { box.innerHTML = `<div class="empty-note">No matches. Is the document indexed?</div>`; return; }
    box.innerHTML = r.results.map((x) => `<div class="res">
      <b>${esc(x.name)}</b>${x.page ? ` <span class="badge">p.${x.page}</span>` : ""}
      <div class="snip">${esc(x.snippet)}</div>
      <div class="meta">${esc(x.path)} · score ${x.score}</div></div>`).join("");
  } catch (e) {
    box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

async function loadDocs() {
  try {
    const r = await api("/api/library");
    const st = r.stats || {};
    $("#libStats").textContent = `${st.documents || 0} docs · ${st.chunks || 0} chunks`;
    const docs = r.docs || [];
    $("#docTable").innerHTML = docs.length ? `<table class="data"><thead><tr>
      <th>Name</th><th>Type</th><th>Size</th><th>Chunks</th><th>Status</th><th>Indexed</th><th></th>
      </tr></thead><tbody>${docs.map((d) => `<tr>
        <td>${esc(d.name)}<div class="tiny muted mono">${esc(d.path)}</div></td>
        <td class="mono">${esc(d.ext || "")}</td><td class="mono">${esc(d.size_h)}</td>
        <td class="mono">${d.chunks}</td>
        <td>${d.status === "ok" ? `<span class="chip ok">ok</span>`
          : `<span class="chip bad" title="${esc(d.error || "")}">error</span>`}</td>
        <td class="mono">${esc((d.indexed_at || "").replace("T", " ").slice(0, 16))}</td>
        <td><button class="ghost small" data-del="${d.id}">Remove</button></td>
      </tr>`).join("")}</tbody></table>` : `<div class="empty-note">Nothing indexed yet.</div>`;
    $$("#docTable [data-del]").forEach((b) => b.onclick = async () => {
      try { await api("/api/library/delete", { method: "POST", body: { id: Number(b.dataset.del) } });
        toast("Removed"); loadDocs(); } catch (e) { toast(e.message, true); }
    });
  } catch (e) {
    $("#docTable").innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}
$("#btnRefreshDocs").onclick = loadDocs;
$("#btnReembed").onclick = async () => {
  try { const r = await api("/api/library/reembed", { method: "POST", body: {} }); toast(r.message || "Started"); }
  catch (e) { toast(e.message, true); }
};

/* ---------------- misc ---------------- */
$("#btnHelp").onclick = () => toast(
  "Tip: use Binary Lab → Explain with AI. Tick “Use library” to cite your documents. Settings → LAN for phone/tablet access.", false);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.streaming) { if (state.abort) state.abort.abort(); }
  if (e.ctrlKey && e.key.toLowerCase() === "k") { e.preventDefault(); $("#input").focus(); switchView("chat"); }
});

/* ---------------- boot ---------------- */
(function boot() {
  renderChatList();
  renderMessages();
  refreshStatus();
  setInterval(() => { if (!document.hidden) refreshStatus(); }, 4000);
  setTimeout(autosize, 30);

  const params = new URLSearchParams(location.search);
  const v = params.get("view");
  if (v && VIEW_META[v]) switchView(v);
  const rid = params.get("report");
  if (rid && /^[0-9a-f]{16}$/.test(rid)) {
    api(`/api/reports/${rid}`).then((r) => {
      switchView("lab");
      renderReport(r.report);
    }).catch(() => toast("Report not found", true));
  }
})();
