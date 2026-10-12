/* 漫剧工作台 — 对齐参考图流程 */
const state = {
  view: "home", // home | project | parsing | epEditor
  projects: [],
  config: null,
  projectId: null,
  project: null,
  pipeline: null,
  step: "outline", // outline | assets | videos
  assetTab: "characters",
  characters: [],
  scenes: [],
  props: [],
  episodes: [],
  search: "",
  parsing: null,
  epEditorId: null,
  epEditorSeg: 0,
  epEditorScene: 0, // 0=全部场，>0=场号
  epAssetTab: "clip", // all | clip | material
  epAssetFilter: "all", // all | characters | scenes | props
  selectedRefs: [],
  pollTimer: null,
  expandedEp: null,
  epSearch: "",
  jobsOpen: false,
  jobsList: [],
  jobsBadge: 0,
  selectedAssetIds: new Set(),
  selectedEpisodeIds: new Set(),
};

function ensureSelectSets() {
  if (!(state.selectedAssetIds instanceof Set)) state.selectedAssetIds = new Set();
  if (!(state.selectedEpisodeIds instanceof Set)) state.selectedEpisodeIds = new Set();
}

function selectedAssetIdList() {
  ensureSelectSets();
  return [...state.selectedAssetIds];
}

function selectedEpisodeIdList() {
  ensureSelectSets();
  return [...state.selectedEpisodeIds];
}

function toggleSelectedAsset(id) {
  ensureSelectSets();
  if (!id) return;
  if (state.selectedAssetIds.has(id)) state.selectedAssetIds.delete(id);
  else state.selectedAssetIds.add(id);
}

function toggleSelectedEpisode(id) {
  ensureSelectSets();
  if (!id) return;
  if (state.selectedEpisodeIds.has(id)) state.selectedEpisodeIds.delete(id);
  else state.selectedEpisodeIds.add(id);
}

function currentKindAssets() {
  if (state.assetTab === "scenes") return state.scenes || [];
  if (state.assetTab === "props") return state.props || [];
  return state.characters || [];
}

function selectedAssetsInCurrentTab() {
  ensureSelectSets();
  return currentKindAssets().filter((it) => state.selectedAssetIds.has(it.id));
}

function selectedEpisodes() {
  ensureSelectSets();
  return (state.episodes || []).filter((e) => state.selectedEpisodeIds.has(e.id));
}

const STAGE_LABELS = {
  video: "视频生成",
  storyboard_images: "分镜出图",
  drama10_export: "Drama10 脚本",
  storyboard: "分镜脚本",
};

const STATUS_LABELS = {
  pending: "待开始",
  running: "进行中",
  done: "已完成",
  failed: "失败",
  skipped: "已跳过",
  cancelled: "已停止",
};

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 任务时间统一显示为北京时间（Asia/Shanghai）。 */
function formatBeijingTime(iso) {
  if (!iso) return "—";
  const raw = String(iso).trim();
  let d = new Date(raw);
  if (Number.isNaN(d.getTime())) {
    // 无时区的旧串：按 UTC 解析再转北京
    d = new Date(raw.replace(" ", "T") + (/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw) ? "" : "Z"));
  }
  if (Number.isNaN(d.getTime())) {
    return raw.slice(0, 19).replace("T", " ");
  }
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (t) => (parts.find((p) => p.type === t) || {}).value || "00";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}:${get("second")}`;
}

function parseApiError(body) {
  const t = (body || "").trim();
  if (!t) return "";
  try {
    const j = JSON.parse(t);
    if (typeof j.detail === "string") return j.detail;
    if (j.detail) return JSON.stringify(j.detail);
  } catch (_) {}
  return t;
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { credentials: "same-origin", ...opts, headers });
  if (res.status === 401) {
    alert("请重新登录");
    throw new Error("401");
  }
  if (!res.ok) throw new Error(parseApiError(await res.text()) || res.statusText);
  if (res.status === 204) return null;
  return res.json();
}

function toast(msg) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 2600);
}

function meta() {
  return state.project?.docs?.script_meta || {};
}

function fileUrl(rel, ver) {
  if (!rel || !state.projectId) return "";
  const base = `/api/projects/${state.projectId}/files/${rel
    .split("/")
    .map(encodeURIComponent)
    .join("/")}`;
  if (ver === undefined || ver === null || ver === "") return base;
  return `${base}?v=${encodeURIComponent(String(ver))}`;
}

async function mediaPlayUrl(rel) {
  if (!rel) return "";
  const r = await api(
    `/api/projects/${state.projectId}/media-url?path=${encodeURIComponent(rel)}&v=${Date.now()}`
  );
  return r.url;
}

function topbar(extraRight = "") {
  const n = state.jobsBadge || (state.pipeline?.active_jobs || []).length || 0;
  const badge = n ? `<span class="jobs-badge">${n}</span>` : "";
  return `<header class="topbar">
    <div class="topbar-brand">三硕画布 <span>v3.1.2</span></div>
    <div class="topbar-right">
      <span class="pill">服务可用</span>
      <button type="button" class="btn sm" id="btnJobs">任务记录${badge}</button>
      <button type="button" class="btn sm primary" id="btnSettings">制作设置</button>
      ${extraRight}
    </div>
  </header>
  <div id="jobsDrawer" class="jobs-drawer ${state.jobsOpen ? "" : "hidden"}"></div>
  <div id="jobsBackdrop" class="jobs-backdrop ${state.jobsOpen ? "" : "hidden"}"></div>`;
}

function stageLabel(id) {
  return STAGE_LABELS[id] || id || "—";
}

function statusLabel(st) {
  return STATUS_LABELS[String(st || "").toLowerCase()] || String(st || "—");
}

function jobParams(j) {
  try {
    return JSON.parse(j.params_json || "{}") || {};
  } catch (_) {
    return {};
  }
}

function jobCurrentShot(j) {
  const p = jobParams(j);
  const n = Number(p.current_shot_num);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function videoJobLabel(j) {
  if (j.stage_id !== "video") return stageLabel(j.stage_id);
  const p = jobParams(j);
  if (p.current_label) return String(p.current_label);
  const msg = String(j.message || "");
  const m = msg.match(/整集-第\d+集第\d+场第\d+段/);
  if (m) return m[0];
  if (Array.isArray(p.shot_nums) && p.shot_nums.length) {
    return `单段视频 · 片段 ${p.shot_nums.join("、")}`;
  }
  if (p.mux_only) return "合并成片";
  if (p.segment_serial || (p.confirm_full_episode && p.auto_concat === false)) {
    return "整集 · 按场/段依次生成";
  }
  if (p.confirm_full_episode) return "整集 · 按场/段依次生成";
  return stageLabel("video");
}

function activeJobs() {
  const pl = state.pipeline;
  if (!pl) return [];
  if (Array.isArray(pl.active_jobs) && pl.active_jobs.length) return pl.active_jobs;
  return pl.active_job ? [pl.active_job] : [];
}

function jobShotNums(j) {
  try {
    const p = JSON.parse(j.params_json || "{}");
    if (Array.isArray(p.shot_nums)) return p.shot_nums.map(Number).filter(Number.isFinite);
  } catch (_) {}
  return [];
}

function formatActiveJobLine(j) {
  const pct = Math.round((j.progress || 0) * 100);
  const msg = esc(friendlyJobError(j.message || ""));
  const stopBtn =
    j.status === "running"
      ? `<button type="button" class="btn sm danger" data-stop-job="${esc(j.id)}">停止</button>`
      : "";
  const contBtn =
    (j.status === "cancelled" || j.status === "failed") && j.stage_id === "video" && j.episode_id
      ? `<button type="button" class="btn sm primary" data-continue-ep="${esc(j.episode_id)}">继续补全</button>`
      : "";
  return `<div class="active-job">
    <div class="active-job-h">
      <strong>${esc(videoJobLabel(j))}</strong>
      <span>${pct}%</span>
      <span class="active-job-actions">${stopBtn}${contBtn}</span>
    </div>
    ${msg ? `<div class="active-job-detail">${msg}</div>` : ""}
    <div class="job-progress"><i style="width:${pct}%"></i></div>
  </div>`;
}

function renderActiveJobsBanner(jobs, { episodeId } = {}) {
  let list = jobs || activeJobs();
  if (episodeId) list = list.filter((j) => j.episode_id === episodeId);
  // 附带本集最近停止/失败的视频任务，方便点「继续补全」
  if (episodeId && state.jobsList?.length) {
    const extra = state.jobsList.filter(
      (j) =>
        j.episode_id === episodeId &&
        j.stage_id === "video" &&
        (j.status === "cancelled" || j.status === "failed") &&
        !list.some((x) => x.id === j.id)
    );
    list = list.concat(extra.slice(0, 2));
  }
  if (!list.length) return "";
  const runningN = list.filter((j) => j.status === "running").length;
  return `<div class="active-jobs-banner">
    <div class="active-jobs-title">${
      runningN ? `进行中 ${runningN} · 按场/段依次生成` : "可继续的任务"
    }</div>
    ${list.map(formatActiveJobLine).join("")}
  </div>`;
}

async function stopJob(jobId) {
  if (!jobId) return;
  if (!confirm("停止当前任务？已生成的段落会保留，之后可点「继续补全视频」。")) return;
  try {
    await api(`/api/jobs/${jobId}/stop`, { method: "POST", body: "{}" });
    toast("已停止任务");
    await refreshPipeline();
    await refreshJobsList();
    updateJobsBadge();
    if (state.jobsOpen) paintJobsDrawer();
    if (state.view === "epEditor") void bindEpEditor();
    else render();
  } catch (e) {
    alert(`停止失败：${friendlyJobError(e.message || e)}`);
  }
}

function bindJobActionButtons(root) {
  const el = root || document;
  el.querySelectorAll("[data-stop-job]").forEach((btn) => {
    btn.onclick = () => stopJob(btn.getAttribute("data-stop-job"));
  });
  el.querySelectorAll("[data-continue-ep]").forEach((btn) => {
    btn.onclick = () =>
      startVideoJob(btn.getAttribute("data-continue-ep"), {
        confirm_full_episode: true,
        auto_concat: false,
        skip_existing: true,
        preview_with_audio: true,
      });
  });
}

function renderJobsDrawerHtml(jobs) {
  const running = (jobs || []).filter((j) => j.status === "running");
  const rows = (jobs || [])
    .map((j) => {
      const ep = j.ep_num != null ? `第${String(j.ep_num).padStart(3, "0")}集` : "—";
      const pct = Math.round((j.progress || 0) * 100);
      const cls = j.status === "failed" ? "row-failed" : j.status === "running" ? "row-run" : "";
      const rawMsg = friendlyJobError(j.message || "");
      const msg = esc(rawMsg.slice(0, 180));
      const t = esc(formatBeijingTime(j.updated_at));
      return `<tr class="${cls}">
        <td>${esc(ep)}</td>
        <td>${esc(videoJobLabel(j))}</td>
        <td class="st-${esc(j.status)}">${esc(statusLabel(j.status))}${j.status === "running" ? ` ${pct}%` : ""}</td>
        <td class="job-msg" title="${esc(rawMsg)}">${msg || "—"}</td>
        <td class="job-time">${t}</td>
      </tr>`;
    })
    .join("");
  return `<div class="jobs-drawer-inner">
    <div class="jobs-drawer-h">
      <strong>任务记录</strong>
      <span class="muted">${running.length ? `进行中 ${running.length}` : "无进行中任务"}</span>
      <button type="button" class="btn sm" id="btnJobsRefresh">刷新</button>
      <button type="button" class="btn sm ghost" id="btnJobsClose">关闭</button>
    </div>
    ${running.length ? `<div class="jobs-running-block">${running.map(formatActiveJobLine).join("")}</div>` : ""}
    <div class="jobs-table-wrap">
      <table class="jobs-table">
        <thead><tr><th>分集</th><th>类型</th><th>状态</th><th>说明</th><th>更新</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="5" class="muted">暂无任务</td></tr>`}</tbody>
      </table>
    </div>
  </div>`;
}

async function refreshJobsList() {
  if (!state.projectId) {
    state.jobsList = [];
    state.jobsBadge = 0;
    return [];
  }
  try {
    const data = await api(`/api/projects/${state.projectId}/jobs`);
    state.jobsList = data.jobs || [];
    state.jobsBadge = state.jobsList.filter((j) => j.status === "running").length;
    const running = state.jobsList.filter((j) => j.status === "running");
    if (state.pipeline) {
      state.pipeline.active_jobs = running;
      state.pipeline.active_job = running[0] || null;
    }
  } catch (_) {
    state.jobsList = state.jobsList || [];
  }
  return state.jobsList;
}

function paintJobsDrawer() {
  const drawer = document.getElementById("jobsDrawer");
  const backdrop = document.getElementById("jobsBackdrop");
  if (!drawer) return;
  if (!state.jobsOpen) {
    drawer.classList.add("hidden");
    backdrop?.classList.add("hidden");
    return;
  }
  drawer.classList.remove("hidden");
  backdrop?.classList.remove("hidden");
  drawer.innerHTML = renderJobsDrawerHtml(state.jobsList);
  bindJobActionButtons(drawer);
  document.getElementById("btnJobsClose")?.addEventListener("click", () => {
    state.jobsOpen = false;
    paintJobsDrawer();
  });
  document.getElementById("btnJobsRefresh")?.addEventListener("click", async () => {
    await refreshJobsList();
    paintJobsDrawer();
    updateJobsBadge();
  });
  backdrop?.addEventListener("click", () => {
    state.jobsOpen = false;
    paintJobsDrawer();
  }, { once: true });
}

function updateJobsBadge() {
  const btn = document.getElementById("btnJobs");
  if (!btn) return;
  const n = state.jobsBadge || 0;
  btn.innerHTML = n ? `任务记录<span class="jobs-badge">${n}</span>` : "任务记录";
}

async function openJobsPanel() {
  if (!state.projectId) {
    toast("请先打开项目");
    return;
  }
  state.jobsOpen = true;
  await refreshJobsList();
  paintJobsDrawer();
  updateJobsBadge();
}

/* —— 分镜图放大 —— */
const lightboxNav = { imgs: [], index: 0 };

function collectLightboxImages(fromImg) {
  const gallery =
    fromImg.closest(".clip-strip") ||
    fromImg.closest(".ep-asset-list") ||
    fromImg.closest(".asset-grid") ||
    fromImg.closest(".multi-ref") ||
    fromImg.closest("#epEditorRoot") ||
    fromImg.closest(".page-body");
  if (!gallery) return [fromImg];
  const list = Array.from(
    gallery.querySelectorAll("img.zoomable, .clip-thumb img, .ep-asset-thumb img, .asset-thumb img")
  ).filter((i) => i.src && !i.classList.contains("img-loading") && !i.classList.contains("img-error"));
  if (!list.length) return [fromImg];
  if (list.includes(fromImg)) return list;
  return [fromImg, ...list];
}

function lightboxStep(delta) {
  const n = lightboxNav.imgs.length;
  if (n <= 1) return;
  let next = lightboxNav.index + delta;
  if (next < 0) next = n - 1;
  if (next >= n) next = 0;
  showLightboxAt(next);
}

function showLightboxAt(index) {
  const lb = document.getElementById("lightbox");
  if (!lb || !lightboxNav.imgs.length) return;
  const imgEl = lightboxNav.imgs[index];
  if (!imgEl?.src) return;
  lightboxNav.index = index;
  const n = lightboxNav.imgs.length;
  const cap = imgEl.alt || imgEl.title || "分镜图";
  lb.querySelector(".lightbox-caption").textContent =
    n > 1 ? `${cap} · ${index + 1} / ${n}（←→ 翻页）` : cap;
  lb.querySelector(".lightbox-img").src = imgEl.src;
  lb.classList.toggle("lightbox--solo", n <= 1);
}

