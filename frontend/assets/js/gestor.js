(function managerDashboard() {
  if (!window.CAIS.requireToken()) return;
  const state = { events: [], reports: [], guards: [], selectedEvent: null, selectedReport: null, stream: null, refreshTimer: null };
  const $ = selector => document.querySelector(selector);
  const byId = id => document.getElementById(id);
  const criticalityRank = { high: 3, medium: 2, low: 1 };

  function debounce(callback, wait = 300) {
    let timer;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => callback(...args), wait); };
  }

  async function ensureManager() {
    try {
      const data = await window.api.get("/api/v1/auth/me");
      if (data.user.role !== "manager") { window.location.href = "guarda.html"; return false; }
      byId("user-name").textContent = data.user.name;
      byId("user-avatar").textContent = data.user.name.split(/\s+/).map(part => part[0]).slice(0, 2).join("").toUpperCase();
      return true;
    } catch (error) {
      window.CAIS.toast(error.message, "error");
      return false;
    }
  }

  async function loadSummary() {
    const data = await window.api.get("/api/v1/dashboard/summary");
    byId("metric-total").textContent = data.total_events;
    byId("metric-pending").textContent = data.pending_approval;
    byId("metric-distributed").textContent = data.distributed;
    byId("metric-ack").textContent = window.CAIS.percent(data.acknowledgement_rate);
  }

  function eventQuery() {
    const params = new URLSearchParams({ limit: "100" });
    const mappings = [
      ["filter-query", "q"], ["filter-status", "status"],
      ["filter-criticality", "criticality"], ["filter-local", "local"]
    ];
    mappings.forEach(([elementId, key]) => {
      const value = byId(elementId).value.trim();
      if (value) params.set(key, value);
    });
    return params.toString();
  }

  async function loadEvents() {
    const data = await window.api.get(`/api/v1/events?${eventQuery()}`);
    state.events = data.items;
    renderEvents(data.items, data.total);
    renderRail(data.items);
    renderInsights(data.items);
    renderMap(data.items);
  }

  async function loadReports() {
    const data = await window.api.get("/api/v1/reports?limit=20");
    state.reports = data.items;
    const schedule = data.schedule || {};
    const cadence = schedule.interval_seconds >= 86400
      ? `${Math.round(schedule.interval_seconds / 86400)} dia(s)`
      : `${Math.round(schedule.interval_seconds / 3600)} hora(s)`;
    byId("reports-schedule").textContent = schedule.enabled
      ? `Geração automática a cada ${cadence} · janela de ${schedule.lookback_days} dia(s)`
      : "Agenda automática desativada · geração manual disponível";
    renderReports(data.items);
  }

  function renderReports(reports) {
    if (!reports.length) {
      byId("reports-list").innerHTML = '<div class="empty-state"><div><strong>Nenhum relatório gerado</strong>Gere o primeiro consolidado quando houver dados disponíveis.</div></div>';
      return;
    }
    byId("reports-list").innerHTML = reports.map(report => {
      const stats = report.statistics || {};
      const options = report.solution_options || [];
      return `
        <button class="report-card" data-report-id="${window.CAIS.escape(report.id)}" type="button">
          <div class="report-card-top"><span class="badge">${window.CAIS.escape(report.generation_reason)}</span><time>${window.CAIS.date(report.created_at)}</time></div>
          <strong>${window.CAIS.escape(report.title)}</strong>
          <p>${window.CAIS.escape(report.executive_summary)}</p>
          <div class="report-card-meta"><span>${stats.total_events || 0} evento(s)</span><span>${options.length} alternativas</span><span>${report.provider === "gemini" ? "Gemini" : "Redação local"}</span></div>
        </button>`;
    }).join("");
    byId("reports-list").querySelectorAll("[data-report-id]").forEach(button => {
      button.addEventListener("click", () => openReport(button.dataset.reportId));
    });
  }

  async function openReport(reportId) {
    byId("report-modal").classList.remove("hidden");
    byId("report-modal-body").innerHTML = '<div class="empty-state">Carregando relatório…</div>';
    try {
      const report = await window.api.get(`/api/v1/reports/${reportId}`);
      state.selectedReport = report;
      byId("report-modal-title").textContent = report.title;
      const stats = report.statistics || {};
      const dispatches = stats.dispatches || {};
      const findings = report.findings || [];
      const options = report.solution_options || [];
      const draft = report.procurement_draft || {};
      byId("report-modal-body").innerHTML = `
        <div class="report-summary">${window.CAIS.escape(report.executive_summary)}</div>
        <div class="report-stats">
          <div class="report-stat"><span>Eventos</span><strong>${stats.total_events || 0}</strong></div>
          <div class="report-stat"><span>Criticidade alta</span><strong>${stats.by_criticality?.high || 0}</strong></div>
          <div class="report-stat"><span>Score médio</span><strong>${window.CAIS.percent(stats.average_probability || 0)}</strong></div>
          <div class="report-stat"><span>Recebimentos</span><strong>${window.CAIS.percent(dispatches.acknowledgement_rate || 0)}</strong></div>
        </div>
        <p class="hint">Período: ${window.CAIS.date(report.period_start, false)} a ${window.CAIS.date(report.period_end, false)} · ${window.CAIS.escape(stats.data_notice || "")}</p>
        <section class="report-block"><h3>Achados sustentados pelos dados</h3><div class="finding-list">${findings.map(item => `<article class="finding"><strong>${window.CAIS.escape(item.title)}</strong><p>${window.CAIS.escape(item.interpretation)}</p></article>`).join("")}</div></section>
        <section class="report-block"><h3>Alternativas para decisão do gestor</h3><div class="solution-list">${options.map(option => `<article class="solution-option"><strong>${window.CAIS.escape(option.title)}</strong><p>${window.CAIS.escape(option.description)}</p><div class="solution-meta"><span class="badge">${window.CAIS.escape(option.cost_band)}</span><span class="badge">${window.CAIS.escape(option.timeframe)}</span></div><p><b>Contratação:</b> ${window.CAIS.escape(option.procurement_posture)}</p><p><b>Métricas:</b> ${(option.success_metrics || []).map(window.CAIS.escape).join(" · ")}</p></article>`).join("")}</div></section>
        <section class="report-block"><h3>Minuta técnica preparatória</h3><div class="draft-warning"><strong>Revisão obrigatória</strong><br>${window.CAIS.escape(draft.warning || "Documento preliminar, não publicável.")}</div><ul class="checklist">${(draft.mandatory_review_gates || []).map(item => `<li>${window.CAIS.escape(item)}</li>`).join("")}</ul></section>`;
      byId("report-modal-actions").innerHTML = '<button class="btn btn-secondary" data-close="report-modal" type="button">Fechar</button><button id="download-report" class="btn btn-orange" type="button">Baixar minuta .md</button>';
      bindModalClosers(byId("report-modal-actions"));
      byId("download-report").addEventListener("click", downloadSelectedDraft);
    } catch (error) {
      byId("report-modal-body").innerHTML = `<div class="empty-state">${window.CAIS.escape(error.message)}</div>`;
      byId("report-modal-actions").innerHTML = '<button class="btn btn-secondary" data-close="report-modal" type="button">Fechar</button>';
      bindModalClosers(byId("report-modal-actions"));
    }
  }

  async function generateReport() {
    const button = byId("generate-report");
    button.disabled = true; button.textContent = "Gerando…";
    try {
      const response = await window.api.post("/api/v1/reports/generate", { lookback_days: 7 });
      window.CAIS.toast(response.message, "success");
      setTimeout(refreshAll, 900);
    } catch (error) { window.CAIS.toast(error.message, "error"); }
    finally { button.disabled = false; button.textContent = "＋ Gerar agora"; }
  }

  async function downloadSelectedDraft() {
    if (!state.selectedReport) return;
    try {
      const markdown = await window.api.get(`/api/v1/reports/${state.selectedReport.id}/draft.md`);
      const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url; link.download = `cais-minuta-${state.selectedReport.id}.md`;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    } catch (error) { window.CAIS.toast(error.message, "error"); }
  }

  function renderEvents(events, total) {
    byId("events-count").textContent = `${total} evento${total === 1 ? "" : "s"} encontrado${total === 1 ? "" : "s"}`;
    if (!events.length) {
      byId("events-body").innerHTML = '<tr><td colspan="5"><div class="empty-state"><div><strong>Nenhum evento encontrado</strong>Ajuste os filtros ou envie um novo sinal.</div></div></td></tr>';
      return;
    }
    byId("events-body").innerHTML = events.map(event => `
      <tr data-event-id="${window.CAIS.escape(event.id)}">
        <td><div class="cell-title">${window.CAIS.escape(event.title)}</div><div class="cell-sub">${window.CAIS.escape(event.local)} · ${window.CAIS.escape(event.source)}</div></td>
        <td><span class="badge badge-${event.criticality}">${window.CAIS.label(event.criticality)}</span></td>
        <td><span class="score">${window.CAIS.percent(event.probability)}</span></td>
        <td><span class="badge badge-${event.status}">${window.CAIS.label(event.status)}</span></td>
        <td>${window.CAIS.date(event.created_at)}</td>
      </tr>`).join("");
    byId("events-body").querySelectorAll("tr[data-event-id]").forEach(row => {
      row.addEventListener("click", () => openEvent(row.dataset.eventId));
    });
  }

  function renderRail(events) {
    const relevant = [...events].sort((a, b) => {
      const pendingDifference = Number(b.status === "pending_approval") - Number(a.status === "pending_approval");
      return pendingDifference || criticalityRank[b.criticality] - criticalityRank[a.criticality] || new Date(b.created_at) - new Date(a.created_at);
    }).slice(0, 14);
    byId("rail-list").innerHTML = relevant.length ? relevant.map(event => `
      <button class="rail-event ${event.criticality}" data-event-id="${window.CAIS.escape(event.id)}" type="button">
        <div class="rail-meta"><span>${window.CAIS.label(event.status)}</span><time>${window.CAIS.date(event.created_at)}</time></div>
        <strong>${window.CAIS.escape(event.title)}</strong>
        <p>${window.CAIS.escape(event.summary)}</p>
      </button>`).join("") : '<div class="empty-state">Aguardando sinais…</div>';
    byId("rail-list").querySelectorAll("[data-event-id]").forEach(button => button.addEventListener("click", () => openEvent(button.dataset.eventId)));
  }

  function renderInsights(events) {
    const items = [...events].sort((a, b) => b.probability - a.probability).slice(0, 5);
    byId("insight-list").innerHTML = items.length ? items.map(event => `
      <div class="insight-row">
        <strong>${window.CAIS.escape(event.local)}</strong>
        <p>${window.CAIS.escape(event.summary)}</p>
        <div class="insight-stat"><div class="risk-track"><div class="risk-fill" style="width:${Math.round(event.probability * 100)}%"></div></div><span class="score">${window.CAIS.percent(event.probability)}</span></div>
      </div>`).join("") : '<div class="empty-state">Sem análises para os filtros atuais.</div>';
  }

  function positionFor(text, index) {
    let hash = 0;
    for (const character of text) hash = ((hash << 5) - hash) + character.charCodeAt(0);
    return { left: 12 + Math.abs(hash + index * 31) % 74, top: 14 + Math.abs(hash * 3 + index * 17) % 70 };
  }

  function renderMap(events) {
    byId("map-stage").querySelectorAll(".map-point").forEach(point => point.remove());
    events.slice(0, 12).forEach((event, index) => {
      const point = document.createElement("button");
      const position = positionFor(event.local, index);
      point.className = `map-point ${event.criticality}`;
      point.style.left = `${position.left}%`;
      point.style.top = `${position.top}%`;
      point.type = "button";
      point.title = `${event.local} · ${window.CAIS.percent(event.probability)}`;
      point.setAttribute("aria-label", point.title);
      point.addEventListener("click", () => openEvent(event.id));
      byId("map-stage").appendChild(point);
    });
  }

  async function loadGuards() {
    const data = await window.api.get("/api/v1/guards?availability=available");
    state.guards = data.items;
  }

  async function openEvent(eventId) {
    try {
      const event = await window.api.get(`/api/v1/events/${eventId}`);
      state.selectedEvent = event;
      byId("event-modal-title").textContent = event.title;
      byId("event-modal-badge").className = `badge badge-${event.criticality}`;
      byId("event-modal-badge").textContent = `${window.CAIS.label(event.criticality)} · ${window.CAIS.label(event.status)}`;
      const indicators = event.indicators || {};
      const dispatches = event.dispatches || [];
      byId("event-modal-body").innerHTML = `
        <div class="detail-score">
          <div class="score-ring" style="--score:${Math.round(event.probability * 100)}"><span>${window.CAIS.percent(event.probability)}</span></div>
          <div class="detail-copy"><strong>${window.CAIS.escape(event.local)}</strong><p>${window.CAIS.escape(event.conversational_summary)}</p><span class="hint">${window.CAIS.escape(event.source)} · ${window.CAIS.date(event.created_at)} · ${window.CAIS.escape(event.model_version)}</span></div>
        </div>
        <div class="indicator-grid">
          <div class="indicator"><span>Densidade</span><strong>${window.CAIS.escape(indicators.density_index ?? "—")}/100</strong></div>
          <div class="indicator"><span>Histórico 7d</span><strong>${window.CAIS.escape(indicators.recent_occurrences ?? "—")}</strong></div>
          <div class="indicator"><span>Iluminação</span><strong>${window.CAIS.escape(indicators.active_lighting_pct ?? "—")}%</strong></div>
          <div class="indicator"><span>Evento próximo</span><strong>${indicators.nearby_event ? "Sim" : "Não"}</strong></div>
        </div>
        <div class="recommendation"><strong>Recomendação do modelo local</strong><p>${window.CAIS.escape(event.recommended_action)}</p></div>
        ${event.status === "pending_approval" ? `<div style="margin-top:20px"><strong style="font-size:13px;color:var(--navy)">Destinatários do alerta</strong><p class="hint">O envio ocorre simultaneamente após a aprovação. A confirmação de um guarda não bloqueia os demais.</p><div class="guard-options">${state.guards.map(guard => `<label class="guard-check"><input type="checkbox" name="guard" value="${window.CAIS.escape(guard.id)}" checked><span><strong>${window.CAIS.escape(guard.code)} · ${window.CAIS.escape(guard.name)}</strong><span>${window.CAIS.escape(guard.specialization)}</span></span></label>`).join("")}</div></div>` : ""}
        ${dispatches.length ? `<div style="margin-top:20px"><strong style="font-size:13px;color:var(--navy)">Distribuição</strong><div class="guard-options">${dispatches.map(dispatch => `<div class="guard-check"><span class="status-dot ${dispatch.status === "sent" ? "warning" : "online"}"></span><span><strong>${window.CAIS.escape(dispatch.guard_code)} · ${window.CAIS.escape(dispatch.guard_name)}</strong><span>${window.CAIS.label(dispatch.status)} · ${window.CAIS.date(dispatch.sent_at)}</span></span></div>`).join("")}</div></div>` : ""}`;
      renderEventActions(event);
      byId("event-modal").classList.remove("hidden");
    } catch (error) { window.CAIS.toast(error.message, "error"); }
  }

  function renderEventActions(event) {
    const actions = byId("event-modal-actions");
    if (event.status !== "pending_approval") {
      actions.innerHTML = `<button class="btn btn-secondary" data-close="event-modal" type="button">Fechar</button>`;
      bindModalClosers(actions);
      return;
    }
    actions.innerHTML = `<button id="reject-event" class="btn btn-danger" type="button">Rejeitar</button><button id="approve-event" class="btn btn-orange" type="button">Aprovar e distribuir</button>`;
    byId("approve-event").addEventListener("click", approveSelectedEvent);
    byId("reject-event").addEventListener("click", rejectSelectedEvent);
  }

  async function approveSelectedEvent() {
    const guardIds = [...document.querySelectorAll('input[name="guard"]:checked')].map(input => input.value);
    if (!guardIds.length) { window.CAIS.toast("Selecione ao menos um destinatário.", "error"); return; }
    const button = byId("approve-event");
    button.disabled = true; button.textContent = "Distribuindo…";
    try {
      await window.api.post(`/api/v1/events/${state.selectedEvent.id}/approve`, { guard_ids: guardIds, note: "Aprovado no painel do gestor" });
      closeModal("event-modal");
      window.CAIS.toast(`Alerta aprovado e enviado a ${guardIds.length} destinatário(s).`, "success");
      await refreshAll();
    } catch (error) { window.CAIS.toast(error.message, "error"); button.disabled = false; button.textContent = "Aprovar e distribuir"; }
  }

  async function rejectSelectedEvent() {
    const reason = window.prompt("Informe o motivo da rejeição (mínimo de 5 caracteres):");
    if (!reason || reason.trim().length < 5) return;
    try {
      await window.api.post(`/api/v1/events/${state.selectedEvent.id}/reject`, { reason: reason.trim() });
      closeModal("event-modal");
      window.CAIS.toast("Evento rejeitado e registrado na auditoria.", "success");
      await refreshAll();
    } catch (error) { window.CAIS.toast(error.message, "error"); }
  }

  async function refreshAll() {
    try {
      await Promise.all([loadSummary(), loadEvents(), loadReports()]);
      byId("connection-dot").className = "status-dot online";
      byId("connection-label").textContent = "API conectada";
    } catch (error) {
      byId("connection-dot").className = "status-dot warning";
      byId("connection-label").textContent = "Reconectando";
      window.CAIS.toast(error.message, "error");
    }
  }

  async function simulateSignal(event) {
    event.preventDefault();
    const submit = byId("simulate-submit");
    submit.disabled = true; submit.textContent = "Enviando…";
    const now = new Date();
    const payload = {
      source: "Simulador do painel", local: byId("signal-local").value.trim(), category: "ordem_publica",
      description: byId("signal-description").value.trim(), dia_semana: (now.getDay() + 6) % 7,
      hora_dia: now.getHours(), eventos_proximos: Number(byId("signal-event").value),
      historico_ocorrencias_7d: Number(byId("signal-history").value),
      iluminacao_ativa_pct: Number(byId("signal-lighting").value), iluminacao_fonte: "simulada",
      densidade_pessoas: Number(byId("signal-density").value), latitude: -8.0611, longitude: -34.8711
    };
    try {
      const response = await window.api.post("/api/v1/signals", payload);
      closeModal("simulate-modal");
      window.CAIS.toast(`${response.message}. O insight aparecerá em instantes.`, "success");
      setTimeout(refreshAll, 900);
    } catch (error) { window.CAIS.toast(error.message, "error"); }
    finally { submit.disabled = false; submit.textContent = "Enviar sinal"; }
  }

  async function showMetrics() {
    byId("metrics-modal").classList.remove("hidden");
    try {
      const data = await window.api.get("/api/v1/model/metrics");
      const metrics = data.metrics || {};
      byId("metrics-modal-body").innerHTML = data.available ? `
        <div class="indicator-grid"><div class="indicator"><span>Acurácia</span><strong>${window.CAIS.percent(metrics.accuracy, 1)}</strong></div><div class="indicator"><span>Precisão +</span><strong>${window.CAIS.percent(metrics.precision_positive, 1)}</strong></div><div class="indicator"><span>Recall +</span><strong>${window.CAIS.percent(metrics.recall_positive, 1)}</strong></div><div class="indicator"><span>F1 +</span><strong>${window.CAIS.percent(metrics.f1_positive, 1)}</strong></div></div>
        <div class="recommendation"><strong>Leitura responsável</strong><p>${window.CAIS.escape(data.dataset?.warning || "Métricas de validação do protótipo.")}</p></div>
        <p class="hint">ROC-AUC ${metrics.roc_auc} · PR-AUC ${metrics.pr_auc} · ${data.dataset?.rows || "—"} registros · base ${data.dataset?.nature || "—"}.</p>` : `<div class="empty-state">${window.CAIS.escape(data.message)}</div>`;
    } catch (error) { byId("metrics-modal-body").innerHTML = `<div class="empty-state">${window.CAIS.escape(error.message)}</div>`; }
  }

  function openChat() { byId("chat-drawer").classList.remove("hidden"); byId("chat-input").focus(); }
  async function askAgent(event) {
    event.preventDefault();
    const input = byId("chat-input");
    const question = input.value.trim();
    if (!question) return;
    appendMessage(question, "user"); input.value = ""; input.disabled = true;
    const loading = appendMessage("Consultando eventos e evidências…", "agent", "loading");
    try {
      const response = await window.api.post("/api/v1/assistant/chat", { question, top_k: 5 });
      loading.remove();
      appendMessage(response.answer, "agent", `${response.provider === "gemini" ? "Gemini" : "Fallback local"} · ${response.sources.length} fonte(s) · ${response.retrieval.elapsed_ms} ms`);
    } catch (error) { loading.remove(); appendMessage(error.message, "agent", "Falha na consulta"); }
    finally { input.disabled = false; input.focus(); }
  }

  function appendMessage(text, type, meta) {
    const message = document.createElement("div");
    message.className = `message ${type}`;
    message.textContent = text;
    if (meta && meta !== "loading") { const small = document.createElement("small"); small.textContent = meta; message.appendChild(small); }
    byId("chat-log").appendChild(message); byId("chat-log").scrollTop = byId("chat-log").scrollHeight;
    return message;
  }

  function closeModal(id) { byId(id).classList.add("hidden"); }
  function bindModalClosers(root = document) {
    root.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => closeModal(button.dataset.close)));
  }

  function connectStream() {
    if (state.stream) state.stream.close();
    const stream = new EventSource(window.api.streamUrl()); state.stream = stream;
    stream.onopen = () => { byId("connection-dot").className = "status-dot online"; byId("connection-label").textContent = "Tempo real ativo"; };
    stream.onerror = () => { byId("connection-dot").className = "status-dot warning"; byId("connection-label").textContent = "Reconectando"; };
    ["insight.created", "event.approved", "event.rejected", "dispatch.sent", "dispatch.acknowledged", "dispatch.completed", "event.completed", "report.generated"].forEach(type => {
      stream.addEventListener(type, () => debounce(refreshAll, 250)());
    });
  }

  async function initialize() {
    if (!(await ensureManager())) return;
    await loadGuards(); await refreshAll(); connectStream();
    state.refreshTimer = setInterval(refreshAll, 12000);
  }

  byId("logout").addEventListener("click", () => window.api.logout());
  byId("refresh").addEventListener("click", refreshAll);
  byId("simulate-open").addEventListener("click", () => byId("simulate-modal").classList.remove("hidden"));
  byId("simulate-form").addEventListener("submit", simulateSignal);
  byId("chat-open").addEventListener("click", openChat);
  byId("nav-agent").addEventListener("click", openChat);
  byId("chat-close").addEventListener("click", () => byId("chat-drawer").classList.add("hidden"));
  byId("chat-form").addEventListener("submit", askAgent);
  byId("nav-metrics").addEventListener("click", showMetrics);
  byId("nav-reports").addEventListener("click", () => byId("reports-section").scrollIntoView({ behavior: "smooth" }));
  byId("generate-report").addEventListener("click", generateReport);
  byId("rail-view-all").addEventListener("click", () => byId("events-section").scrollIntoView({ behavior: "smooth" }));
  document.querySelectorAll('.nav-link[data-section="events"]').forEach(button => button.addEventListener("click", () => byId("events-section").scrollIntoView({ behavior: "smooth" })));
  document.querySelectorAll('.nav-link[data-section="overview"]').forEach(button => button.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" })));
  ["filter-query", "filter-local"].forEach(id => byId(id).addEventListener("input", debounce(loadEvents)));
  ["filter-status", "filter-criticality"].forEach(id => byId(id).addEventListener("change", loadEvents));
  bindModalClosers();
  document.querySelectorAll(".modal-backdrop").forEach(backdrop => backdrop.addEventListener("click", event => { if (event.target === backdrop) closeModal(backdrop.id); }));
  window.addEventListener("beforeunload", () => { if (state.stream) state.stream.close(); clearInterval(state.refreshTimer); });
  initialize();
})();
