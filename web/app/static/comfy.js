const POLL_MS = 5000;

function parseApiError(body) {
  const t = (body || "").trim();
  if (!t) return "";
  try {
    const j = JSON.parse(t);
    if (typeof j.detail === "string") return j.detail;
  } catch (_) {}
  return t;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401) {
    alert("请重新登录（浏览器会弹出账号密码框）");
    throw new Error("401");
  }
  if (!res.ok) {
    const t = await res.text();
    throw new Error(parseApiError(t) || res.statusText);
  }
  return res.json();
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
}

function statusClass(summary) {
  if (!summary) return "";
  if (summary.includes("运行中")) return "comfy-dot--ok";
  if (summary.includes("启动中")) return "comfy-dot--pending";
  if (summary.includes("已停止")) return "comfy-dot--off";
  return "comfy-dot--warn";
}

function renderDetail(st) {
  if (!st) return `<p class="muted">无法读取状态</p>`;
  const pub = st.public_comfyui_url || "#";
  const openDisabled = !st.http_ok ? " disabled" : "";
  return `
    <div class="comfy-detail-top">
      <div class="comfy-detail-status">
        <span class="comfy-dot ${statusClass(st.summary)}" aria-hidden="true"></span>
        <div>
          <strong class="comfy-summary">${esc(st.summary)}</strong>
          <p class="muted comfy-meta">单元 ${esc(st.unit)} · systemd ${esc(st.systemd_active)}${st.main_pid ? ` · PID ${st.main_pid}` : ""}</p>
        </div>
      </div>
      <div class="comfy-actions">
        <a class="btn primary" href="${esc(pub)}" target="_blank" rel="noopener noreferrer"${openDisabled}>打开 ComfyUI 页面</a>
        <button type="button" class="btn" data-comfy-action="start">启动</button>
        <button type="button" class="btn" data-comfy-action="restart">重启</button>
        <button type="button" class="btn btn-danger" data-comfy-action="stop">停止</button>
      </div>
    </div>
    <dl class="comfy-dl">
      <div><dt>队列运行</dt><dd>${st.queue_running ?? 0}</dd></div>
      <div><dt>队列等待</dt><dd>${st.queue_pending ?? 0}</dd></div>
      <div><dt>内网地址</dt><dd><code>${esc(st.comfyui_url)}</code></dd></div>
      <div><dt>外网地址</dt><dd><code>${esc(pub)}</code></dd></div>
    </dl>
    ${st.http_error ? `<p class="comfy-warn">API：${esc(st.http_error)}</p>` : ""}
    <p class="muted comfy-foot">停止或重启前若队列仍有任务，需二次确认。日志：<code>/home/linux/ComfyUI/comfy_run.log</code></p>`;
}

let pollTimer = null;
let busy = false;

async function refresh() {
  const card = document.getElementById("comfyDetailCard");
  if (!card || busy) return;
  try {
    const st = await api("/api/comfy/status");
    card.innerHTML = renderDetail(st);
    bindActions();
  } catch (e) {
    card.innerHTML = `<p class="comfy-warn">${esc(e.message)}</p>`;
  }
}

async function runAction(action) {
  if (busy) return;
  let force = false;
  if (action === "stop" || action === "restart") {
    const label = action === "stop" ? "停止" : "重启";
    if (!confirm(`确定要${label} ComfyUI 吗？进行中的队列任务可能被中断。`)) return;
  }
  busy = true;
  const card = document.getElementById("comfyDetailCard");
  try {
    let st = await api("/api/comfy/control", {
      method: "POST",
      body: JSON.stringify({ action, force }),
    });
    card.innerHTML = renderDetail(st);
    bindActions();
  } catch (e) {
    if (String(e.message).includes("队列") && (action === "stop" || action === "restart")) {
      if (confirm(`${e.message}\n仍要强制执行吗？`)) {
        try {
          const st = await api("/api/comfy/control", {
            method: "POST",
            body: JSON.stringify({ action, force: true }),
          });
          card.innerHTML = renderDetail(st);
          bindActions();
        } catch (e2) {
          alert(e2.message);
        }
      }
    } else {
      alert(e.message);
    }
  } finally {
    busy = false;
  }
}

function bindActions() {
  document.querySelectorAll("[data-comfy-action]").forEach((btn) => {
    btn.onclick = () => runAction(btn.dataset.comfyAction);
  });
}

function startPoll() {
  clearInterval(pollTimer);
  pollTimer = setInterval(refresh, POLL_MS);
}

void refresh().then(startPoll);