function ensureLightbox() {
  let lb = document.getElementById("lightbox");
  if (lb) return lb;
  lb = document.createElement("div");
  lb.id = "lightbox";
  lb.className = "lightbox hidden";
  lb.innerHTML = `<button type="button" class="lightbox-close" aria-label="关闭">×</button>
    <button type="button" class="lightbox-nav lightbox-prev" aria-label="上一张">‹</button>
    <button type="button" class="lightbox-nav lightbox-next" aria-label="下一张">›</button>
    <div class="lightbox-stage">
      <p class="lightbox-caption"></p>
      <img class="lightbox-img" alt="" />
    </div>`;
  document.body.appendChild(lb);
  lb.querySelector(".lightbox-close").onclick = () => closeLightbox();
  lb.querySelector(".lightbox-prev").onclick = (e) => {
    e.stopPropagation();
    lightboxStep(-1);
  };
  lb.querySelector(".lightbox-next").onclick = (e) => {
    e.stopPropagation();
    lightboxStep(1);
  };
  lb.addEventListener("click", (e) => {
    if (e.target === lb) closeLightbox();
  });
  document.addEventListener("keydown", (e) => {
    const box = document.getElementById("lightbox");
    if (!box || box.classList.contains("hidden")) return;
    if (e.key === "Escape") closeLightbox();
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      lightboxStep(-1);
    }
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      lightboxStep(1);
    }
  });
  return lb;
}

function openLightbox(imgEl) {
  if (!imgEl?.src || imgEl.classList.contains("img-loading") || imgEl.classList.contains("img-error")) {
    return;
  }
  ensureLightbox();
  lightboxNav.imgs = collectLightboxImages(imgEl);
  lightboxNav.index = Math.max(0, lightboxNav.imgs.indexOf(imgEl));
  showLightboxAt(lightboxNav.index);
  document.getElementById("lightbox").classList.remove("hidden");
  document.body.classList.add("lightbox-open");
}

function closeLightbox() {
  const lb = document.getElementById("lightbox");
  if (!lb) return;
  lb.classList.add("hidden");
  lb.querySelector(".lightbox-img")?.removeAttribute("src");
  document.body.classList.remove("lightbox-open");
  lightboxNav.imgs = [];
  lightboxNav.index = 0;
}

function bindZoomableImages(root) {
  const scope = root || document;
  scope.querySelectorAll(".clip-thumb img, .ep-asset-thumb img, .asset-thumb img, img.zoomable").forEach((img) => {
    if (img.dataset.zoomBound) return;
    img.dataset.zoomBound = "1";
    img.classList.add("zoomable");
    img.title = img.title || "点击放大";
    img.onclick = (e) => {
      e.stopPropagation();
      e.preventDefault();
      openLightbox(img);
    };
  });
}

function subhead(title) {
  const steps = [
    ["outline", "剧本大纲"],
    ["assets", "资产库"],
    ["videos", "分集视频"],
  ];
  return `<div class="subhead" style="position:relative">
    <button type="button" class="btn ghost icon" id="btnBack">←</button>
    <div class="subhead-title">${esc(title)}</div>
    <div class="workflow">${steps
      .map(
        ([id, lab]) =>
          `<button type="button" class="workflow-step ${state.step === id ? "active" : ""}" data-step="${id}">${lab}</button>`
      )
      .join("")}</div>
    <div class="subhead-actions">
      <button type="button" class="btn sm" id="btnSync">同步配置</button>
    </div>
  </div>`;
}

function render() {
  const root = document.getElementById("app");
  if (state.view === "home") root.innerHTML = renderHome();
  else if (state.view === "parsing") root.innerHTML = renderParsing();
  else if (state.view === "epEditor") root.innerHTML = renderEpEditorShell();
  else root.innerHTML = renderProject();
  bindCommon();
  if (state.view === "home") bindHome();
  else if (state.view === "parsing") bindParsing();
  else if (state.view === "epEditor") void bindEpEditor();
  else bindProject();
}

function renderHome() {
  const q = (state.search || "").trim().toLowerCase();
  const list = (state.projects || []).filter((p) =>
    !q ? true : (p.name || "").toLowerCase().includes(q)
  );
  return `${topbar()}
  <div class="workbench">
    <div class="wb-hero">
      <h1>漫剧工作台</h1>
      <p>输入剧本，一键直出整部漫剧！</p>
    </div>
    <div class="wb-controls">
      <label>制作模式
        <select id="importMode">
          <option>默认版</option>
          <option>紧凑版</option>
          <option>精修版</option>
        </select>
      </label>
      <label>画风
        <input id="importStyle" type="text" value="半写实3D国漫" style="min-width:160px"/>
      </label>
      <label>画幅
        <select id="importRatio">
          <option value="16:9">16:9</option>
          <option value="9:16">9:16</option>
        </select>
      </label>
    </div>
    <div class="dropzone" id="dropzone">
      <div class="dz-icon">▶</div>
      <h3>拖拽剧本到这里，或选择上传方式</h3>
      <p class="hint">支持 TXT，剧本字数不超过 20 万字</p>
      <div class="dz-actions">
        <button type="button" class="btn white" id="btnUpload">上传剧本</button>
        <button type="button" class="btn" id="btnPaste">粘贴文本</button>
      </div>
      <input type="file" id="fileInput" accept=".txt,.md,text/plain" class="hidden"/>
    </div>
    <div class="link-ex" id="btnExample">查看标准剧本示例</div>
    <div class="section-head">
      <h2>我的项目 <span class="badge">${list.length}</span></h2>
      <div class="toolbar">
        <input class="search" id="projSearch" placeholder="搜索项目" value="${esc(state.search)}"/>
        <button type="button" class="btn sm" id="btnRefresh">刷新</button>
      </div>
    </div>
    <div class="project-grid">${
      list.length
        ? list
            .map((p) => {
              const m = (() => {
                try {
                  return typeof p.docs?.script_meta === "object"
                    ? p.docs.script_meta
                    : JSON.parse(p.docs?.script_meta_json || "{}");
                } catch (_) {
                  return {};
                }
              })();
              const st = m.parse_status === "done" ? "已解析" : m.assets_status === "done" ? "资产就绪" : "待处理";
              return `<div class="project-card" data-pid="${esc(p.id)}">
                <div class="thumb"><span class="status">${esc(st)}</span><span>剧本</span>
                  <span class="ep-count">${p.episode_count || 0} 集</span></div>
                <h4>${esc(p.name)}</h4>
                <div class="meta">${esc(p.style_prefix || m.art_style || "")} · ${esc(m.mode || "")}</div>
              </div>`;
            })
            .join("")
        : `<p class="muted">暂无项目，请先导入剧本。</p>`
    }</div>
  </div>`;
}

function renderParsing() {
  const p = state.parsing || {};
  const cols = p.cols || [
    { title: "角色解析", t: "00:00" },
    { title: "场景解析", t: "00:00" },
    { title: "道具解析", t: "00:00" },
  ];
  return `${topbar()}
  <div class="parse-screen">
    <h2>${esc(p.title || "剧集资产解析中")}</h2>
    <p class="muted">${esc(p.sub || "角色、场景和道具使用同一份全局风格与剧本理解")}</p>
    <div class="parse-cols">${cols
      .map(
        (c) => `<div class="parse-col">
        <div class="spinner"></div>
        <div>${esc(c.title)}</div>
        <div class="muted" style="font-size:0.78rem;margin-top:0.35rem">正在解析…</div>
        <div class="muted" style="font-size:0.72rem;margin-top:0.25rem">本轮已用时 ${esc(c.t)}</div>
      </div>`
      )
      .join("")}</div>
    <p class="muted">收到完整的资产设计后即可查看，已保存内容不会因中断丢失。</p>
    <button type="button" class="btn" id="btnParseBack">返回剧本，稍后查看</button>
  </div>`;
}

function renderProject() {
  const p = state.project;
  if (!p) return `${topbar()}<p class="muted" style="padding:2rem">加载中…</p>`;
  let body = "";
  if (state.step === "outline") body = renderOutline();
  else if (state.step === "assets") body = renderAssets();
  else body = renderVideos();
  const footerLeft =
    state.step === "outline"
      ? meta().parse_status === "done"
        ? "解析已完成"
        : "待解析剧本"
      : state.step === "assets"
        ? `${state.characters.length} 角色 · ${state.scenes.length} 场景 · ${state.props.length} 道具 · ${voiceReadyCount()}/${state.characters.length} 音色`
        : `共 ${state.episodes.length} 集`;
  const nextLab =
    state.step === "outline"
      ? "进入资产库 >"
      : state.step === "assets"
        ? "下一步 >"
        : "";
  return `${topbar()}
  ${subhead(p.name)}
  <div class="page-body">${body}</div>
  <div class="page-footer">
    <div class="muted">${esc(footerLeft)}</div>
    <div style="display:flex;gap:0.5rem">
      ${state.step !== "outline" ? `<button type="button" class="btn" id="btnPrev">上一步</button>` : ""}
      ${nextLab ? `<button type="button" class="btn primary" id="btnNext">${nextLab}</button>` : ""}
    </div>
  </div>`;
}

function renderOutline() {
  const m = meta();
  const genres = Array.isArray(m.genres) ? m.genres.join("、") : m.genres || "";
  const docs = state.project.docs || {};
  return `
  <div class="field-row">
    <div class="field">制作模式
      <select id="metaMode"><option ${m.mode === "默认版" ? "selected" : ""}>默认版</option>
      <option ${m.mode === "紧凑版" ? "selected" : ""}>紧凑版</option>
      <option ${m.mode === "精修版" ? "selected" : ""}>精修版</option></select>
    </div>
    <div class="field" style="flex:1;min-width:200px">画风
      <input id="metaStyle" value="${esc(m.art_style || state.project.style_prefix || "半写实3D国漫")}"/>
    </div>
    <div class="field">视频画幅
      <select id="metaRatio">
        <option value="16:9" ${m.aspect_ratio !== "9:16" ? "selected" : ""}>16:9</option>
        <option value="9:16" ${m.aspect_ratio === "9:16" ? "selected" : ""}>9:16</option>
      </select>
    </div>
    <div class="field">集数
      <input value="${state.project.episode_count || state.episodes.length} 集" disabled/>
    </div>
    <button type="button" class="btn primary" id="btnParseScript">解析剧本</button>
  </div>
  <div class="card">
    <div class="status-chip">${m.parse_status === "done" ? "解析已完成" : "待解析"}</div>
    <h3>剧本摘要</h3>
    <div class="summary-grid">
      <div class="summary-row"><div class="lab">故事梗概</div><div>${esc(m.synopsis || "导入后点击「解析剧本」生成摘要")}</div></div>
      <div class="summary-row"><div class="lab">题材类型</div><div>${esc(genres || "—")}</div></div>
      <div class="summary-row"><div class="lab">情感基调</div><div>${esc(m.tone || "—")}</div></div>
      <div class="summary-row"><div class="lab">时代背景</div><div>${esc(m.era || "—")}</div></div>
    </div>
  </div>
  <div class="card">
    <div class="section-head" style="margin-bottom:0.65rem">
      <h3 style="margin:0">分集剧本</h3>
      <button type="button" class="btn sm" id="btnExpandAll">全部展开</button>
    </div>
    <div class="ep-accordion">${(state.episodes || [])
      .map((e) => {
        const open = state.expandedEp === e.id || state.expandedEp === "all";
        return `<div class="ep-row" data-eid="${esc(e.id)}">
          <strong>第${String(e.ep_num).padStart(3, "0")}集｜${esc(e.title || "")}</strong>
          <span class="wc">${esc(e.outline || "")}</span>
        </div>
        ${open ? `<div class="ep-body">${esc(e.script_md || "（空）")}</div>` : ""}`;
      })
      .join("")}</div>
  </div>
  <details class="card"><summary class="muted">原文 / 总纲（可编辑）</summary>
    <label class="field" style="margin-top:0.75rem">总纲 outline_md
      <textarea id="outlineMd" rows="8">${esc(docs.outline_md || "")}</textarea>
    </label>
    <button type="button" class="btn sm primary" id="btnSaveOutline" style="margin-top:0.5rem">保存总纲</button>
  </details>`;
}

function voiceReadyCount() {
  return (state.characters || []).filter(
    (c) => (c.voice_desc || "").trim() || (c.voice_samples || []).length || (c.tts_voice || "").trim()
  ).length;
}

function renderAssets() {
  const voiceN = (state.characters || []).length;
  const tabs = [
    ["characters", "角色", state.characters.length],
    ["scenes", "场景", state.scenes.length],
    ["props", "道具", state.props.length],
    ["voices", "音色", voiceN],
  ];
  ensureSelectSets();
  const selAssetN = state.assetTab === "voices" ? 0 : selectedAssetsInCurrentTab().length;
  const assetToolbar = `
    <div class="toolbar">
      <button type="button" class="btn sm" id="btnSyncAssets" title="重新解析角色/场景/道具">同步分集资产</button>
      <button type="button" class="btn sm" id="btnParseAssets">重新解析</button>
      <button type="button" class="btn sm primary" id="btnEnrichDesc" title="补全资产描述、剧本摘要、分集梗概与缺失视频脚本">${selAssetN ? `整理已选 (${selAssetN})` : "补全内容"}</button>
      ${state.assetTab !== "voices" ? `<button type="button" class="btn sm" id="btnAddAsset">+ 添加</button>` : ""}
      <button type="button" class="btn sm" id="btnImgModel">生图模型设置</button>
      ${
        state.assetTab === "voices"
          ? `<span class="muted" style="font-size:0.8rem">已配置 ${voiceReadyCount()}/${(state.characters || []).length}</span>`
          : `<button type="button" class="btn sm" id="btnSelectAllAssets">全选本页</button>
             <button type="button" class="btn sm" id="btnClearAssetSel"${selAssetN ? "" : " disabled"}>清空所选</button>
             <span class="muted select-hint">${selAssetN ? `已选 ${selAssetN} · 生图/整理将逐个处理` : "可多选后批量生图/整理"}</span>
             <button type="button" class="btn sm primary" id="btnGenBlockImages">${selAssetN ? `生图已选 (${selAssetN})` : "生成块图"}</button>
             <button type="button" class="btn sm" id="btnGenBlockAll">补全缺图</button>`
      }
    </div>`;
  if (state.assetTab === "voices") {
    const list = state.characters || [];
    return `
  <div class="section-head">
    <div class="asset-tabs">${tabs
      .map(
        ([id, lab, n]) =>
          `<button type="button" class="asset-tab ${state.assetTab === id ? "active" : ""}" data-atab="${id}">${lab} ${n}</button>`
      )
      .join("")}</div>
    ${assetToolbar}
  </div>
  <div class="asset-grid">${
    list.length
      ? list
          .map((it) => {
            const samples = it.voice_samples || [];
            const hasText = !!(it.voice_desc || "").trim();
            const hasAudio = samples.length > 0;
            const status = hasAudio
              ? `音频音色 · ${samples.length} 段`
              : hasText
                ? "文字音色 · 已描述"
                : "待设置";
            const idCode = (it.id || "").slice(0, 6).toUpperCase();
            return `<div class="asset-card" data-aid="${esc(it.id)}" data-akind="voices">
              <div class="asset-thumb" style="font-size:1.4rem;color:var(--gold)">♪</div>
              <div>
                <h4>${esc(it.name)}</h4>
                <div class="meta">编号 ${esc(idCode)}<br/>${esc(status)}
                ${it.tts_voice ? `<br/>TTS：${esc(it.tts_voice)}` : ""}
                ${hasText ? `<br/>${esc((it.voice_desc || "").slice(0, 48))}${((it.voice_desc || "").length > 48) ? "…" : ""}` : ""}</div>
              </div>
            </div>`;
          })
          .join("")
      : `<p class="muted">暂无角色音色。请先解析角色资产，再在此为每位角色设置文字音色或上传参考音频。</p>`
  }</div>`;
  }
  const items =
    state.assetTab === "characters"
      ? state.characters
      : state.assetTab === "scenes"
        ? state.scenes
        : state.props;
  return `
  <div class="section-head">
    <div class="asset-tabs">${tabs
      .map(
        ([id, lab, n]) =>
          `<button type="button" class="asset-tab ${state.assetTab === id ? "active" : ""}" data-atab="${id}">${lab} ${n}</button>`
      )
      .join("")}</div>
    ${assetToolbar}
  </div>
  <div class="asset-grid">${
    items.length
      ? items
          .map((it) => {
            const imgs = it.ref_images || [];
            const thumb = imgs[0]?.path ? fileUrl(imgs[0].path) : it.ref_image ? fileUrl(it.ref_image) : "";
            const idCode = (it.id || "").slice(0, 6).toUpperCase();
            const imgN = imgs.length || (it.ref_image ? 1 : 0);
            const checked = state.selectedAssetIds.has(it.id);
            return `<div class="asset-card ${checked ? "selected" : ""}" data-aid="${esc(it.id)}" data-akind="${state.assetTab}">
              <label class="asset-check" data-stop="1" title="多选">
                <input type="checkbox" class="asset-select" data-select-asset="${esc(it.id)}" ${checked ? "checked" : ""}/>
              </label>
              <div class="asset-thumb">${thumb ? `<img src="${esc(thumb)}" alt=""/>` : "＋"}</div>
              <div>
                <h4>${esc(it.name)}</h4>
                <div class="meta">编号 ${esc(idCode)}<br/>
                ${(it.episodes || []).length ? `出现集 ${(it.episodes || []).join(",")}` : "全集"} · 已有图 ${imgN}
                ${!imgN ? ' · <span style="color:var(--run)">待生图</span>' : ""}
                <br/>${esc(((it.role_note || "（无描述，请点补全内容）")).slice(0, 72))}${((it.role_note || "").length > 72) ? "…" : ""}</div>
              </div>
            </div>`;
          })
          .join("")
      : `<p class="muted">暂无资产。请先在剧本大纲完成解析，再点「进入资产库」自动解析角色/场景/道具。</p>`
  }</div>`;
}

