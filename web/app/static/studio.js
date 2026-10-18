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
  epClipTexts: null, // 当前分集各段脚本草稿（与侧栏片段一一对应）
  epClipDirty: false,
  epScriptBoundSeg: null, // textarea 当前展示的段号（切换段后 epEditorSeg 会先变，捕获必须用此值）
  epAssetTab: "all", // all | clip | material
  epAssetFilter: "all", // all | characters | scenes | props
  epLeftOpen: false, // 手机端左侧资产栏默认收起
  epMultiRefOpen: false, // 多图参考默认不展开、不显示图片
  selectedRefs: [],
  pollTimer: null,
  expandedEp: null,
  epSearch: "",
  jobsOpen: false,
  jobsList: [],
  jobsRunning: [],
  jobsBadge: 0,
  jobsPage: 1,
  jobsPageSize: 20,
  jobsTotal: 0,
  selectedAssetIds: new Set(),
  selectedEpisodeIds: new Set(),
  selectedStillShots: new Set(), // 分镜多选重生（shot_num）
  preferSegPreview: false, // 本段视频刚重出后优先播分段，不被整集成片盖住
};

function ensureSelectSets() {
  if (!(state.selectedAssetIds instanceof Set)) state.selectedAssetIds = new Set();
  if (!(state.selectedEpisodeIds instanceof Set)) state.selectedEpisodeIds = new Set();
  if (!(state.selectedStillShots instanceof Set)) state.selectedStillShots = new Set();
}

function toggleSelectedStillShot(sn) {
  ensureSelectSets();
  const n = Number(sn);
  if (!Number.isFinite(n) || n <= 0) return;
  if (state.selectedStillShots.has(n)) state.selectedStillShots.delete(n);
  else state.selectedStillShots.add(n);
}

function selectedStillShotList() {
  ensureSelectSets();
  return [...state.selectedStillShots].sort((a, b) => a - b);
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

/** Collect character/scene/prop names for script hyperlinks (longest first). */
function buildAssetLinkIndex() {
  const items = [];
  for (const c of state.characters || []) {
    if (c?.id && (c.name || "").trim().length >= 2)
      items.push({ kind: "characters", id: c.id, name: String(c.name).trim() });
  }
  for (const s of state.scenes || []) {
    if (s?.id && (s.name || "").trim().length >= 2)
      items.push({ kind: "scenes", id: s.id, name: String(s.name).trim() });
  }
  for (const p of state.props || []) {
    if (p?.id && (p.name || "").trim().length >= 2)
      items.push({ kind: "props", id: p.id, name: String(p.name).trim() });
  }
  items.sort((a, b) => b.name.length - a.name.length);
  return items;
}

/** Turn asset names in script text into clickable links (for AI consistency lookup). */
function linkifyScriptHtml(text) {
  const assets = buildAssetLinkIndex();
  const raw = String(text || "");
  if (!raw) return '<span class="muted">（空）</span>';
  const parts = [];
  let i = 0;
  while (i < raw.length) {
    let hit = null;
    for (const a of assets) {
      if (raw.startsWith(a.name, i) && (!hit || a.name.length > hit.name.length)) hit = a;
    }
    if (hit) {
      parts.push(
        `<a href="#" class="asset-link" data-akind="${esc(hit.kind)}" data-aid="${esc(hit.id)}">${esc(hit.name)}</a>`
      );
      i += hit.name.length;
    } else if (raw[i] === "\n") {
      parts.push("<br/>");
      i += 1;
    } else {
      parts.push(esc(raw[i]));
      i += 1;
    }
  }
  return parts.join("");
}

function bindAssetLinks(root) {
  (root || document).querySelectorAll("a.asset-link").forEach((a) => {
    if (a.dataset.linkBound) return;
    a.dataset.linkBound = "1";
    a.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      const kind = a.dataset.akind;
      const id = a.dataset.aid;
      if (kind && id) showEditAsset(kind, id);
    };
  });
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
  // 禁止浏览器/代理缓存 GET，否则脚本保存后切换片段会读到旧内容
  if (!headers["Cache-Control"]) headers["Cache-Control"] = "no-cache";
  if (!headers["Pragma"]) headers["Pragma"] = "no-cache";
  const res = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...opts,
    headers,
  });
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

/** 占位图：先出壳，后台限流预加载后再显示，避免手机上图片解码卡死无法点击 */
function lazyImgHtml(rel, opts = {}) {
  if (!rel) return "";
  const {
    className = "",
    alt = "",
    ver = "",
    zoomable = false,
    title = "",
  } = opts;
  const url = typeof rel === "string" && rel.startsWith("blob:") ? rel : fileUrl(rel, ver);
  if (!url) return "";
  const cls = ["img-ph", zoomable ? "zoomable" : "", className].filter(Boolean).join(" ");
  const tip = title || (zoomable ? "点击放大" : "");
  // blob 已在本地，直接 src；远端用 data-src 预加载
  if (url.startsWith("blob:")) {
    return `<img class="${cls} img-ready" src="${esc(url)}" alt="${esc(alt)}" title="${esc(tip)}" decoding="async" draggable="false"/>`;
  }
  return `<img class="${cls}" data-src="${esc(url)}" alt="${esc(alt)}" title="${esc(tip)}" decoding="async" draggable="false"/>`;
}

const _imgLoadQueue = [];
let _imgLoadActive = 0;
const IMG_LOAD_CONCURRENCY = 3;

function enqueueImgLoad(fn) {
  _imgLoadQueue.push(fn);
  pumpImgLoadQueue();
}

function pumpImgLoadQueue() {
  while (_imgLoadActive < IMG_LOAD_CONCURRENCY && _imgLoadQueue.length) {
    const fn = _imgLoadQueue.shift();
    _imgLoadActive += 1;
    Promise.resolve()
      .then(fn)
      .catch(() => {})
      .finally(() => {
        _imgLoadActive -= 1;
        setTimeout(pumpImgLoadQueue, 12);
      });
  }
}

function hydrateLazyImages(root) {
  const scope = root || document;
  const imgs = [...scope.querySelectorAll("img[data-src]")];
  if (!imgs.length) return;

  const loadOne = (img) => {
    const u = img.getAttribute("data-src");
    if (!u || img.dataset.hydrated === "1") return;
    img.dataset.hydrated = "1";
    enqueueImgLoad(
      () =>
        new Promise((resolve) => {
          const pre = new Image();
          pre.decoding = "async";
          const apply = () => {
            img.src = u;
            img.classList.add("img-ready");
            img.classList.remove("img-ph");
            img.removeAttribute("data-src");
            resolve();
          };
          pre.onload = () => {
            if (typeof pre.decode === "function") {
              pre.decode().then(apply).catch(apply);
            } else apply();
          };
          pre.onerror = () => {
            img.classList.add("img-error");
            resolve();
          };
          pre.src = u;
        })
    );
  };

  if (typeof IntersectionObserver === "function") {
    const io = new IntersectionObserver(
      (entries) => {
        for (const en of entries) {
          if (!en.isIntersecting) continue;
          loadOne(en.target);
          io.unobserve(en.target);
        }
      },
      { rootMargin: "180px 0px", threshold: 0.01 }
    );
    imgs.forEach((img) => io.observe(img));
  } else {
    imgs.forEach(loadOne);
  }
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
    <div class="topbar-brand">三硕画布 <span>v1.0.0</span></div>
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
  const proj = (j.project_name || "").trim();
  const stopBtn =
    j.status === "running"
      ? `<button type="button" class="btn sm danger" data-stop-job="${esc(j.id)}">停止</button>`
      : "";
  const contBtn =
    (j.status === "cancelled" || j.status === "failed") &&
    j.stage_id === "video" &&
    j.episode_id &&
    (!state.projectId || j.project_id === state.projectId)
      ? `<button type="button" class="btn sm primary" data-continue-ep="${esc(j.episode_id)}">继续补全</button>`
      : "";
  return `<div class="active-job">
    <div class="active-job-h">
      <strong>${esc(videoJobLabel(j))}</strong>
      <span>${pct}%</span>
      <span class="active-job-actions">${stopBtn}${contBtn}</span>
    </div>
    ${proj ? `<div class="muted" style="font-size:0.75rem;margin:0.15rem 0">${esc(proj)}</div>` : ""}
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

function jobsPageCount() {
  const size = Math.max(1, state.jobsPageSize || 20);
  const total = Math.max(0, state.jobsTotal || 0);
  return Math.max(1, Math.ceil(total / size) || 1);
}

function renderJobsPagerHtml() {
  const page = Math.max(1, state.jobsPage || 1);
  const pages = jobsPageCount();
  const total = state.jobsTotal || 0;
  const size = state.jobsPageSize || 20;
  const from = total ? (page - 1) * size + 1 : 0;
  const to = Math.min(page * size, total);
  return `<div class="jobs-pager">
    <span class="muted">${total ? `${from}–${to} / ${total}` : "0 条"}</span>
    <button type="button" class="btn sm ghost" id="btnJobsPrev" ${page <= 1 ? "disabled" : ""}>上一页</button>
    <span class="jobs-page-num">${page} / ${pages}</span>
    <button type="button" class="btn sm ghost" id="btnJobsNext" ${page >= pages ? "disabled" : ""}>下一页</button>
  </div>`;
}

function renderJobsDrawerHtml(jobs, runningJobs) {
  const running = runningJobs || [];
  const rows = (jobs || [])
    .map((j) => {
      const proj = (j.project_name || "").trim() || (j.project_id || "").slice(0, 8) || "—";
      const ep = j.ep_num != null ? `第${String(j.ep_num).padStart(3, "0")}集` : "—";
      const pct = Math.round((j.progress || 0) * 100);
      const cls = j.status === "failed" ? "row-failed" : j.status === "running" ? "row-run" : "";
      const rawMsg = friendlyJobError(j.message || "");
      const msg = esc(rawMsg.slice(0, 180));
      const t = esc(formatBeijingTime(j.updated_at));
      return `<tr class="${cls}">
        <td class="job-proj" title="${esc(proj)}">${esc(proj.length > 14 ? proj.slice(0, 14) + "…" : proj)}</td>
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
      <span class="muted">全系统 · ${running.length ? `进行中 ${running.length}` : "无进行中任务"}</span>
      <button type="button" class="btn sm" id="btnJobsRefresh">刷新</button>
      <button type="button" class="btn sm ghost" id="btnJobsClose">关闭</button>
    </div>
    ${running.length ? `<div class="jobs-running-block">${running.map(formatActiveJobLine).join("")}</div>` : ""}
    <div class="jobs-table-wrap">
      <table class="jobs-table">
        <thead><tr><th>剧本</th><th>分集</th><th>类型</th><th>状态</th><th>说明</th><th>更新</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="muted">暂无任务</td></tr>`}</tbody>
      </table>
    </div>
    ${renderJobsPagerHtml()}
  </div>`;
}

