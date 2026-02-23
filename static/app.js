const state = {
  monthCursor: new Date(),
  today: null,
  plan: [],
  activeWorkoutId: null,
};

const $ = (id) => document.getElementById(id);

function log(msg, obj) {
  const line = `[${new Date().toLocaleTimeString()}] ${msg}`;
  $("log").textContent = obj ? `${line}\n${JSON.stringify(obj, null, 2)}\n\n${$("log").textContent}` : `${line}\n${$("log").textContent}`;
}

function renderConfigStatus(config) {
  const stravaReady = config?.strava?.ready;
  const zwiftReady = config?.zwift?.ready;
  const stravaMissing = config?.strava?.missing || [];
  const zwiftMissing = config?.zwift?.missing || [];

  $("stravaConfig").textContent = stravaReady
    ? "Ready"
    : `Missing: ${stravaMissing.join(", ") || "unknown"}`;
  $("zwiftConfig").textContent = zwiftReady
    ? "Ready"
    : `Missing: ${zwiftMissing.join(", ") || "unknown"}`;
  $("connectStrava").dataset.ready = String(!!stravaReady);
  $("connectZwift").dataset.ready = String(!!zwiftReady);
}

async function api(path, method = "GET", body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    window.location.replace("/login.html");
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    throw new Error(`${method} ${path} failed: ${res.status}`);
  }
  return res.headers.get("content-type")?.includes("application/json") ? res.json() : res.text();
}

function monthBounds(cursor) {
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const start = new Date(first);
  start.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const end = new Date(start);
  end.setDate(start.getDate() + 41);
  return { start, end };
}

function renderCalendar() {
  const { start } = monthBounds(state.monthCursor);
  const cal = $("calendar");
  cal.innerHTML = "";

  $("monthLabel").textContent = state.monthCursor.toLocaleString(undefined, { month: "long", year: "numeric" });

  const map = new Map();
  for (const e of state.plan) {
    const key = e.date;
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(e);
  }

  for (let i = 0; i < 42; i++) {
    const day = new Date(start);
    day.setDate(start.getDate() + i);
    const iso = day.toISOString().slice(0, 10);

    const el = document.createElement("div");
    el.className = "day";
    if (day.getMonth() !== state.monthCursor.getMonth()) el.classList.add("muted");
    if (state.today === iso) el.classList.add("today");

    const num = document.createElement("div");
    num.className = "daynum";
    num.textContent = `${day.getDate()} ${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][(day.getDay() + 6) % 7]}`;
    el.appendChild(num);

    const entries = map.get(iso) || [];
    for (const item of entries) {
      const badge = document.createElement("div");
      badge.className = "entry";
      if (item.status === "completed") badge.classList.add("completed");
      if ((item.intensity || 0) >= 0.85) badge.classList.add("intense");
      badge.textContent = `#${item.id} ${item.title} (${item.discipline}, ${item.tss} TSS)`;
      badge.title = item.description || "";
      badge.style.cursor = "pointer";
      badge.addEventListener("click", () => openWorkoutModal(item.id).catch((e) => log(e.message)));
      el.appendChild(badge);
    }

    cal.appendChild(el);
  }
}

async function loadPlan() {
  const { start, end } = monthBounds(state.monthCursor);
  const res = await api(`/api/plan?start=${start.toISOString().slice(0, 10)}&end=${end.toISOString().slice(0, 10)}`);
  state.plan = res.plan;
  renderCalendar();
}

async function bootstrap() {
  const boot = await api("/api/bootstrap");
  state.today = boot.today;
  state.monthCursor = new Date(boot.today);
  state.plan = boot.plan || [];
  $("currentUser").textContent = `${boot.user?.name || "Athlete"} (${boot.user?.email || ""})`;
  renderConfigStatus(boot.config);
  $("stravaStatus").textContent = boot.integrations?.strava?.connected
    ? "Connected"
    : (boot.integrations?.strava?.oauth_enabled ? "Not connected" : "OAuth not configured on server");
  $("zwiftStatus").textContent = boot.integrations?.zwift?.connected
    ? "Connected"
    : (boot.integrations?.zwift?.oauth_enabled ? "Not connected" : "OAuth not configured on server");
  $("stravaPrompt").style.display = boot.integrations?.strava?.connected ? "none" : "block";
  $("zwiftPrompt").style.display = boot.integrations?.zwift?.connected ? "none" : "block";
  if (boot.integrations?.zwift?.upload_url) {
    $("zwiftUploadUrl").value = boot.integrations.zwift.upload_url;
  }
  renderCalendar();

  const qs = new URLSearchParams(window.location.search);
  const connected = qs.get("connected");
  const connectError = qs.get("connect_error");
  if (connected) {
    log(`${connected} connected`);
    window.history.replaceState({}, "", "/");
  }
  if (connectError) {
    log(`Connection failed: ${connectError}`);
    window.history.replaceState({}, "", "/");
  }

  $("syncStatus").textContent = "Syncing apps...";
  try {
    const sync = await api("/api/sync/all", "POST", {});
    $("syncStatus").textContent = `Strava: ${sync.result.strava.status} | Zwift: ${sync.result.zwift.status}`;
    log("Sync complete", sync.result);
  } catch (e) {
    $("syncStatus").textContent = "Sync failed";
    log(`Sync failed: ${e.message}`);
  }

  await loadPlan();
}

async function generatePlan() {
  const disciplines = $("disciplines").value.split(",").map((x) => x.trim()).filter(Boolean);
  const weeks = parseInt($("weeks").value, 10) || 8;
  const result = await api("/api/plan/generate", "POST", {
    start_date: new Date().toISOString().slice(0, 10),
    disciplines,
    weeks,
  });
  log("Plan generated", result.result);
  await loadPlan();
}