function epStatus(ep, plEp) {
  const d = plEp?.detail || {};
  const active = (state.pipeline?.active_jobs || []).find((j) => j.episode_id === ep.id);
  if (active) return { key: "run", text: active.message || "生成中…", generating: true };
  if (d.video_ready || ep.video_path) return { key: "done", text: "视频已生成", generating: false };
  if ((ep.storyboard_md || "").trim()) {
    const shots = d.shots_total || 0;
    const vids = d.shots_with_video || 0;
    return {
      key: shots && vids >= shots ? "done" : "wait",
      text: shots ? `脚本已生成 · 视频 ${vids}/${shots}` : "脚本已生成",
      generating: false,
    };
  }
  if ((ep.script_md || "").trim()) return { key: "wait", text: "待生成脚本", generating: false };
  return { key: "wait", text: "待处理", generating: false };
}

function renderVideos() {
  const plMap = Object.fromEntries(
    (state.pipeline?.episodes || []).map((x) => [x.episode_id, x])
  );
  const q = (document.getElementById("epSearch")?.value || state.epSearch || "").trim().toLowerCase();
  const eps = (state.episodes || []).filter((e) => {
    if (!q) return true;
    const t = `第${e.ep_num}集 ${e.title || ""}`;
    return t.toLowerCase().includes(q);
  });
  ensureSelectSets();
  const selEps = selectedEpisodes();
  const selEpN = selEps.length;
  const needScriptBase = (state.episodes || []).filter((e) => !(e.storyboard_md || "").trim());
  const hasScriptBase = (state.episodes || []).filter((e) => (e.storyboard_md || "").trim());
  const needScript = selEpN
    ? selEps.filter((e) => !(e.storyboard_md || "").trim()).length
    : needScriptBase.length;
  const hasScript = selEpN
    ? selEps.filter((e) => (e.storyboard_md || "").trim()).length
    : hasScriptBase.length;
  const jobsBanner = renderActiveJobsBanner(activeJobs());
  return `
  <div class="section-head">
    <h2 style="margin:0">共 ${state.episodes.length} 集${selEpN ? ` · 已选 ${selEpN}` : ""}</h2>
    <div class="toolbar">
      <input class="search" id="epSearch" placeholder="搜索分集" value="${esc(state.epSearch || "")}"/>
      <button type="button" class="btn sm" id="btnSelectAllEps">全选列表</button>
      <button type="button" class="btn sm" id="btnClearEpSel"${selEpN ? "" : " disabled"}>清空所选</button>
      <span class="muted select-hint">${selEpN ? "已选将逐个整理/生图/视频" : "可多选后批量处理"}</span>
      <button type="button" class="btn sm primary" id="btnCompleteScripts" title="补全缺失视频脚本与分集梗概">${selEpN ? `整理已选 (${selEpN})` : `补全缺失脚本${needScript ? ` (${needScript})` : ""}`}</button>
      <button type="button" class="btn sm" id="btnBatchScripts">${selEpN ? `脚本已选 (${selEpN})` : `批量生成脚本${needScript ? ` (${needScript})` : ""}`}</button>
      <button type="button" class="btn sm" id="btnBatchStills">${selEpN ? `分镜图已选 (${selEpN})` : "批量补缺分镜图"}</button>
      <button type="button" class="btn sm primary" id="btnBatchVideos">${selEpN ? `视频已选 (${hasScript})` : `批量生成视频${hasScript ? ` (${hasScript})` : ""}`}</button>
    </div>
  </div>
  ${jobsBanner}
  <div class="ep-grid">${eps
    .map((e) => {
      const st = epStatus(e, plMap[e.id]);
      const d = plMap[e.id]?.detail || {};
      const chN = state.characters.filter((c) => !c.episodes?.length || c.episodes.includes(e.ep_num)).length;
      const scN = state.scenes.length;
      const prN = state.props.filter((p) => !p.episodes?.length || p.episodes.includes(e.ep_num)).length;
      const hasSb = !!(e.storyboard_md || "").trim();
      const imgHint = d.shots_total ? `描述词 ${d.shots_with_image || 0}/${d.shots_total} · 视频 ${d.shots_with_video || 0}/${d.shots_total}` : "";
      const checked = state.selectedEpisodeIds.has(e.id);
      return `<div class="ep-card ${st.generating ? "generating" : ""} ${checked ? "selected" : ""}" data-open-ep="${esc(e.id)}" title="点击进入分集编辑">
        <label class="ep-check" data-stop="1" title="多选">
          <input type="checkbox" class="ep-select" data-select-ep="${esc(e.id)}" ${checked ? "checked" : ""}/>
        </label>
        <div class="thumb-vid">${e.video_path ? "▶ 成片" : st.generating ? `<div class="spinner"></div>` : "点击进入编辑"}</div>
        <div class="ep-title">第${String(e.ep_num).padStart(3, "0")}集｜${esc(e.title || "")}</div>
        <div class="ep-status ${st.key}">${esc(st.text)}</div>
        <div class="muted">${chN}角色 · ${scN}场 · ${prN}道具${imgHint ? `<br/>${esc(imgHint)}` : ""}</div>
        <div class="actions" data-stop="1">
          <button type="button" class="btn sm primary" data-open-ep="${esc(e.id)}">进入编辑</button>
          ${
            hasSb
              ? `<button type="button" class="btn sm" data-gen-still="${esc(e.id)}">生成分镜图</button>
                 <button type="button" class="btn sm" data-gen-vid="${esc(e.id)}">生成视频</button>
                 ${(d.shots_with_video || 0) > 0 ? `<button type="button" class="btn sm primary" data-merge-ep="${esc(e.id)}">合并成片</button>` : ""}`
              : `<button type="button" class="btn sm" data-gen-script="${esc(e.id)}">生成脚本</button>`
          }
          <button type="button" class="btn sm" data-reinfer="${esc(e.id)}">整集重新推理</button>
          ${hasSb && (d.shots_with_image || 0) < (d.shots_total || 0) ? `<button type="button" class="btn sm" data-continue-still="${esc(e.id)}">继续补图</button>` : ""}
        </div>
      </div>`;
    })
    .join("") || `<p class="muted">无匹配分集</p>`}</div>`;
}

function openEpEditor(episodeId) {
  if (!episodeId) return;
  state.view = "epEditor";
  state.epEditorId = episodeId;
  state.epEditorSeg = 0;
  state.epEditorScene = 0;
  state.epAssetTab = "clip";
  state.epAssetFilter = "all";
  state.selectedRefs = [];
  state.step = "videos";
  render();
}

/** 从 storyboard P 段解析：场 → 分段列表 */
function parseSceneClipTree(script, shots) {
  const segments = splitScriptSegments(script || "").filter((t) => /^P\d+｜/m.test(t.trim()));
  const clips = (segments.length ? segments : script ? [script] : []).map((t, i) => {
    const head = (t.match(/^P\d+｜[^\n]+/m) || [])[0] || "";
    const dm = head.match(/总时长[：:]\s*(\d+)\s*秒/);
    let dur = dm ? parseInt(dm[1], 10) : 15;
    if (!Number.isFinite(dur) || dur < 1) dur = 15;
    dur = Math.min(15, Math.max(12, dur));
    const sm = head.match(/场(\d+)段(\d+)/);
    const sceneIdx = sm ? parseInt(sm[1], 10) : 1;
    const segInScene = sm ? parseInt(sm[2], 10) : i + 1;
    // 标题·场景名｜场N段M
    let sceneName = "";
    const parts = head.replace(/^P\d+｜/, "").split("｜");
    if (parts[0]) {
      const bit = parts[0];
      const dot = bit.indexOf("·");
      sceneName = dot >= 0 ? bit.slice(dot + 1).trim() : bit.trim();
    }
    if (!sceneName) sceneName = `场${sceneIdx}`;
    const shot = (shots || []).find((s) => Number(s.shot_num) === i + 1) || null;
    return {
      i,
      label: `片段 ${segInScene}`,
      globalLabel: `片段 ${i + 1}`,
      sceneIdx,
      segInScene,
      sceneName,
      dur,
      shot,
      text: t.trim(),
      head,
    };
  });
  if (!clips.length) {
    clips.push({
      i: 0,
      label: "片段 1",
      globalLabel: "片段 1",
      sceneIdx: 1,
      segInScene: 1,
      sceneName: "本集场景",
      dur: 15,
      shot: null,
      text: script || "（暂无视频脚本，请点「整集重新推理」）",
      head: "",
    });
  }
  const sceneMap = new Map();
  for (const c of clips) {
    const key = c.sceneIdx;
    if (!sceneMap.has(key)) {
      sceneMap.set(key, {
        sceneIdx: key,
        name: c.sceneName,
        clips: [],
        totalSec: 0,
      });
    }
    const sc = sceneMap.get(key);
    sc.clips.push(c);
    sc.totalSec += c.dur || 15;
    if (c.sceneName && c.sceneName !== "本集场景") sc.name = c.sceneName;
  }
  const scenes = [...sceneMap.values()].sort((a, b) => a.sceneIdx - b.sceneIdx);
  return { clips, scenes, segments };
}

function renderEpEditorShell() {
  const ep = (state.episodes || []).find((e) => e.id === state.epEditorId);
  const title = ep
    ? `第${String(ep.ep_num).padStart(3, "0")}集｜${ep.title || ""}`
    : "分集编辑";
  return `${topbar()}
  <div class="subhead ep-subhead">
    <button type="button" class="btn ghost" id="btnBackEp">← 返回分集</button>
    <div class="subhead-title">${esc(state.project?.name || "")}</div>
    <div class="workflow">
      <button type="button" class="workflow-step" data-step="outline">剧本大纲</button>
      <button type="button" class="workflow-step" data-step="assets">资产库</button>
      <button type="button" class="workflow-step active" data-step="videos">分集视频</button>
    </div>
    <div class="subhead-actions">
      <span class="pill">${esc(title)}</span>
      <button type="button" class="btn sm" id="btnEpSettings">本集设置</button>
      <button type="button" class="btn sm" id="btnRegenScriptTop">整集重新推理</button>
      <button type="button" class="btn sm primary" id="btnBatchVideoTop" title="按场/段依次生成；已有段落自动跳过">继续补全 / 整集视频</button>
      <button type="button" class="btn sm primary" id="btnMergeEpisode" title="把本集已有分段视频按顺序拼成整集成片">合并成片</button>
    </div>
  </div>
  <div id="epEditorRoot" class="ep-editor"><p class="muted" style="padding:1rem">加载分集…</p></div>`;
}

function episodeScopedAssets(epNum, segText) {
  const text = segText || "";
  const chars = (state.characters || []).filter(
    (c) => !c.episodes?.length || c.episodes.includes(epNum)
  );
  const props = (state.props || []).filter(
    (p) => !p.episodes?.length || p.episodes.includes(epNum)
  );
  const scenes = state.scenes || [];
  const inClip = (name) => name && text.includes(name);
  return {
    all: { characters: chars, scenes, props },
    clip: {
      characters: chars.filter((c) => inClip(c.name)),
      scenes: scenes.filter((s) => inClip(s.name)),
      props: props.filter((p) => inClip(p.name)),
    },
  };
}

function renderEpAssetCard(kind, it) {
  const imgs = it.ref_images || [];
  const thumb = imgs[0]?.path ? fileUrl(imgs[0].path) : it.ref_image ? fileUrl(it.ref_image) : "";
  const hasImg = !!(thumb);
  const note = (it.role_note || "").trim();
  const status = hasImg ? `已有图 · ${imgs.length || 1}` : "仅文字 · 待补图";
  return `<div class="ep-asset-card" data-akind="${kind}" data-aid="${esc(it.id)}">
    <div class="ep-asset-thumb">${thumb ? `<img src="${esc(thumb)}" alt=""/>` : "＋"}</div>
    <div class="ep-asset-meta">
      <strong>${esc(it.name)}</strong>
      <div class="muted">${esc(status)}</div>
      ${note ? `<div class="muted ep-asset-note">${esc(note.slice(0, 42))}${note.length > 42 ? "…" : ""}</div>` : ""}
    </div>
  </div>`;
}

