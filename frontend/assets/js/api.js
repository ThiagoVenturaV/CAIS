class CAISApiError extends Error {
  constructor(message, status, details) {
    super(message);
    this.name = "CAISApiError";
    this.status = status;
    this.details = details;
  }
}

class CAISApi {
  constructor() { this.baseUrl = window.CAIS_CONFIG.API_URL; }
  get token() { return localStorage.getItem(window.CAIS_CONFIG.TOKEN_KEY); }
  set token(value) {
    if (value) localStorage.setItem(window.CAIS_CONFIG.TOKEN_KEY, value);
    else localStorage.removeItem(window.CAIS_CONFIG.TOKEN_KEY);
  }
  setBaseUrl(url) {
    this.baseUrl = url.replace(/\/$/, "");
    localStorage.setItem("cais_api_url", this.baseUrl);
  }
  async request(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    let response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, { ...options, headers });
    } catch (error) {
      throw new CAISApiError(`Não foi possível conectar à API em ${this.baseUrl}.`, 0, error.message);
    }
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const message = data?.detail || data?.message || `Falha na API (${response.status})`;
      if (response.status === 401 && !path.includes("/auth/login")) this.logout(true);
      throw new CAISApiError(message, response.status, data);
    }
    return data;
  }
  get(path) { return this.request(path); }
  post(path, body) { return this.request(path, { method: "POST", body: JSON.stringify(body) }); }
  async login(email, password) {
    const data = await this.post("/api/v1/auth/login", { email, password });
    this.token = data.access_token;
    localStorage.setItem(window.CAIS_CONFIG.USER_KEY, JSON.stringify(data.user));
    return data;
  }
  logout(silent = false) {
    this.token = null;
    localStorage.removeItem(window.CAIS_CONFIG.USER_KEY);
    if (!silent) window.location.href = "index.html";
    else setTimeout(() => { window.location.href = "index.html"; }, 300);
  }
  streamUrl() { return `${this.baseUrl}/api/v1/stream?token=${encodeURIComponent(this.token || "")}`; }
}

window.api = new CAISApi();
window.CAIS = {
  escape(value) {
    return String(value ?? "").replace(/[&<>'"]/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    })[character]);
  },
  date(value, withTime = true) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return new Intl.DateTimeFormat("pt-BR", {
      day: "2-digit", month: "short", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {})
    }).format(parsed);
  },
  percent(value, digits = 0) {
    return new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: digits }).format(Number(value || 0));
  },
  label(value) {
    const labels = {
      high: "Alta", medium: "Média", low: "Baixa", processing: "Processando",
      pending_approval: "Aguardando decisão", approved: "Aprovado", distributed: "Distribuído",
      rejected: "Rejeitado", completed: "Concluído", unassigned: "Sem destinatário",
      sent: "Enviado", acknowledged: "Recebido"
    };
    return labels[value] || String(value || "—").replaceAll("_", " ");
  },
  toast(message, kind = "info", duration = 4200) {
    const stack = document.getElementById("toast-stack");
    if (!stack) return;
    const item = document.createElement("div");
    item.className = `toast ${kind}`;
    item.textContent = message;
    stack.appendChild(item);
    setTimeout(() => item.remove(), duration);
  },
  requireToken() {
    if (!window.api.token) {
      window.location.href = "index.html";
      return false;
    }
    return true;
  }
};
