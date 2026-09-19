(function loginPage() {
  const form = document.getElementById("login-form");
  const email = document.getElementById("email");
  const password = document.getElementById("password");
  const apiUrl = document.getElementById("api-url");
  const errorBox = document.getElementById("login-error");
  const submit = document.getElementById("login-submit");
  apiUrl.value = window.api.baseUrl;

  document.querySelectorAll("[data-demo]").forEach(button => {
    button.addEventListener("click", () => {
      const isManager = button.dataset.demo === "manager";
      email.value = isManager ? "gestora@cais.recife.br" : "guarda12@cais.recife.br";
      password.value = "cais2026";
      email.focus();
    });
  });

  apiUrl.addEventListener("change", () => {
    if (apiUrl.value.trim()) window.api.setBaseUrl(apiUrl.value.trim());
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    errorBox.classList.add("hidden");
    submit.disabled = true;
    submit.textContent = "Conectando…";
    try {
      if (apiUrl.value.trim()) window.api.setBaseUrl(apiUrl.value.trim());
      const session = await window.api.login(email.value.trim(), password.value);
      window.location.href = session.user.role === "manager" ? "gestor.html" : "guarda.html";
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.classList.remove("hidden");
    } finally {
      submit.disabled = false;
      submit.textContent = "Entrar no sistema";
    }
  });

  if (window.api.token) {
    window.api.get("/api/v1/auth/me").then(({ user }) => {
      window.location.href = user.role === "manager" ? "gestor.html" : "guarda.html";
    }).catch(() => {});
  }
})();