async function bindEpEditor() {
  const eid = state.epEditorId;
  const root = document.getElementById("epEditorRoot");
  if (!eid || !root) return;
  let ep;
  try {
    ep = await api(`/api/episodes/${eid}`);
  } catch (e) {
    root.innerHTML = `<p class="muted" style="padding:1rem">加载失败：${esc(e.message || e)}</p>`;
    return;
  }
  const shots = ep.shots || [];
  const script = ep.storyboard_md || "";
  const { clips: clipItems, scenes: sceneList, segments } = parseSceneClipTree(script, shots);

  // 若当前片段不在所选场内，自动对齐场号
  let curSeg = Math.min(state.epEditorSeg, Math.max(0, clipItems.length - 1));
  const curClip0 = clipItems[curSeg];
  if (state.epEditorScene > 0 && curClip0 && curClip0.sceneIdx !== state.epEditorScene) {
    const firstInScene = clipItems.find((c) => c.sceneIdx === state.epEditorScene);
    if (firstInScene) curSeg = firstInScene.i;
    else state.epEditorScene = curClip0.sceneIdx;
  }
  if (state.epEditorScene === 0 && curClip0) {
    // 默认停在当前片段所属场（便于看到场列表选中态）；0 表示「全部」时仍高亮所属场
  }
  state.epEditorSeg = curSeg;
  const cur = clipItems[curSeg];
  if (state.epEditorScene !== 0) state.epEditorScene = cur.sceneIdx;

  const visibleClips =
    state.epEditorScene > 0
      ? clipItems.filter((c) => c.sceneIdx === state.epEditorScene)
      : clipItems;

  const shot = cur.shot;
  const segText = cur.text || script;
  const scoped = episodeScopedAssets(ep.ep_num, state.epAssetTab === "clip" ? segText : script);
  const pack = state.epAssetTab === "material" ? { characters: [], scenes: [], props: [] } : scoped[state.epAssetTab === "all" ? "all" : "clip"];
  const filt = state.epAssetFilter;
  const showChars = filt === "all" || filt === "characters";
  const showScenes = filt === "all" || filt === "scenes";
  const showProps = filt === "all" || filt === "props";
  const totalAssets =
    (state.characters || []).length + (state.scenes || []).length + (state.props || []).length;
  const clipAssetN =
    scoped.clip.characters.length + scoped.clip.scenes.length + scoped.clip.props.length;
  const refs = state.selectedRefs.length
    ? state.selectedRefs
    : shot?.image_path
      ? [shot.image_path]
      : [];
  const sceneTabsHtml = `
    <div class="scene-strip" id="sceneStrip">
      <button type="button" class="scene-tab ${state.epEditorScene === 0 ? "active" : ""}" data-scene="0">
        <strong>全部场</strong>
        <span class="muted">${clipItems.length} 段 · ${clipItems.reduce((a, c) => a + (c.dur || 15), 0)}s</span>
      </button>
      ${sceneList
        .map(
          (sc) => `<button type="button" class="scene-tab ${state.epEditorScene === sc.sceneIdx ? "active" : ""}" data-scene="${sc.sceneIdx}">
            <strong>场${sc.sceneIdx}</strong>
            <span class="scene-name">${esc(sc.name)}</span>
            <span class="muted">${sc.clips.length} 段视频 · ${sc.totalSec}s</span>
          </button>`
        )
        .join("")}
    </div>`;

  const shotImgMtimes = ep.production?.shot_image_mtimes || {};
  const epJobs = activeJobs().filter((j) => j.episode_id === eid);
  const generatingShots = new Set();
  for (const j of epJobs) {
    if (j.status !== "running") continue;
    const cur = jobCurrentShot(j);
    const nums = jobShotNums(j);
    if (cur) {
      generatingShots.add(cur);
      // 不自动跳转当前分段，避免打断右侧视频预览播放
    } else if (nums.length) {
      nums.forEach((n) => generatingShots.add(n));
    }
  }
  const jobsBanner = renderActiveJobsBanner(epJobs);

  root.innerHTML = `
    <aside class="panel ep-left">
      <div class="panel-h">
        <div class="ep-asset-tabs">
          <button type="button" class="asset-tab ${state.epAssetTab === "all" ? "active" : ""}" data-epatab="all">全集资产 ${totalAssets}</button>
          <button type="button" class="asset-tab ${state.epAssetTab === "clip" ? "active" : ""}" data-epatab="clip">当前片段 ${clipAssetN}</button>
          <button type="button" class="asset-tab ${state.epAssetTab === "material" ? "active" : ""}" data-epatab="material">素材 0</button>
        </div>
      </div>
      <div class="panel-b">
        <div class="toolbar" style="margin-bottom:0.5rem">
          <button type="button" class="btn sm" id="btnSyncMissing">同步缺失资产</button>
          <button type="button" class="btn sm" id="btnGoAssets">前往资产库</button>
        </div>
        <h4 class="ep-sec">本集场列表 ${sceneList.length}</h4>
        <div class="ep-scene-list">
          ${sceneList
            .map(
              (sc) => `<button type="button" class="ep-scene-row ${state.epEditorScene === sc.sceneIdx ? "on" : ""}" data-scene="${sc.sceneIdx}">
                <div class="ep-scene-row-t">
                  <strong>场${sc.sceneIdx}</strong>
                  <span class="muted">${sc.clips.length} 段 · ${sc.totalSec}s</span>
                </div>
                <div class="ep-scene-row-n">${esc(sc.name)}</div>
              </button>`
            )
            .join("") || '<p class="muted">暂无场信息，请重新推理脚本</p>'}
        </div>
        <div class="ep-filter-chips">
          ${[
            ["all", "全部"],
            ["characters", "角色"],
            ["scenes", "场景"],
            ["props", "道具"],
          ]
            .map(
              ([id, lab]) =>
                `<button type="button" class="chip ${state.epAssetFilter === id ? "on" : ""}" data-epfilt="${id}">${lab}</button>`
            )
            .join("")}
        </div>
        ${
          state.epAssetTab === "material"
            ? `<p class="muted">暂无外部素材，可在资产库上传形象后使用。</p>`
            : `${
                showChars
                  ? `<h4 class="ep-sec">角色 ${pack.characters.length}</h4>
                     <div class="ep-asset-list">${pack.characters.map((c) => renderEpAssetCard("characters", c)).join("") || '<p class="muted">本范围无角色</p>'}</div>`
                  : ""
              }
              ${
                showScenes
                  ? `<h4 class="ep-sec">场景 ${pack.scenes.length}</h4>
                     <div class="ep-asset-list">${pack.scenes.map((s) => renderEpAssetCard("scenes", s)).join("") || '<p class="muted">本范围无场景</p>'}</div>`
                  : ""
              }
              ${
                showProps
                  ? `<h4 class="ep-sec">道具 ${pack.props.length}</h4>
                     <div class="ep-asset-list">${pack.props.map((p) => renderEpAssetCard("props", p)).join("") || '<p class="muted">本范围无道具</p>'}</div>`
                  : ""
              }`
        }
        <h4 class="ep-sec">多图参考（勾选）</h4>
        <div class="multi-ref" id="assetRefList">${collectAssetImages()
          .slice(0, 24)
          .map((a) => {
            const on = refs.includes(a.path);
            return `<div class="ref-chip ${on ? "selected" : ""}" data-path="${esc(a.path)}" title="${esc(a.label)}">
              <img src="${esc(fileUrl(a.path))}" alt=""/>
            </div>`;
          })
          .join("") || '<span class="muted">暂无资产图</span>'}</div>
      </div>
    </aside>
    <section class="panel ep-center">
      <div class="panel-h">
        <div>
          <strong>场${cur.sceneIdx} · ${esc(cur.sceneName)}</strong>
          <span class="muted" style="margin-left:0.5rem">${esc(cur.label)} · ${cur.dur || 15}秒 · 全集成 ${esc(cur.globalLabel)} · 段内多镜合并为 1 段成片</span>
        </div>
        <div style="display:flex;gap:0.35rem;flex-wrap:wrap">
          <button type="button" class="btn sm" id="btnRegenSeg">重新推理</button>
          <button type="button" class="btn sm" id="btnSaveSeg">保存</button>
          <button type="button" class="btn sm" id="btnCopySeg">复制</button>
        </div>
      </div>
      <div class="panel-b ep-center-body">
        ${jobsBanner}
        ${sceneTabsHtml}
        <textarea class="script-edit" id="segScript" spellcheck="false">${esc(segText)}</textarea>
        <div class="ep-gen-bar">
          <select id="vidModel"><option>MiniMax 多图生视频</option></select>
          <select id="vidRatio"><option>16:9</option><option>9:16</option></select>
          <span class="muted">720P · ${cur.dur || 15}s · 参考图 ${refs.length}</span>
          <button type="button" class="btn primary" id="btnGenSegVideo" ${generatingShots.has(Number(shot?.shot_num || curSeg + 1)) ? "disabled" : ""}>${generatingShots.has(Number(shot?.shot_num || curSeg + 1)) ? "本段生成中…" : "生成本段视频"}</button>
        </div>
        <div class="clip-nav-label muted">场${state.epEditorScene || cur.sceneIdx} 下视频分段（${visibleClips.length}）</div>
        <div class="clip-strip" id="clipStrip">
          ${visibleClips
            .map((c) => {
              const sn = c.shot?.shot_num || c.i + 1;
              const gen = generatingShots.has(Number(sn));
              const thumb = c.shot?.image_path
                ? `<img class="zoomable" src="${esc(
                    fileUrl(c.shot.image_path, shotImgMtimes[sn] || shotImgMtimes[String(sn)] || "")
                  )}" alt="${esc(c.label)}"/>`
                : c.shot?.video_path
                  ? "▶"
                  : "🎞";
              return `<button type="button" class="clip-card ${c.i === curSeg ? "active" : ""} ${gen ? "generating" : ""}" data-seg="${c.i}" data-shot="${esc(String(sn))}">
                <div class="clip-thumb">${thumb}${gen ? `<span class="clip-gen-tag">生成中</span>` : ""}</div>
                <div>${esc(c.label)}</div>
                <div class="muted">场${c.sceneIdx}段${c.segInScene}</div>
                <div class="muted">${gen ? "生成中…" : `${c.dur || 15}s`}</div>
              </button>`;
            })
            .join("")}
        </div>
      </div>
    </section>
    <aside class="panel ep-right">
      <div class="panel-h"><strong>视频预览</strong>
        <div style="display:flex;gap:0.35rem;align-items:center;flex-wrap:wrap">
          ${
            ep.video_path
              ? `<button type="button" class="btn sm primary" id="btnPlayFinal">整集成片</button>`
              : ""
          }
          ${
            shot?.video_path
              ? `<button type="button" class="btn sm" id="btnPlaySeg">本段视频</button>`
              : ""
          }
          <label class="muted" style="font-size:0.78rem;display:flex;align-items:center;gap:0.3rem">
            <input type="checkbox" id="chkContPlay"/> 连续播放
          </label>
        </div>
      </div>
      <div class="panel-b preview-empty" id="previewBox">
        ${
          ep.video_path || shot?.video_path
            ? `<div class="muted" id="previewLabel" style="margin-bottom:0.4rem;font-size:0.8rem"></div><video controls id="prevVideo" style="width:100%;max-height:70%"></video>`
            : `<div style="font-size:2rem">🎬</div><div>暂无视频</div><div class="muted">当前场「${esc(cur.sceneName)}」· ${esc(cur.label)}</div>`
        }
      </div>
    </aside>`;

  // 注意：ep-subhead 按钮在 #epEditorRoot 外，bindEpEditor 会反复调用。
  // 必须用 onclick= 覆盖，禁止 addEventListener 叠加（否则确认框会连弹）。
  const backEp = () => {
    state.view = "project";
    state.step = "videos";
    state.epEditorId = null;
    void openProject(state.projectId);
  };
  const btnBackEp = document.getElementById("btnBackEp");
  if (btnBackEp) btnBackEp.onclick = backEp;
  document.getElementById("btnGoAssets").onclick = () => {
    state.view = "project";
    state.step = "assets";
    state.epEditorId = null;
    render();
  };
  document.getElementById("btnSyncMissing").onclick = () => void parseAssets();
  const btnEpSettings = document.getElementById("btnEpSettings");
  if (btnEpSettings) btnEpSettings.onclick = () => showSettings();
  const btnRegenScriptTop = document.getElementById("btnRegenScriptTop");
  if (btnRegenScriptTop) btnRegenScriptTop.onclick = () => void genVideoScript(eid, true);
  const btnBatchVideoTop = document.getElementById("btnBatchVideoTop");
  if (btnBatchVideoTop) btnBatchVideoTop.onclick = () => void batchEpisodeVideos(eid);
  const btnMergeEpisode = document.getElementById("btnMergeEpisode");
  if (btnMergeEpisode) btnMergeEpisode.onclick = () => void mergeEpisodeVideo(eid);
  bindJobActionButtons(root);
  document.getElementById("btnRegenSeg").onclick = () => void genVideoScript(eid, true);
  const selectScene = (sceneNo) => {
    const n = parseInt(sceneNo, 10) || 0;
    state.epEditorScene = n;
    if (n > 0) {
      const first = clipItems.find((c) => c.sceneIdx === n);
      if (first) state.epEditorSeg = first.i;
    }
    void bindEpEditor();
  };
  root.querySelectorAll("[data-scene]").forEach((b) => {
    b.onclick = () => selectScene(b.dataset.scene);
  });
  root.querySelectorAll("[data-epatab]").forEach((b) => {
    b.onclick = () => {
      state.epAssetTab = b.dataset.epatab;
      void bindEpEditor();
    };
  });
  root.querySelectorAll("[data-epfilt]").forEach((b) => {
    b.onclick = () => {
      state.epAssetFilter = b.dataset.epfilt;
      void bindEpEditor();
    };
  });
  root.querySelectorAll(".ep-asset-card").forEach((c) => {
    c.onclick = () => showEditAsset(c.dataset.akind, c.dataset.aid);
  });
  root.querySelectorAll("[data-seg]").forEach((b) => {
    b.onclick = () => {
      const idx = parseInt(b.dataset.seg, 10) || 0;
      state.epEditorSeg = idx;
      const clip = clipItems[idx];
      if (clip) state.epEditorScene = clip.sceneIdx;
      void bindEpEditor();
    };
  });
  root.querySelectorAll("#assetRefList .ref-chip").forEach((el) => {
    el.onclick = () => {
      const p = el.dataset.path;
      const set = new Set(state.selectedRefs);
      if (set.has(p)) set.delete(p);
      else set.add(p);
      state.selectedRefs = [...set];
      void bindEpEditor();
    };
  });
  document.getElementById("btnCopySeg").onclick = async () => {
    try {
      await navigator.clipboard.writeText(document.getElementById("segScript").value || "");
      toast("已复制片段文本");
    } catch (_) {
      toast("复制失败");
    }
  };
  document.getElementById("btnGenSegVideo").onclick = async () => {
    const sn = shot?.shot_num || curSeg + 1;
    await startVideoJob(eid, {
      shot_nums: [sn],
      ref_image_paths: state.selectedRefs,
      confirm_full_episode: false,
    });
  };
  document.getElementById("btnSaveSeg").onclick = async () => {
    const text = document.getElementById("segScript").value;
    let full = ep.storyboard_md || "";
    if (segments.length && segments[curSeg] != null) {
      segments[curSeg] = text;
      full = segments.join("\n\n");
    } else full = text;
    await api(`/api/episodes/${eid}`, {
      method: "PATCH",
      body: JSON.stringify({ storyboard_md: full }),
    });
    const epIdx = (state.episodes || []).findIndex((e) => e.id === eid);
    if (epIdx >= 0) state.episodes[epIdx].storyboard_md = full;
    toast("已保存");
  };
  // workflow steps from ep editor
  document.querySelectorAll(".ep-subhead [data-step]").forEach((b) => {
    b.onclick = () => {
      state.view = "project";
      state.step = b.dataset.step;
      state.epEditorId = null;
      render();
    };
  });
  const playPreview = async (rel, label) => {
    if (!rel) return;
    try {
      const url = await mediaPlayUrl(rel);
      const v = document.getElementById("prevVideo");
      const lab = document.getElementById("previewLabel");
      if (v) {
        v.pause?.();
        v.removeAttribute("autoplay");
        v.src = url;
        v.load?.();
      }
      if (lab) lab.textContent = label || "";
    } catch (e) {
      toast(`加载失败：${e.message || e}`);
    }
  };
  // 有整集成片时默认播成片；否则播当前段
  if (ep.video_path) {
    void playPreview(ep.video_path, "正在播放：整集成片（全部段落拼接）");
  } else if (shot?.video_path) {
    void playPreview(shot.video_path, `正在播放：${cur.label}`);
  }
  document.getElementById("btnPlayFinal")?.addEventListener("click", () =>
    void playPreview(ep.video_path, "正在播放：整集成片（全部段落拼接）")
  );
  document.getElementById("btnPlaySeg")?.addEventListener("click", () =>
    void playPreview(shot?.video_path, `正在播放：${cur.label}`)
  );
  bindZoomableImages(root);
  if (state.jobsOpen) paintJobsDrawer();
}

function splitScriptSegments(text) {
  const t = text || "";
  const parts = t.split(/(?=^P\d+｜)/m).filter((x) => x.trim());
  return parts.length ? parts : t ? [t] : [];
}

function collectAssetImages() {
  const out = [];
  for (const c of state.characters || []) {
    for (const im of c.ref_images || []) {
      if (im.path) out.push({ path: im.path, label: `${c.name}/${im.label || ""}` });
    }
  }
  for (const s of state.scenes || []) {
    const imgs = s.ref_images || (s.ref_image ? [{ path: s.ref_image, label: s.name }] : []);
    for (const im of imgs) {
      if (im.path) out.push({ path: im.path, label: `${s.name}` });
    }
  }
  for (const p of state.props || []) {
    for (const im of p.ref_images || []) {
      if (im.path) out.push({ path: im.path, label: `${p.name}` });
    }
  }
  return out;
}

function bindCommon() {
  document.getElementById("btnSettings")?.addEventListener("click", showSettings);
  document.getElementById("btnJobs")?.addEventListener("click", () => void openJobsPanel());
  if (state.jobsOpen) {
    void refreshJobsList().then(() => {
      paintJobsDrawer();
      updateJobsBadge();
    });
  } else {
    updateJobsBadge();
  }
  document.getElementById("btnBack")?.addEventListener("click", () => {
    if (state.view === "epEditor") {
      state.view = "project";
      state.step = "videos";
      state.epEditorId = null;
      void openProject(state.projectId);
      return;
    }
    state.view = "home";
    state.projectId = null;
    stopPoll();
    void loadProjects().then(render);
  });
  document.querySelectorAll("[data-step]").forEach((b) => {
    b.onclick = () => {
      // 分集编辑页点工作流：退出编辑回到对应步骤
      if (state.view === "epEditor") {
        state.view = "project";
        state.epEditorId = null;
      }
      state.step = b.dataset.step;
      render();
    };
  });
}

function bindHome() {
  document.getElementById("btnRefresh").onclick = () => void loadProjects().then(render);
  document.getElementById("projSearch").oninput = (e) => {
    state.search = e.target.value;
    render();
  };
  document.querySelectorAll(".project-card").forEach((c) => {
    c.onclick = () => void openProject(c.dataset.pid);
  });
  const dz = document.getElementById("dropzone");
  const fi = document.getElementById("fileInput");
  document.getElementById("btnUpload").onclick = () => fi.click();
  fi.onchange = () => {
    if (fi.files?.[0]) void importFile(fi.files[0]);
  };
  dz.ondragover = (e) => {
    e.preventDefault();
    dz.classList.add("drag");
  };
  dz.ondragleave = () => dz.classList.remove("drag");
  dz.ondrop = (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    const f = e.dataTransfer?.files?.[0];
    if (f) void importFile(f);
  };
  document.getElementById("btnPaste").onclick = showPasteModal;
  document.getElementById("btnExample").onclick = showExample;
}

function bindParsing() {
  document.getElementById("btnParseBack").onclick = () => {
    state.view = "project";
    state.step = "outline";
    render();
  };
}

function bindProject() {
  document.getElementById("btnPrev")?.addEventListener("click", () => {
    if (state.step === "assets") state.step = "outline";
    else if (state.step === "videos") state.step = "assets";
    render();
  });
  document.getElementById("btnNext")?.addEventListener("click", () => void onNext());
  document.getElementById("btnParseScript")?.addEventListener("click", () => void parseScript());
  document.getElementById("btnExpandAll")?.addEventListener("click", () => {
    state.expandedEp = state.expandedEp === "all" ? null : "all";
    render();
  });
  document.querySelectorAll(".ep-row").forEach((r) => {
    r.onclick = () => {
      state.expandedEp = state.expandedEp === r.dataset.eid ? null : r.dataset.eid;
      render();
    };
  });
  document.getElementById("btnSaveOutline")?.addEventListener("click", async () => {
    await api(`/api/projects/${state.projectId}`, {
      method: "PATCH",
      body: JSON.stringify({ outline_md: document.getElementById("outlineMd").value }),
    });
    toast("已保存");
  });
  document.querySelectorAll("[data-atab]").forEach((t) => {
    t.onclick = () => {
      state.assetTab = t.dataset.atab;
      render();
    };
  });
  document.getElementById("btnParseAssets")?.addEventListener("click", () => void parseAssets());
  document.getElementById("btnSyncAssets")?.addEventListener("click", () => void parseAssets());
  document.getElementById("btnEnrichDesc")?.addEventListener("click", () => void completeContent());
  document.getElementById("btnAddAsset")?.addEventListener("click", () => showAddAsset());
  document.getElementById("btnImgModel")?.addEventListener("click", () => showSettings());
  document.getElementById("btnGenBlockImages")?.addEventListener("click", () =>
    void batchGenAssetImages(false)
  );
  document.getElementById("btnGenBlockAll")?.addEventListener("click", () =>
    void batchGenAssetImages(true)
  );
  document.getElementById("btnSelectAllAssets")?.addEventListener("click", () => {
    ensureSelectSets();
    currentKindAssets().forEach((it) => state.selectedAssetIds.add(it.id));
    render();
  });
  document.getElementById("btnClearAssetSel")?.addEventListener("click", () => {
    ensureSelectSets();
    const keep = new Set(
      [...state.selectedAssetIds].filter((id) => !currentKindAssets().some((it) => it.id === id))
    );
    state.selectedAssetIds = keep;
    render();
  });
  document.querySelectorAll(".asset-select").forEach((cb) => {
    cb.onclick = (ev) => ev.stopPropagation();
    cb.onchange = (ev) => {
      ev.stopPropagation();
      toggleSelectedAsset(cb.dataset.selectAsset);
      render();
    };
  });
  document.querySelectorAll(".asset-check").forEach((lab) => {
    lab.onclick = (ev) => ev.stopPropagation();
  });
  document.querySelectorAll(".asset-card").forEach((c) => {
    c.onclick = (ev) => {
      if (ev.target.closest("[data-stop], .asset-check, .asset-select")) return;
      if (c.dataset.akind === "voices") void showEditVoice(c.dataset.aid);
      else showEditAsset(c.dataset.akind, c.dataset.aid);
    };
  });
  bindZoomableImages(document.querySelector(".page-body") || document);
  bindVideoPageEvents();
}

function bindVideoPageEvents() {
  const epSearch = document.getElementById("epSearch");
  if (epSearch) {
    epSearch.oninput = (e) => {
      state.epSearch = e.target.value;
      if (state.step !== "videos") return;
      const body = document.querySelector(".page-body");
      if (!body) return;
      const pos = e.target.selectionStart;
      body.innerHTML = renderVideos();
      bindVideoPageEvents();
      const inp = document.getElementById("epSearch");
      if (inp) {
        inp.focus();
        try {
          inp.setSelectionRange(pos, pos);
        } catch (_) {}
      }
    };
  }
  const bc = document.getElementById("btnCompleteScripts");
  if (bc) bc.onclick = () => void completeContent({ assets: false, outline: true, scripts: true });
  const bs = document.getElementById("btnBatchScripts");
  if (bs) bs.onclick = () => void batchGenScripts();
  const bv = document.getElementById("btnBatchVideos");
  if (bv) bv.onclick = () => void batchGenVideos();
  const bt = document.getElementById("btnBatchStills");
  if (bt) bt.onclick = () => void batchGenStills();
  document.getElementById("btnSelectAllEps")?.addEventListener("click", () => {
    ensureSelectSets();
    const q = (document.getElementById("epSearch")?.value || state.epSearch || "").trim().toLowerCase();
    (state.episodes || []).forEach((e) => {
      if (q) {
        const t = `第${e.ep_num}集 ${e.title || ""}`;
        if (!t.toLowerCase().includes(q)) return;
      }
      state.selectedEpisodeIds.add(e.id);
    });
    render();
  });
  document.getElementById("btnClearEpSel")?.addEventListener("click", () => {
    ensureSelectSets();
    state.selectedEpisodeIds = new Set();
    render();
  });
  document.querySelectorAll(".ep-select").forEach((cb) => {
    cb.onclick = (ev) => ev.stopPropagation();
    cb.onchange = (ev) => {
      ev.stopPropagation();
      toggleSelectedEpisode(cb.dataset.selectEp);
      render();
    };
  });
  document.querySelectorAll(".ep-check").forEach((lab) => {
    lab.onclick = (ev) => ev.stopPropagation();
  });
  document.querySelectorAll("[data-gen-script]").forEach((b) => {
    b.onclick = () => void genVideoScript(b.dataset.genScript);
  });
  document.querySelectorAll("[data-reinfer]").forEach((b) => {
    b.onclick = () => void genVideoScript(b.dataset.reinfer, true);
  });
  document.querySelectorAll(".ep-card[data-open-ep]").forEach((card) => {
    card.onclick = (ev) => {
      if (ev.target.closest("[data-stop], .ep-check, .ep-select")) return;
      const btn = ev.target.closest("button");
      if (btn && !btn.hasAttribute("data-open-ep")) return; // 其它操作按钮自行处理
      openEpEditor(card.dataset.openEp);
    };
  });
  document.querySelectorAll("button[data-open-ep]").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      openEpEditor(b.dataset.openEp);
    };
  });
  document.querySelectorAll("[data-gen-vid]").forEach((b) => {
    b.onclick = () => void batchEpisodeVideos(b.dataset.genVid);
  });
  document.querySelectorAll("[data-merge-ep]").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      void mergeEpisodeVideo(b.dataset.mergeEp);
    };
  });
  document.querySelectorAll("[data-gen-still]").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      void startStillJob(b.dataset.genStill, { mode: "generate" });
    };
  });
  document.querySelectorAll("[data-continue-still]").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      void startStillJob(b.dataset.continueStill, { mode: "fill" });
    };
  });
  bindZoomableImages(document.querySelector(".page-body") || document);
}

async function onNext() {
  if (state.step === "outline") {
    if (meta().assets_status !== "done" && !state.characters.length) {
      await parseAssets();
    } else {
      state.step = "assets";
      render();
    }
  } else if (state.step === "assets") {
    state.step = "videos";
    render();
  }
}

async function loadProjects() {
  const r = await api("/api/projects");
  // list may be slim — fetch light fields only
  state.projects = r.projects || [];
}

async function openProject(pid) {
  state.projectId = pid;
  state.view = "project";
  state.project = await api(`/api/projects/${pid}`);
  state.pipeline = await api(`/api/projects/${pid}/pipeline`);
  const [ch, sc, pr, eps] = await Promise.all([
    api(`/api/projects/${pid}/characters`),
    api(`/api/projects/${pid}/scenes`),
    api(`/api/projects/${pid}/props`),
    api(`/api/projects/${pid}/episodes`),
  ]);
  state.characters = ch.items || [];
  state.scenes = sc.items || [];
  state.props = pr.items || [];
  state.episodes = eps.episodes || [];
  const m = meta();
  state.step = m.workflow_step === "videos" ? "videos" : m.workflow_step === "assets" ? "assets" : "outline";
  startPoll();
  render();
}

async function importFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("name", file.name.replace(/\.[^.]+$/, ""));
  fd.append("art_style", document.getElementById("importStyle")?.value || "半写实3D国漫");
  fd.append("aspect_ratio", document.getElementById("importRatio")?.value || "16:9");
  fd.append("mode", document.getElementById("importMode")?.value || "默认版");
  toast("正在导入…");
  try {
    const proj = await api("/api/studio/import-file", { method: "POST", body: fd });
    toast("导入成功，开始解析剧本…");
    await openProject(proj.id);
    await parseScript();
  } catch (e) {
    alert(`导入失败：${e.message || e}`);
  }
}

async function importText(text, name) {
  toast("正在导入…");
  try {
    const proj = await api("/api/studio/import", {
      method: "POST",
      body: JSON.stringify({
        name: name || "",
        novel_text: text,
        art_style: document.getElementById("importStyle")?.value || "半写实3D国漫",
        aspect_ratio: document.getElementById("importRatio")?.value || "16:9",
        mode: document.getElementById("importMode")?.value || "默认版",
      }),
    });
    await openProject(proj.id);
    await parseScript();
  } catch (e) {
    alert(`导入失败：${e.message || e}`);
  }
}

async function parseScript() {
  const useLlm = !!state.config?.storyboard_image_api_ready || !!state.config?.ohmygpt?.key_configured;
  state.view = "parsing";
  state.parsing = {
    title: "剧本解析中",
    sub: "正在生成剧本摘要与分集脚本结构",
    cols: [
      { title: "摘要解析", t: "00:00" },
      { title: "分集切分", t: "00:00" },
      { title: "风格对齐", t: "00:00" },
    ],
  };
  render();
  const t0 = Date.now();
  const tick = setInterval(() => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    const mm = String(Math.floor(sec / 60)).padStart(2, "0");
    const ss = String(sec % 60).padStart(2, "0");
    state.parsing.cols = state.parsing.cols.map((c) => ({ ...c, t: `${mm}:${ss}` }));
    render();
  }, 1000);
  try {
    // 保存画风等
    const style = document.getElementById("metaStyle")?.value;
    const ratio = document.getElementById("metaRatio")?.value;
    const mode = document.getElementById("metaMode")?.value;
    if (style || ratio || mode) {
      const m = { ...meta(), art_style: style || meta().art_style, aspect_ratio: ratio || meta().aspect_ratio, mode: mode || meta().mode };
      await api(`/api/projects/${state.projectId}`, {
        method: "PATCH",
        body: JSON.stringify({
          style_prefix: m.art_style,
          outline_md: state.project.docs?.outline_md,
        }),
      });
      // script_meta via docs — patch only outline; update meta through parse
    }
    await api(`/api/projects/${state.projectId}/studio/parse-script`, {
      method: "POST",
      body: JSON.stringify({ use_llm: useLlm }),
    });
    clearInterval(tick);
    await openProject(state.projectId);
    state.step = "outline";
    toast("剧本解析完成");
  } catch (e) {
    clearInterval(tick);
    state.view = "project";
    render();
    alert(`解析失败：${e.message || e}`);
  }
}

async function parseAssets() {
  const useLlm = !!state.config?.ohmygpt?.key_configured || !!state.config?.storyboard_image_api_ready;
  state.view = "parsing";
  state.parsing = {
    title: "剧集资产解析中",
    sub: "角色、场景和道具使用同一份全局风格与剧本理解",
    cols: [
      { title: "角色解析", t: "00:00" },
      { title: "场景解析", t: "00:00" },
      { title: "道具解析", t: "00:00" },
    ],
  };
  render();
  const t0 = Date.now();
  const tick = setInterval(() => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    const mm = String(Math.floor(sec / 60)).padStart(2, "0");
    const ss = String(sec % 60).padStart(2, "0");
    state.parsing.cols = state.parsing.cols.map((c) => ({ ...c, t: `${mm}:${ss}` }));
    render();
  }, 1000);
  try {
    // 启发式即带完整描述；再用 enrich 接口 LLM 润色（失败不影响已有描述）
    await api(`/api/projects/${state.projectId}/studio/parse-assets`, {
      method: "POST",
      body: JSON.stringify({ use_llm: false }),
    });
    if (useLlm) {
      try {
        await api(`/api/projects/${state.projectId}/studio/enrich-descriptions`, {
          method: "POST",
          body: JSON.stringify({ use_llm: true }),
        });
      } catch (_) {
        /* 描述已由剧本上下文补全 */
      }
    }
    clearInterval(tick);
    await openProject(state.projectId);
    state.step = "assets";
    toast("资产解析完成");
  } catch (e) {
    clearInterval(tick);
    // 网关断开时服务端可能已写入，尝试刷新
    try {
      await openProject(state.projectId);
      state.step = "assets";
      if ((state.characters || []).length || (state.scenes || []).length) {
        toast("连接中断，已加载已解析资产");
        return;
      }
    } catch (_) {}
    state.view = "project";
    state.step = "assets";
    render();
    alert(`资产解析失败：${e.message || e}`);
  }
}

async function completeContent(opts = {}) {
  ensureSelectSets();
  const selAssets = state.step === "assets" ? selectedAssetsInCurrentTab().map((x) => x.id) : [];
  const selEps = state.step === "videos" ? selectedEpisodeIdList() : [];
  const hasAssetSel = selAssets.length > 0;
  const hasEpSel = selEps.length > 0;
  let includeAssets = opts.assets !== false;
  let includeOutline = opts.outline !== false;
  let includeScripts = opts.scripts !== false;
  if (hasAssetSel) {
    includeAssets = true;
    includeOutline = false;
    includeScripts = false;
  } else if (hasEpSel) {
    includeAssets = false;
    includeOutline = false;
    includeScripts = true;
  }
  const useLlm = !!state.config?.ohmygpt?.key_configured || !!state.config?.storyboard_image_api_ready;
  const stayStep = state.step === "videos" ? "videos" : "assets";
  const scopeHint = hasAssetSel
    ? `已选 ${selAssets.length} 个资产，将逐个整理描述`
    : hasEpSel
      ? `已选 ${selEps.length} 集，将逐个整理脚本`
      : "补全资产描述、分集梗概与缺失视频脚本（含镜头画面/台词）";
  if (hasAssetSel || hasEpSel) {
    if (!confirm(`${scopeHint}。是否继续？`)) return;
  }
  toast(hasAssetSel || hasEpSel ? "正在逐个整理…" : "正在补全资产描述 / 剧本摘要 / 视频脚本…");
  state.view = "parsing";
  state.parsing = {
    title: "内容补全中",
    sub: scopeHint,
    cols: [
      { title: hasAssetSel ? "资产描述" : "资产描述", t: "00:00" },
      { title: "剧本摘要", t: "00:00" },
      { title: hasEpSel ? "视频脚本(逐集)" : "视频脚本", t: "00:00" },
    ],
  };
  render();
  const t0 = Date.now();
  const tick = setInterval(() => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    const mm = String(Math.floor(sec / 60)).padStart(2, "0");
    const ss = String(sec % 60).padStart(2, "0");
    state.parsing.cols = state.parsing.cols.map((c) => ({ ...c, t: `${mm}:${ss}` }));
    render();
  }, 1000);
  try {
    let r = { video_scripts: { generated: 0 }, episode_outlines: 0 };
    if (hasEpSel && includeScripts) {
      // 分集多选：前端逐集调用，严格串行
      let ok = 0;
      let fail = 0;
      for (let i = 0; i < selEps.length; i++) {
        const eid = selEps[i];
        const ep = (state.episodes || []).find((e) => e.id === eid);
        state.parsing.sub = `整理脚本 ${i + 1}/${selEps.length} · 第${ep?.ep_num ?? "?"}集`;
        render();
        try {
          await api(`/api/projects/${state.projectId}/studio/complete-content`, {
            method: "POST",
            body: JSON.stringify({
              use_llm: false,
              include_assets: false,
              include_outline: false,
              include_video_scripts: true,
              only_missing_scripts: true,
              script_limit: 1,
              episode_ids: [eid],
            }),
          });
          ok += 1;
        } catch (_) {
          fail += 1;
        }
      }
      r = { video_scripts: { generated: ok, failed: fail }, episode_outlines: 0 };
    } else if (hasAssetSel && includeAssets) {
      // 资产多选：逐个补全描述
      let ok = 0;
      let fail = 0;
      for (let i = 0; i < selAssets.length; i++) {
        const aid = selAssets[i];
        const it = currentKindAssets().find((x) => x.id === aid);
        state.parsing.sub = `整理资产 ${i + 1}/${selAssets.length} · ${it?.name || aid.slice(0, 6)}`;
        render();
        try {
          await api(`/api/projects/${state.projectId}/studio/complete-content`, {
            method: "POST",
            body: JSON.stringify({
              use_llm: false,
              include_assets: true,
              include_outline: false,
              include_video_scripts: false,
              asset_ids: [aid],
            }),
          });
          if (useLlm) {
            try {
              await api(`/api/projects/${state.projectId}/studio/enrich-descriptions`, {
                method: "POST",
                body: JSON.stringify({ use_llm: true, asset_ids: [aid] }),
              });
            } catch (_) {}
          }
          ok += 1;
        } catch (_) {
          fail += 1;
        }
      }
      r = { video_scripts: { generated: 0 }, episode_outlines: 0, _assetOk: ok, _assetFail: fail };
    } else {
      r = await api(`/api/projects/${state.projectId}/studio/complete-content`, {
        method: "POST",
        body: JSON.stringify({
          use_llm: false,
          include_assets: includeAssets,
          include_outline: includeOutline,
          include_video_scripts: includeScripts,
          only_missing_scripts: true,
          script_limit: 20,
        }),
      });
      if (useLlm && includeAssets) {
        try {
          await api(`/api/projects/${state.projectId}/studio/enrich-descriptions`, {
            method: "POST",
            body: JSON.stringify({ use_llm: true }),
          });
        } catch (_) {}
      }
    }
    clearInterval(tick);
    await openProject(state.projectId);
    state.step = stayStep;
    const vs = r.video_scripts || {};
    if (hasAssetSel) {
      toast(`资产整理完成：成功 ${r._assetOk || 0}，失败 ${r._assetFail || 0}`);
    } else {
      toast(
        `补全完成：视频脚本 ${vs.generated || 0} 集 · 分集梗概 ${r.episode_outlines || 0} · 资产已更新`
      );
    }
  } catch (e) {
    clearInterval(tick);
    try {
      await openProject(state.projectId);
      state.step = stayStep;
    } catch (_) {
      state.view = "project";
      state.step = stayStep;
      render();
    }
    alert(`补全失败：${e.message || e}`);
  }
}

async function enrichDescriptions() {
  return completeContent({ assets: true, outline: true, scripts: true });
}

function setBusyOverlay(on, text) {
  let el = document.getElementById("busyOverlay");
  if (!on) {
    if (el) el.classList.add("hidden");
    document.querySelectorAll("[data-busy-disable]").forEach((b) => {
      b.disabled = false;
    });
    return;
  }
  if (!el) {
    el = document.createElement("div");
    el.id = "busyOverlay";
    el.className = "busy-overlay";
    el.innerHTML = `<div class="busy-card"><div class="spinner"></div><div class="busy-text"></div><div class="muted busy-sub">请勿关闭页面，完成后会自动刷新</div></div>`;
    document.body.appendChild(el);
  }
  el.querySelector(".busy-text").textContent = text || "处理中…";
  el.classList.remove("hidden");
  [
    "btnRegenScriptTop",
    "btnRegenSeg",
    "btnBatchVideoTop",
    "btnMergeEpisode",
    "btnGenSegVideo",
    "btnCompleteScripts",
    "btnBatchScripts",
    "btnBatchStills",
    "btnBatchVideos",
    "btnGenBlockImages",
    "btnGenBlockAll",
    "btnEnrichDesc",
  ].forEach((id) => {
    const b = document.getElementById(id);
    if (b) {
      b.dataset.busyDisable = "1";
      b.disabled = true;
    }
  });
  document.querySelectorAll("[data-reinfer], [data-gen-script]").forEach((b) => {
    b.dataset.busyDisable = "1";
    b.disabled = true;
  });
}

async function genVideoScript(episodeId, force) {
  const stayEditor = state.view === "epEditor" && state.epEditorId === episodeId;
  const useLlm = !!state.config?.ohmygpt?.key_configured || !!state.config?.storyboard_image_api_ready || !!force;
  setBusyOverlay(true, useLlm ? "整集重新推理中（约 1–3 分钟）…" : "正在生成视频脚本…");
  toast(useLlm ? "整集重新推理已开始…" : "正在生成视频脚本…");
  try {
    const r = await api(`/api/projects/${state.projectId}/episodes/${episodeId}/studio/video-script`, {
      method: "POST",
      body: JSON.stringify({ use_llm: useLlm }),
    });
    await openProject(state.projectId);
    if (stayEditor) {
      openEpEditor(episodeId);
    } else {
      state.step = "videos";
      render();
    }
    const n = r?.shot_count || (state.episodes || []).length;
    toast(`视频脚本已生成${r?.shot_count ? ` · ${r.shot_count} 段` : ""}`);
    if (r?.shot_count) {
      alert(
        `整集重新推理完成：共 ${r.shot_count} 段视频脚本。\n` +
          `段内多个「镜头」会合并进同一段成片。\n` +
          `若段数增加，请先「生成分镜图 / 批量补缺分镜图」，再生成视频。`
      );
    }
  } catch (e) {
    alert(`重新推理失败：${e.message || e}`);
  } finally {
    setBusyOverlay(false);
  }
}

function friendlyJobError(msg) {
  const s = String(msg || "");
  const low = s.toLowerCase();
  if (low.includes("insufficient_balance") || low.includes("insufficient balance") || s.includes("余额不足")) {
    return "MiniMax 账户余额不足，请充值后再生成视频。";
  }
  if (s.includes("还没有分镜图")) return s;
  if (s.includes("正在运行") || s.includes("进行中") || low.includes("already")) {
    return "已有视频任务进行中，请打开右上角「任务记录」查看，或等当前任务结束后再试。";
  }
  return s;
}

async function mergeEpisodeVideo(episodeId) {
  let ep;
  try {
    ep = await api(`/api/episodes/${episodeId}`);
  } catch (e) {
    alert(`读取分集失败：${e.message || e}`);
    return;
  }
  const shots = ep.shots || [];
  const have = shots.filter((s) => (s.video_path || "").trim());
  const miss = shots.filter((s) => !(s.video_path || "").trim()).map((s) => s.shot_num);
  if (!have.length) {
    alert("本集还没有任何分段视频，请先「批量生成本集视频」。");
    return;
  }
  if (miss.length) {
    if (
      !confirm(
        `还有 ${miss.length} 段没有视频（镜 ${miss.join("、")}）。\n` +
          `继续将只拼接已有 ${have.length} 段；缺镜会导致合并失败。\n\n建议先补齐视频。仍要尝试合并吗？`
      )
    ) {
      return;
    }
  } else if (
    !confirm(`将按镜号顺序拼接本集 ${have.length} 段视频成整集成片，确定？`)
  ) {
    return;
  }
  try {
    const job = await api(`/api/projects/${state.projectId}/jobs`, {
      method: "POST",
      body: JSON.stringify({
        stage_id: "video",
        episode_id: episodeId,
        params: { mux_only: true, confirm_full_episode: true },
      }),
    });
    toast(`已提交合并成片 ${String(job.id || "").slice(0, 8)}…`);
    state.jobsOpen = true;
    await refreshPipeline();
    await refreshJobsList();
    updateJobsBadge();
    paintJobsDrawer();
    if (state.view === "epEditor") void bindEpEditor();
    else render();
  } catch (e) {
    alert(`合并成片失败：${friendlyJobError(e.message || e)}`);
  }
}

let _batchEpisodeVideosLock = false;

async function batchEpisodeVideos(episodeId) {
  // 防重入：轮询重绑/连点会导致多个 confirm 叠弹
  if (_batchEpisodeVideosLock) return;
  _batchEpisodeVideosLock = true;
  try {
    await _batchEpisodeVideosInner(episodeId);
  } finally {
    _batchEpisodeVideosLock = false;
  }
}

async function _batchEpisodeVideosInner(episodeId) {
  // 预检：缺分镜图先提示补图，避免点了才报错
  let ep;
  try {
    ep = await api(`/api/episodes/${episodeId}`);
  } catch (e) {
    alert(`读取分集失败：${e.message || e}`);
    return;
  }
  const shots = ep.shots || [];
  const missingImg = shots
    .filter((s) => !(s.image_path || "").trim())
    .map((s) => s.shot_num);
  const needVid = shots
    .filter((s) => !(s.video_path || "").trim())
    .map((s) => s.shot_num);
  if (missingImg.length) {
    if (
      confirm(
        `有 ${missingImg.length} 段还没有分镜图（镜 ${missingImg.join("、")}）。\n` +
          `需要先补分镜图才能批量生成视频。\n\n点「确定」提交「批量补缺分镜图」，完成后请再点「批量生成本集视频」。`
      )
    ) {
      await startStillJob(episodeId, { mode: "fill" });
    }
    return;
  }
  if (!needVid.length) {
    toast("本集分镜视频已齐全，无需批量生成");
    return;
  }
  if (
    !confirm(
      `将按「场→段」依次补生成 ${needVid.length} 段视频（跳过已有）。\n` +
        `一段完成后会立刻出现在工作台，再自动进入下一段。\n` +
        `中途可点「停止」，之后可「继续补全」。是否开始？`
    )
  ) {
    return;
  }
  await startVideoJob(episodeId, {
    confirm_full_episode: true,
    auto_concat: false,
    skip_existing: true,
    segment_serial: true,
    preview_with_audio: true,
  });
}

async function startVideoJob(episodeId, params) {
  try {
    const job = await api(`/api/projects/${state.projectId}/jobs`, {
      method: "POST",
      body: JSON.stringify({
        stage_id: "video",
        episode_id: episodeId,
        params: params || {
          confirm_full_episode: true,
          auto_concat: false,
          skip_existing: true,
        },
      }),
    });
    toast(`已开始按场/段依次生成 ${String(job.id || "").slice(0, 8)}…（可随时停止）`);
    state.jobsOpen = true;
    await refreshPipeline();
    await refreshJobsList();
    updateJobsBadge();
    paintJobsDrawer();
    if (state.view === "epEditor") void bindEpEditor();
    else render();
  } catch (e) {
    alert(`提交失败：${friendlyJobError(e.message || e)}`);
  }
}

async function startStillJob(episodeId, opts = {}) {
  const mode = opts.mode || "generate"; // generate=可重出；fill=只补缺
  try {
    const ep =
      (state.episodes || []).find((e) => e.id === episodeId) ||
      (await api(`/api/episodes/${episodeId}`).catch(() => null));
    const d = ep?.production || {};
    const total = Number(d.shots_total || (ep?.shots || []).length || 0);
    const have = Number(d.shots_with_image || 0);
    let skipExisting = mode === "fill";
    let freshImages = false;

    if (mode === "generate" && have > 0) {
      const missing = Math.max(0, total - have);
      const msg =
        missing > 0
          ? `本集已有 ${have}/${total || "?"} 张分镜图，还缺 ${missing} 张。\n\n` +
            `点「确定」：全部重新生成（覆盖已有图）\n` +
            `点「取消」：仅补缺（不改已有图）`
          : `本集 ${have} 张分镜图已齐全。\n\n点「确定」将全部重新生成并覆盖；点「取消」则不提交。`;
      if (missing > 0) {
        if (confirm(msg)) {
          skipExisting = false;
          freshImages = true;
        } else {
          skipExisting = true;
          freshImages = false;
        }
      } else if (!confirm(msg)) {
        return;
      } else {
        skipExisting = false;
        freshImages = true;
      }
    }

    const job = await api(`/api/projects/${state.projectId}/jobs`, {
      method: "POST",
      body: JSON.stringify({
        stage_id: "storyboard_images",
        episode_id: episodeId,
        params: {
          confirm_full_episode: true,
          skip_existing: skipExisting,
          fresh_images: freshImages,
        },
      }),
    });
    toast(
      freshImages
        ? `已提交全部重出分镜图 ${String(job.id || "").slice(0, 8)}…`
        : `已提交分镜出图 ${String(job.id || "").slice(0, 8)}…`
    );
    await refreshPipeline();
    await refreshJobsList();
    updateJobsBadge();
    if (state.jobsOpen) paintJobsDrawer();
    if (state.view === "epEditor") void bindEpEditor();
    else render();
  } catch (e) {
    alert(`提交失败：${e.message || e}`);
  }
}

function currentAssetKindForImages() {
  if (state.assetTab === "scenes") return "scenes";
  if (state.assetTab === "props") return "props";
  return "characters";
}

async function batchGenAssetImages(forceAll) {
  const kind = currentAssetKindForImages();
  if (state.assetTab === "voices") {
    toast("请切换到角色/场景/道具后再生成块图");
    return;
  }
  if (!state.config?.ohmygpt?.key_configured && !state.config?.storyboard_image_api_ready) {
    alert("请先在「制作设置」配置 OhMyGPT Key");
    return showSettings();
  }
  const label = kind === "scenes" ? "场景" : kind === "props" ? "道具" : "角色";
  const sel = selectedAssetsInCurrentTab();
  const ids = sel.map((x) => x.id);
  if (ids.length) {
    if (
      !confirm(
        `将为已选 ${ids.length} 个「${label}」逐个${forceAll ? "重新生图" : "补全缺图"}（串行，不并发）。继续？`
      )
    ) {
      return;
    }
    let ok = 0;
    let fail = 0;
    let skip = 0;
    setBusyOverlay(true, `生图 0/${ids.length}…`);
    try {
      for (let i = 0; i < ids.length; i++) {
        const id = ids[i];
        const name = sel[i]?.name || id.slice(0, 6);
        setBusyOverlay(true, `生图 ${i + 1}/${ids.length} · ${name}`);
        toast(`生图 ${i + 1}/${ids.length} · ${name}`);
        try {
          const r = await api(`/api/projects/${state.projectId}/studio/batch-generate-images`, {
            method: "POST",
            body: JSON.stringify({
              kind,
              only_missing: !forceAll,
              limit: 1,
              ids: [id],
            }),
          });
          if (r.generated) ok += 1;
          else if (r.failed) fail += 1;
          else skip += 1;
        } catch (_) {
          fail += 1;
        }
      }
      await openProject(state.projectId);
      state.step = "assets";
      state.assetTab = kind;
      render();
      toast(`块图完成：成功 ${ok}，失败 ${fail}${skip ? `，跳过 ${skip}` : ""}`);
    } finally {
      setBusyOverlay(false);
    }
    return;
  }
  if (!confirm(`为当前「${label}」${forceAll ? "全部重出" : "补全缺图"}生成块图？（每批最多 12 张，耗时较长）`)) {
    return;
  }
  toast(`正在生成${label}块图…`);
  try {
    const r = await api(`/api/projects/${state.projectId}/studio/batch-generate-images`, {
      method: "POST",
      body: JSON.stringify({
        kind,
        only_missing: !forceAll,
        limit: 12,
      }),
    });
    await openProject(state.projectId);
    state.step = "assets";
    state.assetTab = kind;
    render();
    toast(`块图完成：成功 ${r.generated}，失败 ${r.failed}`);
    if (r.errors?.length) {
      console.warn(r.errors);
    }
  } catch (e) {
    alert(`生成块图失败：${e.message || e}`);
  }
}

async function batchGenScripts() {
  const sel = selectedEpisodes();
  const pool = sel.length ? sel : state.episodes || [];
  const need = pool.filter((e) => !(e.storyboard_md || "").trim());
  const targets = need.length ? need : sel.length ? sel : pool;
  if (!targets.length) {
    alert("没有可处理的分集");
    return;
  }
  if (sel.length) {
    if (!confirm(`将为已选 ${targets.length} 集逐个生成视频脚本（串行）。继续？`)) return;
  } else if (!need.length) {
    if (!confirm("所有分集似乎已有脚本。仍要整批重新生成？")) return;
  } else if (!confirm(`将为 ${need.length} 集批量生成视频脚本，继续？`)) {
    return;
  }
  toast(sel.length ? "逐集生成脚本中…" : "批量生成脚本中…");
  const useLlm = !!state.config?.ohmygpt?.key_configured || !!state.config?.storyboard_image_api_ready;
  if (sel.length) {
    let ok = 0;
    let fail = 0;
    setBusyOverlay(true, `脚本 0/${targets.length}…`);
    try {
      for (let i = 0; i < targets.length; i++) {
        const e = targets[i];
        setBusyOverlay(true, `脚本 ${i + 1}/${targets.length} · 第${e.ep_num}集`);
        try {
          await api(`/api/projects/${state.projectId}/studio/batch-video-scripts`, {
            method: "POST",
            body: JSON.stringify({
              use_llm: useLlm,
              only_missing: need.length > 0 && need.length === targets.length,
              limit: 1,
              episode_ids: [e.id],
            }),
          });
          ok += 1;
        } catch (_) {
          fail += 1;
        }
      }
      await openProject(state.projectId);
      state.step = "videos";
      render();
      toast(`脚本完成：成功 ${ok}，失败 ${fail}`);
    } finally {
      setBusyOverlay(false);
    }
    return;
  }
  try {
    const r = await api(`/api/projects/${state.projectId}/studio/batch-video-scripts`, {
      method: "POST",
      body: JSON.stringify({
        use_llm: useLlm,
        only_missing: need.length > 0,
        limit: 20,
      }),
    });
    await openProject(state.projectId);
    state.step = "videos";
    render();
    toast(`脚本完成：成功 ${r.generated}，失败 ${r.failed}`);
  } catch (e) {
    alert(`批量脚本失败：${e.message || e}`);
  }
}

async function waitJobTerminal(jobId, { onTick } = {}) {
  const done = new Set(["done", "failed", "cancelled"]);
  for (;;) {
    const j = await api(`/api/jobs/${jobId}`);
    if (onTick) onTick(j);
    if (done.has(String(j.status || "").toLowerCase())) return j;
    await new Promise((r) => setTimeout(r, 3000));
  }
}

async function batchGenVideos() {
  const sel = selectedEpisodes();
  const pool = sel.length ? sel : state.episodes || [];
  const list = pool.filter((e) => (e.storyboard_md || "").trim());
  if (!list.length) {
    alert(sel.length ? "已选分集中没有视频脚本，请先生成脚本" : "请先生成视频脚本");
    return;
  }
  if (
    !confirm(
      `将为${sel.length ? "已选 " : ""}${list.length} 集按顺序生成视频（一集做完再做下一集；集内按场/段依次）。\n` +
        `跳过已有分段；中途可在任务条点「停止」。是否开始？`
    )
  ) {
    return;
  }
  let ok = 0;
  let fail = 0;
  let stopped = false;
  toast(sel.length ? "已选分集：开始逐集生成视频…" : "整部视频：开始按集依次排队…");
  for (const e of list) {
    let job;
    try {
      job = await api(`/api/projects/${state.projectId}/jobs`, {
        method: "POST",
        body: JSON.stringify({
          stage_id: "video",
          episode_id: e.id,
          params: {
            confirm_full_episode: true,
            auto_concat: false,
            skip_existing: true,
            segment_serial: true,
            preview_with_audio: true,
          },
        }),
      });
    } catch (_) {
      fail += 1;
      continue;
    }
    state.jobsOpen = true;
    await refreshPipeline();
    await refreshJobsList();
    paintJobsDrawer();
    updateJobsBadge();
    const finished = await waitJobTerminal(job.id, {
      onTick: async () => {
        await refreshPipeline();
        await refreshJobsList();
        updateJobsBadge();
        if (state.jobsOpen) paintJobsDrawer();
        if (state.view === "epEditor" && state.epEditorId === e.id) void bindEpEditor();
      },
    });
    if (String(finished.status).toLowerCase() === "cancelled") {
      stopped = true;
      toast(`已停止（做到第${e.ep_num}集）；可稍后继续补全`);
      break;
    }
    if (String(finished.status).toLowerCase() === "done") ok += 1;
    else fail += 1;
  }
  await refreshPipeline();
  render();
  if (!stopped) toast(`整部视频结束：完成 ${ok} 集，失败 ${fail} 集`);
}

async function batchGenStills() {
  const sel = selectedEpisodes();
  const pool = sel.length ? sel : state.episodes || [];
  const list = pool.filter((e) => (e.storyboard_md || "").trim());
  if (!list.length) {
    alert(sel.length ? "已选分集中没有视频脚本，请先生成脚本" : "请先生成视频脚本");
    return;
  }
  if (
    !confirm(
      sel.length
        ? `为已选 ${list.length} 集逐个补缺分镜图（一集完成再下一集）？`
        : `为 ${list.length} 集逐个补缺分镜图（一集完成再下一集）？`
    )
  ) {
    return;
  }
  let ok = 0;
  let fail = 0;
  let stopped = false;
  setBusyOverlay(true, `分镜图 0/${list.length}…`);
  try {
    for (let i = 0; i < list.length; i++) {
      const e = list[i];
      setBusyOverlay(true, `分镜图 ${i + 1}/${list.length} · 第${e.ep_num}集`);
      let job;
      try {
        job = await api(`/api/projects/${state.projectId}/jobs`, {
          method: "POST",
          body: JSON.stringify({
            stage_id: "storyboard_images",
            episode_id: e.id,
            params: { confirm_full_episode: true, skip_existing: true },
          }),
        });
      } catch (_) {
        fail += 1;
        continue;
      }
      state.jobsOpen = true;
      await refreshPipeline();
      await refreshJobsList();
      paintJobsDrawer();
      updateJobsBadge();
      const finished = await waitJobTerminal(job.id, {
        onTick: async () => {
          await refreshPipeline();
          await refreshJobsList();
          updateJobsBadge();
          if (state.jobsOpen) paintJobsDrawer();
        },
      });
      if (String(finished.status).toLowerCase() === "cancelled") {
        stopped = true;
        toast(`已停止（做到第${e.ep_num}集）`);
        break;
      }
      if (String(finished.status).toLowerCase() === "done") ok += 1;
      else fail += 1;
    }
    await refreshPipeline();
    render();
    if (!stopped) toast(`分镜出图结束：完成 ${ok} 集，失败 ${fail} 集`);
  } finally {
    setBusyOverlay(false);
  }
}

async function refreshPipeline() {
  if (!state.projectId) return;
  state.pipeline = await api(`/api/projects/${state.projectId}/pipeline`);
  const eps = await api(`/api/projects/${state.projectId}/episodes`);
  state.episodes = eps.episodes || [];
  const running = activeJobs();
  state.jobsBadge = running.length;
}

function isPreviewBusy() {
  const v = document.getElementById("prevVideo");
  if (!v) return false;
  if (!v.paused && !v.ended) return true;
  // 已加载视频源时也避免整页重绑，防止进度轮询打断播放/定位
  return !!(v.currentSrc || v.src);
}

function softUpdateEpEditorProgress() {
  const eid = state.epEditorId;
  const root = document.getElementById("epEditorRoot");
  if (!eid || !root || state.view !== "epEditor") return;
  const epJobs = activeJobs().filter((j) => j.episode_id === eid);
  const generatingShots = new Set();
  for (const j of epJobs) {
    if (j.status !== "running") continue;
    const cur = jobCurrentShot(j);
    const nums = jobShotNums(j);
    if (cur) generatingShots.add(cur);
    else if (nums.length) nums.forEach((n) => generatingShots.add(n));
  }
  // 仅刷新任务条，不重建视频节点
  let bannerHost = root.querySelector(".active-jobs-banner");
  const bannerHtml = renderActiveJobsBanner(epJobs);
  const centerBody = root.querySelector(".ep-center-body");
  if (bannerHtml) {
    if (bannerHost) bannerHost.outerHTML = bannerHtml;
    else if (centerBody) centerBody.insertAdjacentHTML("afterbegin", bannerHtml);
    bannerHost = root.querySelector(".active-jobs-banner");
    if (bannerHost) bindJobActionButtons(bannerHost);
  } else if (bannerHost) {
    bannerHost.remove();
  }
  root.querySelectorAll(".clip-card[data-seg]").forEach((card) => {
    const sn = parseInt(card.getAttribute("data-shot") || String((parseInt(card.dataset.seg, 10) || 0) + 1), 10);
    const gen = generatingShots.has(Number(sn));
    card.classList.toggle("generating", gen);
    const tag = card.querySelector(".clip-gen-tag");
    if (gen && !tag) {
      const thumb = card.querySelector(".clip-thumb");
      if (thumb) thumb.insertAdjacentHTML("beforeend", `<span class="clip-gen-tag">生成中</span>`);
    } else if (!gen && tag) tag.remove();
  });
  const btn = document.getElementById("btnGenSegVideo");
  if (btn) {
    const active = root.querySelector(".clip-card.active");
    const sn = parseInt(
      active?.getAttribute("data-shot") || String((state.epEditorSeg || 0) + 1),
      10
    );
    const busy = generatingShots.has(Number(sn));
    btn.disabled = busy;
    if (busy) btn.textContent = "本段生成中…";
    else if (btn.textContent.includes("生成中")) btn.textContent = "生成本段视频";
  }
}

function startPoll() {
  stopPoll();
  let lastSig = "";
  state.pollTimer = setInterval(async () => {
    if (!state.projectId || state.view === "home") return;
    try {
      await refreshPipeline();
      await refreshJobsList();
      updateJobsBadge();
      if (state.jobsOpen) paintJobsDrawer();
      const sig = JSON.stringify(
        (state.jobsList || []).map((j) => [j.id, j.status, j.progress, j.message, j.updated_at])
      );
      const changed = sig !== lastSig;
      lastSig = sig;
      if (state.view === "project" && state.step === "videos") {
        // 列表页有任务时只在状态变化时刷新，避免每 3 秒整页闪烁
        if (changed) render();
      } else if (
        state.view === "epEditor" &&
        (changed || activeJobs().some((j) => j.episode_id === state.epEditorId))
      ) {
        // 有预览视频时禁止整页重绑（会中断播放）；仅软更新任务进度
        if (isPreviewBusy() || document.getElementById("prevVideo")) {
          softUpdateEpEditorProgress();
        } else if (changed) {
          void bindEpEditor();
        } else {
          softUpdateEpEditorProgress();
        }
      }
    } catch (_) {}
  }, 3000);
}

function stopPoll() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function showPasteModal() {
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  modal.innerHTML = `<div class="modal-box wide">
    <h3>粘贴剧本文本</h3>
    <label class="field">项目名称（可空）<input id="pasteName"/></label>
    <label class="field">剧本正文
      <textarea id="pasteText" rows="16" placeholder="粘贴含「第001集｜标题」的剧本…"></textarea>
    </label>
    <div class="modal-actions">
      <button type="button" class="btn" id="mCancel">取消</button>
      <button type="button" class="btn primary" id="mOk">导入并解析</button>
    </div>
  </div>`;
  document.getElementById("mCancel").onclick = () => modal.classList.add("hidden");
  document.getElementById("mOk").onclick = async () => {
    const text = document.getElementById("pasteText").value;
    const name = document.getElementById("pasteName").value;
    modal.classList.add("hidden");
    await importText(text, name);
  };
}

function showExample() {
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  modal.innerHTML = `<div class="modal-box wide">
    <h3>标准剧本示例（节选）</h3>
    <pre style="white-space:pre-wrap;font-size:0.82rem;line-height:1.5;background:#141418;padding:0.75rem;border-radius:8px;max-height:50vh;overflow:auto">《示例短剧》十集漫剧