async function refreshJobsList() {
  try {
    const size = Math.max(1, Math.min(50, state.jobsPageSize || 20));
    let page = Math.max(1, state.jobsPage || 1);
    let offset = (page - 1) * size;
    const data = await api(`/api/jobs?limit=${size}&offset=${offset}`);
    state.jobsTotal = Number(data.total || 0);
    state.jobsPageSize = Number(data.limit || size);
    // 若删任务后当前页越界，退回最后一页再拉一次
    const pages = jobsPageCount();
    if (page > pages) {
      state.jobsPage = pages;
      offset = (pages - 1) * size;
      const data2 = await api(`/api/jobs?limit=${size}&offset=${offset}`);
      state.jobsList = data2.jobs || [];
      state.jobsTotal = Number(data2.total || 0);
      state.jobsRunning = data2.running_jobs || [];
    } else {
      state.jobsList = data.jobs || [];
      state.jobsRunning = data.running_jobs || [];
    }
    const runningAll = state.jobsRunning || [];
    state.jobsBadge = runningAll.length;
    if (state.pipeline) {
      const runningHere = state.projectId
        ? runningAll.filter((j) => j.project_id === state.projectId)
        : runningAll;
      state.pipeline.active_jobs = runningHere;
      state.pipeline.active_job = runningHere[0] || null;
    }
  } catch (_) {
    state.jobsList = state.jobsList || [];
    state.jobsRunning = state.jobsRunning || [];
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
  drawer.innerHTML = renderJobsDrawerHtml(state.jobsList, state.jobsRunning);
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
  document.getElementById("btnJobsPrev")?.addEventListener("click", async () => {
    if ((state.jobsPage || 1) <= 1) return;
    state.jobsPage -= 1;
    await refreshJobsList();
    paintJobsDrawer();
    updateJobsBadge();
  });
  document.getElementById("btnJobsNext")?.addEventListener("click", async () => {
    if ((state.jobsPage || 1) >= jobsPageCount()) return;
    state.jobsPage += 1;
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
  state.jobsOpen = true;
  state.jobsPage = 1;
  await refreshJobsList();
  paintJobsDrawer();
  updateJobsBadge();
}

/* —— 分镜图放大 —— */
const lightboxNav = { imgs: [], index: 0 };

function collectLightboxImages(fromImg) {
  const gallery =
    fromImg.closest(".asset-img-grid") ||
    fromImg.closest(".clip-strip") ||
    fromImg.closest(".ep-asset-list") ||
    fromImg.closest(".asset-grid") ||
    fromImg.closest(".multi-ref") ||
    fromImg.closest(".modal-box") ||
    fromImg.closest("#epEditorRoot") ||
    fromImg.closest(".page-body");
  if (!gallery) return [fromImg];
  const list = Array.from(
    gallery.querySelectorAll(
      "img.zoomable, .clip-thumb img, .ep-asset-thumb img, .asset-thumb img, .asset-img-preview img"
    )
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
  if (!imgEl || imgEl.classList.contains("img-loading") || imgEl.classList.contains("img-error")) {
    return;
  }
  // 懒加载未完成时用 data-src 顶上，避免「看得见却点不开」
  if (!imgEl.src || imgEl.src.endsWith("/") || imgEl.classList.contains("img-ph")) {
    const u = imgEl.getAttribute("data-src") || "";
    if (u) {
      imgEl.src = u;
      imgEl.classList.add("img-ready");
      imgEl.classList.remove("img-ph");
      imgEl.removeAttribute("data-src");
    }
  }
  if (!imgEl.src) return;
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
  scope
    .querySelectorAll(
      ".clip-thumb img, .ep-asset-thumb img, .asset-thumb img, .asset-img-preview img, img.zoomable"
    )
    .forEach((img) => {
      img.classList.add("zoomable");
      if (!img.title) img.title = "点击放大";
    });
  ensureZoomDelegation();
  hydrateLazyImages(scope);
}

function ensureZoomDelegation() {
  if (document.documentElement.dataset.zoomDel === "1") return;
  document.documentElement.dataset.zoomDel = "1";
  document.addEventListener(
    "click",
    (e) => {
      // 点缩略图容器或图本身都放大（手机上 target 常是容器）
      const thumb = e.target.closest?.(".clip-thumb, .ep-asset-thumb, .asset-thumb, .asset-img-preview");
      const img =
        e.target.closest?.("img.zoomable") ||
        thumb?.querySelector?.("img.zoomable, img[data-src], img");
      if (!img || !document.body.contains(img)) return;
      if (img.classList.contains("img-error")) return;
      if (
        e.target.closest(
          "a, label, input, .asset-check, .clip-check, .clip-regen-still, .clip-still-actions, .clip-still-upload-cta, .clip-still-btn"
        )
      ) {
        return;
      }
      // 其它按钮仍放行；分镜缩略图即使在 clip-card 内也放大
      const btn = e.target.closest("button");
      if (
        btn &&
        !btn.classList.contains("clip-card") &&
        !img.closest(".clip-thumb, .ep-asset-thumb, .asset-thumb, .asset-img-preview")
      ) {
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      openLightbox(img);
    },
    true
  );
}

function subhead(title) {
  const steps = [
    ["outline", "剧本大纲"],
    ["assets", "资产库"],
    ["videos", "分集视频"],
  ];
  const canDel = state.project && !isProtectedProject(state.project);
  return `<div class="subhead" style="position:relative">
    <button type="button" class="btn ghost icon" id="btnBack">←</button>
    <div class="subhead-title">${esc(title)}</div>
    <div class="workflow">${steps
      .map(
        ([id, lab]) =>
          `<button type="button" class="workflow-step ${state.step === id ? "active" : ""}" data-step="${id}">${lab}</button>`
      )
      .join("")}</div>
    <div class="h-scroll subhead-actions">
      <button type="button" class="btn sm" id="btnSync">同步配置</button>
      ${
        canDel
          ? `<button type="button" class="btn sm danger" id="btnDeleteProject" title="删除剧本">删除剧本</button>`
          : ""
      }
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

const PROTECTED_PROJECT_IDS = new Set([
  "78be2a4b-282c-4e4b-8a3e-18f4d076149b",
  "d7e69d56-8592-42a9-8fb3-febe68b8f64b",
]);

/** 每部剧本可自定义；导入/大纲页共用预设，不强制全局统一 */
const ART_STYLE_PRESETS = [
  "半写实3D国漫",
  "国风萌娃卡通",
  "古风Q版童趣",
  "日系二次元赛璐珞",
  "国风水墨写意",
  "欧美卡通厚涂",
  "赛博朋克霓虹",
  "写实电影感",
  "黏土定格动画",
  "像素复古",
];

function projectArtStyle() {
  const m = meta();
  return (
    (m.art_style || "").trim() ||
    (state.project?.style_prefix || "").trim() ||
    "半写实3D国漫"
  );
}

function artStyleFieldHtml(idPrefix, current) {
  const cur = (current || "").trim() || "半写实3D国漫";
  const inPreset = ART_STYLE_PRESETS.includes(cur);
  const opts = ART_STYLE_PRESETS.map(
    (s) =>
      `<option value="${esc(s)}" ${s === cur ? "selected" : ""}>${esc(s)}</option>`
  ).join("");
  return `<div class="art-style-field">
    <select id="${idPrefix}Preset">
      ${opts}
      <option value="__custom__" ${!inPreset ? "selected" : ""}>自定义…</option>
    </select>
    <input id="${idPrefix}Style" type="text" value="${esc(cur)}" placeholder="本剧画风（仅对本项目生效）" style="min-width:180px;flex:1"/>
  </div>`;
}

async function saveProjectArtMeta(opts = {}) {
  if (!state.projectId) return null;
  const style =
    (opts.art_style != null ? opts.art_style : document.getElementById("metaStyle")?.value) ||
    projectArtStyle();
  const ratio =
    opts.aspect_ratio != null
      ? opts.aspect_ratio
      : document.getElementById("metaRatio")?.value || meta().aspect_ratio || "16:9";
  const mode =
    opts.mode != null
      ? opts.mode
      : document.getElementById("metaMode")?.value || meta().mode || "默认版";
  const styleVal = String(style).trim() || "半写实3D国漫";
  const body = {
    style_prefix: styleVal,
    width: ratio === "9:16" ? 720 : 1280,
    height: ratio === "9:16" ? 1280 : 720,
    script_meta: {
      art_style: styleVal,
      aspect_ratio: ratio,
      mode,
    },
  };
  const proj = await api(`/api/projects/${state.projectId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  state.project = proj;
  const rw = proj?.assets_art_rewritten;
  if (rw) {
    const n =
      (rw.characters || 0) + (rw.scenes || 0) + (rw.props || 0);
    if (n > 0) {
      toast(
        `已按本剧画风更新资产（角色${rw.characters || 0}/场景${rw.scenes || 0}/道具${rw.props || 0}），请重新出图/视频`
      );
    }
  }
  return proj;
}

function bindArtStyleField(idPrefix) {
  const sel = document.getElementById(`${idPrefix}Preset`);
  const inp = document.getElementById(`${idPrefix}Style`);
  if (!sel || !inp) return;
  sel.onchange = () => {
    if (sel.value !== "__custom__") {
      inp.value = sel.value;
      inp.dispatchEvent(new Event("change"));
    } else {
      inp.focus();
    }
  };
  inp.oninput = () => {
    const v = inp.value.trim();
    if (ART_STYLE_PRESETS.includes(v)) sel.value = v;
    else sel.value = "__custom__";
  };
}

function isProtectedProject(p) {
  if (!p) return false;
  const id = (p.id || "").trim();
  const name = (p.name || "").trim();
  return PROTECTED_PROJECT_IDS.has(id) || name === "冰镜";
}

async function deleteProject(id, name) {
  if (!id) return;
  const label = name || id.slice(0, 8);
  if (
    !confirm(
      `确定删除剧本「${label}」？\n将清除该项目的分集、角色/场景/道具与全部图片/视频，且不可恢复。`
    )
  ) {
    return;
  }
  try {
    await api(`/api/projects/${id}`, { method: "DELETE" });
    toast(`已删除「${label}」`);
    if (state.projectId === id) {
      state.projectId = null;
      state.project = null;
      state.view = "home";
      state.step = "outline";
      state.characters = [];
      state.scenes = [];
      state.props = [];
      state.episodes = [];
    }
    await loadProjects();
    render();
  } catch (e) {
    alert(`删除失败：${e.message || e}`);
  }
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
      <label>本剧画风（导入时写入该项目）
        ${artStyleFieldHtml("import", "半写实3D国漫")}
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
      <div class="h-scroll toolbar">
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
              const canDel = !isProtectedProject(p);
              return `<div class="project-card" data-pid="${esc(p.id)}">
                <div class="thumb"><span class="status">${esc(st)}</span><span>剧本</span>
                  <span class="ep-count">${p.episode_count || 0} 集</span></div>
                <div class="card-head">
                  <h4 title="${esc(p.name)}">${esc(p.name)}</h4>
                  ${
                    canDel
                      ? `<button type="button" class="btn sm danger card-del" data-del-id="${esc(p.id)}" data-del-name="${esc(p.name)}" title="删除剧本">删除</button>`
                      : ""
                  }
                </div>
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
    <p class="muted">${esc(p.sub || "角色、场景和道具使用本剧画风与剧本理解")}</p>
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
    <div class="field" style="flex:1.4;min-width:260px">本剧画风
      ${artStyleFieldHtml("meta", projectArtStyle())}
    </div>
    <button type="button" class="btn sm" id="btnSaveStyle" title="仅保存本剧画风/画幅/模式">保存画风</button>
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
    <div class="h-scroll toolbar">
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
            const thumbRel = imgs[0]?.path || it.ref_image || "";
            const idCode = (it.id || "").slice(0, 6).toUpperCase();
            const imgN = imgs.length || (it.ref_image ? 1 : 0);
            const checked = state.selectedAssetIds.has(it.id);
            return `<div class="asset-card ${checked ? "selected" : ""}" data-aid="${esc(it.id)}" data-akind="${state.assetTab}">
              <label class="asset-check" data-stop="1" title="多选">
                <input type="checkbox" class="asset-select" data-select-asset="${esc(it.id)}" ${checked ? "checked" : ""}/>
              </label>
              <div class="asset-thumb">${thumbRel ? lazyImgHtml(thumbRel, { alt: it.name || "" }) : "＋"}
                <button type="button" class="btn sm asset-regen-btn" data-stop="1" data-regen-asset="${esc(it.id)}" data-regen-kind="${esc(state.assetTab)}" title="重新生成设定图">${imgN ? "重新生成" : "生成图片"}</button>
              </div>
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
    <p class="muted h-scroll-hint" style="margin:0;font-size:0.75rem">工具栏可左右滑动查看全部按钮 →</p>
    <div class="h-scroll toolbar" id="epToolbarScroll">
      <input class="search" id="epSearch" placeholder="搜索分集" value="${esc(state.epSearch || "")}"/>
      <button type="button" class="btn sm" id="btnSelectAllEps">全选列表</button>
      <button type="button" class="btn sm" id="btnClearEpSel"${selEpN ? "" : " disabled"}>清空所选</button>
      <span class="muted select-hint">${selEpN ? "已选将逐个整理/生图/视频" : "可多选后批量处理"}</span>
      <button type="button" class="btn sm primary" id="btnCompleteScripts" title="补全缺失视频脚本与分集梗概">${selEpN ? `整理已选 (${selEpN})` : `补全缺失脚本${needScript ? ` (${needScript})` : ""}`}</button>
      <button type="button" class="btn sm" id="btnBatchScripts">${selEpN ? `脚本已选 (${selEpN})` : `批量生成脚本${needScript ? ` (${needScript})` : ""}`}</button>
      <button type="button" class="btn sm" id="btnBatchStills" title="只补没有分镜图的镜头">${selEpN ? `补缺分镜已选 (${selEpN})` : "批量补缺分镜图"}</button>
      <button type="button" class="btn sm primary" id="btnBatchRegenStills" title="覆盖已有图，整集重新出分镜">${selEpN ? `重出分镜已选 (${selEpN})` : "批量重出分镜图"}</button>
      <button type="button" class="btn sm primary" id="btnBatchVideos">${selEpN ? `视频已选 (${hasScript})` : `批量生成视频${hasScript ? ` (${hasScript})` : ""}`}</button>
      <button type="button" class="btn sm primary" id="btnAddEpisode" title="在末尾新增一集">+ 新增分集</button>
    </div>
  </div>
  ${jobsBanner}
  <div class="ep-grid">${eps
    .map((e) => {
      const st = epStatus(e, plMap[e.id]);
      const d = plMap[e.id]?.detail || {};
      const epBody = String(e.script_md || e.storyboard_md || "");
      const chN = state.characters.filter((c) => {
        const name = String(c.name || "");
        if (Array.isArray(c.episodes) && c.episodes.length) return c.episodes.includes(e.ep_num);
        return !!(name && epBody.includes(name));
      }).length;
      const scN = state.scenes.filter((s) => {
        const name = String(s.name || "");
        if (Array.isArray(s.episodes) && s.episodes.length) return s.episodes.includes(e.ep_num);
        // 短剧场名「外景·赛车场·夜」也匹配正文「赛车场」
        if (!name) return false;
        if (epBody.includes(name)) return true;
        const core = name.split("·").find((x) => x && !/^(内|外)景$/.test(x) && !/^(日|夜|晨|黄昏)/.test(x));
        return !!(core && epBody.includes(core));
      }).length;
      const prN = state.props.filter((p) => {
        if (Array.isArray(p.episodes) && p.episodes.length) return p.episodes.includes(e.ep_num);
        return !!(p.name && epBody.includes(p.name));
      }).length;
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
          <button type="button" class="btn sm danger" data-del-ep="${esc(e.id)}" data-del-ep-label="第${e.ep_num}集" title="删除本集">删除分集</button>
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
  state.epClipTexts = null;
  state.epClipDirty = false;
  state.epScriptBoundSeg = null;
  state.epAssetTab = "all";
  state.epAssetFilter = "all";
  state.selectedRefs = [];
  state.step = "videos";
  render();
}

/** 单镜 3~10s：内容少可短，禁止为空声硬凑；优先镜头表 duration_sec */
function clampClipDurationSec(raw, fallback = 5) {
  let dur = Number(raw);
  if (!Number.isFinite(dur) || dur < 1) dur = fallback;
  return Math.min(10, Math.max(3, Math.round(dur)));
}

/** 从 storyboard P 段解析：场 → 分段列表 */
function parseSceneClipTree(script, shots) {
  // 与界面片段一一对应的可保存文本；禁止再拿「未对齐的旧 segments」去拼整集（会丢段/整集被单段覆盖）
  const rawSegs = splitScriptSegments(script || "").filter((t) =>
    /^P\d+[｜|]/m.test(t.trim())
  );
  const shotRows = Array.isArray(shots)
    ? [...shots].sort((a, b) => Number(a.shot_num) - Number(b.shot_num))
    : [];
  // 镜头表比脚本多段时（常见：脚本缺 P010），用镜头表补齐，避免侧栏只显示 9 段
  let texts = rawSegs.length ? [...rawSegs] : script ? [script] : [];
  // 检测坏稿：多个片段共用同一个 P 号（曾导致整场台词被第一段覆盖）
  const pNums = texts
    .map((t) => ((String(t || "").trim().match(/^P(\d+)[｜|]/m) || [])[1] || ""))
    .filter(Boolean);
  const dupP =
    pNums.length >= 2 && new Set(pNums).size < Math.max(2, Math.floor(pNums.length * 0.6));
  if (dupP && script) {
    // 以脚本重新拆段，忽略镜头表补齐，避免把 P001 复制到每一段
    texts = splitScriptSegments(script || "").filter((t) => /^P\d+[｜|]/m.test(t.trim()));
  }
  if (!dupP && shotRows.length > texts.length) {
    const usedIdx = new Set();
    texts = shotRows.map((s, i) => {
      const sn = Number(s.shot_num) || i + 1;
      let foundAt = -1;
      for (let ti = 0; ti < texts.length; ti++) {
        if (usedIdx.has(ti)) continue;
        if (new RegExp(`^P0*${sn}[｜|]`, "m").test(String(texts[ti] || "").trim())) {
          foundAt = ti;
          break;
        }
      }
      if (foundAt >= 0) {
        usedIdx.add(foundAt);
        return texts[foundAt];
      }
      // 按顺序回退：第 i 个镜头优先对齐第 i 段脚本，禁止所有镜头都吃到 P001
      if (i < rawSegs.length && !usedIdx.has(i)) {
        usedIdx.add(i);
        return rawSegs[i];
      }
      const title = (s.title || s.scene || `镜${sn}`).trim();
      return `P${String(sn).padStart(3, "0")}｜${title}｜总时长：${clampClipDurationSec(s.duration_sec, 5)}秒\n`;
    });
  }
  const clips = texts.map((t, i) => {
    const head = (t.match(/^P\d+[｜|][^\n]+/m) || [])[0] || "";
    const numFromHead = (head.match(/^P(\d+)/) || [])[1];
    const snHint = numFromHead ? parseInt(numFromHead, 10) : i + 1;
    const shot =
      (shots || []).find((s) => Number(s.shot_num) === snHint) ||
      (shots || []).find((s) => Number(s.shot_num) === i + 1) ||
      null;
    // 优先脚本头「总时长：N秒」，与编辑区所见一致；其次镜头表
    const dm = head.match(/总时长[：:]\s*(\d+(?:\.\d+)?)\s*秒/);
    const dmTail = head.match(/[｜|]\s*(\d+(?:\.\d+)?)\s*秒(?:[｜|]|$)/);
    let durRaw = dm
      ? dm[1]
      : shot && shot.duration_sec != null && shot.duration_sec !== ""
        ? shot.duration_sec
        : dmTail
          ? dmTail[1]
          : 5;
    const dur = clampClipDurationSec(durRaw, 5);
    const sm = head.match(/场(\d+)段(\d+)/);
    const sceneIdx = sm ? parseInt(sm[1], 10) : 1;
    const segInScene = sm ? parseInt(sm[2], 10) : i + 1;
    // 标题·场景名｜场N段M
    let sceneName = "";
    const parts = head.replace(/^P\d+[｜|]/, "").split(/[｜|]/);
    if (parts[0]) {
      const bit = parts[0];
      const dot = bit.indexOf("·");
      sceneName = dot >= 0 ? bit.slice(dot + 1).trim() : bit.trim();
    }
    if (!sceneName) sceneName = `场${sceneIdx}`;
    let text = String(t || "").trim();
    // 仅标题、无正文时，用镜头表拼可编辑正文，避免脚本区空白
    const bodyOnly = text.replace(/^P\d+[｜|][^\n]+\n?/, "").trim();
    if (!bodyOnly && shot) {
      const voices = Array.isArray(shot.voices)
        ? shot.voices
        : (() => {
            try {
              return JSON.parse(shot.voices_json || "[]");
            } catch {
              return [];
            }
          })();
      const lines = (voices || [])
        .filter((v) => v && (v.text || "").trim())
        .map((v) => `${v.role || "角色"}：「${(v.text || "").trim()}」`)
        .join("\n");
      text =
        `${head}\n\n` +
        `[画面]\n${(shot.visual || shot.scene || "按参考分镜继续表演。").trim()}\n\n` +
        `[台词]\n${lines || "（本段无对白）"}\n\n` +
        `[音效]\n${(shot.ambience || "安静环境底噪，无人声").trim()}`;
      // 展示用补全文也写回 texts，保证「保存」与所见一致
      texts[i] = text;
    }
    return {
      i,
      label: `片段 ${segInScene}`,
      globalLabel: `片段 ${i + 1}`,
      sceneIdx,
      segInScene,
      sceneName,
      dur,
      shot,
      text,
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
      dur: 9,
      shot: null,
      text: script || "（暂无视频脚本，请点「整集重新推理」）",
      head: "",
    });
    texts = [clips[0].text];
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
    sc.totalSec += c.dur || 9;
    if (c.sceneName && c.sceneName !== "本集场景") sc.name = c.sceneName;
  }
  const scenes = [...sceneMap.values()].sort((a, b) => a.sceneIdx - b.sceneIdx);
  // segments 必须与 clips 同序同长，供保存整集脚本
  return { clips, scenes, segments: texts.map((t, i) => (clips[i] ? clips[i].text : t)) };
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
    <div class="h-scroll subhead-actions">
      <span class="pill">${esc(title)}</span>
      <button type="button" class="btn sm" id="btnEpSettings">本集设置</button>
      <button type="button" class="btn sm" id="btnRegenScriptTop">整集重新推理</button>
      <button type="button" class="btn sm primary" id="btnBatchVideoTop" title="按场/段依次生成；已有段落自动跳过">继续补全 / 整集视频</button>
      <button type="button" class="btn sm primary" id="btnMergeEpisode" title="把本集已有分段视频按顺序拼成整集成片">合并成片</button>
    </div>
  </div>
  <div id="epEditorRoot" class="ep-editor"><p class="muted" style="padding:1rem">加载分集…</p></div>`;
}

function nameInEpisodeText(name, body) {
  const n = String(name || "").trim();
  const t = String(body || "");
  if (!n || !t) return false;
  if (t.includes(n)) return true;
  // 「外景·赛车场·夜」↔ 正文「赛车场」
  const parts = n.split("·").filter(Boolean);
  for (const p of parts) {
    if (/^(内|外)景$/.test(p)) continue;
    if (/^(日|夜|晨|清晨|黄昏|傍晚|午|午后|深夜)$/.test(p)) continue;
    if (p.length >= 2 && t.includes(p)) return true;
  }
  return false;
}

function episodeScopedAssets(epNum, segText, episodeScriptMd) {
  const text = segText || "";
  const epBody = episodeScriptMd || text || "";
  const chars = (state.characters || []).filter((c) => {
    if (Array.isArray(c.episodes) && c.episodes.length) return c.episodes.includes(epNum);
    return nameInEpisodeText(c.name, epBody);
  });
  const props = (state.props || []).filter((p) => {
    if (Array.isArray(p.episodes) && p.episodes.length) return p.episodes.includes(epNum);
    return nameInEpisodeText(p.name, epBody);
  });
  const scenes = (state.scenes || []).filter((s) => {
    if (Array.isArray(s.episodes) && s.episodes.length) return s.episodes.includes(epNum);
    return nameInEpisodeText(s.name, epBody);
  });
  const inClip = (name) => nameInEpisodeText(name, text);
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
  const thumbRel = imgs[0]?.path || it.ref_image || "";
  const hasImg = !!thumbRel;
  const note = (it.role_note || "").trim();
  const status = hasImg ? `已有图 · ${imgs.length || 1}` : "仅文字 · 待补图";
  return `<div class="ep-asset-card" data-akind="${kind}" data-aid="${esc(it.id)}">
    <div class="ep-asset-thumb">${thumbRel ? lazyImgHtml(thumbRel, { alt: it.name || "" }) : "＋"}</div>
    <div class="ep-asset-meta">
      <strong>${esc(it.name)}</strong>
      <div class="muted">${esc(status)}</div>
      ${note ? `<div class="muted ep-asset-note">${esc(note.slice(0, 42))}${note.length > 42 ? "…" : ""}</div>` : ""}
      <button type="button" class="btn sm ep-asset-regen" data-stop="1" data-regen-asset="${esc(it.id)}" data-regen-kind="${esc(kind)}" title="重新生成设定图">${hasImg ? "重新生成" : "生成图片"}</button>
    </div>
  </div>`;
}

async function bindEpEditor() {
  const eid = state.epEditorId;
  const root = document.getElementById("epEditorRoot");
  if (!eid || !root) return;
  // 切换/重绑前：按 textarea 实际绑定的段号落草稿（勿用已更新的 epEditorSeg，否则会把旧段内容写进新段）
  if (
    Array.isArray(state.epClipTexts) &&
    state.epEditorId === eid &&
    document.getElementById("segScript") &&
    Number.isInteger(state.epScriptBoundSeg)
  ) {
    captureEpSegDraft(state.epScriptBoundSeg);
  }
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
  // 仅在有未落盘草稿时覆盖服务器文本；已保存则以服务器为准
  // 若本地草稿 P 号重复/与服务器段数不一致，丢弃脏草稿，避免把坏稿写回
  const dirtyP = (state.epClipTexts || [])
    .map((t) => ((String(t || "").trim().match(/^P(\d+)[｜|]/m) || [])[1] || ""))
    .filter(Boolean);
  // 任一 P 号重复即视为坏草稿（曾把第1段台词写进第2段）
  const dirtyDup = dirtyP.length >= 2 && new Set(dirtyP).size < dirtyP.length;
  const serverP = segments
    .map((t) => ((String(t || "").trim().match(/^P(\d+)[｜|]/m) || [])[1] || ""))
    .filter(Boolean);
  const serverUnique = new Set(serverP).size;
  if (
    state.epClipDirty &&
    Array.isArray(state.epClipTexts) &&
    state.epClipTexts.length === clipItems.length &&
    !dirtyDup &&
    serverUnique >= Math.max(2, Math.floor(clipItems.length * 0.5))
  ) {
    for (let i = 0; i < clipItems.length; i++) {
      if (state.epClipTexts[i] != null) {
        clipItems[i].text = state.epClipTexts[i];
        segments[i] = state.epClipTexts[i];
      }
    }
  } else {
    state.epClipTexts = segments.map((t) => String(t || ""));
    state.epClipDirty = false;
  }

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
  const segText =
    (Array.isArray(state.epClipTexts) && state.epClipTexts[curSeg]) ||
    cur.text ||
    script;
  const epScriptMd = ep.script_md || "";
  const scoped = episodeScopedAssets(
    ep.ep_num,
    state.epAssetTab === "clip" ? segText : script || epScriptMd,
    epScriptMd || script
  );
  // 「全集资产」优先按分集正文挂角色/场景；视频脚本仅作补充
  if (state.epAssetTab === "all" && epScriptMd) {
    const fromScript = episodeScopedAssets(ep.ep_num, epScriptMd, epScriptMd);
    scoped.all = fromScript.all;
  }
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
    <div class="scene-strip h-scroll" id="sceneStrip">
      <button type="button" class="scene-tab ${state.epEditorScene === 0 ? "active" : ""}" data-scene="0">
        <strong>全部场</strong>
        <span class="muted">${clipItems.length} 段 · ${clipItems.reduce((a, c) => a + (c.dur || 9), 0)}s</span>
      </button>
      ${sceneList
        .map(
          (sc) => `<div class="scene-tab-wrap ${state.epEditorScene === sc.sceneIdx ? "active" : ""}">
            <button type="button" class="scene-tab ${state.epEditorScene === sc.sceneIdx ? "active" : ""}" data-scene="${sc.sceneIdx}">
              <strong>场${sc.sceneIdx}</strong>
              <span class="scene-name">${esc(sc.name)}</span>
              <span class="muted">${sc.clips.length} 段 · ${sc.totalSec}s</span>
            </button>
            <button type="button" class="btn sm danger scene-del" data-del-scene="${sc.sceneIdx}" title="删除场${sc.sceneIdx}及其全部段">删场</button>
          </div>`
        )
        .join("")}
      <button type="button" class="btn sm primary scene-add" id="btnAddScene" title="新增一场（含1段）">+ 场</button>
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
  const aspectRatio =
    (meta().aspect_ratio || "").trim() ||
    (Number(state.project?.width) > 0 &&
    Number(state.project?.height) > 0 &&
    Number(state.project.height) > Number(state.project.width)
      ? "9:16"
      : "16:9");
  const ratioClass = aspectRatio === "9:16" ? "ratio-9-16" : "ratio-16-9";
  const thumbClass = aspectRatio === "9:16" ? "thumb-portrait" : "thumb-landscape";

  const multiRefImgs = collectAssetImages().slice(0, 24);
  const multiRefOpen = !!state.epMultiRefOpen;
  root.className = `ep-editor ${ratioClass}${state.epLeftOpen ? " left-open" : ""}`;
  root.innerHTML = `
    <aside class="panel ep-left" id="epLeftPanel">
      <div class="panel-h">
        <div class="ep-asset-tabs h-scroll">
          <button type="button" class="asset-tab ${state.epAssetTab === "all" ? "active" : ""}" data-epatab="all">全集资产 ${totalAssets}</button>
          <button type="button" class="asset-tab ${state.epAssetTab === "clip" ? "active" : ""}" data-epatab="clip">当前片段 ${clipAssetN}</button>
          <button type="button" class="asset-tab ${state.epAssetTab === "material" ? "active" : ""}" data-epatab="material">素材 0</button>
        </div>
        <button type="button" class="btn sm ep-left-close" id="btnCloseEpLeft" title="收起">收起</button>
      </div>
      <div class="panel-b">
        <div class="h-scroll toolbar" style="margin-bottom:0.5rem">
          <button type="button" class="btn sm" id="btnSyncMissing">同步缺失资产</button>
          <button type="button" class="btn sm" id="btnGoAssets">前往资产库</button>
        </div>
        <h4 class="ep-sec">本集场列表 ${sceneList.length}
          <button type="button" class="btn sm primary" id="btnAddSceneSide" title="新增一场">+ 场</button>
        </h4>
        <div class="ep-scene-list">
          ${sceneList
            .map(
              (sc) => `<div class="ep-scene-row-wrap ${state.epEditorScene === sc.sceneIdx ? "on" : ""}">
                <button type="button" class="ep-scene-row ${state.epEditorScene === sc.sceneIdx ? "on" : ""}" data-scene="${sc.sceneIdx}">
                  <div class="ep-scene-row-t">
                    <strong>场${sc.sceneIdx}</strong>
                    <span class="muted">${sc.clips.length} 段 · ${sc.totalSec}s</span>
                  </div>
                  <div class="ep-scene-row-n">${esc(sc.name)}</div>
                </button>
                <button type="button" class="btn sm danger ep-scene-del" data-del-scene="${sc.sceneIdx}" title="删除场${sc.sceneIdx}">删</button>
              </div>`
            )
            .join("") || '<p class="muted">暂无场信息，请重新推理脚本</p>'}
        </div>
        <div class="ep-filter-chips h-scroll">
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
        <div class="multi-ref-block">
          <button type="button" class="btn sm multi-ref-toggle" id="btnToggleMultiRef" title="勾选后随本段视频提交；与首帧合计最多 9 张（人物/场景/道具）">
            ${multiRefOpen ? "收起多图参考" : "展开多图参考"}（${multiRefImgs.length}）· 视频最多9张
          </button>
          ${
            multiRefOpen
              ? `<div class="multi-ref" id="assetRefList">${
                  multiRefImgs
                    .map((a) => {
                      const on = refs.includes(a.path);
                      return `<div class="ref-chip ${on ? "selected" : ""}" data-path="${esc(a.path)}" title="${esc(a.label)}">
                        ${lazyImgHtml(a.path, { alt: a.label || "" })}
                        <span class="ref-chip-lab">${esc(a.label || "")}</span>
                      </div>`;
                    })
                    .join("") || '<span class="muted">暂无资产图</span>'
                }</div>`
              : `<p class="muted multi-ref-hint">默认不加载参考图。生成视频时自动带本镜点名的人物/场景/道具图；也可展开勾选，与首帧合计最多 9 张。</p><div class="multi-ref hidden" id="assetRefList"></div>`
          }
        </div>
      </div>
    </aside>
    <section class="panel ep-center">
      <div class="panel-h">
        <div class="ep-center-title">
          <strong>场${cur.sceneIdx} · ${esc(cur.sceneName)}</strong>
          <span class="muted ep-center-sub">${esc(cur.label)} · ${cur.dur || 9}秒</span>
        </div>
        <div class="h-scroll ep-center-actions">
          <button type="button" class="btn sm" id="btnToggleEpLeft">资产/参考</button>
          <button type="button" class="btn sm" id="btnRegenSeg">重新推理</button>
          <button type="button" class="btn sm" id="btnSaveSeg">保存</button>
          <button type="button" class="btn sm" id="btnCopySeg">复制</button>
        </div>
      </div>
      <div class="panel-b ep-center-body">
        ${sceneTabsHtml}
        ${jobsBanner}
        <div class="script-toolbar">
          <span class="muted">本段脚本（一份）· 点击角色/场景/道具名可查看资产 · 口播仅念引号内台词</span>
          <button type="button" class="btn sm" id="btnToggleScriptEdit">编辑脚本</button>
        </div>
        <div class="script-preview script-one" id="segScriptView"></div>
        <textarea class="script-edit script-one hidden" id="segScript" spellcheck="false"></textarea>
        <div class="ep-gen-block">
          <button type="button" class="btn primary ep-gen-main" id="btnGenSegVideo" ${generatingShots.has(Number(shot?.shot_num || curSeg + 1)) ? "disabled" : ""}>${generatingShots.has(Number(shot?.shot_num || curSeg + 1)) ? "本段生成中…" : (shot?.video_path ? "重新生成本段视频" : "生成本段视频")}</button>
          <div class="h-scroll ep-gen-bar">
            <select id="vidModel"><option>MiniMax H3 多图（首帧+参考≤9）</option></select>
            <select id="vidRatio">
              <option value="16:9" ${aspectRatio !== "9:16" ? "selected" : ""}>16:9</option>
              <option value="9:16" ${aspectRatio === "9:16" ? "selected" : ""}>9:16</option>
            </select>
            <span class="muted">768P · ${cur.dur || 9}s · 参考图 ${refs.length}</span>
            <button type="button" class="btn sm" id="btnRegenStillSeg" title="仅重新生成当前分段的分镜图">重新生成本段分镜图</button>
            <button type="button" class="btn sm" id="btnRegenStillSelected" title="重新生成勾选的多分镜图" ${selectedStillShotList().length ? "" : "disabled"}>重出所选分镜${selectedStillShotList().length ? `（${selectedStillShotList().length}）` : ""}</button>
          </div>
        </div>
        <div class="clip-nav-label">
          <span class="muted">场${state.epEditorScene || cur.sceneIdx} · ${visibleClips.length} 段 · 可左右滑</span>
          <div class="h-scroll clip-nav-actions">
            <button type="button" class="btn sm primary" id="btnAddSegment" title="在当前场末尾新增一段">+ 段</button>
            <button type="button" class="btn sm danger" id="btnDelSegment" title="删除当前选中段">删段</button>
            <button type="button" class="btn sm" id="btnStillSelectVisible">全选本场</button>
            <button type="button" class="btn sm" id="btnStillClearSelect">清空勾选</button>
          </div>
        </div>
        <div class="clip-strip h-scroll" id="clipStrip">
          ${visibleClips
            .map((c) => {
              const sn = Number(c.shot?.shot_num || c.i + 1);
              const gen = generatingShots.has(sn);
              ensureSelectSets();
              const checked = state.selectedStillShots.has(sn);
              const hasImg = !!(c.shot?.image_path || "").trim();
              const hasVid = !!(c.shot?.video_path || "").trim();
              const thumb = hasImg
                ? lazyImgHtml(c.shot.image_path, {
                    zoomable: true,
                    alt: c.label || `镜${sn}`,
                    ver: shotImgMtimes[sn] || shotImgMtimes[String(sn)] || "",
                  })
                : `<button type="button" class="clip-still-upload-cta" data-shot="${esc(String(sn))}" title="点击上传或拖入图片">上传</button>`;
              const stillActions = hasImg
                ? `<div class="clip-still-actions">
                    <button type="button" class="clip-still-btn clip-still-update" data-shot="${esc(String(sn))}" title="更新分镜图">更新</button>
                    <button type="button" class="clip-still-btn clip-still-del" data-shot="${esc(String(sn))}" title="删除分镜图">删除</button>
                  </div>`
                : "";
              const statusTag = gen
                ? `<span class="clip-gen-tag">生成中</span>`
                : hasVid
                  ? `<span class="clip-vid-tag" title="本段已生成视频">已成视频</span>`
                  : "";
              return `<div class="clip-wrap ${checked ? "still-checked" : ""} ${hasVid ? "has-video" : ""}">
                <label class="clip-check" title="勾选以多选重出分镜图">
                  <input type="checkbox" class="still-shot-check" data-shot="${esc(String(sn))}" ${checked ? "checked" : ""}/>
                  <span>选</span>
                </label>
                <div class="clip-thumb ${thumbClass}${hasImg ? "" : " clip-thumb-empty clip-thumb-drop"}" data-zoom-shot="${esc(String(sn))}" data-shot="${esc(String(sn))}" title="${hasImg ? "点击放大；右上角可更新/删除" : "点击上传或拖入分镜图"}">${thumb}${stillActions}${statusTag}
                  <input type="file" class="clip-still-file hidden" data-shot="${esc(String(sn))}" accept="image/*"/>
                </div>
                <button type="button" class="clip-card ${c.i === curSeg ? "active" : ""} ${gen ? "generating" : ""} ${hasVid ? "has-video" : ""}" data-seg="${c.i}" data-shot="${esc(String(sn))}">
                  <div>${esc(c.label)}</div>
                  <div class="muted">场${c.sceneIdx}段${c.segInScene}</div>
                  <div class="muted">${gen ? "生成中…" : hasVid ? `${c.dur || 9}s · 已成视频` : `${c.dur || 9}s`}</div>
                </button>
                <button type="button" class="btn sm clip-regen-still" data-shot="${esc(String(sn))}" title="重新生成该段分镜图">重新生成</button>
                <button type="button" class="btn sm danger clip-del-seg" data-shot="${esc(String(sn))}" title="删除该段">删段</button>
              </div>`;
            })
            .join("")}
        </div>
      </div>
    </section>
    <aside class="panel ep-right">
      <div class="panel-h"><strong>视频预览</strong>
        <div style="display:flex;gap:0.35rem;align-items:center;flex-wrap:wrap">
          ${
            shot?.video_path
              ? `<button type="button" class="btn sm primary" id="btnPlaySeg">本段视频</button>`
              : ""
          }
          ${
            ep.video_path
              ? `<button type="button" class="btn sm" id="btnPlayFinal" title="仅播放已有本集成片，不会重新生成">查看本集视频</button>`
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

  // 脚本正文用 .value / innerHTML 写入，避免 textarea 塞进模板字符串丢内容
  const segViewInit = document.getElementById("segScriptView");
  const segScriptInit = document.getElementById("segScript");
  if (segViewInit) {
    segViewInit.innerHTML = linkifyScriptHtml(segText);
  }
  if (segScriptInit) {
    segScriptInit.value = segText;
    state.epScriptBoundSeg = curSeg;
  }

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
  const btnGoAssets = document.getElementById("btnGoAssets");
  if (btnGoAssets) {
    btnGoAssets.onclick = () => {
      state.view = "project";
      state.step = "assets";
      state.epEditorId = null;
      render();
    };
  }
  const btnSyncMissing = document.getElementById("btnSyncMissing");
  if (btnSyncMissing) btnSyncMissing.onclick = () => void parseAssets();
  const btnToggleEpLeft = document.getElementById("btnToggleEpLeft");
  if (btnToggleEpLeft) {
    btnToggleEpLeft.onclick = () => {
      state.epLeftOpen = !state.epLeftOpen;
      void bindEpEditor();
    };
  }
  const btnCloseEpLeft = document.getElementById("btnCloseEpLeft");
  if (btnCloseEpLeft) {
    btnCloseEpLeft.onclick = () => {
      state.epLeftOpen = false;
      void bindEpEditor();
    };
  }
  const btnToggleMultiRef = document.getElementById("btnToggleMultiRef");
  if (btnToggleMultiRef) {
    btnToggleMultiRef.onclick = () => {
      state.epMultiRefOpen = !state.epMultiRefOpen;
      void bindEpEditor();
    };
  }
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
  const selectScene = async (sceneNo) => {
    try {
      captureEpSegDraft(curSeg);
      // 仅脏时落盘；禁止「有 textarea 就整集回写」把坏草稿覆盖服务器
      if (state.epClipDirty) {
        const text = document.getElementById("segScript")?.value ?? "";
        const full = mergeSegIntoStoryboard(clipItems, curSeg, text);
        await persistEpStoryboard(eid, full);
        ep.storyboard_md = full;
      }
    } catch (e) {
      toast(`自动保存失败：${e.message || e}（已丢弃本地坏草稿，从服务器重载）`);
      state.epClipDirty = false;
      state.epClipTexts = null;
      state.epScriptBoundSeg = null;
    }
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
  const runStructure = async (fn, okMsg) => {
    try {
      setBusyOverlay(true, "更新结构…");
      await fn();
      toast(okMsg || "已更新");
      state.epEditorSeg = Math.min(state.epEditorSeg || 0, 999);
      await bindEpEditor();
    } catch (e) {
      alert(`操作失败：${e.message || e}`);
    } finally {
      setBusyOverlay(false);
    }
  };
  const addSceneNow = () => {
    const name = prompt("新场名称（可留空）", "") || "";
    const after =
      state.epEditorScene > 0 ? state.epEditorScene : cur.sceneIdx || null;
    void runStructure(
      () =>
        api(`/api/episodes/${eid}/scenes`, {
          method: "POST",
          body: JSON.stringify({
            scene_name: name.trim() || null,
            after_scene: after || null,
          }),
        }),
      "已新增场"
    );
  };
  const delSceneNow = (si) => {
    const n = Number(si);
    if (!Number.isFinite(n) || n <= 0) return;
    if (!confirm(`删除场${n}及其全部段落？分镜图/视频会随段移除，且不可恢复。`)) return;
    void runStructure(
      () => api(`/api/episodes/${eid}/scenes/${n}`, { method: "DELETE" }),
      `已删除场${n}`
    );
  };
  document.getElementById("btnAddScene")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    addSceneNow();
  });
  document.getElementById("btnAddSceneSide")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    addSceneNow();
  });
  root.querySelectorAll("[data-del-scene]").forEach((b) => {
    b.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      delSceneNow(b.dataset.delScene);
    };
  });
  document.getElementById("btnAddSegment")?.addEventListener("click", () => {
    const sceneIdx = state.epEditorScene > 0 ? state.epEditorScene : cur.sceneIdx;
    const afterSn = Number(shot?.shot_num || 0) || null;
    void runStructure(
      () =>
        api(`/api/episodes/${eid}/segments`, {
          method: "POST",
          body: JSON.stringify({
            scene_idx: sceneIdx || 1,
            after_shot_num: afterSn,
          }),
        }),
      "已新增段"
    );
  });
  const delSegNow = (sn) => {
    const n = Number(sn);
    if (!Number.isFinite(n) || n <= 0) return;
    if (!confirm(`删除镜${n} 这一段？对应分镜图/视频将移除。`)) return;
    void runStructure(async () => {
      await api(`/api/episodes/${eid}/segments/${n}`, { method: "DELETE" });
      if (state.epEditorSeg > 0) state.epEditorSeg -= 1;
    }, `已删除镜${n}`);
  };
  document.getElementById("btnDelSegment")?.addEventListener("click", () => {
    delSegNow(shot?.shot_num || curSeg + 1);
  });
  root.querySelectorAll(".clip-del-seg").forEach((b) => {
    b.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      delSegNow(b.dataset.shot);
    };
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
    c.onclick = (ev) => {
      if (ev.target.closest("[data-stop], .ep-asset-regen")) return;
      showEditAsset(c.dataset.akind, c.dataset.aid);
    };
  });
  root.querySelectorAll(".ep-asset-regen").forEach((b) => {
    b.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      void regenerateAssetImage(b.dataset.regenKind, b.dataset.regenAsset);
    };
  });
  root.querySelectorAll("[data-seg]").forEach((b) => {
    b.onclick = async () => {
      const idx = parseInt(b.dataset.seg, 10) || 0;
      // 切换前先落盘当前段，避免「保存了但一切换就丢」
      try {
        captureEpSegDraft(curSeg);
        // 仅用户改过本段时才整集保存，避免切换片段把重复 P001 写回库
        if (state.epClipDirty) {
          const text = document.getElementById("segScript")?.value ?? state.epClipTexts?.[curSeg] ?? "";
          const full = mergeSegIntoStoryboard(clipItems, curSeg, text);
          await persistEpStoryboard(eid, full);
          ep.storyboard_md = full;
        }
      } catch (e) {
        toast(`自动保存失败：${e.message || e}（已丢弃本地坏草稿，从服务器重载）`);
        state.epClipDirty = false;
        state.epClipTexts = null;
        state.epScriptBoundSeg = null;
      }
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
  const segScriptEl = document.getElementById("segScript");
  const segViewEl = document.getElementById("segScriptView");
  const btnToggleScriptEdit = document.getElementById("btnToggleScriptEdit");
  if (btnToggleScriptEdit && segScriptEl && segViewEl) {
    btnToggleScriptEdit.onclick = async () => {
      const editing = !segScriptEl.classList.contains("hidden");
      if (editing) {
        // 退出编辑 → 同步预览并自动落盘
        captureEpSegDraft(curSeg);
        segViewEl.innerHTML = linkifyScriptHtml(segScriptEl.value);
        bindAssetLinks(segViewEl);
        segScriptEl.classList.add("hidden");
        segViewEl.classList.remove("hidden");
        btnToggleScriptEdit.textContent = "编辑脚本";
        try {
          const text = segScriptEl.value;
          const full = mergeSegIntoStoryboard(clipItems, curSeg, text);
          await persistEpStoryboard(eid, full);
          ep.storyboard_md = full;
          toast("已自动保存");
        } catch (e) {
          toast(`自动保存失败：${e.message || e}`);
        }
      } else {
        segScriptEl.classList.remove("hidden");
        segViewEl.classList.add("hidden");
        btnToggleScriptEdit.textContent = "完成编辑";
        segScriptEl.focus();
      }
    };
    segScriptEl.oninput = () => {
      captureEpSegDraft(curSeg);
    };
  }
  bindAssetLinks(root);
  document.getElementById("btnRegenStillSeg").onclick = async () => {
    const sn = Number(shot?.shot_num || curSeg + 1);
    await startStillJob(eid, { mode: "generate", shot_nums: [sn] });
  };
  document.getElementById("btnRegenStillSelected")?.addEventListener("click", async () => {
    const nums = selectedStillShotList();
    if (!nums.length) {
      alert("请先勾选要重出的分镜片段");
      return;
    }
    await startStillJob(eid, { mode: "generate", shot_nums: nums });
  });
  document.getElementById("btnStillSelectVisible")?.addEventListener("click", () => {
    ensureSelectSets();
    visibleClips.forEach((c) => {
      const sn = Number(c.shot?.shot_num || c.i + 1);
      if (Number.isFinite(sn) && sn > 0) state.selectedStillShots.add(sn);
    });
    void bindEpEditor();
  });
  document.getElementById("btnStillClearSelect")?.addEventListener("click", () => {
    ensureSelectSets();
    state.selectedStillShots.clear();
    void bindEpEditor();
  });
  root.querySelectorAll(".still-shot-check").forEach((ck) => {
    ck.onchange = (e) => {
      e.stopPropagation();
      toggleSelectedStillShot(ck.dataset.shot);
      void bindEpEditor();
    };
    ck.onclick = (e) => e.stopPropagation();
  });
  root.querySelectorAll(".clip-regen-still").forEach((b) => {
    b.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      const sn = Number(b.dataset.shot);
      if (!Number.isFinite(sn) || sn <= 0) return;
      void startStillJob(eid, { mode: "generate", shot_nums: [sn] });
    };
  });
  bindClipStillUploadControls(root, eid);
  document.getElementById("btnGenSegVideo").onclick = async () => {
    const sn = Number(shot?.shot_num || curSeg + 1);
    const hasVid = !!(shot?.video_path || "").trim();
    // 编辑框有改动时先保存脚本，再按当前脚本重出（否则仍用库里旧对白）
    const segEl = document.getElementById("segScript");
    if (segEl) {
      try {
        captureEpSegDraft(curSeg);
        const text = segEl.value;
        const full = mergeSegIntoStoryboard(clipItems, curSeg, text);
        const { md } = await persistEpStoryboard(eid, full);
        ep.storyboard_md = md;
        if (clipItems[curSeg]) clipItems[curSeg].text = text;
        if (segments[curSeg] != null) segments[curSeg] = text;
      } catch (e) {
        toast(`保存脚本失败：${e.message || e}`);
        return;
      }
    }
    if (hasVid) {
      if (
        !confirm(
          `本段（镜${sn}）已有视频。将整段重新生成一条成片（模型原生音轨，不做混音拼接）并覆盖。是否继续？`
        )
      ) {
        return;
      }
    }
    state.preferSegPreview = true; // 重出本段后优先播本段，不要被整集成片盖住
    await startVideoJob(eid, {
      shot_nums: [sn],
      ref_image_paths: state.selectedRefs,
      confirm_full_episode: false,
      fresh_video: true,
      skip_existing: false,
      auto_concat: false,
      no_web_remux: true,
      preview_with_audio: true,
    });
  };
  document.getElementById("btnSaveSeg").onclick = async () => {
    const segEl = document.getElementById("segScript");
    if (!segEl) {
      toast("保存失败：找不到编辑框");
      return;
    }
    // 若仍在预览模式，先切到编辑框再读（预览改不了字；兼容误点）
    if (segEl.classList.contains("hidden")) {
      const viewEl = document.getElementById("segScriptView");
      const btn = document.getElementById("btnToggleScriptEdit");
      segEl.classList.remove("hidden");
      viewEl?.classList.add("hidden");
      if (btn) btn.textContent = "完成编辑";
    }
    const text = segEl.value;
    captureEpSegDraft(curSeg);
    const full = mergeSegIntoStoryboard(clipItems, curSeg, text);
    try {
      const { md } = await persistEpStoryboard(eid, full);
      ep.storyboard_md = md;
      if (clipItems[curSeg]) clipItems[curSeg].text = text;
      if (segments[curSeg] != null) segments[curSeg] = text;
      const viewEl = document.getElementById("segScriptView");
      if (viewEl) viewEl.innerHTML = linkifyScriptHtml(text);
      toast("已保存");
    } catch (e) {
      toast(`保存失败：${e.message || e}`);
    }
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
        // 先清空再赋新 URL，避免同 path 时浏览器继续播缓存旧片
        v.removeAttribute("src");
        v.load?.();
        v.src = url;
        v.load?.();
      }
      if (lab) lab.textContent = label || "";
    } catch (e) {
      toast(`加载失败：${e.message || e}`);
    }
  };
  // 点击/选中片段时默认播本段；本集成片只在点「查看本集视频」时播放
  if (state.preferSegPreview && shot?.video_path) {
    state.preferSegPreview = false;
    void playPreview(shot.video_path, `正在播放：${cur.label}（刚重生成）`);
  } else if (shot?.video_path) {
    void playPreview(shot.video_path, `正在播放：${cur.label}`);
  } else if (ep.video_path) {
    void playPreview(ep.video_path, "正在播放：本集视频（当前段暂无分段视频）");
  }
  document.getElementById("btnPlayFinal")?.addEventListener("click", () =>
    void playPreview(ep.video_path, "正在播放：本集视频（仅查看，不生成）")
  );
  document.getElementById("btnPlaySeg")?.addEventListener("click", () =>
    void playPreview(shot?.video_path, `正在播放：${cur.label}`)
  );
  bindZoomableImages(root);
  if (state.jobsOpen) paintJobsDrawer();
}

function splitScriptSegments(text) {
  const t = text || "";
  // 兼容全角｜与半角|
  const parts = t.split(/(?=^P\d+[｜|])/m).filter((x) => x.trim());
  return parts.length ? parts : t ? [t] : [];
}

/** 把当前编辑段写回整集脚本（与侧栏片段列表一一对应，禁止整集被单段覆盖） */
function mergeSegIntoStoryboard(clipItems, curSeg, segText) {
  const draft =
    Array.isArray(state.epClipTexts) && state.epClipTexts.length
      ? state.epClipTexts
      : (clipItems || []).map((c) => String(c?.text ?? ""));
  const parts = draft.map((t) => String(t ?? ""));
  if (!parts.length) return String(segText ?? "");
  if (curSeg >= 0 && curSeg < parts.length) {
    parts[curSeg] = String(segText ?? "");
  }
  // 防御：若合并结果 P 号严重重复，回退为侧栏各段原文，避免整场被 P001 覆盖
  const nums = parts
    .map((t) => ((String(t || "").trim().match(/^P(\d+)[｜|]/m) || [])[1] || ""))
    .filter(Boolean);
  if (nums.length >= 3 && new Set(nums).size < Math.max(2, Math.floor(nums.length * 0.6))) {
    const fallback = (clipItems || []).map((c, i) =>
      i === curSeg ? String(segText ?? c?.text ?? "") : String(c?.text ?? "")
    );
    const fbNums = fallback
      .map((t) => ((String(t || "").trim().match(/^P(\d+)[｜|]/m) || [])[1] || ""))
      .filter(Boolean);
    if (new Set(fbNums).size >= new Set(nums).size) {
      return fallback.filter((t) => String(t || "").trim()).join("\n\n");
    }
  }
  return parts.join("\n\n");
}

/** 从 textarea 同步当前段到内存草稿（仅内容变化时标脏） */
function captureEpSegDraft(curSeg) {
  const segEl = document.getElementById("segScript");
  if (!segEl || !Array.isArray(state.epClipTexts)) return;
  // textarea 仍显示旧段时，必须以 boundSeg 为准，禁止用已切换的 epEditorSeg 写错位
  const idx =
    Number.isInteger(state.epScriptBoundSeg) &&
    state.epScriptBoundSeg >= 0 &&
    state.epScriptBoundSeg < state.epClipTexts.length
      ? state.epScriptBoundSeg
      : curSeg;
  if (idx < 0 || idx >= state.epClipTexts.length) return;
  const next = String(segEl.value ?? "");
  if (state.epClipTexts[idx] === next) return;
  state.epClipTexts[idx] = next;
  state.epClipDirty = true;
}

/** 保存整集视频脚本（storyboard_md），返回服务端最新文本 */
async function persistEpStoryboard(eid, full) {
  const nums = String(full || "")
    .split(/\n/)
    .map((ln) => (ln.match(/^P(\d+)[｜|]/) || [])[1])
    .filter(Boolean);
  if (nums.length >= 2 && new Set(nums).size < nums.length) {
    throw new Error("脚本 P 段编号重复（疑似坏草稿），已阻止覆盖服务器");
  }
  if (/【台词】[^「\"“\n]*[「\"“][^」\"”]{0,5}[，,][」\"”]/.test(String(full || ""))) {
    throw new Error("检测到残句台词（如「白晓月，」），已阻止覆盖服务器");
  }
  const saved = await api(`/api/episodes/${eid}`, {
    method: "PATCH",
    body: JSON.stringify({ storyboard_md: full }),
  });
  const md = (saved && saved.storyboard_md) || full;
  const epIdx = (state.episodes || []).findIndex((e) => e.id === eid);
  if (epIdx >= 0) state.episodes[epIdx].storyboard_md = md;
  // 用服务端回写刷新草稿，避免本地与库不一致
  const { segments: parts } = parseSceneClipTree(md, (saved && saved.shots) || []);
  state.epClipTexts = parts.slice();
  state.epClipDirty = false;
  return { saved, md };
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
  document.getElementById("btnSync")?.addEventListener("click", async () => {
    if (!state.projectId) return;
    try {
      if (state.step === "outline" && document.getElementById("metaStyle")) {
        await saveProjectArtMeta();
      }
      toast("本剧配置已同步");
      await openProject(state.projectId);
    } catch (e) {
      alert(`同步失败：${e.message || e}`);
    }
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
  document.querySelectorAll("[data-del-id]").forEach((btn) => {
    btn.onclick = (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      void deleteProject(btn.dataset.delId, btn.dataset.delName || "");
    };
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
  bindArtStyleField("import");
}

function bindParsing() {
  document.getElementById("btnParseBack").onclick = () => {
    state.view = "project";
    state.step = "outline";
    render();
  };
}

function bindProject() {
  bindArtStyleField("meta");
  document.getElementById("btnSaveStyle")?.addEventListener("click", async () => {
    try {
      await saveProjectArtMeta();
      toast("本剧画风已保存");
      render();
    } catch (e) {
      alert(`保存失败：${e.message || e}`);
    }
  });
  document.getElementById("btnDeleteProject")?.addEventListener("click", () => {
    void deleteProject(state.projectId, state.project?.name || "");
  });
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
      if (ev.target.closest("[data-stop], .asset-check, .asset-select, .asset-regen-btn")) return;
      if (c.dataset.akind === "voices") void showEditVoice(c.dataset.aid);
      else showEditAsset(c.dataset.akind, c.dataset.aid);
    };
  });
  document.querySelectorAll(".asset-regen-btn").forEach((b) => {
    b.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      void regenerateAssetImage(b.dataset.regenKind, b.dataset.regenAsset);
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
  if (bt) bt.onclick = () => void batchGenStills({ regenerate: false });
  const btr = document.getElementById("btnBatchRegenStills");
  if (btr) btr.onclick = () => void batchGenStills({ regenerate: true });
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
  document.getElementById("btnAddEpisode")?.addEventListener("click", () => {
    void (async () => {
      const title = prompt("新分集标题（可留空）", "") || "";
      try {
        setBusyOverlay(true, "新增分集…");
        const ep = await api(`/api/projects/${state.projectId}/episodes`, {
          method: "POST",
          body: JSON.stringify({ title: title.trim() || null }),
        });
        toast(`已新增第${ep.ep_num}集`);
        await openProject(state.projectId);
        state.step = "videos";
        render();
      } catch (e) {
        alert(`新增失败：${e.message || e}`);
      } finally {
        setBusyOverlay(false);
      }
    })();
  });
  document.querySelectorAll("[data-del-ep]").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      const id = b.dataset.delEp;
      const label = b.dataset.delEpLabel || "该集";
      if (!confirm(`确定删除「${label}」？脚本、分镜图与视频将一并删除，且不可恢复。`)) return;
      void (async () => {
        try {
          setBusyOverlay(true, "删除分集…");
          await api(`/api/episodes/${id}`, { method: "DELETE" });
          toast(`已删除${label}`);
          ensureSelectSets();
          state.selectedEpisodeIds.delete(id);
          await openProject(state.projectId);
          state.step = "videos";
          render();
        } catch (e) {
          alert(`删除失败：${e.message || e}`);
        } finally {
          setBusyOverlay(false);
        }
      })();
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

function readImportSettings() {
  return {
    art_style: (document.getElementById("importStyle")?.value || "").trim() || "半写实3D国漫",
    aspect_ratio: document.getElementById("importRatio")?.value || "16:9",
    mode: document.getElementById("importMode")?.value || "默认版",
  };
}

async function importFile(file) {
  // 先读首页导入栏设置，避免跳转后丢失画风/画幅
  const settings = readImportSettings();
  const fd = new FormData();
  fd.append("file", file);
  fd.append("name", file.name.replace(/\.[^.]+$/, ""));
  fd.append("art_style", settings.art_style);
  fd.append("aspect_ratio", settings.aspect_ratio);
  fd.append("mode", settings.mode);
  toast("正在导入…");
  try {
    const proj = await api("/api/studio/import-file", { method: "POST", body: fd });
    toast("导入成功（已自动排版为系统标准格式），开始解析剧本…");
    await openProject(proj.id);
    await parseScript(settings);
  } catch (e) {
    alert(`导入失败：${e.message || e}`);
  }
}

async function importText(text, name) {
  const settings = readImportSettings();
  toast("正在导入…");
  try {
    const proj = await api("/api/studio/import", {
      method: "POST",
      body: JSON.stringify({
        name: name || "",
        novel_text: text,
        art_style: settings.art_style,
        aspect_ratio: settings.aspect_ratio,
        mode: settings.mode,
      }),
    });
    await openProject(proj.id);
    await parseScript(settings);
  } catch (e) {
    alert(`导入失败：${e.message || e}`);
  }
}

async function parseScript(forcedSettings = null) {
  const useLlm = !!state.config?.storyboard_image_api_ready || !!state.config?.ohmygpt?.key_configured;
  // 强制设置优先（导入栏）；其次大纲页当前值；再回退项目已存画风/画幅
  const style =
    (forcedSettings?.art_style || "").trim() ||
    document.getElementById("metaStyle")?.value?.trim() ||
    projectArtStyle();
  const ratio =
    (forcedSettings?.aspect_ratio || "").trim() ||
    document.getElementById("metaRatio")?.value ||
    meta().aspect_ratio ||
    "16:9";
  const mode =
    (forcedSettings?.mode || "").trim() ||
    document.getElementById("metaMode")?.value ||
    meta().mode ||
    "默认版";
  state.view = "parsing";
  state.parsing = {
    title: "剧本解析中",
    sub: `使用本剧画风「${style}」· 画幅 ${ratio}`,
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
    await saveProjectArtMeta({ art_style: style, aspect_ratio: ratio, mode });
    await api(`/api/projects/${state.projectId}/studio/parse-script`, {
      method: "POST",
      body: JSON.stringify({
        use_llm: useLlm,
        art_style: style,
        aspect_ratio: ratio,
        mode,
      }),
    });
    clearInterval(tick);
    await openProject(state.projectId);
    state.step = "outline";
    toast(`剧本解析完成（${style} · ${ratio}）`);
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
    sub: "角色、场景和道具使用本剧画风与剧本理解",
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
    // parse-assets 内部已启发式+必要时 LLM 抽取/润色；勿再二次 enrich（易转圈超时）
    await api(`/api/projects/${state.projectId}/studio/parse-assets`, {
      method: "POST",
      body: JSON.stringify({ use_llm: useLlm }),
    });
    clearInterval(tick);
    await openProject(state.projectId);
    state.step = "assets";
    const n =
      (state.characters || []).length +
      (state.scenes || []).length +
      (state.props || []).length;
    toast(
      n
        ? `资产解析完成：角色 ${(state.characters || []).length} · 场景 ${(state.scenes || []).length} · 道具 ${(state.props || []).length}`
        : useLlm
          ? "资产解析完成，但未识别到角色/场景；请检查剧本格式或稍后重试"
          : "未识别到角色。请在制作设置配置 OhMyGPT Key 后重新解析（支持网文）"
    );
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
    "btnBatchRegenStills",
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
  // 短剧场标剧本默认走结构化拆段（保完整台词）；force 重推时才尝试 LLM（服务端仍会校验并可能回退）
  const useLlm = !!force && (!!state.config?.ohmygpt?.key_configured || !!state.config?.storyboard_image_api_ready);
  setBusyOverlay(true, useLlm ? "整集重新推理中（约 1–3 分钟）…" : "正在按剧本场次生成视频脚本…");
  toast(useLlm ? "整集重新推理已开始…" : "正在按剧本场次生成视频脚本…");
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

/** 收集同集角色/场景/道具参考图路径，提交出图/视频时自动带上 */
function collectEpisodeAssetRefPaths(epNum, { limit = 8 } = {}) {
  const n = Number(epNum) || 0;
  const out = [];
  const pushItem = (item) => {
    if (!item) return;
    const eps = Array.isArray(item.episodes) ? item.episodes.map(Number) : [];
    if (n > 0 && eps.length && !eps.includes(n)) return;
    const imgs = Array.isArray(item.ref_images)
      ? item.ref_images
      : item.ref_image
        ? [{ path: item.ref_image }]
        : [];
    for (const im of imgs) {
      const p = (im && (im.path || im)) || "";
      const rel = String(p || "").trim();
      if (!rel || out.includes(rel)) continue;
      out.push(rel);
      if (out.length >= limit) return;
    }
  };
  (state.characters || []).forEach(pushItem);
  (state.scenes || []).forEach(pushItem);
  (state.props || []).forEach(pushItem);
  return out;
}

function mergeRefImagePaths(epNum, manual) {
  const selected = Array.isArray(manual)
    ? manual.map(String).filter(Boolean)
    : Array.isArray(state.selectedRefs)
      ? state.selectedRefs.map(String).filter(Boolean)
      : [];
  const auto = collectEpisodeAssetRefPaths(epNum, { limit: 8 });
  const out = [];
  for (const p of [...selected, ...auto]) {
    if (p && !out.includes(p)) out.push(p);
    if (out.length >= 8) break;
  }
  return out;
}

async function startVideoJob(episodeId, params) {
  try {
    const base = params || {
      confirm_full_episode: true,
      auto_concat: false,
      skip_existing: true,
    };
    const ep =
      (state.episodes || []).find((e) => e.id === episodeId) ||
      (await api(`/api/episodes/${episodeId}`).catch(() => null));
    const refs = mergeRefImagePaths(ep?.ep_num, base.ref_image_paths);
    // 成片一律模型原生音轨直出，禁止静音片 + Web/TTS 后配
    const bodyParams = {
      ...base,
      preview_with_audio: true,
      no_web_remux: true,
      ref_image_paths: refs,
    };
    const job = await api(`/api/projects/${state.projectId}/jobs`, {
      method: "POST",
      body: JSON.stringify({
        stage_id: "video",
        episode_id: episodeId,
        params: bodyParams,
      }),
    });
    toast(`已开始生成带声音视频 ${String(job.id || "").slice(0, 8)}…（可随时停止）`);
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

async function uploadShotStillImage(episodeId, shotNum, file) {
  if (!episodeId || !file || !Number.isFinite(shotNum) || shotNum <= 0) return null;
  const fd = new FormData();
  fd.append("file", file);
  const res = await api(`/api/episodes/${episodeId}/shots/${shotNum}/image`, {
    method: "POST",
    body: fd,
  });
  // 同步内存里的镜头表，便于立刻重绘
  const ep = (state.episodes || []).find((e) => e.id === episodeId);
  if (ep && Array.isArray(ep.shots)) {
    const row = ep.shots.find((s) => Number(s.shot_num) === Number(shotNum));
    if (row) row.image_path = res.image_path || "";
  }
  if (ep?.production?.shot_image_mtimes) {
    ep.production.shot_image_mtimes[shotNum] = Date.now();
  }
  return res;
}

async function deleteShotStillImage(episodeId, shotNum) {
  if (!episodeId || !Number.isFinite(shotNum) || shotNum <= 0) return null;
  const res = await api(`/api/episodes/${episodeId}/shots/${shotNum}/image`, {
    method: "DELETE",
  });
  const ep = (state.episodes || []).find((e) => e.id === episodeId);
  if (ep && Array.isArray(ep.shots)) {
    const row = ep.shots.find((s) => Number(s.shot_num) === Number(shotNum));
    if (row) row.image_path = "";
  }
  return res;
}

function bindClipStillUploadControls(root, episodeId) {
  if (!root || !episodeId) return;
  const pickFile = (sn) => {
    const inp = root.querySelector(`.clip-still-file[data-shot="${sn}"]`);
    if (inp) {
      inp.value = "";
      inp.click();
    }
  };
  const applyFile = async (sn, file) => {
    if (!file || !String(file.type || "").startsWith("image/")) {
      toast("请选择图片文件");
      return;
    }
    try {
      setBusyOverlay(true, `上传镜${sn}分镜图…`);
      await uploadShotStillImage(episodeId, sn, file);
      toast(`镜${sn} 分镜图已上传`);
      if (state.view === "epEditor") await bindEpEditor();
      else {
        await refreshPipeline();
        render();
      }
    } catch (e) {
      alert(`上传失败：${e.message || e}`);
    } finally {
      setBusyOverlay(false);
    }
  };
  root.querySelectorAll(".clip-still-update, .clip-still-upload-cta").forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      const sn = Number(btn.dataset.shot);
      if (!Number.isFinite(sn) || sn <= 0) return;
      pickFile(sn);
    };
  });
  root.querySelectorAll(".clip-still-del").forEach((btn) => {
    btn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const sn = Number(btn.dataset.shot);
      if (!Number.isFinite(sn) || sn <= 0) return;
      if (!confirm(`删除镜${sn} 的分镜图？删除后可重新上传。`)) return;
      try {
        await deleteShotStillImage(episodeId, sn);
        toast(`镜${sn} 分镜图已删除，可点击上传`);
        if (state.view === "epEditor") await bindEpEditor();
        else {
          await refreshPipeline();
          render();
        }
      } catch (err) {
        alert(`删除失败：${err.message || err}`);
      }
    };
  });
  root.querySelectorAll(".clip-still-file").forEach((inp) => {
    inp.onchange = () => {
      const sn = Number(inp.dataset.shot);
      const file = inp.files?.[0];
      if (file) void applyFile(sn, file);
    };
  });
  root.querySelectorAll(".clip-thumb-drop, .clip-thumb").forEach((thumb) => {
    const sn = Number(thumb.dataset.shot || thumb.dataset.zoomShot);
    if (!Number.isFinite(sn) || sn <= 0) return;
    thumb.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.stopPropagation();
      thumb.classList.add("clip-thumb-dragover");
    });
    thumb.addEventListener("dragleave", () => {
      thumb.classList.remove("clip-thumb-dragover");
    });
    thumb.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      thumb.classList.remove("clip-thumb-dragover");
      const file = e.dataTransfer?.files?.[0];
      if (file) void applyFile(sn, file);
    });
  });
}

async function startStillJob(episodeId, opts = {}) {
  const mode = opts.mode || "generate"; // generate=可重出；fill=只补缺
  const shotNums = Array.isArray(opts.shot_nums)
    ? opts.shot_nums.map(Number).filter((n) => Number.isFinite(n) && n > 0)
    : [];
  try {
    const ep =
      (state.episodes || []).find((e) => e.id === episodeId) ||
      (await api(`/api/episodes/${episodeId}`).catch(() => null));
    const d = ep?.production || {};
    const total = Number(d.shots_total || (ep?.shots || []).length || 0);
    const have = Number(d.shots_with_image || 0);
    let skipExisting = mode === "fill";
    let freshImages = false;

    if (shotNums.length) {
      const label =
        shotNums.length === 1 ? `镜${shotNums[0]}` : `镜 ${shotNums.join("、")}`;
      if (!confirm(`重新生成${label}的分镜图？将覆盖该段已有图片。`)) return;
      skipExisting = false;
      freshImages = true;
    } else if (mode === "generate" && have > 0) {
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

    const params = {
      confirm_full_episode: !shotNums.length,
      skip_existing: skipExisting,
      fresh_images: freshImages,
    };
    if (shotNums.length) params.shot_nums = shotNums;

    const job = await api(`/api/projects/${state.projectId}/jobs`, {
      method: "POST",
      body: JSON.stringify({
        stage_id: "storyboard_images",
        episode_id: episodeId,
        params,
      }),
    });
    toast(
      shotNums.length
        ? `已提交重出分镜图（镜${shotNums.join("、")}） ${String(job.id || "").slice(0, 8)}…`
        : freshImages
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

/** 重新生成单个资产设定图（角色/场景/道具）。 */
async function regenerateAssetImage(kind, id, opts = {}) {
  const k = (kind || "characters").trim();
  if (!id) return;
  if (!state.config?.ohmygpt?.key_configured && !state.config?.storyboard_image_api_ready) {
    alert("请先在「制作设置」配置 OhMyGPT Key");
    return showSettings();
  }
  const label =
    k === "scenes" ? "场景" : k === "props" ? "道具" : "角色";
  if (!opts.silent && !confirm(`重新生成该${label}设定图？将覆盖/新增参考图。`)) return;
  setBusyOverlay(true, `重新生成${label}图…`);
  try {
    if (k === "characters") {
      const list = state.characters || [];
      const ch = list.find((x) => x.id === id);
      const first = (ch?.ref_images || [])[0];
      await api(`/api/projects/${state.projectId}/characters/${id}/generate-image`, {
        method: "POST",
        body: JSON.stringify({
          angle_label: (first?.label || "").replace(/^AI·/, "") || "基础三视图",
          sheet_mode: "body",
          appearance_override: first?.desc || ch?.visual_prompt || "",
          replace_path: first?.path || "",
        }),
      });
    } else {
      await api(`/api/projects/${state.projectId}/studio/batch-generate-images`, {
        method: "POST",
        body: JSON.stringify({
          kind: k,
          only_missing: false,
          limit: 1,
          ids: [id],
        }),
      });
    }
    await openProject(state.projectId);
    if (state.view === "epEditor") void bindEpEditor();
    else {
      state.step = "assets";
      if (k === "scenes" || k === "props" || k === "characters") state.assetTab = k;
      render();
    }
    toast(`${label}图已重新生成`);
  } catch (e) {
    alert(`重新生成失败：${e.message || e}`);
  } finally {
    setBusyOverlay(false);
  }
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

async function batchGenStills(opts = {}) {
  const regenerate = !!opts.regenerate;
  const sel = selectedEpisodes();
  const pool = sel.length ? sel : state.episodes || [];
  const list = pool.filter((e) => (e.storyboard_md || "").trim());
  if (!list.length) {
    alert(sel.length ? "已选分集中没有视频脚本，请先生成脚本" : "请先生成视频脚本");
    return;
  }
  const scope = sel.length ? `已选 ${list.length} 集` : `${list.length} 集`;
  if (
    !confirm(
      regenerate
        ? `将为${scope}逐个全部重新生成分镜图（覆盖已有图，一集完成再下一集）。是否继续？`
        : `将为${scope}逐个补缺分镜图（不改已有图，一集完成再下一集）。是否继续？`
    )
  ) {
    return;
  }
  let ok = 0;
  let fail = 0;
  let stopped = false;
  const label = regenerate ? "重出分镜" : "补缺分镜";
  setBusyOverlay(true, `${label} 0/${list.length}…`);
  try {
    for (let i = 0; i < list.length; i++) {
      const e = list[i];
      setBusyOverlay(true, `${label} ${i + 1}/${list.length} · 第${e.ep_num}集`);
      let job;
      try {
        job = await api(`/api/projects/${state.projectId}/jobs`, {
          method: "POST",
          body: JSON.stringify({
            stage_id: "storyboard_images",
            episode_id: e.id,
            params: {
              confirm_full_episode: true,
              skip_existing: !regenerate,
              fresh_images: regenerate,
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
    if (!stopped) toast(`${label}结束：完成 ${ok} 集，失败 ${fail} 集`);
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
  // 仅刷新任务条，不重建视频节点；插在场名条之后，避免挡住场名
  let bannerHost = root.querySelector(".active-jobs-banner");
  const bannerHtml = renderActiveJobsBanner(epJobs);
  const centerBody = root.querySelector(".ep-center-body");
  if (bannerHtml) {
    if (bannerHost) bannerHost.outerHTML = bannerHtml;
    else if (centerBody) {
      const strip = centerBody.querySelector(".scene-strip");
      if (strip) strip.insertAdjacentHTML("afterend", bannerHtml);
      else centerBody.insertAdjacentHTML("afterbegin", bannerHtml);
    }
    bannerHost = root.querySelector(".active-jobs-banner");
    if (bannerHost) bindJobActionButtons(bannerHost);
  } else if (bannerHost) {
    bannerHost.remove();
  }
  const epSoft = (state.episodes || []).find((e) => e.id === eid);
  const shotBySn = Object.fromEntries(
    (epSoft?.shots || []).map((s) => [Number(s.shot_num), s])
  );
  root.querySelectorAll(".clip-wrap").forEach((wrap) => {
    const card = wrap.querySelector(".clip-card[data-seg]");
    if (!card) return;
    const sn = parseInt(card.getAttribute("data-shot") || String((parseInt(card.dataset.seg, 10) || 0) + 1), 10);
    const gen = generatingShots.has(Number(sn));
    const hasVid = !!(shotBySn[Number(sn)]?.video_path || "").trim();
    card.classList.toggle("generating", gen);
    card.classList.toggle("has-video", hasVid);
    wrap.classList.toggle("has-video", hasVid);
    const thumb = wrap.querySelector(".clip-thumb");
    const genTag = thumb?.querySelector(".clip-gen-tag");
    const vidTag = thumb?.querySelector(".clip-vid-tag");
    if (gen) {
      if (vidTag) vidTag.remove();
      if (thumb && !genTag) {
        thumb.insertAdjacentHTML("beforeend", `<span class="clip-gen-tag">生成中</span>`);
      }
    } else {
      if (genTag) genTag.remove();
      if (hasVid && thumb && !thumb.querySelector(".clip-vid-tag")) {
        thumb.insertAdjacentHTML(
          "beforeend",
          `<span class="clip-vid-tag" title="本段已生成视频">已成视频</span>`
        );
      } else if (!hasVid && vidTag) {
        vidTag.remove();
      }
    }
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
    else {
      const ep0 = (state.episodes || []).find((e) => e.id === eid);
      const shot0 = (ep0?.shots || []).find((s) => Number(s.shot_num) === Number(sn));
      btn.textContent = shot0?.video_path ? "重新生成本段视频" : "生成本段视频";
    }
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
  const noteLabel =
    kind === "characters" ? "角色描述" : kind === "scenes" ? "场景描述" : "资产说明";
  const notePh =
    kind === "characters"
      ? "身份、性格与剧情作用（勿只填姓名）"
      : kind === "scenes"
        ? "空间氛围与用途说明（勿与标题相同）"
        : "材质、用途与剧情作用（勿与名称相同）";
  const promptPh =
    kind === "characters"
      ? "填写本张设定的外表（五官/发型发饰/服装材质纹样/鞋子）。\n主设定图（一张合集）：全身正/左/背/右 + 头部正/左/右 + 局部特写，同框必出。"
      : kind === "scenes"
        ? "场景外表描述：空间结构、材质、光影…"
        : "道具外表描述：轮廓、材质、磨损…";
  const descLabel =
    kind === "characters" ? "角色外表描述（本状态）" : kind === "scenes" ? "场景外表描述" : "道具外表描述";
  const viewTitle =
    kind === "characters" ? "多角度设定图（基础 + 各状态）" : kind === "scenes" ? "视角列表" : "状态列表";
  const baseLabel =
    kind === "characters"
      ? `${it.name}·基础三视图`
      : kind === "scenes"
        ? `${it.name}·基础母景`
        : `${it.name}·基础状态`;
  const sheetHint =
    kind === "characters"
      ? "标准：一张主设定合集图内同时含全身四向、头部三向与局部特写。可另补头部加强图，但不能只出全身。"
      : "每张图可单独描述；点击图片放大";
  const workingImgs = (it.ref_images || []).map((im, idx) => ({
    path: im.path || "",
    label: im.label || (idx === 0 ? baseLabel : `${it.name}·状态${idx + 1}`),
    // First image inherits legacy visual_prompt when per-image desc is empty
    desc: im.desc || (idx === 0 ? it.visual_prompt || "" : ""),
  }));
  const pendingReplace = {};
  const selectedEps = new Set(eps);
  let draftName = it.name || "";
  let draftNote = it.role_note || "";
  let draftVoice = it.voice_desc || "";

  const uploadUrl =
    kind === "characters"
      ? `/api/projects/${state.projectId}/characters/${id}/images`
      : kind === "props"
        ? `/api/projects/${state.projectId}/props/${id}/images`
        : null;
  const savePath =
    kind === "characters"
      ? `/api/projects/${state.projectId}/characters`
      : kind === "scenes"
        ? `/api/projects/${state.projectId}/scenes`
        : `/api/projects/${state.projectId}/props`;

  const renderImgCards = () => {
    if (!workingImgs.length) {
      return `<p class="muted" style="grid-column:1/-1">暂无参考图。每张图可单独填写外表描述；点击图片可放大。</p>`;
    }
    return workingImgs
      .map((im, idx) => {
        const previewRel = pendingReplace[idx]
          ? URL.createObjectURL(pendingReplace[idx])
          : im.path || "";
        const aiBtns =
          kind === "characters"
            ? `<button type="button" class="btn sm primary asset-img-ai-body" data-idx="${idx}" title="重新生成该状态多角度设定图">重新生成</button>
               <button type="button" class="btn sm asset-img-ai-head" data-idx="${idx}" title="附加头部多角度设定图">头部多角度</button>`
            : `<button type="button" class="btn sm primary asset-img-ai-regen" data-idx="${idx}" title="重新生成该设定图">重新生成</button>`;
        return `<div class="asset-img-card" data-idx="${idx}">
          <div class="asset-img-preview">${
            previewRel
              ? lazyImgHtml(previewRel, {
                  zoomable: true,
                  alt: im.label || it.name || "",
                })
              : `<div class="muted">待生成多角度设定图</div>`
          }</div>
          <div class="asset-img-actions">
            ${aiBtns}
            <button type="button" class="btn sm asset-img-replace" data-idx="${idx}">替换图片</button>
            <button type="button" class="btn sm asset-img-rm" data-idx="${idx}">移除</button>
            <input type="file" class="asset-img-file hidden" data-idx="${idx}" accept="image/*"/>
          </div>
          <label class="field">状态/名称<input class="aImgLabel" data-idx="${idx}" value="${esc(im.label || "")}" placeholder="${esc(baseLabel)}"/></label>
          <label class="field">${descLabel}
            <textarea class="aImgDesc" data-idx="${idx}" rows="8" placeholder="${esc(promptPh)}">${esc(im.desc || "")}</textarea>
          </label>
        </div>`;
      })
      .join("");
  };

  const renderDots = () =>
    Array.from({ length: state.project.episode_count || 10 }, (_, i) => i + 1)
      .map(
        (n) =>
          `<button type="button" class="ep-dot ${selectedEps.has(n) ? "on" : ""}" data-n="${n}">${n}</button>`
      )
      .join("");

  const paint = () => {
    modal.innerHTML = `<div class="modal-box wide asset-edit-modal">
      <h3>${esc(draftName || it.name)}</h3>
      <label class="field">名称<input id="aName" value="${esc(draftName)}"/></label>
      <div class="muted">出现集数（可多选）</div>
      <div class="ep-dots" id="epDots">${renderDots()}</div>
      <label class="field">${noteLabel}
        <textarea id="aNote" rows="3" placeholder="${esc(notePh)}">${esc(draftNote)}</textarea>
      </label>
      ${
        kind === "characters"
          ? `<div class="field-row" style="gap:0.75rem;align-items:stretch">
              <label class="field" style="flex:1">文字音色·当前使用
                <textarea id="aVoice" rows="3" placeholder="音域、音质、语速与说话习惯">${esc(draftVoice)}</textarea>
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
        <span class="muted" style="font-size:0.78rem">${esc(sheetHint)}</span>
      </div>
      ${
        kind === "characters"
          ? `<div class="asset-sheet-toolbar">
              <button type="button" class="btn sm primary" id="aGenBaseSheet">重新生成基础三视图</button>
              <button type="button" class="btn sm" id="aGenHeadSheet">生成头部多角度</button>
              <button type="button" class="btn sm" id="aAddStateSlot">+ 新增状态槽</button>
              <span class="muted" style="font-size:0.75rem">状态槽填外表后点「AI三视图」，与基础图同结构</span>
            </div>`
          : ""
      }
      <div class="asset-img-grid" id="assetImgGrid">${renderImgCards()}</div>
      <label class="field" style="margin-top:0.5rem">上传参考图（也可 AI 生成）
        <input type="file" id="aImgAdd" accept="image/*"/>
      </label>
      <div class="modal-actions">
        <button type="button" class="btn" id="mCancel">取消</button>
        <button type="button" class="btn primary" id="mOk">保存</button>
      </div>
    </div>`;

    modal.querySelectorAll(".ep-dot").forEach((b) => {
      b.onclick = () => {
        const n = parseInt(b.dataset.n, 10);
        if (selectedEps.has(n)) selectedEps.delete(n);
        else selectedEps.add(n);
        b.classList.toggle("on", selectedEps.has(n));
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

    const syncWorkingFromDom = () => {
      draftName = document.getElementById("aName")?.value?.trim() || draftName;
      draftNote = document.getElementById("aNote")?.value?.trim() ?? draftNote;
      draftVoice = document.getElementById("aVoice")?.value?.trim() ?? draftVoice;
      modal.querySelectorAll(".asset-img-card").forEach((card) => {
        const idx = Number(card.dataset.idx);
        if (!workingImgs[idx]) return;
        workingImgs[idx].label = card.querySelector(".aImgLabel")?.value?.trim() || workingImgs[idx].label;
        workingImgs[idx].desc = card.querySelector(".aImgDesc")?.value?.trim() || "";
      });
    };

    modal.querySelectorAll(".asset-img-rm").forEach((btn) => {
      btn.onclick = () => {
        syncWorkingFromDom();
        const idx = Number(btn.dataset.idx);
        workingImgs.splice(idx, 1);
        const nextPending = {};
        Object.keys(pendingReplace).forEach((k) => {
          const ki = Number(k);
          if (ki < idx) nextPending[ki] = pendingReplace[k];
          else if (ki > idx) nextPending[ki - 1] = pendingReplace[k];
        });
        Object.keys(pendingReplace).forEach((k) => delete pendingReplace[k]);
        Object.assign(pendingReplace, nextPending);
        paint();
      };
    });

    modal.querySelectorAll(".asset-img-replace").forEach((btn) => {
      btn.onclick = () => {
        const idx = Number(btn.dataset.idx);
        modal.querySelector(`.asset-img-file[data-idx="${idx}"]`)?.click();
      };
    });
    modal.querySelectorAll(".asset-img-file").forEach((inp) => {
      inp.onchange = () => {
        const idx = Number(inp.dataset.idx);
        const file = inp.files?.[0];
        if (!file) return;
        syncWorkingFromDom();
        pendingReplace[idx] = file;
        paint();
      };
    });

    document.getElementById("aImgAdd").onchange = (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      syncWorkingFromDom();
      workingImgs.push({
        path: "",
        label: `${draftName || it.name}·状态${workingImgs.length + 1}`,
        desc: "",
      });
      pendingReplace[workingImgs.length - 1] = file;
      paint();
    };

    const genCharacterSheet = async ({ sheetMode, angleLabel, appearance, replacePath, afterClose }) => {
      if (kind !== "characters") return;
      setBusyOverlay(true, sheetMode === "head" ? "生成头部多角度…" : "重新生成设定图…");
      try {
        await api(`/api/projects/${state.projectId}/characters/${id}/generate-image`, {
          method: "POST",
          body: JSON.stringify({
            angle_label: angleLabel || (sheetMode === "head" ? "头部多角度" : "基础三视图"),
            sheet_mode: sheetMode || "body",
            appearance_override: appearance || "",
            replace_path: replacePath || "",
          }),
        });
        toast(sheetMode === "head" ? "头部多角度已生成" : "设定图已重新生成");
        modal.classList.add("hidden");
        await openProject(state.projectId);
        if (afterClose === "ep") void bindEpEditor();
        else {
          state.step = "assets";
          render();
        }
        // reopen editor so user sees new sheets
        showEditAsset(kind, id);
      } catch (e) {
        alert(`生图失败：${e.message || e}`);
      } finally {
        setBusyOverlay(false);
      }
    };

    const regenSceneOrProp = async (idx) => {
      syncWorkingFromDom();
      const im = workingImgs[idx];
      setBusyOverlay(true, "重新生成设定图…");
      try {
        await api(`/api/projects/${state.projectId}/studio/batch-generate-images`, {
          method: "POST",
          body: JSON.stringify({
            kind: kind === "scenes" ? "scenes" : "props",
            only_missing: false,
            limit: 1,
            ids: [id],
          }),
        });
        toast("设定图已重新生成");
        modal.classList.add("hidden");
        await openProject(state.projectId);
        if (state.view === "epEditor") void bindEpEditor();
        else {
          state.step = "assets";
          render();
        }
        showEditAsset(kind, id);
      } catch (e) {
        alert(`重新生成失败：${e.message || e}`);
      } finally {
        setBusyOverlay(false);
      }
    };

    document.getElementById("aGenBaseSheet")?.addEventListener("click", () => {
      syncWorkingFromDom();
      const first = workingImgs[0];
      const appearance =
        (first?.desc || "").trim() ||
        workingImgs.find((x) => (x.desc || "").trim())?.desc ||
        draftNote ||
        it.visual_prompt ||
        "";
      void genCharacterSheet({
        sheetMode: "body",
        angleLabel: (first?.label || "").replace(/^AI·/, "").trim() || "基础三视图",
        appearance,
        replacePath: first?.path || "",
        afterClose: state.view === "epEditor" ? "ep" : "assets",
      });
    });
    document.getElementById("aGenHeadSheet")?.addEventListener("click", () => {
      syncWorkingFromDom();
      const appearance =
        workingImgs.find((x) => (x.desc || "").trim())?.desc ||
        draftNote ||
        it.visual_prompt ||
        "";
      void genCharacterSheet({
        sheetMode: "head",
        angleLabel: "头部多角度",
        appearance,
        afterClose: state.view === "epEditor" ? "ep" : "assets",
      });
    });
    document.getElementById("aAddStateSlot")?.addEventListener("click", () => {
      syncWorkingFromDom();
      workingImgs.push({
        path: "",
        label: `${draftName || it.name}·状态${workingImgs.length + 1}`,
        desc: "",
      });
      paint();
    });
    modal.querySelectorAll(".asset-img-ai-body").forEach((btn) => {
      btn.onclick = () => {
        syncWorkingFromDom();
        const idx = Number(btn.dataset.idx);
        const im = workingImgs[idx];
        if (!im) return;
        const appearance = (im.desc || "").trim();
        if (!appearance) {
          alert("请先填写本状态的外表描述，再重新生成");
          return;
        }
        void genCharacterSheet({
          sheetMode: "body",
          angleLabel: (im.label || "").replace(/^AI·/, "").trim() || `状态${idx + 1}`,
          appearance,
          replacePath: im.path || "",
          afterClose: state.view === "epEditor" ? "ep" : "assets",
        });
      };
    });
    modal.querySelectorAll(".asset-img-ai-regen").forEach((btn) => {
      btn.onclick = () => void regenSceneOrProp(Number(btn.dataset.idx));
    });
    modal.querySelectorAll(".asset-img-ai-head").forEach((btn) => {
      btn.onclick = () => {
        syncWorkingFromDom();
        const idx = Number(btn.dataset.idx);
        const im = workingImgs[idx];
        if (!im) return;
        const appearance = (im.desc || "").trim() || draftNote || it.visual_prompt || "";
        if (!appearance) {
          alert("请先填写外表描述，再生成头部多角度");
          return;
        }
        void genCharacterSheet({
          sheetMode: "head",
          angleLabel: `${(im.label || "基础").trim()}·头部多角度`,
          appearance,
          afterClose: state.view === "epEditor" ? "ep" : "assets",
        });
      };
    });

    document.getElementById("mOk").onclick = async () => {
      syncWorkingFromDom();
      const name = draftName.trim();
      const note = draftNote.trim();
      if (note && note === name) {
        alert(`${noteLabel}不能与名称相同，请填写实质说明`);
        return;
      }

      // Upload pending files first (append), then prune ref_images to final list
      const uploadOne = async (file, label) => {
        if (!uploadUrl) {
          toast("场景图上传暂请用批量生图或稍后补传");
          return null;
        }
        const fd = new FormData();
        fd.append("file", file);
        fd.append("label", label || "形象");
        const updated = await api(uploadUrl, { method: "POST", body: fd });
        const imgs = updated.ref_images || [];
        return imgs.length ? imgs[imgs.length - 1] : null;
      };

      for (const [idxStr, file] of Object.entries(pendingReplace)) {
        const idx = Number(idxStr);
        if (!workingImgs[idx] || !file) continue;
        const uploaded = await uploadOne(file, workingImgs[idx].label);
        if (uploaded?.path) {
          workingImgs[idx].path = uploaded.path;
          if (!workingImgs[idx].label) workingImgs[idx].label = uploaded.label || "形象";
        }
      }

      const ref_images = workingImgs
        .filter((im) => im.path)
        .map((im) => {
          const row = { path: im.path, label: im.label || "形象" };
          if ((im.desc || "").trim()) row.desc = im.desc.trim();
          return row;
        });
      const visual_prompt =
        ref_images.find((r) => r.desc)?.desc ||
        workingImgs.find((r) => (r.desc || "").trim())?.desc ||
        it.visual_prompt ||
        "";

      const body = {
        id,
        name,
        episodes: [...selectedEps].sort((a, b) => a - b),
        role_note: note,
        visual_prompt,
        voice_desc: draftVoice.trim() || it.voice_desc || "",
        ref_images,
        ref_image: ref_images[0]?.path || it.ref_image || "",
        code: it.code || "",
      };
      await api(savePath, { method: "POST", body: JSON.stringify(body) });
      modal.classList.add("hidden");
      await openProject(state.projectId);
      if (state.view === "epEditor") {
        void bindEpEditor();
      } else {
        state.step = "assets";
        render();
      }
      toast("已保存");
    };

    bindZoomableImages(modal);
  };

  paint();
}

function showSettings() {
  const oh = state.config?.ohmygpt || {};
  const vb = state.config?.video_backend || {};
  const models = Array.isArray(vb.comfy_video_models) ? vb.comfy_video_models : [
    { id: "minimax_h3", label: "MiniMax H3（人脸/口型优先）" },
    { id: "ltx25", label: "LTX-2.5 INT8（音频稳定优先）" },
  ];
  const curModel = vb.comfy_video_model || "minimax_h3";
  const modelOpts = models
    .map(
      (m) =>
        `<option value="${esc(m.id)}" ${m.id === curModel ? "selected" : ""}>${esc(
          m.label || m.id
        )}</option>`
    )
    .join("");
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  modal.innerHTML = `<div class="modal-box wide">
    <h3>制作设置</h3>
    <label class="field">OhMyGPT API Base<input id="ohBase" value="${esc(oh.api_base || "https://api.ohmygpt.com/v1")}"/></label>
    <label class="field">OhMyGPT Key<input id="ohKey" type="password" placeholder="${oh.key_configured ? "已保存，留空不改" : "粘贴 Key"}/></label>
    <label class="field">视频后端
      <select id="vbSel">
        <option value="comfy" ${vb.backend !== "ppio" ? "selected" : ""}>云 GPU ComfyUI</option>
        <option value="ppio" ${vb.backend === "ppio" ? "selected" : ""}>PPIO</option>
      </select>
    </label>
    <label class="field">Comfy 成片模型
      <select id="vbModel">${modelOpts}</select>
    </label>
    <p class="muted" style="margin:0.35rem 0 0.75rem">成片直接带原生音轨（对白+环境声），不做后期配音。H3 偏口型；LTX-2.5 偏音频稳定、少乱说话。</p>
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
    const vbBody = {
      backend: document.getElementById("vbSel").value,
      comfy_video_model: document.getElementById("vbModel").value,
      clear_minimax_key: true,
    };
    state.config.video_backend = await api("/api/settings/video-backend", {
      method: "PUT",
      body: JSON.stringify(vbBody),
    });
    state.config = await api("/api/config");
    modal.classList.add("hidden");
    toast("设置已保存");
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
