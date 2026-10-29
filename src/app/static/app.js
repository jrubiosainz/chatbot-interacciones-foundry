"use strict";

(() => {
  const NF = new Intl.NumberFormat("es-ES");
  const COMPACT = new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 1 });
  const RANGE_LABELS = { "1h": "Última hora", "24h": "Últimas 24 h", "7d": "Últimos 7 días", "30d": "Últimos 30 días" };
  const DASH_REFRESH_MS = 15000;
  const SUMMARY_REFRESH_MS = 10000;
  const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus", "audio/ogg"];

  const state = {
    config: null,
    active: null,
    messages: {},
    loaded: {},
    summary: {},
    drafts: {},
    modelByUser: {},
    busy: {},
    errors: {},
    audioEnabled: false, // Siempre arranca silenciado y no se guarda entre sesiones.
    player: null,
    playingUrl: null,
    rec: null,
    range: "30d",
    dash: null,
    dashLoading: false,
    dashTimer: null,
  };

  const $ = (selector) => document.querySelector(selector);

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else if (key === "style") Object.entries(value).forEach(([prop, val]) => node.style.setProperty(prop, val));
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  async function api(path, { json, form, method } = {}) {
    const init = { method: method || (json || form ? "POST" : "GET"), headers: { Accept: "application/json" } };
    if (json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(json);
    } else if (form) {
      init.body = form;
    }
    const response = await fetch(path, init);
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!response.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join(" · ");
      const error = new Error(detail || `Error ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  // ---------- utilidades de formato ----------
  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmtNum = (n) => NF.format(n || 0);
  const compact = (n) => ((n || 0) >= 10000 ? COMPACT.format(n) : NF.format(n || 0));
  const toDate = (iso) => (iso ? new Date(iso) : null);
  const fmtTime = (iso) => (iso ? toDate(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" }) : "");
  const fmtDate = (iso) => (iso ? toDate(iso).toLocaleString("es-ES") : "");
  function relTime(iso) {
    if (!iso) return "—";
    const seconds = Math.max(0, (Date.now() - toDate(iso).getTime()) / 1000);
    if (seconds < 60) return "hace unos segundos";
    if (seconds < 3600) return `hace ${Math.floor(seconds / 60)} min`;
    if (seconds < 86400) return `hace ${Math.floor(seconds / 3600)} h`;
    return `hace ${Math.floor(seconds / 86400)} d`;
  }
  const safeUrl = (url) => (/^https?:\/\//i.test(url || "") ? url : null);
  const hostOf = (url) => {
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch {
      return url;
    }
  };

  function inlineMd(text) {
    let s = escapeHtml(text);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[\s(])[*_]([^*_\s][^*_]*?)[*_](?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
    s = s.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );
    s = s.replace(
      /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>'
    );
    return s;
  }

  function renderMarkdown(source) {
    const lines = String(source || "").replace(/【[^】]*】/g, "").replace(/\r/g, "").split("\n");
    const out = [];
    let para = [];
    let list = null;
    let table = null;
    const flushPara = () => {
      if (para.length) out.push(`<p>${para.map(inlineMd).join("<br>")}</p>`);
      para = [];
    };
    const renderList = (current) =>
      `<${current.tag}>${current.items
        .map((item) => `<li>${inlineMd(item.text)}${item.sub ? renderList(item.sub) : ""}</li>`)
        .join("")}</${current.tag}>`;
    const flushList = () => {
      if (list) out.push(renderList(list));
      list = null;
    };
    const flushTable = () => {
      if (table && table.length) {
        const [head, ...body] = table;
        const row = (cells, tag) => `<tr>${cells.map((cell) => `<${tag}>${inlineMd(cell)}</${tag}>`).join("")}</tr>`;
        out.push(`<table><thead>${row(head, "th")}</thead><tbody>${body.map((r) => row(r, "td")).join("")}</tbody></table>`);
      }
      table = null;
    };
    const flushAll = () => {
      flushPara();
      flushList();
      flushTable();
    };
    for (const raw of lines) {
      const line = raw.trimEnd();
      const trimmed = line.trim();
      if (!trimmed) {
        flushAll();
        continue;
      }
      if (/^\|.*\|$/.test(trimmed)) {
        flushPara();
        flushList();
        if (/^\|[\s:|-]+\|$/.test(trimmed)) continue;
        table = table || [];
        table.push(trimmed.slice(1, -1).split("|").map((cell) => cell.trim()));
        continue;
      }
      flushTable();
      const heading = trimmed.match(/^#{1,6}\s+(.*)$/);
      if (heading) {
        flushAll();
        out.push(`<p><strong>${inlineMd(heading[1])}</strong></p>`);
        continue;
      }
      const bullet = trimmed.match(/^[-*•]\s+(.*)$/);
      const ordered = trimmed.match(/^\d+[.)]\s+(.*)$/);
      const indented = /^\s{2,}/.test(raw);
      if (bullet || ordered) {
        flushPara();
        const tag = bullet ? "ul" : "ol";
        const text = (bullet || ordered)[1];
        if (indented && list && list.items.length) {
          // Un nivel de anidación: la viñeta sangrada cuelga del último elemento.
          const parent = list.items[list.items.length - 1];
          parent.sub = parent.sub || { tag, items: [] };
          parent.sub.items.push({ text });
          continue;
        }
        if (!list || list.tag !== tag) {
          flushList();
          list = { tag, items: [] };
        }
        list.items.push({ text });
        continue;
      }
      if (list && indented) {
        const last = list.items[list.items.length - 1];
        const target = last.sub ? last.sub.items[last.sub.items.length - 1] : last;
        target.text += ` ${trimmed}`;
        continue;
      }
      flushList();
      para.push(trimmed);
    }
    flushAll();
    return out.join("");
  }

  // ---------- configuración y arranque ----------
  const users = () => (state.config ? state.config.users : []);
  const currentUser = () => users().find((user) => user.id === state.active);
  const modelFor = (uid) => state.modelByUser[uid] || state.config.default_model;
  const userCount = (uid) => (state.messages[uid] || []).filter((m) => m.role === "user").length;
  const isReady = () => Boolean(state.config && state.config.bootstrap && state.config.bootstrap.ready);

  function setBootPill(kind, text, title) {
    const pill = $("#boot-pill");
    pill.className = `pill pill-${kind}`;
    pill.textContent = text;
    pill.title = title || text;
  }

  function updateBoot() {
    const boot = state.config.bootstrap;
    if (boot.ready) {
      setBootPill("ok", `Foundry listo · ${state.config.foundry.project}`, "Índice de AI Search y agentes de Foundry preparados");
    } else if (!state.config.foundry.project) {
      setBootPill("error", "Modo local sin Azure", boot.error || "Sin proyecto de Foundry configurado");
    } else {
      const done = Object.values(boot.steps).filter((step) => step === "ok").length;
      const total = Object.keys(boot.steps).length;
      setBootPill("wait", `Preparando entorno ${done}/${total}…`, boot.error ? `Último error: ${boot.error}` : "Creando índice y agentes");
    }
  }

  async function loadConfig() {
    try {
      state.config = await api("/api/config");
    } catch {
      setBootPill("error", "Sin conexión con el servidor");
      setTimeout(loadConfig, 5000);
      return;
    }
    if (!state.active) initUi();
    updateBoot();
    updateComposer();
    if (!isReady() && state.config.foundry.project) setTimeout(loadConfig, 5000);
  }

  function initUi() {
    const cfg = state.config;
    for (const user of cfg.users) state.modelByUser[user.id] = cfg.default_model;
    $("#model-select").replaceChildren(
      ...cfg.models.map((model) => h("option", { value: model.key, text: model.label, title: `Agente ${model.agent}` }))
    );
    state.range = cfg.default_range || "30d";
    $("#range-select").replaceChildren(
      ...cfg.ranges.map((key) => h("option", { value: key, text: RANGE_LABELS[key] || key, selected: key === state.range }))
    );
    bindEvents();
    selectUser(cfg.users[0].id);
    refreshSummary();
    loadDashboard();
    setInterval(() => !document.hidden && refreshSummary(), SUMMARY_REFRESH_MS);
    setInterval(() => !document.hidden && loadDashboard(), DASH_REFRESH_MS);
    setInterval(refreshRelativeTimes, 30000);
  }

  function bindEvents() {
    $("#composer").addEventListener("submit", (event) => {
      event.preventDefault();
      sendText();
    });
    const input = $("#input");
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendText();
      }
    });
    input.addEventListener("input", () => {
      state.drafts[state.active] = input.value;
      autoGrow();
    });
    $("#model-select").addEventListener("change", (event) => {
      state.modelByUser[state.active] = event.target.value;
    });
    $("#mic-btn").addEventListener("click", () => (state.rec ? stopRecording(false) : startRecording()));
    $("#audio-toggle").addEventListener("click", () => setAudioEnabled(!state.audioEnabled));
    $("#range-select").addEventListener("change", (event) => {
      state.range = event.target.value;
      loadDashboard();
    });
    $("#refresh-btn").addEventListener("click", () => loadDashboard());
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.rec) {
        stopRecording(true);
        flash("Grabación cancelada.");
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshSummary();
        loadDashboard();
      } else if (state.player) {
        stopAudio();
      }
    });
  }

  function autoGrow() {
    const input = $("#input");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }

  // ---------- pestañas y mensajes ----------
  function renderTabs() {
    $("#tabs").replaceChildren(
      ...users().map((user) => {
        const known = state.summary[user.id] ? state.summary[user.id].messages : state.loaded[user.id] ? userCount(user.id) : null;
        return h(
          "button",
          {
            type: "button",
            role: "tab",
            class: "tab",
            id: `tab-${user.id}`,
            "aria-selected": String(user.id === state.active),
            title: `${user.name} · ${user.role}`,
            style: { "--c": user.color },
            onclick: () => selectUser(user.id),
          },
          h("span", { class: "avatar sm", text: user.initials }),
          h("span", { text: user.name.split(" ")[0] }),
          h("span", { class: "tab-count", title: "Mensajes enviados", text: known === null ? "–" : String(known) }),
          state.busy[user.id] ? h("span", { class: "tab-busy", title: "Mensaje en curso" }) : null
        );
      })
    );
  }

  function renderPersona() {
    const user = currentUser();
    const bar = $("#persona-bar");
    bar.style.setProperty("--c", user.color);
    $("#messages").style.setProperty("--c", user.color);
    bar.replaceChildren(
      h("span", { class: "avatar lg", style: { "--c": user.color }, text: user.initials }),
      h(
        "div",
        { class: "persona-text" },
        h("strong", { text: user.name }),
        h("span", { class: "role", text: ` · ${user.role}` }),
        h("p", { text: user.description })
      ),
      h("button", {
        type: "button",
        class: "ghost-btn",
        text: "Reiniciar",
        title: "Borra el historial de este usuario y pone a cero su fila del tablero (la telemetría ya registrada se conserva en Application Insights)",
        onclick: resetActive,
      })
    );
  }

  function selectUser(uid) {
    if (state.rec && state.rec.uid !== uid) {
      stopRecording(true);
      flash("Grabación cancelada al cambiar de usuario.");
    }
    if (state.player && uid !== state.active) stopAudio();
    state.active = uid;
    const input = $("#input");
    input.value = state.drafts[uid] || "";
    autoGrow();
    $("#model-select").value = modelFor(uid);
    renderTabs();
    renderPersona();
    renderMessages();
    updateComposer();
    highlightDashboardRow();
    if (!state.loaded[uid]) loadMessages(uid);
  }

  async function loadMessages(uid) {
    try {
      const data = await api(`/api/users/${encodeURIComponent(uid)}/messages`);
      if (state.busy[uid]) return;
      state.messages[uid] = data.messages;
      state.loaded[uid] = true;
      state.errors[uid] = null;
    } catch (error) {
      state.errors[uid] = `No se pudo cargar la conversación: ${error.message}`;
    }
    if (uid === state.active) renderMessages();
    renderTabs();
  }

  function emptyState() {
    const user = currentUser();
    return h(
      "div",
      { class: "empty" },
      h("strong", { text: `Conversación de ${user.name}` }),
      "Todavía no hay mensajes. Escribe una pregunta sobre trámites municipales o pulsa 🎙️ para hablar."
    );
  }

  function pendingNodes(pending) {
    const text = pending.kind === "voz" ? "🎙️ Transcribiendo audio…" : pending.text;
    return [
      h("div", { class: "msg from-user pending" }, h("div", { class: "bubble" }, h("div", { class: "bubble-body", text }))),
      h(
        "div",
        { class: "msg from-bot pending" },
        h("span", { class: "avatar bot", text: "IA" }),
        h("div", { class: "bubble typing", role: "status", "aria-label": "El asistente está respondiendo" }, h("span"), h("span"), h("span"))
      ),
    ];
  }

  function renderMessages() {
    const box = $("#messages");
    const uid = state.active;
    const list = state.messages[uid] || [];
    const nodes = [];
    if (!state.loaded[uid] && !state.errors[uid]) nodes.push(h("div", { class: "empty", text: "Cargando conversación…" }));
    else if (state.loaded[uid] && !list.length && !state.busy[uid]) nodes.push(emptyState());
    for (const message of list) nodes.push(renderMessage(message));
    if (state.busy[uid]) nodes.push(...pendingNodes(state.busy[uid]));
    if (state.errors[uid]) nodes.push(h("div", { class: "msg-error", role: "alert", text: state.errors[uid] }));
    box.replaceChildren(...nodes);
    box.style.scrollBehavior = "auto";
    box.scrollTop = box.scrollHeight;
    box.style.scrollBehavior = "";
  }

  function renderMessage(message) {
    const isUser = message.role === "user";
    const meta = [
      h("span", {
        class: `badge ${message.modality === "voz" ? "b-voice" : "b-text"}`,
        text: message.modality === "voz" ? "🎙️ voz" : "💬 texto",
      }),
    ];
    if (isUser) {
      if (message.simulated) {
        meta.push(h("span", { class: "badge b-sim", text: "simulado", title: "Mensaje de demostración enviado por el script de siembra" }));
      }
      if (message.stt) {
        meta.push(h("span", { class: "badge b-voice", text: message.stt.model, title: "Transcripción voz → texto" }));
      }
    } else {
      meta.push(h("span", { class: "badge b-model", text: message.deployment || message.model_label, title: `Agente de Foundry: ${message.agent || ""}` }));
      const usage = message.usage || {};
      if (usage.input_tokens != null) {
        const reasoning = usage.reasoning_tokens ? ` (razonamiento: ${fmtNum(usage.reasoning_tokens)})` : "";
        meta.push(
          h("span", {
            class: "badge",
            text: `${fmtNum(usage.input_tokens)} → ${fmtNum(usage.output_tokens)} tokens`,
            title: `Tokens de entrada → salida del agente${reasoning}`,
          })
        );
      }
      for (const tool of message.tools || []) {
        const isSearch = tool.label.startsWith("AI Search");
        meta.push(
          h("span", {
            class: `badge ${isSearch ? "b-search" : "b-web"}`,
            text: `${isSearch ? "🔎" : "🌐"} ${tool.label}${tool.count > 1 ? ` ×${tool.count}` : ""}`,
          })
        );
      }
      if (message.tts) {
        meta.push(h("span", { class: "badge b-voice", text: message.tts.model, title: `Texto → voz (voz «${message.tts.voice}»)` }));
      }
      if (message.latency_ms) {
        meta.push(h("span", { class: "badge b-muted", text: `${(message.latency_ms / 1000).toFixed(1)} s`, title: "Tiempo total del turno" }));
      }
    }
    const body = h("div", {
      class: "bubble-body",
      html: isUser ? escapeHtml(message.text).replace(/\n/g, "<br>") : renderMarkdown(message.text),
    });
    const parts = [body];
    const citations = (message.citations || []).filter((citation) => safeUrl(citation.url));
    if (!isUser && citations.length) {
      parts.push(
        h(
          "div",
          { class: "citations" },
          h("span", { text: "Fuentes:" }),
          citations.map((citation) =>
            citation.source === "kb"
              ? h("span", { class: "kb-source", title: `Documento del índice de AI Search · ${citation.url}`, text: citation.title || hostOf(citation.url) })
              : h("a", { href: citation.url, target: "_blank", rel: "noopener noreferrer", title: citation.url, text: citation.title || hostOf(citation.url) })
          )
        )
      );
    }
    if (message.has_audio) parts.push(listenButton(message));
    parts.push(h("div", { class: "meta" }, meta, h("time", { datetime: message.ts, title: fmtDate(message.ts), text: fmtTime(message.ts) })));
    return h(
      "div",
      { class: `msg ${isUser ? "from-user" : "from-bot"}` },
      isUser ? null : h("span", { class: "avatar bot", text: "IA" }),
      h("div", { class: "bubble" }, parts)
    );
  }

  // ---------- audio (silenciado por defecto, nunca automático) ----------
  function listenButton(message) {
    const button = h("button", {
      type: "button",
      class: "listen-btn",
      "data-url": `/api/audio/${encodeURIComponent(state.active)}/${encodeURIComponent(message.id)}`,
    });
    button.addEventListener("click", () => toggleListen(button));
    paintListenButton(button);
    return button;
  }

  function paintListenButton(button) {
    const playing = Boolean(state.playingUrl) && state.playingUrl === button.dataset.url;
    button.disabled = !state.audioEnabled;
    button.classList.toggle("playing", playing);
    button.textContent = playing ? "⏹ Detener" : "▶ Escuchar";
    button.title = state.audioEnabled
      ? "Reproducir el audio de este mensaje"
      : "Audio silenciado: actívalo arriba a la derecha para poder escucharlo";
  }

  function refreshListenButtons() {
    document.querySelectorAll(".listen-btn").forEach(paintListenButton);
  }

  function toggleListen(button) {
    if (!state.audioEnabled) return;
    const url = button.dataset.url;
    if (state.playingUrl === url) {
      stopAudio();
      return;
    }
    stopAudio();
    const audio = new Audio();
    audio.preload = "none";
    audio.autoplay = false;
    audio.src = url;
    state.player = audio;
    state.playingUrl = url;
    audio.addEventListener("ended", () => state.player === audio && stopAudio());
    audio.addEventListener("error", () => {
      if (state.player !== audio) return;
      stopAudio();
      flash("No se pudo reproducir el audio.");
    });
    audio.play().catch(() => state.player === audio && stopAudio());
    refreshListenButtons();
  }

  function stopAudio() {
    const audio = state.player;
    state.player = null;
    state.playingUrl = null;
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    refreshListenButtons();
  }

  function setAudioEnabled(enabled) {
    state.audioEnabled = enabled;
    const toggle = $("#audio-toggle");
    toggle.setAttribute("aria-pressed", String(enabled));
    toggle.classList.toggle("on", enabled);
    toggle.textContent = enabled ? "🔊 Audio activado" : "🔇 Audio silenciado";
    toggle.title = enabled
      ? "Solo suena al pulsar «Escuchar» en un mensaje. Pulsa para silenciar."
      : "El audio está silenciado: no se reproducirá ningún sonido";
    if (!enabled) stopAudio();
    refreshListenButtons();
  }

  // ---------- envío de mensajes ----------
  function updateComposer() {
    if (!state.config || !state.active) return;
    const uid = state.active;
    const ready = isReady();
    const busy = Boolean(state.busy[uid]);
    const recording = Boolean(state.rec);
    $("#input").disabled = !ready || busy || recording;
    $("#send-btn").disabled = !ready || busy || recording;
    $("#model-select").disabled = busy || recording;
    const mic = $("#mic-btn");
    mic.disabled = !ready || busy;
    mic.classList.toggle("recording", recording);
    mic.textContent = recording ? "⏹" : "🎙️";
    mic.title = recording ? "Detener y enviar (Esc para cancelar)" : "Grabar un mensaje de voz";
    mic.setAttribute("aria-label", mic.title);
    const hint = $("#composer-hint");
    hint.classList.toggle("rec", recording);
    if (!ready) {
      hint.textContent = state.config.foundry.project
        ? "Preparando el entorno (índice de AI Search y agentes de Foundry)…"
        : "Modo local: configura FOUNDRY_PROJECT_ENDPOINT para conversar.";
    } else if (recording) {
      const seconds = Math.floor((Date.now() - state.rec.started) / 1000);
      hint.textContent = `● Grabando ${seconds}s / ${state.config.max_recording_seconds}s · pulsa ⏹ para enviar o Esc para cancelar`;
    } else if (busy) {
      hint.textContent = state.busy[uid].kind === "voz" ? "Transcribiendo, consultando al agente y generando la voz…" : "El agente está respondiendo…";
    } else {
      hint.textContent = "Enter para enviar · Mayús+Enter salto de línea · 🎙️ para hablar · el audio nunca se reproduce solo";
    }
  }

  function flash(text) {
    const hint = $("#composer-hint");
    hint.textContent = text;
    clearTimeout(flash.timer);
    flash.timer = setTimeout(updateComposer, 3500);
  }

  async function runTurn(uid, pending, request) {
    state.busy[uid] = pending;
    state.errors[uid] = null;
    renderTabs();
    if (uid === state.active) {
      renderMessages();
      updateComposer();
    }
    try {
      const result = await request();
      const list = state.messages[uid] || (state.messages[uid] = []);
      list.push(result.user_message, result.assistant_message);
      state.loaded[uid] = true;
      if (state.summary[uid]) state.summary[uid].messages = userCount(uid);
      scheduleDashboard(4000);
    } catch (error) {
      state.errors[uid] = error.message || "Error inesperado.";
      if (pending.kind === "texto" && !state.drafts[uid]) state.drafts[uid] = pending.text;
    } finally {
      delete state.busy[uid];
      renderTabs();
      if (uid === state.active) {
        if (pending.kind === "texto" && state.errors[uid] && !$("#input").value) $("#input").value = state.drafts[uid] || "";
        renderMessages();
        updateComposer();
        $("#input").focus();
      }
    }
  }

  function sendText() {
    const uid = state.active;
    const input = $("#input");
    const text = input.value.trim();
    if (!text || state.busy[uid] || !isReady()) return;
    input.value = "";
    state.drafts[uid] = "";
    autoGrow();
    runTurn(uid, { kind: "texto", text }, () => api("/api/chat", { json: { user_id: uid, text, model: modelFor(uid) } }));
  }

  async function startRecording() {
    if (!isReady() || state.busy[state.active]) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
      flash("Este navegador no permite grabar audio.");
      return;
    }
    const uid = state.active;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch {
      flash("No se ha concedido acceso al micrófono.");
      return;
    }
    const mimeType = MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type)) || "";
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const rec = { uid, recorder, stream, chunks: [], cancelled: false, started: Date.now(), timer: null, limit: null };
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size) rec.chunks.push(event.data);
    });
    recorder.addEventListener("stop", () => {
      stream.getTracks().forEach((track) => track.stop());
      clearInterval(rec.timer);
      clearTimeout(rec.limit);
      if (state.rec === rec) state.rec = null;
      updateComposer();
      if (rec.cancelled) return;
      const type = (recorder.mimeType || mimeType || "audio/webm").split(";")[0];
      const blob = new Blob(rec.chunks, { type });
      if (blob.size < 1500 || Date.now() - rec.started < 700) {
        flash("Grabación demasiado corta.");
        return;
      }
      sendVoice(rec.uid, blob, type);
    });
    recorder.start(250);
    state.rec = rec;
    rec.timer = setInterval(updateComposer, 500);
    rec.limit = setTimeout(() => stopRecording(false), (state.config.max_recording_seconds || 60) * 1000);
    updateComposer();
  }

  function stopRecording(cancel) {
    const rec = state.rec;
    if (!rec) return;
    rec.cancelled = Boolean(cancel);
    if (rec.recorder.state !== "inactive") rec.recorder.stop();
    else {
      rec.stream.getTracks().forEach((track) => track.stop());
      state.rec = null;
      updateComposer();
    }
  }

  function sendVoice(uid, blob, type) {
    const extension = type.includes("mp4") ? "mp4" : type.includes("ogg") ? "ogg" : "webm";
    const form = new FormData();
    form.append("user_id", uid);
    form.append("model", modelFor(uid));
    form.append("audio", blob, `voz.${extension}`);
    runTurn(uid, { kind: "voz" }, () => api("/api/voice", { form }));
  }

  async function resetActive() {
    const user = currentUser();
    if (state.busy[user.id]) return;
    if (!window.confirm(`¿Borrar la conversación de ${user.name}? Su fila del tablero volverá a cero; la telemetría ya registrada se conserva en Application Insights.`)) return;
    try {
      await api(`/api/users/${encodeURIComponent(user.id)}/reset`, { method: "POST" });
      state.messages[user.id] = [];
      state.loaded[user.id] = true;
      state.errors[user.id] = null;
      if (state.summary[user.id]) state.summary[user.id].messages = 0;
      if (state.player) stopAudio();
      scheduleDashboard(0);
    } catch (error) {
      state.errors[user.id] = `No se pudo reiniciar: ${error.message}`;
    }
    renderTabs();
    renderMessages();
  }

  async function refreshSummary() {
    try {
      const data = await api("/api/users/summary");
      for (const entry of data.users) {
        state.summary[entry.id] = entry;
        if (!state.loaded[entry.id] || state.busy[entry.id]) continue;
        const local = state.messages[entry.id] || [];
        const lastLocal = local.length ? local[local.length - 1].ts : null;
        if (userCount(entry.id) !== entry.messages || lastLocal !== entry.last_ts) {
          if (entry.id === state.active) loadMessages(entry.id);
          else state.loaded[entry.id] = false;
        }
      }
      renderTabs();
    } catch {
      /* el siguiente ciclo lo reintenta */
    }
  }

  // ---------- cuadro de telemetría ----------
  function scheduleDashboard(delay) {
    clearTimeout(state.dashTimer);
    state.dashTimer = setTimeout(loadDashboard, delay);
  }

  async function loadDashboard() {
    if (state.dashLoading || !state.config) return;
    state.dashLoading = true;
    $("#refresh-btn").classList.add("spinning");
    try {
      const data = await api(`/api/dashboard?range=${encodeURIComponent(state.range)}`);
      state.dash = data;
      renderDashboard(data);
    } catch (error) {
      showDashStatus("error", `No se pudo cargar la telemetría: ${error.message}`);
    } finally {
      state.dashLoading = false;
      $("#refresh-btn").classList.remove("spinning");
    }
  }

  function showDashStatus(kind, text) {
    const box = $("#dash-status");
    box.className = `dash-status ${kind}`;
    box.textContent = text;
    box.hidden = false;
  }

  function kpi(label, value, sub, title) {
    return h(
      "div",
      { class: "kpi", title },
      h("span", { class: "kpi-label", text: label }),
      h("strong", { class: "kpi-value", text: value }),
      h("span", { class: "kpi-sub", text: sub })
    );
  }

  function modelRole(model) {
    const match = (model.label || "").match(/\(([^)]+)\)/);
    return match ? match[1] : model.type;
  }

  function modelChip(model) {
    return h(
      "span",
      {
        class: `chip ${model.type === "voz" ? "chip-voice" : "chip-text"}`,
        title: `${model.label} · modelo de ${model.type}: ${fmtNum(model.calls)} llamadas · ${fmtNum(model.tokens_in)} / ${fmtNum(model.tokens_out)} tokens`,
      },
      h("span", { text: modelRole(model) }),
      h("b", { text: fmtNum(model.calls) })
    );
  }

  function renderDashboard(data) {
    const totals = data.totals;
    if (data.status === "ok") {
      if (data.pending_total > 0) {
        showDashStatus("info", `⏳ ${data.pending_total} mensaje(s) todavía en ingesta en Application Insights (suele tardar 1–3 min).`);
      } else {
        $("#dash-status").hidden = true;
      }
    } else if (data.status === "not_configured") {
      showDashStatus("warn", "Telemetría no configurada en este entorno (ejecución local sin Azure).");
    } else {
      showDashStatus(data.status === "forbidden" ? "warn" : "error", data.error || "Error consultando la telemetría.");
    }
    $("#dash-subtitle").textContent =
      `Application Insights conectado al proyecto · ${RANGE_LABELS[data.range] || data.range} · ` +
      `${totals.active_users}/${data.users.length} usuarios activos`;

    const voicePct = totals.messages ? Math.round((100 * totals.messages_voice) / totals.messages) : 0;
    const usedModels = new Set(data.users.flatMap((user) => user.models.map((model) => model.model))).size;
    $("#kpis").replaceChildren(
      kpi("Mensajes", fmtNum(totals.messages), `${fmtNum(totals.messages_text)} texto · ${fmtNum(totals.messages_voice)} voz`),
      kpi("Por voz", `${voicePct} %`, "de los mensajes"),
      kpi("Llamadas a modelos", fmtNum(totals.model_calls), `${usedModels} modelos distintos`),
      kpi("Tokens", compact(totals.tokens), `${compact(totals.tokens_in)} entrada · ${compact(totals.tokens_out)} salida`, `${fmtNum(totals.tokens)} tokens`),
      kpi("AI Search", fmtNum(totals.ai_search), "consultas al índice"),
      kpi("Bing", fmtNum(totals.bing), "búsquedas web")
    );

    const rows = data.users.map((user) =>
      h(
        "tr",
        {
          "data-user": user.id,
          class: user.id === state.active ? "active" : null,
          style: { "--c": user.color },
          title: `Ver la conversación de ${user.name}`,
          onclick: () => selectUser(user.id),
        },
        h(
          "td",
          null,
          h("span", { class: "who" }, h("span", { class: "avatar sm", style: { "--c": user.color }, text: user.initials }), user.name),
          h("div", {
            class: "seen",
            title: [fmtDate(user.last_seen), user.reset_at ? `Contadores desde el reinicio del ${fmtDate(user.reset_at)}` : ""].filter(Boolean).join(" · "),
            "data-rel": user.last_seen || "",
            text: user.last_seen ? relTime(user.last_seen) : user.reset_at ? "reiniciado, sin actividad" : "sin actividad",
          })
        ),
        h(
          "td",
          { class: "num" },
          h("strong", { text: fmtNum(user.messages) }),
          h("div", { class: "split" }, h("span", { title: "Mensajes de texto", text: `💬 ${user.messages_text}` }), h("span", { title: "Mensajes de voz", text: `🎙️ ${user.messages_voice}` })),
          user.pending ? h("div", { class: "pending", title: "Mensajes guardados que aún no aparecen en la telemetría", text: `+${user.pending} en ingesta` }) : null,
          user.errors ? h("div", { class: "pending", text: `${user.errors} con error` }) : null
        ),
        h("td", null, h("div", { class: "chips" }, user.models.length ? user.models.map(modelChip) : h("span", { class: "muted", text: "—" }))),
        h(
          "td",
          { class: "num", title: `${fmtNum(user.tokens_in)} entrada / ${fmtNum(user.tokens_out)} salida` },
          h("strong", { text: compact(user.tokens_in + user.tokens_out) }),
          h("div", { class: "split", text: `${compact(user.tokens_in)} / ${compact(user.tokens_out)}` })
        ),
        h("td", { class: "num", text: fmtNum(user.ai_search) }),
        h("td", { class: "num", text: fmtNum(user.bing) })
      )
    );
    const table = $("#user-table");
    table.tBodies[0].replaceChildren(...rows);
    if (table.tFoot) table.tFoot.remove();
    const foot = table.createTFoot();
    foot.append(
      h(
        "tr",
        null,
        h("td", { text: "Total" }),
        h("td", { class: "num", text: fmtNum(totals.messages) }),
        h("td", { text: `${fmtNum(totals.model_calls)} llamadas` }),
        h("td", { class: "num", text: compact(totals.tokens) }),
        h("td", { class: "num", text: fmtNum(totals.ai_search) }),
        h("td", { class: "num", text: fmtNum(totals.bing) })
      )
    );

    renderPivot(data);
    const source = data.source || {};
    $("#kql").textContent = data.kql || "";
    $("#source").replaceChildren(
      h(
        "span",
        null,
        "Fuente: ",
        h("strong", { text: source.resource || "Application Insights" }),
        " conectado al proyecto de Foundry ",
        h("strong", { text: source.project || "—" }),
        ` · servicio «${source.service || ""}»`,
        source.schema ? ` · tablas ${source.schema === "classic" ? "dependencies" : "AppDependencies"}` : "",
        ` · actualizado a las ${fmtTime(data.generated_at)}`,
        safeUrl(source.portal_url)
          ? [" · ", h("a", { href: source.portal_url, target: "_blank", rel: "noopener noreferrer", text: "Abrir en Azure Portal" })]
          : null
      )
    );
  }

  function renderPivot(data) {
    const models = data.models;
    const callsOf = (user, model) => user.models.find((entry) => entry.model === model.model);
    const max = Math.max(1, ...data.users.flatMap((user) => user.models.map((entry) => entry.calls)));
    const sublabel = modelRole;
    const head = h(
      "thead",
      null,
      h(
        "tr",
        null,
        h("th", { text: "Usuario" }),
        models.map((model) =>
          h(
            "th",
            { class: "num", title: model.label },
            h("div", { class: "model-name", text: model.model }),
            h("span", { class: `type ${model.type === "voz" ? "t-voice" : "t-text"}`, text: `${model.type} · ${sublabel(model)}` })
          )
        )
      )
    );
    const body = h(
      "tbody",
      null,
      data.users.map((user) =>
        h(
          "tr",
          { "data-user": user.id, class: user.id === state.active ? "active" : null, style: { "--c": user.color }, onclick: () => selectUser(user.id) },
          h("td", null, h("span", { class: "who" }, h("span", { class: "avatar sm", style: { "--c": user.color }, text: user.initials }), user.name.split(" ")[0])),
          models.map((model) => {
            const entry = callsOf(user, model);
            if (!entry) return h("td", { class: "num zero", text: "0" });
            return h(
              "td",
              { class: "num heat", style: { "--heat": (entry.calls / max).toFixed(2) }, title: `${fmtNum(entry.tokens_in)} / ${fmtNum(entry.tokens_out)} tokens` },
              h("strong", { text: fmtNum(entry.calls) }),
              h("small", { text: `${compact(entry.tokens_in + entry.tokens_out)} tok` })
            );
          })
        )
      )
    );
    $("#pivot-table").replaceChildren(head, body);
  }

  function highlightDashboardRow() {
    document.querySelectorAll("tr[data-user]").forEach((row) => row.classList.toggle("active", row.dataset.user === state.active));
  }

  function refreshRelativeTimes() {
    document.querySelectorAll("[data-rel]").forEach((cell) => {
      if (!cell.dataset.rel) return;
      cell.textContent = relTime(cell.dataset.rel || null);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    setAudioEnabled(false);
    loadConfig();
  });
})();