版本：SCRIPTSET-v2.0
定位：抖音竖屏短剧
第001集｜开场钩子

EP001-SC01｜外·主城广场·日

人物：主角、反派

▲主角推开人群，目光锁定反派。

主角：你欠我的，今天该还了。

反派：那就看看，谁先倒下。</pre>
    <div class="modal-actions"><button type="button" class="btn primary" id="mClose">知道了</button></div>
  </div>`;
  document.getElementById("mClose").onclick = () => modal.classList.add("hidden");
}

function showAddAsset() {
  const kind = state.assetTab;
  if (kind === "voices") {
    toast("请在「角色」中添加人物后，再到「音色」里配置");
    state.assetTab = "characters";
    render();
    return;
  }
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  modal.innerHTML = `<div class="modal-box">
    <h3>添加${kind === "characters" ? "角色" : kind === "scenes" ? "场景" : "道具"}</h3>
    <label class="field">名称<input id="aName"/></label>
    <label class="field">描述<textarea id="aNote" rows="4"></textarea></label>
    <div class="modal-actions">
      <button type="button" class="btn" id="mCancel">取消</button>
      <button type="button" class="btn primary" id="mOk">保存</button>
    </div>
  </div>`;
  document.getElementById("mCancel").onclick = () => modal.classList.add("hidden");
  document.getElementById("mOk").onclick = async () => {
    const name = document.getElementById("aName").value.trim();
    if (!name) return alert("请填写名称");
    const note = document.getElementById("aNote").value;
    const path =
      kind === "characters"
        ? `/api/projects/${state.projectId}/characters`
        : kind === "scenes"
          ? `/api/projects/${state.projectId}/scenes`
          : `/api/projects/${state.projectId}/props`;
    const body =
      kind === "scenes"
        ? { name, visual_prompt: note, code: "" }
        : { name, role_note: note, visual_prompt: note };
    await api(path, { method: "POST", body: JSON.stringify(body) });
    modal.classList.add("hidden");
    await openProject(state.projectId);
    state.step = "assets";
    render();
  };
}

async function showEditVoice(id) {
  const it = (state.characters || []).find((x) => x.id === id);
  if (!it) return;
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  const samples = it.voice_samples || [];
  const mode = samples.length ? "audio" : "text";
  const ttsOpts = ["", "onyx", "echo", "fable", "ash", "nova", "shimmer", "alloy", "verse", "sage", "coral", "ballad"]
    .map(
      (v) =>
        `<option value="${v}" ${(it.tts_voice || "") === v ? "selected" : ""}>${v || "自动"}</option>`
    )
    .join("");
  modal.innerHTML = `<div class="modal-box wide">
    <h3>${esc(it.name)} · 音色</h3>
    <label class="field">绑定角色
      <select id="vRole" disabled><option>${esc(it.name)}</option></select>
    </label>
    <label class="field">确认状态
      <select id="vStatus">
        <option value="pending" ${!(it.voice_desc || samples.length) ? "selected" : ""}>待设置</option>
        <option value="ready" ${it.voice_desc || samples.length ? "selected" : ""}>已配置</option>
      </select>
    </label>
    <div class="field-row" style="margin:0.35rem 0 0.75rem">
      <label style="display:flex;align-items:center;gap:0.35rem;color:var(--text);font-size:0.88rem">
        <input type="radio" name="vMode" value="text" ${mode === "text" ? "checked" : ""}/> 文字音色
      </label>
      <label style="display:flex;align-items:center;gap:0.35rem;color:var(--text);font-size:0.88rem">
        <input type="radio" name="vMode" value="audio" ${mode === "audio" ? "checked" : ""}/> 音频音色
      </label>
    </div>
    <div id="vTextPane">
      <label class="field">文字音色描述
        <textarea id="vDesc" rows="5" placeholder="描述角色的音域、音质、语速和说话习惯">${esc(it.voice_desc || "")}</textarea>
      </label>
      <label class="field">固定 TTS 音色（可选）
        <select id="vTts">${ttsOpts}</select>
      </label>
      <button type="button" class="btn sm" id="vGenAi" ${state.config?.ohmygpt?.key_configured || state.config?.storyboard_image_api_ready ? "" : "disabled"}>AI 生成试听</button>
    </div>
    <div id="vAudioPane" class="${mode === "audio" ? "" : "hidden"}">
      <label class="field">上传参考音频
        <input type="file" id="vAudio" accept="audio/*,.wav,.mp3,.m4a,.ogg,.webm"/>
      </label>
      <div class="muted" style="margin:0.35rem 0">已有样本 ${samples.length} 段</div>
      <div id="vSampleList">${samples
        .map(
          (p, i) =>
            `<div style="margin:0.35rem 0"><audio controls preload="none" src="${esc(fileUrl(p))}" style="width:100%;max-width:420px"></audio>
            <span class="muted" style="font-size:0.75rem">样本 ${i + 1}</span></div>`
        )
        .join("") || '<p class="muted">暂无参考音频</p>'}</div>
    </div>
    <div class="modal-actions">
      <button type="button" class="btn" id="mCancel">取消</button>
      <button type="button" class="btn primary" id="mOk">保存音色</button>
    </div>
  </div>`;
  const syncMode = () => {
    const m = modal.querySelector('input[name="vMode"]:checked')?.value || "text";
    document.getElementById("vTextPane").classList.toggle("hidden", m === "audio");
    document.getElementById("vAudioPane").classList.toggle("hidden", m !== "audio");
  };
  modal.querySelectorAll('input[name="vMode"]').forEach((r) => {
    r.onchange = syncMode;
  });
  syncMode();
  document.getElementById("mCancel").onclick = () => modal.classList.add("hidden");
  document.getElementById("vGenAi").onclick = async () => {
    try {
      toast("正在生成试听…");
      await api(`/api/projects/${state.projectId}/characters/${id}/generate-voice`, {
        method: "POST",
        body: JSON.stringify({
          demo_text: document.getElementById("vDesc").value.trim() || undefined,
          voice: document.getElementById("vTts").value || undefined,
        }),
      });
      modal.classList.add("hidden");
      await openProject(state.projectId);
      state.step = "assets";
      state.assetTab = "voices";
      render();
      toast("试听已生成");
    } catch (e) {
      alert(`生成失败：${e.message || e}`);
    }
  };
  document.getElementById("mOk").onclick = async () => {
    const voice_desc = document.getElementById("vDesc").value.trim();
    const tts_voice = document.getElementById("vTts").value || "";
    await api(`/api/projects/${state.projectId}/characters`, {
      method: "POST",
      body: JSON.stringify({
        id: it.id,
        name: it.name,
        role_note: it.role_note || "",
        visual_prompt: it.visual_prompt || "",
        voice_desc,
        tts_voice,
        voice_samples: it.voice_samples || [],
        ref_images: it.ref_images || [],
        ref_image: it.ref_image || "",
        episodes: it.episodes || [],
        sort_order: it.sort_order || 0,
      }),
    });
    const file = document.getElementById("vAudio")?.files?.[0];
    if (file) {
      const fd = new FormData();
      fd.append("file", file);
      await api(`/api/projects/${state.projectId}/characters/${id}/voices`, {
        method: "POST",
        body: fd,
      });
    }
    modal.classList.add("hidden");
    await openProject(state.projectId);
    state.step = "assets";
    state.assetTab = "voices";
    render();
    toast("音色已保存");
  };
}

async function showEditAsset(kind, id) {
  if (kind === "voices") return showEditVoice(id);
  const list =
    kind === "characters" ? state.characters : kind === "scenes" ? state.scenes : state.props;
  const it = list.find((x) => x.id === id);
  if (!it) return;
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  const eps = it.episodes || [];
  const dots = Array.from({ length: state.project.episode_count || 10 }, (_, i) => i + 1)
    .map(
      (n) =>
        `<button type="button" class="ep-dot ${eps.includes(n) ? "on" : ""}" data-n="${n}">${n}</button>`
    )
    .join("");
  const noteLabel =
    kind === "characters" ? "角色描述" : kind === "scenes" ? "场景描述" : "资产说明";
  const promptLabel =
    kind === "characters"
      ? "形象生图提示（外表+风格+三视图结构）"
      : kind === "scenes"
        ? "基础母景·生图提示（外表+风格+三视图结构）"
        : "基础状态·生图提示（外表+风格+三视图结构）";
  const notePh =
    kind === "characters"
      ? "身份、性格与剧情作用（勿只填姓名）"
      : kind === "scenes"
        ? "空间氛围与用途说明（勿与标题相同）"
        : "材质、用途与剧情作用（勿与名称相同）";
  const promptPh =
    kind === "characters"
      ? "角色外表描述：…\n风格描述：半写实3D国漫…\n图片展示结构：单张横向16:9，左正面无头全身+背面有头全身，右正面胸像…"
      : kind === "scenes"
        ? "场景外表描述：…\n风格描述：半写实3D国漫…\n图片展示结构：单张横向16:9三视图母景组合…"
        : "道具外表描述：…\n风格描述：半写实3D国漫…\n图片展示结构：单张横向16:9正背+细节特写…";
  const imgs = it.ref_images || [];
  const viewTitle =
    kind === "characters" ? "形象列表" : kind === "scenes" ? "视角列表" : "状态列表";
  const baseLabel =
    kind === "characters"
      ? `${it.name}`
      : kind === "scenes"
        ? `${it.name}·基础母景`
        : `${it.name}·基础状态`;
  modal.innerHTML = `<div class="modal-box wide">
    <h3>${esc(it.name)}</h3>
    <label class="field">名称<input id="aName" value="${esc(it.name)}"/></label>
    <div class="muted">出现集数</div>
    <div class="ep-dots" id="epDots">${dots}</div>
    <label class="field">${noteLabel}
      <textarea id="aNote" rows="4" placeholder="${esc(notePh)}">${esc(it.role_note || "")}</textarea>
    </label>
    ${
      kind === "characters"
        ? `<div class="field-row" style="gap:0.75rem;align-items:stretch">
            <label class="field" style="flex:1">文字音色·当前使用
              <textarea id="aVoice" rows="3" placeholder="音域、音质、语速与说话习惯">${esc(it.voice_desc || "")}</textarea>
            </label>
            <div class="field" style="flex:1">
              <div class="muted" style="margin-bottom:0.35rem">音频音色</div>
              <div class="muted">${(it.voice_samples || []).length ? `已有 ${(it.voice_samples || []).length} 段参考音频` : "暂无参考音频"}</div>
              <button type="button" class="btn sm" id="aEditVoice" style="margin-top:0.5rem">编辑音色 / 上传参考音频</button>
            </div>
          </div>`
        : ""
    }
    <div class="section-head" style="margin-top:0.75rem">
      <strong>${viewTitle}</strong>
      <span class="muted" style="font-size:0.78rem">基础三视图用资产名；特点位/不同状态另建「名·状态」条目并上传对应图</span>
    </div>
    <div class="asset-view-card" style="border:1px solid var(--line);border-radius:10px;padding:0.75rem;margin:0.5rem 0">
      <label class="field">名称<input id="aViewName" value="${esc(baseLabel)}"/></label>
      <label class="field">${promptLabel}
        <textarea id="aPrompt" rows="10" placeholder="${esc(promptPh)}">${esc(it.visual_prompt || "")}</textarea>
      </label>
      <label class="field">上传参考图<input type="file" id="aImg" accept="image/*"/></label>
      <div class="multi-ref">${imgs
        .map((im) => `<div class="ref-chip"><img src="${esc(fileUrl(im.path))}" alt=""/><span class="muted">${esc(im.label || "")}</span></div>`)
        .join("") || '<p class="muted">点击上方上传；亦可先保存描述再批量生图</p>'}</div>
    </div>
    <div class="modal-actions">
      <button type="button" class="btn" id="mCancel">取消</button>
      <button type="button" class="btn primary" id="mOk">保存</button>
    </div>
  </div>`;
  const selected = new Set(eps);
  modal.querySelectorAll(".ep-dot").forEach((b) => {
    b.onclick = () => {
      const n = parseInt(b.dataset.n, 10);
      if (selected.has(n)) selected.delete(n);
      else selected.add(n);
      b.classList.toggle("on", selected.has(n));
    };
  });
  document.getElementById("mCancel").onclick = () => modal.classList.add("hidden");
  const btnVoice = document.getElementById("aEditVoice");
  if (btnVoice) {
    btnVoice.onclick = () => {
      modal.classList.add("hidden");
      showEditVoice(id);
    };
  }
  document.getElementById("mOk").onclick = async () => {
    const name = document.getElementById("aName").value.trim();
    const note = document.getElementById("aNote").value.trim();
    const prompt = document.getElementById("aPrompt").value.trim();
    if (note && note === name) {
      alert(`${noteLabel}不能与名称相同，请填写实质说明`);
      return;
    }
    const path =
      kind === "characters"
        ? `/api/projects/${state.projectId}/characters`
        : kind === "scenes"
          ? `/api/projects/${state.projectId}/scenes`
          : `/api/projects/${state.projectId}/props`;
    const body = {
      id,
      name,
      episodes: [...selected].sort((a, b) => a - b),
      role_note: note,
      visual_prompt: prompt || it.visual_prompt || "",
      voice_desc: document.getElementById("aVoice")?.value?.trim() || it.voice_desc || "",
      ref_images: it.ref_images || [],
      ref_image: it.ref_image || "",
      code: it.code || "",
    };
    await api(path, { method: "POST", body: JSON.stringify(body) });
    const file = document.getElementById("aImg")?.files?.[0];
    if (file) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("label", document.getElementById("aViewName")?.value?.trim() || "基础");
      const up =
        kind === "characters"
          ? `/api/projects/${state.projectId}/characters/${id}/images`
          : kind === "props"
            ? `/api/projects/${state.projectId}/props/${id}/images`
            : null;
      if (up) await api(up, { method: "POST", body: fd });
      else toast("场景图上传暂请用批量生图或稍后补传");
    }
    modal.classList.add("hidden");
    await openProject(state.projectId);
    state.step = "assets";
    render();
    toast("已保存");
  };
}

function showSettings() {
  const oh = state.config?.ohmygpt || {};
  const vb = state.config?.video_backend || {};
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  modal.innerHTML = `<div class="modal-box wide">
    <h3>制作设置</h3>
    <label class="field">OhMyGPT API Base<input id="ohBase" value="${esc(oh.api_base || "https://api.ohmygpt.com/v1")}"/></label>
    <label class="field">OhMyGPT Key<input id="ohKey" type="password" placeholder="${oh.key_configured ? "已保存，留空不改" : "粘贴 Key"}"/></label>
    <label class="field">视频后端
      <select id="vbSel">
        <option value="comfy" ${vb.backend !== "ppio" ? "selected" : ""}>云 GPU ComfyUI（MiniMax H3）</option>
        <option value="ppio" ${vb.backend === "ppio" ? "selected" : ""}>PPIO</option>
      </select>
    </label>
    <p class="muted" style="margin:0.35rem 0 0.75rem">官方 MiniMax API 已彻底关闭；成片由云 GPU Comfy（H3）直接生成。</p>
    <div class="modal-actions">
      <button type="button" class="btn" id="mCancel">关闭</button>
      <button type="button" class="btn primary" id="mOk">保存</button>
    </div>
  </div>`;
  document.getElementById("mCancel").onclick = () => modal.classList.add("hidden");
  document.getElementById("mOk").onclick = async () => {
    const ohBody = { api_base: document.getElementById("ohBase").value };
    const k = document.getElementById("ohKey").value.trim();
    if (k) ohBody.api_key = k;
    await api("/api/settings/ohmygpt", { method: "PUT", body: JSON.stringify(ohBody) });
    const vbBody = { backend: document.getElementById("vbSel").value, clear_minimax_key: true };
    state.config.video_backend = await api("/api/settings/video-backend", {
      method: "PUT",
      body: JSON.stringify(vbBody),
    });
    state.config = await api("/api/config");
    modal.classList.add("hidden");
    toast("设置已保存（官方 MiniMax API 已关闭）");
  };
}

async function init() {
  state.config = await api("/api/config");
  await loadProjects();
  // enrich project cards with meta if missing
  render();
}

init().catch((e) => {
  document.getElementById("app").innerHTML = `<p style="padding:2rem;color:#f66">加载失败：${esc(e.message || e)}</p>`;
});
