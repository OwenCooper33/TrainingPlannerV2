const $ = (id) => document.getElementById(id);

function log(msg) {
  $("authLog").textContent = `[${new Date().toLocaleTimeString()}] ${msg}\n${$("authLog").textContent}`;
}

async function api(path, method = "GET", body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.error || `${method} ${path} failed`);
  return payload;
}

async function checkSession() {
  try {
    await api("/api/auth/me");
    window.location.replace("/index.html");
  } catch (_) {
    // not logged in
  }
}

$("loginBtn").addEventListener("click", async () => {
  try {
    await api("/api/auth/login", "POST", {
      email: $("loginEmail").value,
      password: $("loginPassword").value,
    });
    window.location.replace("/index.html");
  } catch (e) {
    log(e.message);
  }
});

$("registerBtn").addEventListener("click", async () => {
  try {
    await api("/api/auth/register", "POST", {
      name: $("registerName").value,
      email: $("registerEmail").value,
      password: $("registerPassword").value,
    });
    window.location.replace("/index.html");
  } catch (e) {
    log(e.message);
  }
});

checkSession();