async function completeWorkout() {
  await api("/api/workouts/complete", "POST", {
    plan_entry_id: parseInt($("completePlanId").value, 10),
    discipline: $("completeDiscipline").value,
    tss: parseInt($("completeTss").value, 10),
    duration_minutes: parseInt($("completeDuration").value, 10),
    intensity: 0.75,
    provider: "manual",
  });
  log("Workout completion saved");
  await loadPlan();
}

async function sendToZwift() {
  const id = parseInt($("zwiftPlanId").value, 10);
  await sendEntryToZwift(id, "zwoDownload");
}

async function sendEntryToZwift(id, anchorId) {
  const res = await api("/api/workouts/send-to-zwift", "POST", { plan_entry_id: id });
  log("Zwift send result", res.result);
  if (res.download) {
    $(anchorId).textContent = "Download ZWO";
    $(anchorId).href = res.download;
  }
}

async function saveZwiftConfig() {
  await api("/api/integrations/zwift/config", "POST", {
    upload_url: $("zwiftUploadUrl").value,
  });
  log("Zwift config saved");
}

$("generateBtn").addEventListener("click", () => generatePlan().catch((e) => log(e.message)));
$("completeBtn").addEventListener("click", () => completeWorkout().catch((e) => log(e.message)));
$("sendZwiftBtn").addEventListener("click", () => sendToZwift().catch((e) => log(e.message)));
$("connectStrava").addEventListener("click", () => {
  if ($("connectStrava").dataset.ready !== "true") {
    log(`Cannot connect Strava. ${$("stravaConfig").textContent}`);
    return;
  }
  window.location.assign("/api/integrations/strava/connect");
});
$("connectZwift").addEventListener("click", () => {
  if ($("connectZwift").dataset.ready !== "true") {
    log(`Cannot connect Zwift. ${$("zwiftConfig").textContent}`);
    return;
  }
  window.location.assign("/api/integrations/zwift/connect");
});
$("saveZwiftConfig").addEventListener("click", () => saveZwiftConfig().catch((e) => log(e.message)));
$("syncBtn").addEventListener("click", async () => {
  try {
    const sync = await api("/api/sync/all", "POST", {});
    log("Manual sync complete", sync.result);
    await loadPlan();
  } catch (e) {
    log(`Sync failed: ${e.message}`);
  }
});

$("prevMonth").addEventListener("click", async () => {
  state.monthCursor = new Date(state.monthCursor.getFullYear(), state.monthCursor.getMonth() - 1, 1);
  await loadPlan();
});
$("nextMonth").addEventListener("click", async () => {
  state.monthCursor = new Date(state.monthCursor.getFullYear(), state.monthCursor.getMonth() + 1, 1);
  await loadPlan();
});
$("logoutBtn").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", "POST", {});
  } finally {
    window.location.replace("/login.html");
  }
});

function drawWorkoutGraph(profile, duration, entry) {
  const canvas = $("workoutGraph");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const pad = { l: 46, r: 10, t: 10, b: 28 };
  const w = canvas.width - pad.l - pad.r;
  const h = canvas.height - pad.t - pad.b;
  const maxY = 1.05;

  ctx.strokeStyle = "#d1d7c6";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + h * (i / 4);
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + w, y);
    ctx.stroke();
  }

  ctx.strokeStyle = "#2e7d32";
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < profile.length; i++) {
    const b = profile[i];
    const x1 = pad.l + (b.start_minute / duration) * w;
    const x2 = pad.l + (b.end_minute / duration) * w;
    const y = pad.t + (1 - b.intensity / maxY) * h;
    if (i === 0) ctx.moveTo(x1, y);
    ctx.lineTo(x2, y);
    if (i < profile.length - 1) {
      const n = profile[i + 1];
      const y2 = pad.t + (1 - n.intensity / maxY) * h;
      ctx.lineTo(x2, y2);
    }
  }
  ctx.stroke();

  ctx.fillStyle = "#14281d";
  ctx.font = "12px Space Grotesk";
  ctx.fillText("Intensity", 6, 16);
  ctx.fillText("0", pad.l - 10, pad.t + h + 14);
  ctx.fillText(`${duration} min`, pad.l + w - 50, pad.t + h + 14);
  ctx.fillText(`${entry.title} (${entry.tss} TSS)`, pad.l, canvas.height - 6);
}

async function openWorkoutModal(entryId) {
  const res = await api(`/api/plan-entry/${entryId}`);
  const entry = res.entry;
  const profile = res.profile || [];
  const duration = Math.max(20, parseInt(entry.duration_minutes, 10) || 60);
  state.activeWorkoutId = entryId;

  $("modalTitle").textContent = entry.title;
  $("modalMeta").textContent = `${entry.date} • ${entry.discipline} • ${entry.duration_minutes} min • ${entry.tss} TSS • ${entry.workout_type}`;
  $("modalDownloadZwo").textContent = "";
  $("modalDownloadZwo").removeAttribute("href");
  drawWorkoutGraph(profile, duration, entry);
  $("workoutModal").classList.remove("hidden");
}

$("closeModal").addEventListener("click", () => $("workoutModal").classList.add("hidden"));
$("workoutModal").addEventListener("click", (e) => {
  if (e.target.id === "workoutModal") $("workoutModal").classList.add("hidden");
});
$("modalSendZwift").addEventListener("click", async () => {
  if (!state.activeWorkoutId) return;
  await sendEntryToZwift(state.activeWorkoutId, "modalDownloadZwo");
});

bootstrap().catch((e) => log(e.message));
