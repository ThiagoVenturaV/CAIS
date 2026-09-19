(function guardApp() {
  if (!window.CAIS.requireToken()) return;
  const state = { dispatches: [], guard: null, showHistory: false, stream: null };
  const byId = id => document.getElementById(id);

  async function ensureGuard() {
    try {
      const data = await window.api.get("/api/v1/auth/me");
      if (data.user.role !== "guard") { window.location.href = "gestor.html"; return false; }
      state.guard = data.guard;
      byId("guard-first-name").textContent = data.user.name.split(" ")[0];
      byId("guard-code").textContent = data.guard?.code || "GCM";
      byId("guard-specialization").textContent = data.guard?.specialization || "Operação";
      return true;
    } catch (error) { window.CAIS.toast(error.message, "error"); return false; }
  }

  async function loadDispatches() {
    try {
      const data = await window.api.get("/api/v1/guards/me/dispatches");
      state.dispatches = data.items;
      renderDispatches();
    } catch (error) { window.CAIS.toast(error.message, "error"); }
  }

  function renderDispatches() {
    const filtered = state.dispatches.filter(item => state.showHistory ? item.status !== "sent" : item.status !== "completed");
    if (!filtered.length) {
      byId("dispatch-list").innerHTML = `<div class="empty-state"><div><strong>${state.showHistory ? "Nenhuma confirmação ainda" : "Nenhum alerta ativo"}</strong>${state.showHistory ? "Os alertas confirmados aparecerão aqui." : "Você será avisado quando um alerta for distribuído."}</div></div>`;
      return;
    }
    byId("dispatch-list").innerHTML = filtered.map(dispatch => {
      const button = dispatch.status === "sent"
        ? `<button class="btn btn-orange single" data-action="acknowledge" data-id="${window.CAIS.escape(dispatch.id)}" type="button">✓ Confirmar recebimento</button>`
        : dispatch.status === "acknowledged"
          ? `<button class="btn btn-primary single" data-action="complete" data-id="${window.CAIS.escape(dispatch.id)}" type="button">Marcar atendimento como concluído</button>`
          : `<button class="btn btn-secondary single" type="button" disabled>Atendimento concluído</button>`;
      return `<article class="dispatch-card ${dispatch.criticality}">
        <div class="dispatch-accent"></div><div class="dispatch-body">
          <div class="dispatch-top"><span class="badge badge-${dispatch.criticality}">${window.CAIS.label(dispatch.criticality)}</span><time class="dispatch-time">${window.CAIS.date(dispatch.sent_at)}</time></div>
          <h3>${window.CAIS.escape(dispatch.title)}</h3><span class="location">⌖ ${window.CAIS.escape(dispatch.local)}</span>
          <p>${window.CAIS.escape(dispatch.conversational_summary || dispatch.summary)}</p>
          <div class="dispatch-recommendation"><strong>Orientação:</strong> ${window.CAIS.escape(dispatch.recommended_action)}</div>
          <div class="dispatch-actions">${button}</div>
        </div></article>`;
    }).join("");
    byId("dispatch-list").querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", () => respond(button)));
  }

  async function respond(button) {
    const action = button.dataset.action;
    button.disabled = true;
    button.textContent = action === "acknowledge" ? "Confirmando…" : "Concluindo…";
    try {
      await window.api.post(`/api/v1/dispatches/${button.dataset.id}/respond`, {
        action,
        note: action === "acknowledge" ? "Recebimento confirmado no portal do guarda" : "Atendimento encerrado na demonstração"
      });
      window.CAIS.toast(action === "acknowledge" ? "Recebimento confirmado. Os demais destinatários não são bloqueados." : "Atendimento marcado como concluído.", "success");
      await loadDispatches();
    } catch (error) { window.CAIS.toast(error.message, "error"); button.disabled = false; }
  }

  function connectStream() {
    if (state.stream) state.stream.close();
    state.stream = new EventSource(window.api.streamUrl());
    ["dispatch.sent", "dispatch.acknowledged", "dispatch.completed"].forEach(type => {
      state.stream.addEventListener(type, event => {
        if (type === "dispatch.sent") window.CAIS.toast("Novo alerta operacional recebido.", "success");
        loadDispatches();
      });
    });
  }

  async function initialize() {
    if (!(await ensureGuard())) return;
    await loadDispatches(); connectStream(); setInterval(loadDispatches, 15000);
  }

  byId("guard-logout").addEventListener("click", () => window.api.logout());
  byId("guard-refresh").addEventListener("click", loadDispatches);
  byId("guard-history").addEventListener("click", event => { state.showHistory = !state.showHistory; event.currentTarget.classList.toggle("active", state.showHistory); renderDispatches(); });
  byId("guard-profile").addEventListener("click", () => window.CAIS.toast(`${state.guard?.code || "GCM"} · ${state.guard?.specialization || "Operação"}`));
  window.addEventListener("beforeunload", () => state.stream?.close());
  initialize();
})();
