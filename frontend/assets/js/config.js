(function configureCAIS() {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("api");
  if (fromQuery) localStorage.setItem("cais_api_url", fromQuery.replace(/\/$/, ""));
  const inferredLocal = ["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "http://localhost:8000"
    : "https://CONFIGURE-SEU-BACKEND.ngrok.app";
  window.CAIS_CONFIG = {
    API_URL: (localStorage.getItem("cais_api_url") || inferredLocal).replace(/\/$/, ""),
    TOKEN_KEY: "cais_access_token",
    USER_KEY: "cais_user"
  };
})();
