/* 短剧制作台 SPA */
const state = {
  projects: [],
  currentProjectId: null,
  currentTab: "overview",
  config: null,
  pipeline: null,
  pollTimer: null,
  systemMetrics: null,
  comfyStatus: null,
  episodeModalOpen: false,
  episodeModalId: null,
};

function parseApiError(body) {
  const t = (body || "").trim();
  if (!t) return "";
  try {
    const j = JSON.parse(t);
    if (typeof j.detail === "string") return j.detail;
    if (j.detail) return JSON.stringify(j.detail);
  } catch (_) {
    /* plain text */
  }
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
  if (res.status === 204) return null;
  return res.json();
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** 任务时间统一显示为北京时间（Asia/Shanghai）。 */
function formatBeijingTime(iso) {
  if (!iso) return "—";
  const raw = String(iso).trim();
  let d = new Date(raw);
  if (Number.isNaN(d.getTime())) {
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

function stageClass(st) {
  return st || "pending";
}

const STATUS_ZH = {
  pending: "待开始",
  running: "进行中",
  done: "已完成",
  failed: "失败",
  skipped: "已跳过",
};

const POLL_MS = 2500;

const STAGE_ZH_EXTRA = {
  drama10_export: "Drama10 脚本",
};

const TTS_VOICE_OPTIONS = [
  "onyx",
  "echo",
  "fable",
  "ash",
  "nova",
  "shimmer",
  "alloy",
  "verse",
  "sage",
  "coral",
  "ballad",
];

function renderVoiceCastTable(cast, { editable = false, tableId = "voiceCastTable" } = {}) {
  const items = Array.isArray(cast) && cast.length ? cast : [];
  if (!items.length) {
    return `<p class="muted">暂无固定音色表。保存全局设置后会写入《冰境》默认卡司。</p>`;
  }
  const rows = items
    .map((item, i) => {
      const role = item.role || "";
      const tv = item.tts_voice || "";
      const desc = item.voice_desc || "";
      const emo = item.emotion || "";
      if (!editable) {
        return `<tr><td>${esc(role)}</td><td><code>${esc(tv || "—")}</code></td><td>${esc(emo)}</td><td>${esc(desc)}</td></tr>`;
      }
      const opts = ['<option value="">自动</option>']
        .concat(
          TTS_VOICE_OPTIONS.map(
            (v) => `<option value="${v}" ${tv === v ? "selected" : ""}>${v}</option>`
          )
        )
        .join("");
      return `<tr data-vc-idx="${i}">
        <td><input class="vc-role" value="${esc(role)}" placeholder="角色名（与对白一致）"/></td>
        <td><select class="vc-tts">${opts}</select></td>
        <td><input class="vc-emo" value="${esc(emo)}" placeholder="情绪"/></td>
        <td><textarea class="vc-desc" rows="2" placeholder="声音设定；可写 TTS:onyx">${esc(desc)}</textarea></td>
      </tr>`;
    })
    .join("");
  return `<table class="voice-cast-table" id="${tableId}"><thead><tr>
    <th>角色</th><th>固定 TTS</th><th>情绪</th><th>声音设定</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

function collectVoiceCastFromTable(tableId = "voiceCastTable") {
  const tbody = document.getElementById(tableId)?.querySelector("tbody");
  if (!tbody) return [];
  const out = [];
  tbody.querySelectorAll("tr[data-vc-idx]").forEach((tr) => {
    const role = tr.querySelector(".vc-role")?.value?.trim();
    if (!role) return;
    out.push({
      role,
      tts_voice: tr.querySelector(".vc-tts")?.value?.trim() || "",
      emotion: tr.querySelector(".vc-emo")?.value?.trim() || "",
      voice_desc: tr.querySelector(".vc-desc")?.value?.trim() || "",
      scope: "series",
    });
  });
  return out;
}

function statusKey(st) {
  if (st == null || st === "") return "pending";
  const key = String(st).toLowerCase().trim();
  return Object.prototype.hasOwnProperty.call(STATUS_ZH, key) ? key : key;
}

function statusLabel(st) {
  if (st == null || st === "") return "待开始";
  const key = String(st).toLowerCase().trim();
  return STATUS_ZH[key] || "待开始";
}

function statusTd(value, hint) {
  const key = statusKey(value);
  const text = hint ? `${statusLabel(value)} · ${hint}` : statusLabel(value);
  return `<td class="cell-status cell-${key}">${esc(text)}</td>`;
}

function activeJobsFromPipeline(pl) {
  if (!pl) return [];
  if (Array.isArray(pl.active_jobs) && pl.active_jobs.length) return pl.active_jobs;
  return pl.active_job ? [pl.active_job] : [];
}

function videoJobDisplayLabel(j) {
  if (j.stage_id !== "video") return stageLabel(j.stage_id);
  try {
    const p = JSON.parse(j.params_json || "{}");
    if (Array.isArray(p.shot_nums) && p.shot_nums.length) {
      return `单镜视频 · 镜 ${p.shot_nums.join("、")}`;
    }
    if (p.mux_only) return "合并成片";
    if (p.confirm_full_episode && p.auto_concat === false) return "批量补缺分镜视频";
    if (p.confirm_full_episode) return "整集视频";
  } catch (_) {
    /* ignore */
  }
  return stageLabel("video");
}

function formatActiveJobLine(j, { compact = false } = {}) {
  const ep =
    j.ep_num != null
      ? `EP${String(j.ep_num).padStart(2, "0")} · `
      : "";
  const pct = Math.round((j.progress || 0) * 100);
  const stage = esc(videoJobDisplayLabel(j));
  const msg = esc(j.message || "");
  const detail =
    j.stage_id === "video" && msg
      ? `<div class="active-job-detail">${msg}</div>`
      : `<span class="muted">${msg}</span>`;
  if (compact) {
    return `<div class="active-job-item"><strong>${ep}${stage}</strong> ${pct}%<br/>${detail}<div class="job-progress"><i style="width:${pct}%"></i></div></div>`;
  }
  return `<div class="active-job"><strong>进行中 · ${ep}${stage}</strong> <span class="muted">${pct}%</span>${detail}<div class="job-progress"><i style="width:${pct}%"></i></div></div>`;
}

function renderActiveJobsBanner(jobs) {
  if (!jobs.length) return "";
  if (jobs.length === 1) return formatActiveJobLine(jobs[0]);
  return `<div class="active-jobs-stack"><p class="muted" style="margin:0 0 0.5rem;font-size:0.85rem">共 ${jobs.length} 个任务进行中</p>${jobs.map((j) => formatActiveJobLine(j)).join("")}</div>`;
}

function renderSidebarJob(plOrJob) {
  const el = document.getElementById("sidebarJob");
  if (!el) return;
  const jobs = Array.isArray(plOrJob)
    ? plOrJob
    : plOrJob?.active_jobs
      ? plOrJob.active_jobs
      : plOrJob
        ? [plOrJob]
        : activeJobsFromPipeline(state.pipeline);
  if (!jobs.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.classList.remove("failed");
  el.innerHTML =
    jobs.length > 1
      ? `<strong>进行中（${jobs.length}）</strong>${jobs.map((j) => formatActiveJobLine(j, { compact: true })).join("")}`
      : formatActiveJobLine(jobs[0], { compact: true });
}

const _detailTextStore = new Map();
let _detailTextSeq = 0;

function stashDetailText(text) {
  const id = `dt-${++_detailTextSeq}`;
  _detailTextStore.set(id, String(text || ""));
  return id;
}

function jobMessageSummary(raw) {
  const s = String(raw || "").trim();
  if (!s) return { preview: "—", detail: "" };
  if (s[0] === "{" || s[0] === "[") {
    try {
      const o = JSON.parse(s);
      if (o.status_str === "error" && Array.isArray(o.messages)) {
        for (const m of o.messages) {
          if (Array.isArray(m) && m[0] === "execution_interrupted") {
            const info = m[1] || {};
            const node = info.node_type || info.node_id || "";
            return {
              preview: `Comfy 执行被中断${node ? ` · 节点 ${node}` : ""}`,
              detail: JSON.stringify(o, null, 2),
            };
          }
        }
        return { preview: "Comfy 执行错误", detail: JSON.stringify(o, null, 2) };
      }
    } catch (_) {
      /* keep raw */
    }
    return { preview: "结构化错误日志（可查看详情）", detail: s };
  }
  if (s.length > 160) {
    return { preview: `${s.slice(0, 157)}…`, detail: s };
  }
  return { preview: s, detail: "" };
}

function renderScrollDescCell(preview, detail, title = "说明") {
  const inBox = esc(preview);
  let btn = "";
  if (detail) {
    const id = stashDetailText(detail);
    btn = `<button type="button" class="btn sm btn-text-detail" data-detail-id="${esc(id)}" data-detail-title="${esc(title)}">查看详情</button>`;
  }
  return `<div class="cell-desc-wrap">${btn}<div class="cell-desc-scroll" tabindex="0">${inBox}</div></div>`;
}

function showTextDetailModal(title, text) {
  let el = document.getElementById("textDetailModal");
  if (!el) {
    el = document.createElement("div");
    el.id = "textDetailModal";
    el.className = "modal hidden";
    el.innerHTML = `<div class="modal-box modal-box--detail">
      <h3 class="text-detail-title"></h3>
      <pre class="text-detail-body"></pre>
      <button type="button" class="btn sm primary text-detail-close">关闭</button>
    </div>`;
    document.body.appendChild(el);
    el.querySelector(".text-detail-close").onclick = () => el.classList.add("hidden");
    el.addEventListener("click", (e) => {
      if (e.target === el) el.classList.add("hidden");
    });
  }
  el.querySelector(".text-detail-title").textContent = title || "详情";
  el.querySelector(".text-detail-body").textContent = text || "";
  el.classList.remove("hidden");
}

function bindJobsTableEvents() {
  const table = document.querySelector(".jobs-table");
  if (!table || table.dataset.detailBound) return;
  table.dataset.detailBound = "1";
  table.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-text-detail");
    if (!btn) return;
    const id = btn.dataset.detailId;
    const title = btn.dataset.detailTitle || "任务说明";
    showTextDetailModal(title, _detailTextStore.get(id) || "");
  });
}

function formatJobRow(j) {
  const ep = j.ep_num ? `EP${String(j.ep_num).padStart(2, "0")}` : "—";
  const pct = Math.round((j.progress || 0) * 100);
  const rowClass = j.status === "failed" ? "row-failed" : "";
  const { preview, detail } = jobMessageSummary(j.message);
  return `<tr class="${rowClass}">
    <td>${esc(ep)}</td>
    <td>${esc(stageLabel(j.stage_id))}</td>
    <td class="cell-status cell-${statusKey(j.status)}">${esc(statusLabel(j.status))}</td>
    <td class="progress-cell">${j.status === "running" ? pct + "%" : "—"}</td>
    <td class="job-msg-cell">${renderScrollDescCell(preview, detail, "任务说明")}</td>
    <td class="job-time-cell">${esc(formatBeijingTime(j.updated_at))}</td>
  </tr>`;
}

const _blobUrls = new Set();
const _fileBlobCache = new Map();

function productionFileVer(ep, relPath) {
  const d = ep.production || {};
  if (!relPath) return "";
  if (d.video_path === relPath && d.video_mtime) {
    return String(d.video_mtime);
  }
  if (d.collage_path === relPath && d.collage_mtime) {
    return String(d.collage_mtime);
  }
  const mt = d.shot_image_mtimes || {};
  const vt = d.shot_video_mtimes || {};
  for (const s of ep.shots || []) {
    if (s.image_path === relPath && mt[s.shot_num]) {
      return String(mt[s.shot_num]);
    }
    if (s.video_path === relPath && vt[s.shot_num]) {
      return String(vt[s.shot_num]);
    }
  }
  return (d.last_jobs?.storyboard_images?.updated_at || "").slice(0, 19);
}

function episodeAssetSignature(ep) {
  // 仅随「落盘资产」变化；不要把视频任务进度/文案算进签名，
  // 否则轮询会整表重绘镜头表，正在显示的分镜图会被冲掉。
  const d = ep.production || {};
  const paths = (ep.shots || [])
    .map((s) => `${s.image_path || ""}|${s.video_path || ""}`)
    .join("|");
  const mt = d.shot_image_mtimes || {};
  const vt = d.shot_video_mtimes || {};
  const vtSig = Object.keys(vt)
    .sort((a, b) => Number(a) - Number(b))
    .map((k) => `${k}:${vt[k]}`)
    .join(",");
  const mtSig = Object.keys(mt)
    .sort((a, b) => Number(a) - Number(b))
    .map((k) => `${k}:${mt[k]}`)
    .join(",");
  const jobDone = d.last_jobs?.storyboard_images?.updated_at || "";
  const vidDone = d.last_jobs?.video?.updated_at || "";
  return `${d.shots_with_image}/${d.shots_total}:${d.collage_path}:${d.collage_mtime || 0}:${d.video_path}:${d.video_mtime || 0}:${mtSig}:${vtSig}:${jobDone}:${vidDone}:${paths}`;
}

function invalidateProjectFileCache(relPath) {
  const prefix = relPath ? `${relPath}::` : "";
  for (const key of [..._fileBlobCache.keys()]) {
    if (!relPath || key.startsWith(prefix)) {
      const url = _fileBlobCache.get(key);
      if (url) {
        URL.revokeObjectURL(url);
        _blobUrls.delete(url);
      }
      _fileBlobCache.delete(key);
    }
  }
}

function projectFileUrl(relPath, ver = "", projectId = null) {
  const pid = projectId || state.currentProjectId;
  if (!relPath || !pid) return null;
  const pathEnc = relPath.split("/").map(encodeURIComponent).join("/");
  const q = ver ? `?v=${encodeURIComponent(ver)}` : "";
  return `/api/projects/${pid}/files/${pathEnc}${q}`;
}

/** 短期 media_token 直链：浏览器原生流式播放 + Range 拖拽，不必整文件下载完 */
async function projectMediaStreamUrl(relPath, ver = "", projectId = null) {
  const pid = projectId || state.currentProjectId;
  if (!relPath || !pid) return null;
  const q = new URLSearchParams({ path: relPath });
  if (ver) q.set("v", String(ver));
  const r = await api(`/api/projects/${pid}/media-url?${q.toString()}`);
  return r?.url || null;
}

async function fetchProjectFileBlobUrl(relPath, ver = "", projectId = null) {
  const pid = projectId || state.currentProjectId;
  if (!relPath || !pid) return null;
  const cacheKey = `${relPath}::${ver}`;
  if (_fileBlobCache.has(cacheKey)) return _fileBlobCache.get(cacheKey);
  const urlPath = projectFileUrl(relPath, ver, pid);
  if (!urlPath) return null;
  const res = await fetch(urlPath, { credentials: "same-origin", cache: "no-store" });
  if (!res.ok) return null;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  _blobUrls.add(url);
  _fileBlobCache.set(cacheKey, url);
  return url;
}

const lightboxNav = { imgs: [], index: 0 };

function collectLightboxImages(fromImg) {
  const gallery =
    fromImg.closest(".storyboard-gallery") ||
    fromImg.closest(".shots-editor") ||
    fromImg.closest("#shotsEditor");
  if (!gallery) return [fromImg];
  const inEditor = gallery.classList.contains("shots-editor") || gallery.id === "shotsEditor";
  const sel = inEditor ? "img.shot-thumb" : "img.collage-img, img.shot-grid-img";
  const list = Array.from(gallery.querySelectorAll(sel)).filter(
    (i) =>
      i.src &&
      !i.classList.contains("img-loading") &&
      !i.classList.contains("img-error")
  );
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
  const cap = imgEl.alt || "";
  lb.querySelector(".lightbox-caption").textContent =
    n > 1 ? `${cap} · ${index + 1} / ${n}（↑↓ 或 ←→ 翻页）` : cap;
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
    <button type="button" class="lightbox-nav lightbox-prev" aria-label="上一张">↑</button>
    <button type="button" class="lightbox-nav lightbox-next" aria-label="下一张">↓</button>
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
    if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      lightboxStep(-1);
    }
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
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
  const lb = document.getElementById("lightbox");
  lb.classList.remove("hidden");
  document.body.classList.add("lightbox-open");
}

function closeLightbox() {
  const lb = document.getElementById("lightbox");
  if (!lb) return;
  lb.classList.add("hidden");
  lb.querySelector(".lightbox-img").removeAttribute("src");
  document.body.classList.remove("lightbox-open");
  lightboxNav.imgs = [];
  lightboxNav.index = 0;
}

function bindZoomableImages(root) {
  root.querySelectorAll("img.collage-img, img.shot-grid-img, img.shot-thumb").forEach((img) => {
    if (img.dataset.zoomBound) return;
    img.dataset.zoomBound = "1";
    img.classList.add("zoomable");
    img.title = "点击放大 · 大图内 ↑↓ 翻页";
    img.onclick = (e) => {
      e.stopPropagation();
      e.preventDefault();
      openLightbox(img);
    };
  });
}

async function hydrateProtectedVideos(root, projectId = null) {
  const pid = projectId || state.currentProjectId;
  const vids = root.querySelectorAll("video[data-file]");
  await Promise.all(
    Array.from(vids).map(async (vid) => {
      const rel = vid.getAttribute("data-file");
      if (!rel) return;
      const ver = vid.getAttribute("data-file-ver") || "";
      const hydratedKey = `${rel}::stream::${ver}`;
      if (vid.dataset.hydrated === hydratedKey && vid.src) return;
      const errEl = vid.parentElement?.querySelector(".video-load-err");
      const setMsg = (t) => {
        if (errEl) errEl.textContent = t || "";
      };
      setMsg("正在打开流式播放…");
      try {
        // 短期 token 直链：边下边播，可拖拽进度（不必整文件下完）
        const url = await projectMediaStreamUrl(rel, ver, pid);
        if (!url) {
          setMsg("无法签发播放链接。");
          return;
        }
        vid.onerror = () => {
          setMsg("视频无法播放（链接失效或文件损坏，请关闭后重试）。");
        };
        const onReady = () => {
          setMsg("可边播边拖拽进度条");
          setTimeout(() => setMsg(""), 2500);
          vid.removeEventListener("loadeddata", onReady);
        };
        vid.addEventListener("loadeddata", onReady);
        vid.preload = "auto";
        vid.src = url;
        vid.dataset.hydrated = hydratedKey;
        vid.load();
        const dl = root.querySelector("#finalDownloadLink");
        if (dl) dl.href = url;
      } catch (e) {
        setMsg(`无法加载视频：${e.message || e}`);
      }
    })
  );
}

async function hydrateProtectedImages(root) {
  const imgs = root.querySelectorAll("img[data-file]");
  await Promise.all(
    Array.from(imgs).map(async (img) => {
      const rel = img.getAttribute("data-file");
      if (!rel) return;
      const ver = img.getAttribute("data-file-ver") || "";
      const hydratedKey = `${rel}::${ver}`;
      if (img.dataset.hydrated === hydratedKey && img.src) {
        return;
      }
      const url = await fetchProjectFileBlobUrl(rel, ver);
      if (url) {
        img.src = url;
        img.dataset.hydrated = hydratedKey;
        img.classList.remove("img-loading");
      } else {
        img.alt = "加载失败";
        img.classList.add("img-error");
      }
    })
  );
  bindZoomableImages(root);
  bindStoryboardListOpens(root);
}

function bindStoryboardListOpens(root) {
  root.querySelectorAll(".btn-open-still").forEach((btn) => {
    if (btn.dataset.listBound) return;
    btn.dataset.listBound = "1";
    btn.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const rel = btn.getAttribute("data-file");
      const ver = btn.getAttribute("data-file-ver") || "";
      const url = await fetchProjectFileBlobUrl(rel, ver);
      if (!url) {
        alert("加载失败");
        return;
      }
      const img = document.createElement("img");
      img.src = url;
      img.alt = btn.getAttribute("data-alt") || "";
      img.classList.add("zoomable");
      openLightbox(img);
    };
  });
}

function renderFinalVideoSection(_ep) {
  return "";
}

function renderFinalVideoAction(episodeId, detail) {
  const d = detail || {};
  if (!d.video_ready) {
    return `<span class="muted">暂无</span>`;
  }
  return `<button type="button" class="btn sm primary btn-view-final" data-eid="${esc(episodeId)}">查看成品</button>`;
}

function closeFinalVideoViewer() {
  const vm = document.getElementById("videoViewer");
  if (!vm) return;
  const vid = vm.querySelector("video");
  if (vid) {
    vid.pause();
    vid.removeAttribute("src");
    vid.load();
  }
  vm.classList.add("hidden");
  vm.innerHTML = "";
}

async function openFinalVideoViewer(episodeId) {
  let vm = document.getElementById("videoViewer");
  if (!vm) {
    vm = document.createElement("div");
    vm.id = "videoViewer";
    vm.className = "modal hidden";
    vm.addEventListener("click", (ev) => {
      if (ev.target === vm) closeFinalVideoViewer();
    });
    document.body.appendChild(vm);
  }
  // 先弹出窗口，避免等 API 时像“点了没反应”
  vm.classList.remove("hidden");
  vm.innerHTML = `<div class="modal-box modal-box--video">
    <div class="modal-episode-head">
      <h2>成片加载中…</h2>
      <button type="button" class="modal-close" id="closeVideoViewer" title="关闭" aria-label="关闭">×</button>
    </div>
    <p class="muted video-load-err">正在读取成片信息…</p>
  </div>`;
  document.getElementById("closeVideoViewer").onclick = () => closeFinalVideoViewer();

  let ep;
  try {
    ep = await api(`/api/episodes/${episodeId}`);
  } catch (e) {
    const err = vm.querySelector(".video-load-err");
    if (err) err.textContent = `读取失败：${e.message || e}`;
    return;
  }
  if (ep.project_id) state.currentProjectId = ep.project_id;
  const d = ep.production || {};
  const rel = (d.video_path || ep.video_path || "").trim();
  if (!d.video_ready || !rel) {
    const err = vm.querySelector(".video-load-err");
    if (err) err.textContent = "本集尚无成片文件（数据库未标记 video_ready）";
    return;
  }
  const ver = productionFileVer(ep, rel);
  vm.innerHTML = `<div class="modal-box modal-box--video">
    <div class="modal-episode-head">
      <h2>成片 · EP${String(ep.ep_num).padStart(2, "0")} ${esc(ep.title)}</h2>
      <button type="button" class="modal-close" id="closeVideoViewer" title="关闭" aria-label="关闭">×</button>
    </div>
    <p class="muted video-load-err">正在打开流式播放…</p>
    <video controls playsinline preload="auto" class="final-video-player final-video-player--fullscreen" data-file="${esc(rel)}" data-file-ver="${esc(ver)}"></video>
    <p class="muted final-video-path">路径：${esc(rel)}</p>
    <p><a class="btn sm" id="finalDownloadLink" href="#" download="EP${String(ep.ep_num).padStart(2, "0")}_final.mp4">下载成片</a></p>
  </div>`;
  document.getElementById("closeVideoViewer").onclick = () => closeFinalVideoViewer();
  await hydrateProtectedVideos(vm, ep.project_id || state.currentProjectId);
}

function bindViewFinalEvents() {
  document.querySelectorAll(".btn-view-final").forEach((btn) => {
    if (btn.dataset.finalBound) return;
    btn.dataset.finalBound = "1";
    btn.onclick = (e) => {
      e.stopPropagation();
      e.preventDefault();
      void openFinalVideoViewer(btn.dataset.eid);
    };
  });
}

function bindViewShotVideoEvents(root = document) {
  root.querySelectorAll(".btn-view-shot-video").forEach((btn) => {
    if (btn.dataset.shotVidBound) return;
    btn.dataset.shotVidBound = "1";
    btn.onclick = (e) => {
      e.stopPropagation();
      e.preventDefault();
      void openShotVideoViewer({
        episodeId: btn.dataset.eid,
        shotNum: btn.dataset.shot,
        relPath: btn.dataset.file,
        fileVer: btn.dataset.fileVer || "",
        title: btn.dataset.title || "",
      });
    };
  });
}

async function openShotVideoViewer({ episodeId, shotNum, relPath, fileVer, title }) {
  let ep = null;
  if (episodeId) {
    try {
      ep = await api(`/api/episodes/${episodeId}`);
    } catch (_) {}
  }
  if (!relPath && ep && shotNum) {
    const shot = (ep.shots || []).find((s) => Number(s.shot_num) === Number(shotNum));
    if (shot) relPath = shotVideoRel(ep, shot);
  }
  if (!relPath || !state.currentProjectId) {
    alert("暂无该镜视频文件");
    return;
  }
  const ver =
    fileVer ||
    (ep ? productionFileVer(ep, relPath) : "");
  let vm = document.getElementById("videoViewer");
  if (!vm) {
    vm = document.createElement("div");
    vm.id = "videoViewer";
    vm.className = "modal hidden";
    vm.addEventListener("click", (ev) => {
      if (ev.target === vm) closeFinalVideoViewer();
    });
    document.body.appendChild(vm);
  }
  vm.classList.remove("hidden");
  const head = title || (shotNum ? `镜 ${shotNum}` : "分镜视频");
  vm.innerHTML = `<div class="modal-box modal-box--video">
    <div class="modal-episode-head">
      <h2>${esc(head)}</h2>
      <button type="button" class="modal-close" id="closeVideoViewer" title="关闭" aria-label="关闭">×</button>
    </div>
    <video controls playsinline class="final-video-player final-video-player--shot" data-file="${esc(relPath)}" data-file-ver="${esc(ver)}"></video>
    <p class="video-load-err muted" style="color:var(--fail);margin:0.35rem 0"></p>
    <p class="muted final-video-path">${esc(relPath)}</p>
  </div>`;
  document.getElementById("closeVideoViewer").onclick = () => closeFinalVideoViewer();
  await hydrateProtectedVideos(vm);
}

function renderShotStillImg(ep, s) {
  if (!s.image_path) return `<span class="muted">无</span>`;
  const ver = productionFileVer(ep, s.image_path);
  return `<img class="shot-thumb shot-thumb--inline img-loading" data-file="${esc(s.image_path)}" data-file-ver="${esc(ver)}" alt="镜${s.shot_num}" />`;
}

function shotVideoRel(ep, s) {
  const rel = (s.video_path || "").trim();
  if (rel) return rel;
  const vt = ep.production?.shot_video_mtimes || {};
  const sn = Number(s.shot_num);
  if (vt[sn] || vt[String(sn)]) {
    const epn = String(ep.ep_num).padStart(2, "0");
    return `episodes/ep${epn}/shots/shot_${String(sn).padStart(2, "0")}.mp4`;
  }
  return "";
}

function renderShotVideoBtn(ep, s) {
  const rel = shotVideoRel(ep, s);
  const ver = rel ? productionFileVer(ep, rel) : "";
  const hasStill = !!(s.image_path || "").trim();
  const view = rel
    ? `<button type="button" class="btn sm btn-view-shot-video" data-eid="${esc(ep.id)}" data-shot="${s.shot_num}" data-file="${esc(rel)}" data-file-ver="${esc(ver)}" data-title="镜 ${s.shot_num} · 分镜视频">查看视频</button>`
    : "";
  const imgM = ep.production?.shot_image_mtimes?.[s.shot_num] || ep.production?.shot_image_mtimes?.[String(s.shot_num)];
  const vidM = ep.production?.shot_video_mtimes?.[s.shot_num] || ep.production?.shot_video_mtimes?.[String(s.shot_num)];
  const staleVid = rel && imgM && vidM && Number(vidM) < Number(imgM);
  const vidBtn = !hasStill
    ? `<button type="button" class="btn sm muted-btn" disabled title="请先生成本镜分镜图">② 图生视频（需先有分镜图）</button>`
    : staleVid
      ? `<button type="button" class="btn sm primary gen-shot-video" data-shot="${s.shot_num}">② 分镜已更新·请重生成视频</button>`
      : `<button type="button" class="btn sm gen-shot-video" data-shot="${s.shot_num}">② 图生视频（基于分镜图）</button>`;
  return `<div class="shot-video-actions">${view}${vidBtn}</div>`;
}

async function saveEpisodeEditor(episodeId) {
  const shots = collectShotsFromEditor();
  await api(`/api/episodes/${episodeId}`, {
    method: "PATCH",
    body: JSON.stringify({
      script_md: document.getElementById("epScript")?.value ?? "",
      storyboard_md: document.getElementById("epStoryboard")?.value ?? "",
      video_path: document.getElementById("epVideo")?.value ?? "",
    }),
  });
  await api(`/api/episodes/${episodeId}/shots`, {
    method: "PUT",
    body: JSON.stringify({ shots }),
  });
  await refreshPipeline();
  return shots;
}

async function addShotFromEditor(episodeId) {
  const shots = collectShotsFromEditor();
  const next = shots.length ? Math.max(...shots.map((s) => s.shot_num)) + 1 : 1;
  shots.push({
    shot_num: next,
    duration_sec: 5,
    title: `镜${next}`,
    visual: "",
    scene: "",
    motion: "",
    performance: "",
    continuity: "",
    ambience: "",
    voices: [],
    image_path: "",
    video_path: "",
  });
  await api(`/api/episodes/${episodeId}/shots`, {
    method: "PUT",
    body: JSON.stringify({ shots }),
  });
  openEpisodeModal(episodeId);
}

async function deleteShotFromEditor(episodeId, shotNum) {
  if (!confirm(`确定删除镜 ${shotNum}？`)) return;
  const shots = collectShotsFromEditor().filter((s) => s.shot_num !== shotNum);
  await api(`/api/episodes/${episodeId}/shots`, {
    method: "PUT",
    body: JSON.stringify({ shots }),
  });
  openEpisodeModal(episodeId);
}

function openShotVideoGenChoice(episodeId, shotNum) {
  const row = document.querySelector(`#shotsEditor tr[data-shot="${shotNum}"]`);
  const imgPath = (row?.dataset?.imagePath || "").trim();
  if (!imgPath) {
    alert(`镜 ${shotNum} 还没有分镜图。请先点「① 生成分镜图」，确认图片后再图生视频。`);
    return;
  }
  removeRegenOverlay();
  const host = document.querySelector(".modal-box--episode");
  if (!host) {
    alert("请先打开本集编辑窗口。");
    return;
  }
  const overlay = document.createElement("div");
  overlay.className = "regen-overlay";
  overlay.innerHTML = `<div class="modal-box regen-box">
    <h3>镜 ${shotNum} · 图生视频</h3>
    <p class="muted regen-hint">使用本镜已保存的分镜图 PNG 做图生视频（约 8–15 分钟/镜）。若没有分镜图请先点「生成分镜图」。</p>
    <div class="regen-actions regen-actions--stack">
      <button type="button" class="btn primary" id="vidGenAudio">生成本镜视频（云 GPU H3）</button>
      <button type="button" class="btn muted-btn" id="vidGenCancel">取消</button>
    </div>
  </div>`;
  host.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector("#vidGenCancel").onclick = close;
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  overlay.querySelector(".regen-box").onclick = (ev) => ev.stopPropagation();
  const run = async (withAudio) => {
    close();
    try {
      await saveEpisodeEditor(episodeId);
      await startJob("video", episodeId, {
        shot_nums: [shotNum],
        fresh_video: false,
        preview_with_audio: withAudio,
      });
    } catch (e) {
      alert(`提交失败：${e.message || e}`);
    }
  };
  overlay.querySelector("#vidGenAudio").onclick = () => void run(true);
}

function renderStoryboardGallery(ep) {
  const d = ep.production || {};
  const shots = ep.shots || [];
  if (!shots.some((s) => s.image_path) && !(d.video_ready && d.video_path)) {
    return `<p class="muted">暂无分镜。完成「分镜出图」或上传单镜后，在下方镜头表中编辑与查看视频。</p>`;
  }
  let html = `<div class="storyboard-gallery"><h3>分镜预览</h3>
    <p class="muted gallery-hint">点击图片可放大；单镜视频请在下方「镜头表」中查看。</p>`;
  if (d.video_ready && d.video_path) {
    html += `<p class="storyboard-final-row"><strong>本集成片</strong>
      <button type="button" class="btn sm primary btn-view-final" data-eid="${esc(ep.id)}">播放成片</button></p>`;
  }
  const withImg = shots.filter((s) => s.image_path);
  if (withImg.length) {
    html += `<div class="shot-grid shot-grid--row">`;
    for (const s of withImg) {
      const ver = productionFileVer(ep, s.image_path);
      html += `<figure class="shot-card">
        <img class="shot-grid-img img-loading" data-file="${esc(s.image_path)}" data-file-ver="${esc(ver)}" alt="镜${s.shot_num}" />
        <figcaption>镜 ${s.shot_num}</figcaption>
      </figure>`;
    }
    html += `</div>`;
  }
  html += `</div>`;
  return html;
}

function episodeStatusBanner(ep) {
  const pl = ep.pipeline || {};
  const d = ep.production || {};
  const lines = [];
  lines.push(`分镜图：${d.shots_with_image ?? 0}/${d.shots_total ?? 0} 张已落盘`);
  if (d.video_ready && d.video_path) {
    lines.push(`成片：已生成 MP4 · 可点「查看成品」播放`);
  }
  const active = d.active_job;
  if (active) {
    return `<div class="ep-status-banner running"><strong>正在执行</strong>：${esc(stageLabel(active.stage_id))} — ${esc(active.message)}（${Math.round((active.progress || 0) * 100)}%）</div>`;
  }
  const failedStages = ["storyboard_images", "video"]
    .map((key) => ({ key, last: d.last_jobs?.[key] }))
    .filter((x) => x.last?.status === "failed");
  if (failedStages.length) {
    const pick = failedStages.sort((a, b) =>
      (b.last.updated_at || "").localeCompare(a.last.updated_at || "")
    )[0];
    const key = pick.key;
    const last = pick.last;
    const retry =
      key === "video"
        ? "若上方未显示「正在执行」，可再次点击「排队：本集成片」重试。"
        : "若上方未显示「正在执行」，可再次点击「排队：分镜出图」或「重生本镜」。";
    return `<div class="ep-status-banner failed"><strong>上次失败</strong>（${esc(stageLabel(key))}）<br/>${esc(last.message)}<br/><span class="muted">${retry}</span><br/><span class="muted">${lines.join(" · ")}</span></div>`;
  }
  if (pl.storyboard_images === "done" && pl.video === "done") {
    return `<div class="ep-status-banner done"><strong>本集产物齐全</strong><br/><span class="muted">${lines.join(" · ")}</span></div>`;
  }
  return `<div class="ep-status-banner"><strong>当前状态</strong>：分镜脚本 ${esc(statusLabel(pl.storyboard_script))} · 分镜图 ${esc(statusLabel(pl.storyboard_images))} · 视频 ${esc(statusLabel(pl.video))}<br/><span class="muted">${lines.join(" · ")}</span></div>`;
}

function stageLabel(stageId) {
  if (!stageId) return "";
  if (STAGE_ZH_EXTRA[stageId]) return STAGE_ZH_EXTRA[stageId];
  const all = [
    ...(state.config?.stages?.project || []),
    ...(state.config?.stages?.episode || []),
  ];
  const hit = all.find((s) => s.id === stageId);
  return hit ? hit.label : stageId;
}

function metricPieColor(p) {
  if (p >= 90) return "#e74c3c";
  if (p >= 75) return "#f39c12";
  if (p >= 55) return "#e6c04a";
  return "#5cb8ff";
}

function metricPieItem(label, pct, sub) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  const color = metricPieColor(p);
  const r = 24;
  const c = 2 * Math.PI * r;
  const dash = (p / 100) * c;
  return `<div class="metric-pie-item">
    <div class="metric-pie-wrap">
      <svg class="metric-pie" viewBox="0 0 56 56" role="img" aria-label="${esc(label)} ${Math.round(p)}%">
        <circle cx="28" cy="28" r="${r}" fill="none" stroke="#1a2230" stroke-width="6"/>
        <circle cx="28" cy="28" r="${r}" fill="none" stroke="${color}" stroke-width="6"
          stroke-dasharray="${dash.toFixed(2)} ${c.toFixed(2)}" stroke-linecap="round" transform="rotate(-90 28 28)"/>
      </svg>
      <span class="metric-pie-pct">${Math.round(p)}%</span>
    </div>
    <span class="metric-pie-label">${esc(label)}</span>
    ${sub ? `<span class="metric-pie-sub">${sub}</span>` : ""}
  </div>`;
}

function formatMetricsTime(ts) {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch (_) {
    return "";
  }
}

function renderServerMetricsInner(m) {
  if (!m || m.error) {
    return `<p class="muted">无法读取服务器指标（请确认在 Linux 制作台主机上运行）。</p>`;
  }
  const cpu = m.cpu || {};
  const mem = m.memory || {};
  const sw = m.swap || {};
  const pies = [];
  pies.push(
    metricPieItem(
      "CPU",
      cpu.usage_percent,
      `负载 ${cpu.load_1m ?? "—"} · ${cpu.cores || "?"} 核`
    )
  );
  pies.push(
    metricPieItem(
      "内存",
      mem.used_percent,
      `${mem.used_gb ?? 0}/${mem.total_gb ?? 0} GB`
    )
  );
  pies.push(
    metricPieItem("Swap", sw.used_percent, `${sw.used_gb ?? 0}/${sw.total_gb ?? 0} GB`)
  );
  const disks = (m.disks || []).filter((d) => !d.error);
  if (disks.length) {
    const d = disks[0];
    pies.push(
      metricPieItem(
        d.label || "磁盘",
        d.used_percent,
        `余 ${d.free_gb ?? 0} GB`
      )
    );
  }
  const gpus = m.gpus || [];
  if (gpus.length) {
    const g = gpus[0];
    pies.push(
      metricPieItem(
        "GPU 显存",
        g.memory_used_percent,
        `${g.memory_used_mb ?? 0}/${g.memory_total_mb ?? 0} MB · ${g.utilization_gpu ?? 0}% · ${g.temperature_c ?? 0}°C`
      )
    );
  }
  let html = `<p class="muted metrics-updated">更新于 ${formatMetricsTime(m.ts)}</p>
    <div class="metrics-pie-row">${pies.join("")}</div>`;
  if (!gpus.length) {
    html += `<p class="muted metrics-no-gpu">未检测到 NVIDIA GPU。</p>`;
  }
  return html;
}

function renderServerMetricsCard(m) {
  return `<div class="card" id="serverMetricsCard"><h3>服务器实时负荷</h3><div id="serverMetricsInner">${renderServerMetricsInner(m)}</div></div>`;
}

function comfyStatusDotClass(summary) {
  const s = summary || "";
  if (s.includes("运行中")) return "comfy-dot--ok";
  if (s.includes("启动中")) return "comfy-dot--pending";
  if (s.includes("已停止")) return "comfy-dot--off";
  return "comfy-dot--warn";
}

function renderComfyStatusInner(st) {
  if (!st || st.error) {
    return `<span class="comfy-dot comfy-dot--warn"></span><div><strong>ComfyUI</strong><p class="muted">状态未知</p></div><span class="comfy-chevron" aria-hidden="true">›</span>`;
  }
  return `<span class="comfy-dot ${comfyStatusDotClass(st.summary)}" aria-hidden="true"></span>
    <div class="comfy-status-text">
      <strong>ComfyUI · ${esc(st.summary)}</strong>
      <p class="muted">队列 ${st.queue_running ?? 0} 运行 / ${st.queue_pending ?? 0} 等待 · 启动 / 重启 / 停止</p>
    </div>
    <span class="comfy-chevron" aria-hidden="true">›</span>`;
}

function renderComfyStatusCard(st) {
  return `<div class="card comfy-status-card" id="comfyStatusCard">
    <a href="/comfy" class="comfy-status-link">${renderComfyStatusInner(st)}</a>
  </div>`;
}

async function refreshComfyStatus() {
  try {
    state.comfyStatus = await api("/api/comfy/status");
  } catch (_) {
    state.comfyStatus = { error: true, summary: "不可用" };
  }
  const link = document.querySelector("#comfyStatusCard .comfy-status-link");
  if (link) link.innerHTML = renderComfyStatusInner(state.comfyStatus);
}

async function refreshSystemMetrics() {
  try {
    state.systemMetrics = await api("/api/system/metrics");
  } catch (_) {
    state.systemMetrics = { error: true, ts: Date.now() / 1000 };
  }
  const inner = document.getElementById("serverMetricsInner");
  if (inner) inner.innerHTML = renderServerMetricsInner(state.systemMetrics);
}

function renderStageList(stages, pipeline, scope) {
  return stages
    .filter((s) => s.scope === scope)
    .map(
      (s) => `
    <div class="stage ${stageClass(pipeline[s.id])}">
      <div class="label">${esc(s.label)}</div>
      <div class="status">${esc(statusLabel(pipeline[s.id] || "pending"))}</div>
    </div>`
    )
    .join("");
}

async function loadProjects() {
  const data = await api("/api/projects");
  state.projects = data.projects || [];
  renderProjectNav();
}

function renderProjectNav() {
  const nav = document.getElementById("projectNav");
  const protectedIds = new Set([
    "78be2a4b-282c-4e4b-8a3e-18f4d076149b",
    "d7e69d56-8592-42a9-8fb3-febe68b8f64b",
  ]);
  nav.innerHTML = state.projects
    .map((p) => {
      const isProt =
        protectedIds.has(p.id) || (p.name || "").trim() === "冰镜";
      const delBtn = isProt
        ? ""
        : `<button type="button" class="nav-del" data-del-id="${esc(p.id)}" data-del-name="${esc(p.name)}" title="删除短剧">删除</button>`;
      return `<div class="nav-item-row ${p.id === state.currentProjectId ? "active" : ""}" data-id="${esc(p.id)}">
          <button type="button" class="nav-item" data-open-id="${esc(p.id)}" title="${esc(p.name)}">${esc(p.name)}</button>
          ${delBtn}
        </div>`;
    })
    .join("");
  nav.querySelectorAll("[data-open-id]").forEach((btn) => {
    btn.onclick = () => openProject(btn.dataset.openId);
  });
  nav.querySelectorAll("[data-del-id]").forEach((btn) => {
    btn.onclick = (ev) => {
      ev.stopPropagation();
      void deleteProject(btn.dataset.delId, btn.dataset.delName || "");
    };
  });
}

async function deleteProject(id, name) {
  if (!id) return;
  const label = name || id.slice(0, 8);
  if (!confirm(`确定删除短剧「${label}」？\n将清除该项目的剧本镜头、人物场景与全部图片/视频，且不可恢复。`)) {
    return;
  }
  try {
    await api(`/api/projects/${id}`, { method: "DELETE" });
    if (state.currentProjectId === id) {
      state.currentProjectId = null;
      state.pipeline = null;
      clearInterval(state.pollTimer);
    }
    await loadProjects();
    if (state.currentProjectId) {
      await openProject(state.currentProjectId);
    } else if (state.projects.length) {
      await openProject(state.projects[0].id);
    } else {
      await renderMain(true);
    }
  } catch (e) {
    alert(`删除失败：${e.message || e}`);
  }
}

async function openProject(id) {
  state.currentProjectId = id;
  state.currentTab = "overview";
  renderProjectNav();
  await refreshPipeline();
  renderMain();
  startPoll();
}

function startPoll() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    if (state.currentTab === "overview" || !state.currentProjectId) {
      await refreshSystemMetrics();
      await refreshComfyStatus();
    }
    if (!state.currentProjectId) return;
    await refreshPipeline();
    renderSidebarJob(state.pipeline);
    const busy = activeJobsFromPipeline(state.pipeline).length > 0;
    const liveTab = state.currentTab === "jobs" || state.currentTab === "overview";
    if (!state.episodeModalOpen && (busy || liveTab)) {
      await renderMain(true);
    }
    if (state.episodeModalOpen && state.episodeModalId) {
      if (state._epModalWasBusy && !busy) {
        // 任务结束只失效视频缓存；保留分镜图 blob，避免「视频跑完图也没了」
        const epCache = state._epModalLastEp;
        const vids = (epCache?.shots || [])
          .map((s) => (s.video_path || "").trim())
          .filter(Boolean);
        if (epCache?.video_path) vids.push(epCache.video_path);
        if (vids.length) vids.forEach((p) => invalidateProjectFileCache(p));
        else invalidateProjectFileCache();
        state.epModalAssetSig = "";
      }
      state._epModalWasBusy = busy;
      await refreshEpisodeModal(state.episodeModalId);
    }
  }, POLL_MS);
}

async function refreshPipeline() {
  if (!state.currentProjectId) return;
  const pid = state.currentProjectId;
  const [pl, jobsData] = await Promise.all([
    api(`/api/projects/${pid}/pipeline`),
    api(`/api/projects/${pid}/jobs`),
  ]);
  const running = (jobsData.jobs || [])
    .filter((j) => j.status === "running")
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  pl.active_jobs = pl.active_jobs?.length ? pl.active_jobs : running;
  if (!pl.active_jobs?.length && running.length) pl.active_jobs = running;
  pl.active_job = pl.active_jobs[0] || pl.active_job || null;
  state.pipeline = pl;
}

async function renderMain(full = true) {
  const main = document.getElementById("mainView");
  const pid = state.currentProjectId;
  if (!pid) {
    main.innerHTML = `${renderServerMetricsCard(state.systemMetrics)}${renderComfyStatusCard(state.comfyStatus)}<div class="card"><h2>欢迎使用</h2><p class="muted">左侧选择短剧项目，或新建一个。</p></div>`;
    if (!state.systemMetrics) void refreshSystemMetrics();
    if (!state.comfyStatus) void refreshComfyStatus();
    return;
  }
  const proj = await api(`/api/projects/${pid}`);
  const pl = state.pipeline;
  const tabs = [
    ["overview", "总览"],
    ["production", "全局制作"],
    ["outline", "总纲剧本"],
    ["characters", "人物"],
    ["scenes", "场景"],
    ["episodes", "分集"],
    ["jobs", "任务"],
  ];
  let body = "";
  if (state.currentTab === "overview") {
    const activeJobs = activeJobsFromPipeline(pl);
    body = `
      ${renderServerMetricsCard(state.systemMetrics)}
      ${renderComfyStatusCard(state.comfyStatus)}
      ${renderActiveJobsBanner(activeJobs)}
      <div class="card"><h2>${esc(proj.name)}</h2><p class="muted">${esc(proj.description)} · ${proj.episode_count} 集 · ${proj.width}×${proj.height}</p></div>
      <div class="card"><h3>项目级流程</h3><div class="pipeline">${renderStageList(pl.project_stages, pl.project_pipeline, "project")}</div></div>
      <div class="card"><h3>各集进度</h3>
        <table><thead><tr><th>集</th><th>标题</th><th>剧本</th><th>分镜</th><th>分镜图</th><th>视频</th><th>成品</th></tr></thead>
        <tbody>${pl.episodes
          .map((e) => {
            const p = e.pipeline || {};
            const d = e.detail || {};
            const imgHint = d.shots_total ? `${d.shots_with_image || 0}/${d.shots_total}` : "";
            return `<tr class="ep-row" data-eid="${e.episode_id}"><td>EP${String(e.ep_num).padStart(2, "0")}</td><td>${esc(e.title)}</td>
              ${statusTd(p.episode_script)}${statusTd(p.storyboard_script)}${statusTd(p.storyboard_images, imgHint)}${statusTd(p.video, d.video_ready ? "MP4" : "")}
              <td class="cell-final">${renderFinalVideoAction(e.episode_id, d)}</td></tr>`;
          })
          .join("")}</tbody></table>
      <p class="muted" style="margin-top:0.75rem">状态按<strong>磁盘文件 + Comfy 任务</strong>计算；「已完成」任务若说明含占位已自动标为失败，请重新提交。</p>
      </div>`;
  } else if (state.currentTab === "production") {
    const docs = proj.docs || {};
    let prefs = {};
    try {
      const raw = docs.generation_prefs_json;
      prefs = typeof raw === "string" ? JSON.parse(raw || "{}") : raw || {};
    } catch (_) {
      prefs = {};
    }
    const look = prefs.look || "live_action";
    const sbBack = prefs.storyboard_backend || "image_api";
    const sbModel = prefs.storyboard_image_model || "";
    const apiReady = !!state.config?.storyboard_image_api_ready;
    const oh = state.config?.ohmygpt || {};
    const sbOpts = (state.config?.storyboard_backends || [
      { id: "minimax_h3", label: "MiniMax H3（逐镜静帧，与成片同模型）" },
      { id: "sdxl_t2i", label: "SDXL 文生图（需安装 checkpoint）" },
      { id: "image_api", label: "第三方 API 文生图" },
    ])
      .map(
        (b) =>
          `<option value="${esc(b.id)}" ${sbBack === b.id ? "selected" : ""}>${esc(b.label)}</option>`
      )
      .join("");
    const vb = state.config?.video_backend || {};
    const vbActive = vb.active || vb.backend || "comfy";
    const vbActiveLabel =
      vbActive === "ppio" ? "PPIO Async" : "云 GPU ComfyUI（MiniMax H3）";
    const vbOpts = (vb.backends || [
      { id: "comfy", label: "云 GPU ComfyUI（MiniMax H3）" },
      { id: "ppio", label: "PPIO Serverless Endpoint" },
    ])
      .filter((b) => b.id !== "minimax" && !b.disabled)
      .map(
        (b) =>
          `<option value="${esc(b.id)}" ${vb.backend === b.id || (!vb.backend && b.id === "comfy") ? "selected" : ""}>${esc(b.label)}${b.ready === false ? "（密钥未配）" : ""}</option>`
      )
      .join("");
    body = `<div class="card"><h3>视频生成后端</h3>
      <p class="muted">成片 / 图生视频走云 GPU Comfy（MiniMax H3 本地推理）。<strong>官方 MiniMax API 已彻底关闭</strong>。</p>
      <label>当前后端
        <select id="videoBackendSelect">${vbOpts}</select>
      </label>
      <p class="muted" style="font-size:0.78rem;margin:0.25rem 0 0.65rem">实际生效：<strong>${esc(vbActiveLabel)}</strong>${vb.note ? " · " + esc(vb.note) : ""}</p>
      <h4 style="margin:0.85rem 0 0.35rem">PPIO（可选）</h4>
      <label>PPIO Endpoint Key（形如 <code>id-app</code>）
        <input id="ppioEndpointKey" value="${esc(vb.ppio?.endpoint_key || "9bbe8e4bf9c2c122-duan")}" placeholder="9bbe8e4bf9c2c122-duan"/>
      </label>
      <label>PPIO Async Base
        <input id="ppioAsyncBase" value="${esc(vb.ppio?.async_base || "https://async-public.serverless.ppinfra.com/v1")}"/>
      </label>
      <label>PPIO API Key
        <input type="password" id="ppioApiKey" autocomplete="new-password" placeholder="${vb.ppio?.key_configured ? `已保存 ${esc(vb.ppio.key_hint)}，留空不修改` : "粘贴 sk_…"}"/>
      </label>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin:0.75rem 0 0">
        <button type="button" class="btn primary" id="saveVideoBackend">保存视频后端</button>
        <button type="button" class="btn" id="clearMinimaxKey">清除 MiniMax Key</button>
        <button type="button" class="btn" id="clearPpioKey">清除 PPIO Key</button>
      </div>
    </div>
    <div class="card"><h3>OhMyGPT 分镜出图</h3>
      <p class="muted">密钥保存在服务器数据库，不会写进代码仓库。接口文档：<a href="https://docs.ohmygpt.com/docs/image-generation" target="_blank" rel="noopener">OhMyGPT 图像生成</a>（OpenAI 兼容 <code>/v1/images/generations</code>）。</p>
      <label>API Base URL
        <input id="ohmygptBase" value="${esc(oh.api_base || "https://api.ohmygpt.com/v1")}" placeholder="https://api.ohmygpt.com/v1"/>
      </label>
      <p class="muted" style="font-size:0.78rem;margin:0.2rem 0 0.65rem">大陆网络可试 <code>https://c-z0-api-01.hash070.com/v1</code> 或文档中的 CDN 地址。</p>
      <label>API Key
        <input type="password" id="ohmygptKey" autocomplete="new-password" placeholder="${oh.key_configured ? `已保存 ${esc(oh.key_hint)}，留空则不修改` : "粘贴 OhMyGPT API Key"}"/>
      </label>
      <p class="muted" style="font-size:0.78rem;margin:0.35rem 0 0.75rem">状态：${apiReady ? `<span style="color:var(--done)">已配置（${esc(oh.key_source === "env" ? "环境变量" : "后台")}）</span>` : "未配置"}</p>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1rem">
        <button type="button" class="btn primary" id="saveOhmygpt">保存 OhMyGPT 密钥</button>
        <button type="button" class="btn" id="clearOhmygptKey">清除已存密钥</button>
      </div>
      <h4 style="margin:0.75rem 0 0.35rem">OhMyGPT 全剧脚本（聊天模型）</h4>
      <p class="muted" style="font-size:0.78rem;margin:0 0 0.5rem">默认 <code>gpt-6-astra</code>（最新旗舰）；备选 <code>gpt-6.1-sol</code> / <code>deepseek-chat</code>。会写入 <code>web/data/epXX.json</code> 并同步镜头表，<strong>不会</strong>自动出分镜图/视频。</p>
      <label>脚本模型
        <select id="scriptLlmModel">${(state.config?.script_llm_models || [{ id: "gpt-6-astra", label: "gpt-6-astra" }])
          .map((m) => `<option value="${esc(m.id)}">${esc(m.label || m.id)}</option>`)
          .join("")}</select>
      </label>
      <span class="grid2" style="display:grid;gap:0.5rem;margin:0.5rem 0">
        <label>从第<input type="number" id="scriptFromEp" min="1" max="99" value="1"/></label>
        <label>到第<input type="number" id="scriptToEp" min="1" max="99" value="2"/></label>
      </span>
      <label style="display:flex;align-items:center;gap:0.35rem;font-size:0.85rem"><input type="checkbox" id="scriptRegenOutline" checked/> 重新生成 20 集大纲</label>
      <label style="display:flex;align-items:center;gap:0.35rem;font-size:0.85rem"><input type="checkbox" id="scriptSyncDb" checked/> 同步到各集镜头表</label>
      <button type="button" class="btn" id="runScriptLlm" ${apiReady ? "" : "disabled"}>OhMyGPT 生成脚本（耗时较长）</button>
      <p id="scriptLlmStatus" class="muted" style="font-size:0.78rem;margin:0.5rem 0 0"></p>
    </div>
    <div class="card"><h3>全局制作要素</h3>
      <p class="muted">保存后，后续「Drama10 / 分镜出图 / 单镜重生 / 成片」都会自动注入这些设定。<strong>默认分镜出图为第三方 API</strong>；<strong>成片视频仍用 MiniMax H3</strong>。不同集可同时跑（例如 A 集成片 + B 集 API 分镜）；<strong>同一集</strong>分镜出图与成片仍互斥。</p>
      <label>分镜静帧引擎
        <select id="prefStoryboardBackend">${sbOpts}</select>
      </label>
      <label>OhMyGPT 出图模型 ID（选「第三方 API」时）
        <input id="prefStoryboardImageModel" placeholder="如 gpt-image-2.5-flare、dall-e-3" value="${esc(sbModel)}"/>
      </label>
      <p class="muted" style="margin:0.25rem 0 0.75rem;font-size:0.78rem">
        分镜推荐：<strong>第三方 API 文生图</strong> + 上方 OhMyGPT 密钥；横屏 16:9 剧照、脸更稳。
        ${apiReady ? "" : "请先保存 OhMyGPT Key，否则选 API 会自动回退 H3。"}
      </p>
      <label>画面风格
        <select id="prefLook">
          <option value="live_action" ${look === "live_action" ? "selected" : ""}>真人写实（推荐）</option>
          <option value="stylized" ${look === "stylized" ? "selected" : ""}>仅项目风格前缀（偏插画/动漫风险）</option>
        </select>
      </label>
      <label>项目风格前缀<textarea id="projStyle" rows="2">${esc(proj.style_prefix || "")}</textarea></label>
      <label>额外正向前缀<textarea id="prefExtra" rows="2">${esc(prefs.extra_prefix || "")}</textarea></label>
      <label>负向 / 禁止项<textarea id="prefNeg" rows="3">${esc(prefs.negative_prompt || "")}</textarea></label>
      <label>全局记忆（人物关系、色调、镜头习惯等）<textarea id="prefMemory" rows="4">${esc(prefs.global_memory || "")}</textarea></label>
      <h4 style="margin:1rem 0 0.35rem">全剧固定音色（20 集共用，保存后同步到「人物库」）</h4>
      <p class="muted" style="font-size:0.78rem;margin:0 0 0.5rem">对白里的角色名须与下表一致（如 凌川、旁白、神秘黑影）。</p>
      ${renderVoiceCastTable(prefs.voice_cast, { editable: true })}
      <h4 style="margin:1rem 0 0.35rem">分辨率（分镜图 / 成片可不同）</h4>
      <p class="muted" style="font-size:0.78rem;margin:0 0 0.5rem">项目展示尺寸默认作回退；分镜图与成片可分别指定。成片会再按 H3 短边规则对齐（最低约 352）。</p>
      <label>项目展示（宽×高）<span class="grid2" style="display:grid;gap:0.5rem">
        <input type="number" id="projW" value="${proj.width}" step="32"/>
        <input type="number" id="projH" value="${proj.height}" step="32"/>
      </span></label>
      <label>分镜图分辨率（宽×高，0=用项目展示）<span class="grid2" style="display:grid;gap:0.5rem">
        <input type="number" id="stillW" value="${Number(prefs.still_width) || 0}" step="32" min="0"/>
        <input type="number" id="stillH" value="${Number(prefs.still_height) || 0}" step="32" min="0"/>
      </span></label>
      <label>成片/图生视频分辨率（宽×高，0=用项目展示）<span class="grid2" style="display:grid;gap:0.5rem">
        <input type="number" id="videoW" value="${Number(prefs.video_width) || 0}" step="32" min="0"/>
        <input type="number" id="videoH" value="${Number(prefs.video_height) || 0}" step="32" min="0"/>
      </span></label>
      <button class="btn primary" id="saveProduction">保存全局设置</button>
    </div>`;
  } else if (state.currentTab === "outline") {
    const docs = proj.docs || {};
    body = `
      <div class="card"><h3>总纲 / 世界观</h3>
        <label>内容<textarea id="outlineMd" rows="12">${esc(docs.outline_md || "")}</textarea></label>
        <label>分集剧本说明<textarea id="seriesNote" rows="4">${esc(docs.series_scripts_note || "")}</textarea></label>
        <button class="btn primary" id="saveOutline">保存</button>
      </div>`;
  } else if (state.currentTab === "characters") {
    const ch = await api(`/api/projects/${pid}/characters`);
    body = `<div class="card"><h3>人物库</h3>
      <p class="muted gallery-hint">与「全局制作 → 全剧固定音色」同步；对白角色名须与「姓名」一致。可上传试听样本与形象参考图。</p>
      <button class="btn sm primary" id="addChar">+ 添加人物</button>
      <div class="char-library" id="charLibrary">${renderCharacterCards(ch.items || [], !!state.config?.storyboard_image_api_ready)}</div></div>`;
  } else if (state.currentTab === "scenes") {
    const sc = await api(`/api/projects/${pid}/scenes`);
    body = `<div class="card"><h3>场景库</h3>
      <button class="btn sm primary" id="addScene">+ 添加场景</button>
      <table><thead><tr><th>编号</th><th>名称</th><th>Prompt</th></tr></thead>
      <tbody>${(sc.items || [])
        .map(
          (s) =>
            `<tr><td>${esc(s.code)}</td><td>${esc(s.name)}</td><td>${esc(s.visual_prompt)}</td></tr>`
        )
        .join("")}</tbody></table></div>`;
  } else if (state.currentTab === "episodes") {
    const eps = await api(`/api/projects/${pid}/episodes`);
    const plById = Object.fromEntries(
      (state.pipeline?.episodes || []).map((x) => [x.episode_id, x.detail || {}])
    );
    body = `<div class="card"><h3>分集列表</h3>
      <table><thead><tr><th>集</th><th>标题</th><th>成品</th><th>操作</th></tr></thead>
      <tbody>${(eps.episodes || [])
        .map(
          (e) =>
            `<tr><td>EP${String(e.ep_num).padStart(2, "0")}</td><td>${esc(e.title)}</td>
            <td>${renderFinalVideoAction(e.id, plById[e.id])}</td>
            <td><button class="btn sm" data-open-ep="${e.id}">管理本集</button></td></tr>`
        )
        .join("")}</tbody></table></div>`;
  } else if (state.currentTab === "jobs") {
    const jobs = await api(`/api/projects/${pid}/jobs`);
    const activeJobs = activeJobsFromPipeline(pl);
    body = `<div class="card"><h3>生成任务</h3>
      ${activeJobs.length ? renderActiveJobsBanner(activeJobs) : `<p class="muted">无进行中的任务。分镜出图 / 成片提交后会在此显示真实进度。</p>`}
      <table class="jobs-table"><thead><tr><th>集</th><th>阶段</th><th>状态</th><th>进度</th><th>说明</th><th>更新时间</th></tr></thead>
      <tbody>${(jobs.jobs || []).map(formatJobRow).join("")}</tbody></table></div>`;
  }

  if (full) {
    main.innerHTML = `
      <div class="tabs">${tabs
        .map(
          ([id, label]) =>
            `<button type="button" class="tab ${state.currentTab === id ? "active" : ""}" data-tab="${id}">${label}</button>`
        )
        .join("")}</div>
      ${body}`;
    main.querySelectorAll(".tab").forEach((t) => {
      t.onclick = () => {
        state.currentTab = t.dataset.tab;
        renderMain();
      };
    });
  }

  renderSidebarJob(pl);

  bindOverviewEvents();
  bindProductionEvents();
  bindOutlineEvents();
  bindCharSceneEvents();
  if (state.currentTab === "characters") {
    const lib = document.querySelector(".char-table-wrap");
    if (lib) {
      void hydrateProtectedImages(lib);
      bindCharLibraryEvents();
    }
  }
  bindEpisodeListEvents();
  bindViewFinalEvents();
  if (state.currentTab === "jobs") bindJobsTableEvents();
  if (state.currentTab === "overview") {
    if (!document.getElementById("serverMetricsInner")?.textContent?.includes("%")) void refreshSystemMetrics();
    if (!state.comfyStatus?.summary) void refreshComfyStatus();
  }
}

function bindProductionEvents() {
  const saveVb = document.getElementById("saveVideoBackend");
  if (saveVb) {
    saveVb.onclick = async () => {
      const payload = {
        backend: document.getElementById("videoBackendSelect")?.value || "comfy",
        ppio_endpoint_key: document.getElementById("ppioEndpointKey")?.value?.trim() || undefined,
        ppio_async_base: document.getElementById("ppioAsyncBase")?.value?.trim() || undefined,
        clear_minimax_key: true,
      };
      const k = document.getElementById("ppioApiKey")?.value?.trim();
      if (k) payload.ppio_api_key = k;
      const r = await api("/api/settings/video-backend", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      state.config.video_backend = r;
      const keyEl = document.getElementById("ppioApiKey");
      if (keyEl) keyEl.value = "";
      const activeLabel = r.active === "ppio" ? "PPIO" : "云 GPU Comfy";
      alert(`视频后端已保存（当前生效：${activeLabel}；官方 MiniMax API 已关闭）`);
      renderMain();
      void refreshComfyStatus?.();
    };
  }
  const clearMm = document.getElementById("clearMinimaxKey");
  if (clearMm) {
    clearMm.onclick = async () => {
      if (!confirm("确定清除已保存的 MiniMax API Key？")) return;
      const r = await api("/api/settings/video-backend", {
        method: "PUT",
        body: JSON.stringify({ clear_minimax_key: true }),
      });
      state.config.video_backend = r;
      alert("已清除 MiniMax Key");
      renderMain();
    };
  }
  const clearPpio = document.getElementById("clearPpioKey");
  if (clearPpio) {
    clearPpio.onclick = async () => {
      if (!confirm("确定清除已保存的 PPIO API Key？")) return;
      const r = await api("/api/settings/video-backend", {
        method: "PUT",
        body: JSON.stringify({ clear_ppio_key: true }),
      });
      state.config.video_backend = r;
      alert("已清除 PPIO Key");
      renderMain();
    };
  }
  const saveO = document.getElementById("saveOhmygpt");
  if (saveO) {
    saveO.onclick = async () => {
      const keyEl = document.getElementById("ohmygptKey");
      const payload = {
        api_base: document.getElementById("ohmygptBase").value.trim(),
      };
      const k = keyEl.value.trim();
      if (k) payload.api_key = k;
      const r = await api("/api/settings/ohmygpt", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      state.config.ohmygpt = r;
      state.config.storyboard_image_api_ready = r.key_configured;
      if (keyEl) keyEl.value = "";
      alert("OhMyGPT 设置已保存");
      renderMain();
    };
  }
  const clearO = document.getElementById("clearOhmygptKey");
  if (clearO) {
    clearO.onclick = async () => {
      if (!confirm("确定清除服务器上保存的 OhMyGPT API Key？")) return;
      const r = await api("/api/settings/ohmygpt", {
        method: "PUT",
        body: JSON.stringify({ clear_key: true }),
      });
      state.config.ohmygpt = r;
      state.config.storyboard_image_api_ready = r.key_configured;
      alert("已清除");
      renderMain();
    };
  }
  const runScript = document.getElementById("runScriptLlm");
  if (runScript) {
    runScript.onclick = async () => {
      const fromEp = parseInt(document.getElementById("scriptFromEp")?.value || "1", 10);
      const toEp = parseInt(document.getElementById("scriptToEp")?.value || "20", 10);
      const model = document.getElementById("scriptLlmModel")?.value || "gpt-6-astra";
      const msg = `将用 OhMyGPT（${model}）生成 EP${String(fromEp).padStart(2, "0")}–EP${String(toEp).padStart(2, "0")} 的 web/data/epXX.json，并可选同步镜头表。不会自动出图/视频。继续？`;
      if (!confirm(msg)) return;
      const statusEl = document.getElementById("scriptLlmStatus");
      runScript.disabled = true;
      if (statusEl) statusEl.textContent = "生成中，请勿关闭页面（约数分钟到数十分钟）…";
      try {
        const r = await api(`/api/projects/${state.currentProjectId}/scripts/generate-ohmygpt`, {
          method: "POST",
          body: JSON.stringify({
            from_ep: fromEp,
            to_ep: toEp,
            model,
            regenerate_outline: !!document.getElementById("scriptRegenOutline")?.checked,
            sync_to_db: !!document.getElementById("scriptSyncDb")?.checked,
            confirm: true,
          }),
        });
        if (statusEl) {
          statusEl.textContent = `完成：${(r.files || []).length} 个文件；已同步集 ${(r.synced_episodes || []).join(", ")}`;
        }
        alert(`脚本生成完成（模型 ${r.model}）`);
        await refreshPipeline();
      } catch (e) {
        if (statusEl) statusEl.textContent = String(e.message || e);
        alert(e.message || String(e));
      } finally {
        runScript.disabled = false;
      }
    };
  }
  const save = document.getElementById("saveProduction");
  if (!save) return;
  save.onclick = async () => {
    await api(`/api/projects/${state.currentProjectId}`, {
      method: "PATCH",
      body: JSON.stringify({
        style_prefix: document.getElementById("projStyle").value,
        width: parseInt(document.getElementById("projW").value, 10),
        height: parseInt(document.getElementById("projH").value, 10),
        generation_prefs: {
          look: document.getElementById("prefLook").value,
          storyboard_backend: document.getElementById("prefStoryboardBackend").value,
          storyboard_image_model: document.getElementById("prefStoryboardImageModel").value.trim(),
          extra_prefix: document.getElementById("prefExtra").value,
          negative_prompt: document.getElementById("prefNeg").value,
          global_memory: document.getElementById("prefMemory").value,
          still_width: parseInt(document.getElementById("stillW")?.value || "0", 10) || 0,
          still_height: parseInt(document.getElementById("stillH")?.value || "0", 10) || 0,
          video_width: parseInt(document.getElementById("videoW")?.value || "0", 10) || 0,
          video_height: parseInt(document.getElementById("videoH")?.value || "0", 10) || 0,
          voice_cast: collectVoiceCastFromTable("voiceCastTable"),
        },
      }),
    });
    alert("全局制作设置已保存（含固定音色 → 人物库），下次 TTS 将统一使用。");
  };
}

function bindOverviewEvents() {
  document.querySelectorAll(".ep-row").forEach((row) => {
    row.onclick = () => openEpisodeModal(row.dataset.eid);
  });
}

function bindOutlineEvents() {
  const save = document.getElementById("saveOutline");
  if (save) {
    save.onclick = async () => {
      await api(`/api/projects/${state.currentProjectId}`, {
        method: "PATCH",
        body: JSON.stringify({
          outline_md: document.getElementById("outlineMd").value,
          series_scripts_note: document.getElementById("seriesNote").value,
        }),
      });
      await refreshPipeline();
      alert("已保存");
    };
  }
}

function renderCharacterCards(items, ohmyReady = false) {
  if (!items.length) {
    return `<p class="muted">暂无人物，点击上方添加。</p>`;
  }
  const ohHint = ohmyReady
    ? ""
    : `<p class="muted char-ohmy-hint">OhMyGPT 生成需先在「全局制作」保存 API Key。</p>`;
  const rows = items
    .map((c) => {
      const voices = c.voice_samples || [];
      const images = c.ref_images || [];
      const voicePaths = esc(JSON.stringify(voices));
      const imgHtml = images.length
        ? images
            .map((img) => {
              const p = img.path || img;
              const lab = img.label || "形象";
              return `<figure class="char-ref-fig"><img class="char-ref-img img-loading" data-file="${esc(p)}" alt="${esc(lab)}" title="${esc(lab)}" /><figcaption>${esc(lab)}</figcaption></figure>`;
            })
            .join("")
        : `<span class="muted char-no-img">无</span>`;
      return `<tr class="char-card char-row" data-char-id="${esc(c.id)}" data-voice-paths="${voicePaths}">
        <td class="char-td-name"><input class="char-in-name" value="${esc(c.name)}" /></td>
        <td><textarea class="char-in-role" rows="2">${esc(c.role_note || "")}</textarea></td>
        <td><textarea class="char-in-visual" rows="2">${esc(c.visual_prompt || "")}</textarea></td>
        <td class="char-td-voice">
          <select class="char-in-tts-voice" title="全片对白统一用此 OpenAI 音色（与角色名匹配）">
            ${["", "onyx", "echo", "fable", "ash", "nova", "shimmer", "alloy", "verse", "sage", "coral", "ballad"]
              .map((v) => `<option value="${v}" ${(c.tts_voice || "") === v ? "selected" : ""}>${v || "自动（旁白 nova 年轻女声 / 角色 echo）"}</option>`)
              .join("")}
          </select>
          <textarea class="char-in-voice-desc" rows="2" placeholder="声音描述；也可写 TTS:onyx">${esc(c.voice_desc || "")}</textarea>
          <div class="char-btn-row">
            <button type="button" class="btn sm btn-char-play-voice" ${voices.length ? "" : "disabled"}>试听(${voices.length})</button>
            <button type="button" class="btn sm btn-char-ai-voice" ${ohmyReady ? "" : "disabled"}>AI声音</button>
            <label class="btn sm char-file-label">上传<input type="file" class="char-upload-voice" accept="audio/*" multiple /></label>
          </div>
        </td>
        <td class="char-td-images">
          <div class="char-images-row">${imgHtml}</div>
          <div class="char-images-actions">
            <input type="text" class="char-img-label-in" placeholder="角度" value="" />
            <button type="button" class="btn sm btn-char-ai-image" ${ohmyReady ? "" : "disabled"}>AI出图</button>
            <label class="btn sm char-file-label">上传<input type="file" class="char-upload-image" accept="image/*" multiple /></label>
          </div>
        </td>
        <td class="char-td-save"><button type="button" class="btn sm primary btn-char-save">保存</button></td>
      </tr>`;
    })
    .join("");
  return `${ohHint}<div class="char-table-wrap"><table class="char-table mt"><thead><tr>
    <th>姓名</th><th>设定</th><th>出图 Prompt</th><th>声音</th><th>形象</th><th></th>
  </tr></thead><tbody id="charLibrary">${rows}</tbody></table></div>`;
}

async function uploadCharacterFile(pid, cid, kind, file, label = "") {
  const fd = new FormData();
  fd.append("file", file);
  let url = `/api/projects/${pid}/characters/${cid}/${kind === "voice" ? "voices" : "images"}`;
  if (kind === "image" && label) {
    url += `?label=${encodeURIComponent(label)}`;
  }
  const res = await fetch(url, { method: "POST", credentials: "same-origin", body: fd });
  if (res.status === 401) throw new Error("401");
  if (!res.ok) {
    const t = await res.text();
    throw new Error(parseApiError(t) || res.statusText);
  }
  return res.json();
}

async function playRandomCharacterVoice(voicePaths) {
  const list = Array.isArray(voicePaths) ? voicePaths.filter(Boolean) : [];
  if (!list.length) {
    alert("请先上传声音样本");
    return;
  }
  const rel = list[Math.floor(Math.random() * list.length)];
  const url = await fetchProjectFileBlobUrl(rel, "");
  if (!url) {
    alert("无法加载音频");
    return;
  }
  const audio = new Audio(url);
  audio.play().catch(() => alert("播放失败"));
}

function collectCharacterPayload(card) {
  const id = card.dataset.charId;
  let voicePaths = [];
  try {
    voicePaths = JSON.parse(card.dataset.voicePaths || "[]");
  } catch (_) {}
  const images = [];
  card.querySelectorAll(".char-ref-fig").forEach((fig) => {
    const path = fig.querySelector("img")?.getAttribute("data-file");
    const label = fig.querySelector("figcaption")?.textContent?.trim() || "形象";
    if (path) images.push({ path, label });
  });
  return {
    id,
    name: card.querySelector(".char-in-name")?.value?.trim() || "",
    role_note: card.querySelector(".char-in-role")?.value || "",
    visual_prompt: card.querySelector(".char-in-visual")?.value || "",
    voice_desc: card.querySelector(".char-in-voice-desc")?.value || "",
    tts_voice: card.querySelector(".char-in-tts-voice")?.value || "",
    voice_samples: voicePaths,
    ref_images: images,
  };
}

function bindCharLibraryEvents() {
  const lib = document.querySelector(".char-table-wrap");
  if (!lib || lib.dataset.bound) return;
  lib.dataset.bound = "1";
  lib.addEventListener("click", (ev) => {
    const play = ev.target.closest(".btn-char-play-voice");
    if (play) {
      const card = play.closest(".char-card");
      if (!card) return;
      let paths = [];
      try {
        paths = JSON.parse(card.dataset.voicePaths || "[]");
      } catch (_) {}
      void playRandomCharacterVoice(paths);
      return;
    }
    const aiVoice = ev.target.closest(".btn-char-ai-voice");
    if (aiVoice) {
      const card = aiVoice.closest(".char-card");
      const pid = state.currentProjectId;
      const cid = card?.dataset.charId;
      if (!card || !pid || !cid) return;
      const defaultLine =
        card.querySelector(".char-in-voice-desc")?.value?.trim().slice(0, 120) || "";
      const demo = prompt(
        "AI 试听台词（留空则用声音描述或默认一句）",
        defaultLine || "你好，我是这个角色。这是一段音色试听。"
      );
      if (demo === null) return;
      aiVoice.disabled = true;
      aiVoice.textContent = "生成中…";
      void (async () => {
        try {
          await api(`/api/projects/${pid}/characters/${cid}`, {
            method: "POST",
            body: JSON.stringify(collectCharacterPayload(card)),
          });
          await api(`/api/projects/${pid}/characters/${cid}/generate-voice`, {
            method: "POST",
            body: JSON.stringify({ demo_text: demo.trim() || null }),
          });
          await renderMain();
        } catch (e) {
          alert(e.message || "生成失败");
          aiVoice.disabled = false;
          aiVoice.textContent = "OhMyGPT 生成声音";
        }
      })();
      return;
    }
    const aiImg = ev.target.closest(".btn-char-ai-image");
    if (aiImg) {
      const card = aiImg.closest(".char-card");
      const pid = state.currentProjectId;
      const cid = card?.dataset.charId;
      if (!card || !pid || !cid) return;
      const angle = card.querySelector(".char-img-label-in")?.value?.trim() || "正面";
      aiImg.disabled = true;
      aiImg.textContent = "出图中…";
      void (async () => {
        try {
          await api(`/api/projects/${pid}/characters/${cid}`, {
            method: "POST",
            body: JSON.stringify(collectCharacterPayload(card)),
          });
          await api(`/api/projects/${pid}/characters/${cid}/generate-image`, {
            method: "POST",
            body: JSON.stringify({ angle_label: angle }),
          });
          await renderMain();
        } catch (e) {
          alert(e.message || "出图失败");
          aiImg.disabled = false;
          aiImg.textContent = "OhMyGPT 出形象";
        }
      })();
      return;
    }
    const save = ev.target.closest(".btn-char-save");
    if (save) {
      const card = save.closest(".char-card");
      if (!card || !state.currentProjectId) return;
      const payload = collectCharacterPayload(card);
      if (!payload.name) {
        alert("请填写姓名");
        return;
      }
      void api(`/api/projects/${state.currentProjectId}/characters`, {
        method: "POST",
        body: JSON.stringify(payload),
      }).then(() => renderMain());
    }
  });
  lib.addEventListener("change", (ev) => {
    const vin = ev.target.closest(".char-upload-voice");
    if (vin?.files?.length) {
      const card = vin.closest(".char-card");
      const pid = state.currentProjectId;
      const cid = card?.dataset.charId;
      if (!card || !pid || !cid) return;
      void (async () => {
        for (const f of vin.files) {
          const updated = await uploadCharacterFile(pid, cid, "voice", f);
          card.dataset.voicePaths = JSON.stringify(updated.voice_samples || []);
          const btn = card.querySelector(".btn-char-play-voice");
          if (btn) {
            btn.disabled = !(updated.voice_samples || []).length;
            btn.textContent = `随机试听（${(updated.voice_samples || []).length}）`;
          }
        }
        vin.value = "";
        alert("声音已上传");
      })().catch((e) => alert(e.message));
      return;
    }
    const iin = ev.target.closest(".char-upload-image");
    if (iin?.files?.length) {
      const card = iin.closest(".char-card");
      const pid = state.currentProjectId;
      const cid = card?.dataset.charId;
      const label = card?.querySelector(".char-img-label-in")?.value?.trim() || "形象";
      if (!card || !pid || !cid) return;
      void (async () => {
        for (const f of iin.files) {
          await uploadCharacterFile(pid, cid, "image", f, label);
        }
        iin.value = "";
        await renderMain();
      })().catch((e) => alert(e.message));
    }
  });
}

function bindCharSceneEvents() {
  const addC = document.getElementById("addChar");
  if (addC) {
    addC.onclick = () => {
      const name = prompt("人物姓名");
      if (!name) return;
      api(`/api/projects/${state.currentProjectId}/characters`, {
        method: "POST",
        body: JSON.stringify({ name, role_note: "", visual_prompt: "", voice_desc: "" }),
      }).then(() => renderMain());
    };
  }
  const addS = document.getElementById("addScene");
  if (addS) {
    addS.onclick = () => {
      const name = prompt("场景名称");
      if (!name) return;
      api(`/api/projects/${state.currentProjectId}/scenes`, {
        method: "POST",
        body: JSON.stringify({ name, code: "", visual_prompt: "" }),
      }).then(() => renderMain());
    };
  }
}

function bindEpisodeListEvents() {
  document.querySelectorAll("[data-open-ep]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      openEpisodeModal(btn.dataset.openEp);
    };
  });
}

function patchShotMediaCells(editor, ep) {
  /** 资产签名未变时：只刷新视频按钮；若分镜图丢失显示则就地补回，绝不整表重绘。 */
  if (!editor) return;
  const needHydrate = [];
  for (const s of ep.shots || []) {
    const tr = editor.querySelector(`tr[data-shot="${s.shot_num}"]`);
    if (!tr) continue;
    const imgPath = (s.image_path || "").trim();
    const vidPath = (s.video_path || "").trim();
    if (imgPath) tr.dataset.imagePath = imgPath;
    if (vidPath) tr.dataset.videoPath = vidPath;
    const vidCell = tr.querySelector(".shot-video-cell");
    if (vidCell) vidCell.innerHTML = renderShotVideoBtn(ep, s);
    const wrap = tr.querySelector(".shot-still-wrap");
    if (wrap && imgPath) {
      const img = wrap.querySelector("img.shot-thumb");
      const shown = (img?.getAttribute("data-file") || "").trim();
      const broken = !img || !img.src || img.classList.contains("img-error") || shown !== imgPath;
      if (broken) {
        wrap.innerHTML = renderShotStillImg(ep, s);
        needHydrate.push(wrap);
      }
    } else if (wrap && !imgPath) {
      wrap.innerHTML = `<span class="muted">无</span>`;
    }
  }
  bindViewShotVideoEvents(editor);
  for (const el of needHydrate) void hydrateProtectedImages(el);
}

async function refreshEpisodeModal(episodeId) {
  if (!state.episodeModalOpen || state.episodeModalId !== episodeId) return;
  const modal = document.getElementById("modal");
  if (modal.classList.contains("hidden")) return;
  const ep = await api(`/api/episodes/${episodeId}`);
  state._epModalLastEp = ep;
  const pl = ep.pipeline || {};
  const sig = episodeAssetSignature(ep);
  const banner = modal.querySelector(".ep-status-banner-wrap");
  if (banner) banner.innerHTML = episodeStatusBanner(ep);
  const pipe = modal.querySelector(".pipeline.mb");
  if (pipe) pipe.innerHTML = renderStageList(state.config.stages.episode, pl, "episode");
  const editor = modal.querySelector("#shotsEditor");
  if (state.epModalAssetSig === sig) {
    // 进度轮询：只更新状态条；镜头表资产不变则不重绘
    if (editor) patchShotMediaCells(editor, ep);
    return;
  }
  state.epModalAssetSig = sig;
  const gal = modal.querySelector(".storyboard-gallery-wrap");
  if (gal) {
    gal.innerHTML = renderStoryboardGallery(ep);
    await hydrateProtectedImages(gal);
    bindViewFinalEvents();
  }
  if (editor) {
    const scrollTop = editor.scrollTop;
    editor.innerHTML = renderShotsEditor(ep);
    editor.scrollTop = scrollTop;
    await hydrateProtectedImages(editor);
    bindViewShotVideoEvents(editor);
    bindShotTableActions(episodeId);
  }
}

function closeEpisodeModal() {
  const modal = document.getElementById("modal");
  modal.classList.add("hidden");
  state.episodeModalOpen = false;
  state.episodeModalId = null;
  _blobUrls.forEach((u) => URL.revokeObjectURL(u));
  _blobUrls.clear();
  _fileBlobCache.clear();
  state.epModalAssetSig = "";
}

async function openEpisodeModal(episodeId) {
  state.episodeModalOpen = true;
  state.episodeModalId = episodeId;
  state.epModalAssetSig = "";
  const ep = await api(`/api/episodes/${episodeId}`);
  const pl = ep.pipeline || {};
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  modal.innerHTML = `<div class="modal-box modal-box--episode">
    <div class="modal-episode-head">
      <h2>EP${String(ep.ep_num).padStart(2, "0")} ${esc(ep.title)}</h2>
      <div class="modal-episode-head-actions">
        <button type="button" class="btn sm primary" id="saveEpTop" title="保存本集剧本、镜头表与成片路径">保存</button>
        <button type="button" class="modal-close" id="closeModalTop" title="关闭" aria-label="关闭">×</button>
      </div>
    </div>
    <div class="ep-status-banner-wrap">${episodeStatusBanner(ep)}</div>
    <div class="card ep-voice-cast-card" style="margin:0.75rem 0;padding:0.65rem 0.85rem">
      <h3 style="margin:0 0 0.35rem;font-size:1rem">本集人物与固定音色</h3>
      ${ep.character_bible ? `<p class="muted" style="font-size:0.82rem;margin:0 0 0.5rem">${esc(ep.character_bible)}</p>` : ""}
      ${renderVoiceCastTable(ep.voice_cast || [], { editable: false, tableId: "epVoiceCastTable" })}
      <p class="muted" style="font-size:0.78rem;margin:0.5rem 0 0">修改音色请至「全局制作」或编辑 <code>web/data/epXX.json</code> 的 <code>voice_cast</code> 后重新同步本集。</p>
    </div>
    <div class="pipeline mb">${renderStageList(state.config.stages.episode, pl, "episode")}</div>
    <div class="storyboard-gallery-wrap">${renderStoryboardGallery(ep)}</div>
    <div class="grid2 episode-edit-grid">
      <label>本集剧本<textarea id="epScript" rows="8">${esc(ep.script_md || "")}</textarea></label>
      <label>分镜脚本<textarea id="epStoryboard" rows="8">${esc(ep.storyboard_md || "")}</textarea></label>
    </div>
    <div class="shots-editor-head">
      <h3>镜头表</h3>
      <button type="button" class="btn sm primary" id="addShotRowHead">+ 添加镜头</button>
    </div>
    <div id="shotsEditor" class="shots-editor">${renderShotsEditor(ep)}</div>
    <label>成片路径（MP4）<input id="epVideo" value="${esc(ep.video_path || "")}" placeholder="/path or projects/.../video.mp4" /></label>
    <div class="actions" style="margin-top:1rem;display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center">
      <button class="btn primary" id="saveEp">保存本集</button>
      <button class="btn primary" id="jobShot1Still" title="只生成第 1 镜分镜图，不跑整集">生成镜 1 分镜图（单镜）</button>
      <button class="btn" id="jobDrama10" title="按当前镜头表写入导出文件，供 Comfy 使用">导出 Drama10 文件</button>
      <details class="ep-batch-actions" open style="width:100%;margin-top:0.35rem">
        <summary class="muted" style="cursor:pointer;font-size:0.82rem">整集批量（已有图/视频自动跳过）</summary>
        <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:0.5rem">
          <button class="btn sm" type="button" id="jobImages" title="只补没有 PNG 的镜">① 批量补缺分镜图</button>
          <button class="btn sm" type="button" id="jobVideo" title="只补没有 mp4 的镜，不自动拼接">② 批量补缺分镜视频</button>
          <button class="btn sm primary" type="button" id="jobMerge" title="把已有分镜视频按镜号拼成整集成片">③ 合并成片</button>
        </div>
      </details>
      <p class="muted" style="margin:0.35rem 0 0;font-size:0.82rem;width:100%">流程：先补缺分镜图 → 再补缺分镜视频 → 全部有视频后点「合并成片」。单镜重跑仍用镜头表按钮。</p>
    </div>
  </div>`;

  document.getElementById("closeModalTop").onclick = () => closeEpisodeModal();
  state.epModalAssetSig = episodeAssetSignature(ep);
  await hydrateProtectedImages(modal);
  bindViewFinalEvents();
  bindViewShotVideoEvents(modal);
  const runSaveEpisode = async () => {
    const topBtn = document.getElementById("saveEpTop");
    const bottomBtn = document.getElementById("saveEp");
    topBtn?.setAttribute("disabled", "");
    bottomBtn?.setAttribute("disabled", "");
    try {
      await saveEpisodeEditor(episodeId);
      alert("已保存");
      openEpisodeModal(episodeId);
    } finally {
      topBtn?.removeAttribute("disabled");
      bottomBtn?.removeAttribute("disabled");
    }
  };
  document.getElementById("saveEpTop").onclick = () => void runSaveEpisode();
  document.getElementById("saveEp").onclick = () => void runSaveEpisode();
  const bindAddShot = () => void addShotFromEditor(episodeId);
  document.getElementById("addShotRowHead")?.addEventListener("click", bindAddShot);
  document.getElementById("jobDrama10").onclick = () => startJob("drama10_export", episodeId);
  document.getElementById("jobShot1Still").onclick = async () => {
    await saveEpisodeEditor(episodeId);
    await startJob("storyboard_images", episodeId, { shot_nums: [1] });
  };
  document.getElementById("jobImages").onclick = async () => {
    if (
      !confirm(
        "批量补缺分镜图：已有 PNG 的镜会跳过，只生成缺失的镜。确定？"
      )
    ) {
      return;
    }
    try {
      await saveEpisodeEditor(episodeId);
      await startJob("storyboard_images", episodeId, {
        confirm_full_episode: true,
        skip_existing: true,
      });
    } catch (e) {
      alert(`批量补缺分镜图失败：${e.message || e}`);
    }
  };
  document.getElementById("jobVideo").onclick = async () => {
    if (
      !confirm(
        "批量补缺分镜视频：已有 mp4 的镜会跳过，只生成缺失的镜，生成后不自动拼接。确定？"
      )
    ) {
      return;
    }
    try {
      await saveEpisodeEditor(episodeId);
      await startJob("video", episodeId, {
        confirm_full_episode: true,
        skip_existing: true,
        auto_concat: false,
        preview_with_audio: true,
      });
    } catch (e) {
      alert(`批量补缺分镜视频失败：${e.message || e}`);
    }
  };
  document.getElementById("jobMerge").onclick = async () => {
    if (
      !confirm(
        "合并成片：按镜号顺序拼接本集已有分镜视频（不重新生成）。缺镜会失败。确定？"
      )
    ) {
      return;
    }
    try {
      await saveEpisodeEditor(episodeId);
      await startJob("video", episodeId, { mux_only: true });
    } catch (e) {
      alert(`合并成片失败：${e.message || e}`);
    }
  };

  ensureEpisodeModalDelegates();
  document.getElementById("addShotRow")?.addEventListener("click", bindAddShot);
  document.getElementById("addShot1")?.addEventListener("click", bindAddShot);
}

let _episodeModalDelegates = false;

function ensureEpisodeModalDelegates() {
  if (_episodeModalDelegates) return;
  const modal = document.getElementById("modal");
  if (!modal) return;
  _episodeModalDelegates = true;
  modal.addEventListener("click", (ev) => {
    if (!state.episodeModalOpen || !state.episodeModalId) return;
    const viewVid = ev.target.closest(".btn-view-shot-video");
    if (viewVid) {
      ev.preventDefault();
      ev.stopPropagation();
      void openShotVideoViewer({
        episodeId: viewVid.dataset.eid || state.episodeModalId,
        shotNum: viewVid.dataset.shot,
        relPath: viewVid.dataset.file,
        fileVer: viewVid.dataset.fileVer || "",
        title: viewVid.dataset.title || "",
      });
      return;
    }
    const genVid = ev.target.closest(".gen-shot-video");
    if (genVid) {
      ev.preventDefault();
      ev.stopPropagation();
      const sn = parseInt(genVid.dataset.shot, 10);
      if (!Number.isFinite(sn)) return;
      openShotVideoGenChoice(state.episodeModalId, sn);
      return;
    }
    const delBtn = ev.target.closest(".del-shot");
    if (delBtn) {
      ev.preventDefault();
      const tr = delBtn.closest("tr[data-shot]");
      const sn = parseInt(tr?.dataset?.shot, 10);
      if (Number.isFinite(sn)) void deleteShotFromEditor(state.episodeModalId, sn);
      return;
    }
    const regen = ev.target.closest(".regen-shot");
    if (regen) {
      ev.preventDefault();
      ev.stopPropagation();
      const sn = parseInt(regen.dataset.shot, 10);
      if (!Number.isFinite(sn)) return;
      void openShotRegenDialog(state.episodeModalId, sn);
      return;
    }
    const quickStill = ev.target.closest(".gen-shot-still");
    if (quickStill) {
      ev.preventDefault();
      ev.stopPropagation();
      const sn = parseInt(quickStill.dataset.shot, 10);
      if (!Number.isFinite(sn)) return;
      void (async () => {
        await saveEpisodeEditor(state.episodeModalId);
        await startJob("storyboard_images", state.episodeModalId, { shot_nums: [sn] });
        alert(`已提交仅镜 ${sn} 的分镜出图（单镜）`);
      })();
      return;
    }
  });
  modal.addEventListener("change", (ev) => {
    if (!state.episodeModalOpen || !state.episodeModalId) return;
    const inp = ev.target;
    if (inp?.matches?.('input[type="file"][data-shot]') && inp.files?.[0]) {
      void (async () => {
        const fd = new FormData();
        fd.append("file", inp.files[0]);
        await fetch(
          `/api/episodes/${state.episodeModalId}/shots/${inp.dataset.shot}/image`,
          { method: "POST", body: fd, credentials: "same-origin" }
        );
        openEpisodeModal(state.episodeModalId);
      })();
    }
  });
}

function bindShotTableActions(_episodeId) {
  ensureEpisodeModalDelegates();
}

function removeRegenOverlay() {
  document.querySelectorAll(".regen-overlay").forEach((el) => el.remove());
}

async function openShotRegenDialog(episodeId, shotNum) {
  removeRegenOverlay();
  const host = document.querySelector(".modal-box--episode");
  if (!host) {
    alert("请先打开本集编辑窗口后再点「重生本镜」。");
    return;
  }
  const row = document.querySelector(`#shotsEditor tr[data-shot="${shotNum}"]`);

  const overlay = document.createElement("div");
  overlay.className = "regen-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.innerHTML = `<div class="modal-box regen-box">
    <h3>镜 ${shotNum} · 确认脚本后再重生</h3>
    <p class="regen-hint muted" id="regenStatus">正在加载本镜脚本…</p>
    <label for="regenVisual">画面描述（镜头表）</label>
    <textarea id="regenVisual" rows="4" disabled></textarea>
    <label for="regenStill">Comfy 出图提示词</label>
    <textarea id="regenStill" rows="8" disabled></textarea>
    <label style="display:flex;align-items:center;gap:0.4rem;margin-top:0.65rem;color:var(--text)">
      <input type="checkbox" id="regenSaveVisual" checked /> 提交时保存「画面描述」到镜头表
    </label>
    <div class="regen-actions">
      <button type="button" class="btn" id="regenCancel">取消</button>
      <button type="button" class="btn primary" id="regenSubmit" disabled>提交重生</button>
    </div>
  </div>`;
  host.appendChild(overlay);

  const close = () => overlay.remove();
  overlay.querySelector("#regenCancel").onclick = close;
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  overlay.querySelector(".regen-box").onclick = (ev) => ev.stopPropagation();

  let preview;
  try {
    preview = await api(`/api/episodes/${episodeId}/shots/${shotNum}/regen-preview`);
  } catch (e) {
    alert(`无法加载本镜脚本：${e.message || e}`);
    close();
    return;
  }
  if (!document.body.contains(overlay)) return;

  const visualFromRow = row?.querySelector(".shot-vis")?.value;
  const visualInit = visualFromRow != null ? visualFromRow : preview.visual || "";
  const stillInit = preview.still_prompt || "";

  overlay.querySelector("#regenStatus").textContent =
    "修改下方内容后点「提交重生」。出图提示词已含全局真人/风格设定。";
  const visEl = overlay.querySelector("#regenVisual");
  const stillEl = overlay.querySelector("#regenStill");
  visEl.value = visualInit;
  stillEl.value = stillInit;
  visEl.disabled = false;
  stillEl.disabled = false;
  const submitBtn = overlay.querySelector("#regenSubmit");
  submitBtn.disabled = false;

  submitBtn.onclick = async () => {
    const visual = visEl.value;
    const still = stillEl.value;
    const saveVisual = overlay.querySelector("#regenSaveVisual").checked;
    const visualChanged = visual.trim() !== (preview.visual || "").trim();
    const stillChanged = still.trim() !== stillInit.trim();
    close();
    if (row && saveVisual) {
      row.querySelector(".shot-vis").value = visual;
    }
    await saveEpisodeEditor(episodeId);
    const params = { shot_nums: [shotNum] };
    if (saveVisual && visualChanged) {
      params.shot_visuals = { [String(shotNum)]: visual };
    }
    if (stillChanged) {
      params.still_prompts = { [String(shotNum)]: still };
    }
    await startJob("storyboard_images", episodeId, params);
  };
}

function parseVoiceRoleTag(rolePart) {
  const raw = (rolePart || "").trim();
  const m = raw.match(/^([^[\{]+)(?:\[([^\]]*)\])?(?:\{([^}]*)\})?$/);
  if (!m) return { role: raw, tts_voice: "", emotion: "" };
  return {
    role: (m[1] || "").trim(),
    tts_voice: (m[2] || "").trim(),
    emotion: (m[3] || "").trim(),
  };
}

function formatVoicesForEditor(voices) {
  if (!voices || !voices.length) return "";
  return voices
    .map((v) => {
      const role = (v.role || "旁白").trim();
      const tv = (v.tts_voice || "").trim();
      const emo = (v.emotion || "").trim();
      const tag = tv ? `[${tv}]` : "";
      const emoTag = emo ? `{${emo}}` : "";
      const text = (v.text || "").trim();
      const note = (v.note || "").trim();
      const st = v.start_sec != null && v.start_sec !== "" ? `@${v.start_sec}s` : "";
      if (!text && note) {
        return `${role}${tag}${emoTag}：（${note}）`;
      }
      return `${role}${tag}${emoTag}：${text}${st}`;
    })
    .join("\n");
}

function parseVoicesFromEditor(raw) {
  const lines = (raw || "")
    .split(/\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  const out = [];
  for (const line of lines) {
    let body = line;
    let startSec = null;
    const tm = line.match(/@([\d.]+)s\s*$/);
    if (tm) {
      startSec = parseFloat(tm[1]);
      body = line.slice(0, tm.index).trim();
    }
    const m = body.match(/^([^：:]+)[：:](.+)$/);
    if (!m) continue;
    const { role, tts_voice, emotion } = parseVoiceRoleTag(m[1]);
    let text = m[2].trim();
    let note = "";
    const nm = text.match(/^（(.+)）$/);
    if (nm && !text.includes("@")) {
      note = nm[1].trim();
      text = "";
    }
    const item = { role, text };
    if (note) item.note = note;
    if (tts_voice) item.tts_voice = tts_voice;
    if (emotion) item.emotion = emotion;
    if (startSec != null && !Number.isNaN(startSec)) item.start_sec = startSec;
    out.push(item);
  }
  return out;
}

function renderShotsEditor(ep) {
  const shots = ep.shots || [];
  if (!shots.length) {
    return `<p class="muted">暂无镜头，点击下方按钮添加第一镜。</p>
      <button type="button" class="btn sm primary" id="addShot1">+ 添加镜头</button>`;
  }
  const d = ep.production || {};
  const finalRow =
    d.video_ready && d.video_path
      ? `<tr class="shot-asset-row"><td colspan="2">—</td><td colspan="2"><strong>本集成片</strong>
      <button type="button" class="btn sm primary btn-view-final" data-eid="${esc(ep.id)}">播放成片</button></td><td colspan="2"></td></tr>`
      : "";
  return `<table class="shots-table shots-table--video"><colgroup>
    <col class="col-num"/><col class="col-dur"/><col class="col-vis"/><col class="col-script"/>
    <col class="col-img"/><col class="col-vid"/><col class="col-act"/>
    </colgroup><thead><tr>
    <th>#</th><th>秒</th><th>分镜画面</th><th>视频脚本（运镜/对白/声音）</th><th>分镜图</th><th>视频</th><th></th>
    </tr></thead><tbody>
    ${finalRow}
    ${shots
      .map((s) => {
        const voicesTxt = formatVoicesForEditor(s.voices);
        return `<tr data-shot="${s.shot_num}" data-image-path="${esc(s.image_path || "")}" data-video-path="${esc(s.video_path || "")}">
          <td>${s.shot_num}</td>
          <td><input class="shot-dur" value="${s.duration_sec}" style="width:4rem"/></td>
          <td class="shot-script-cell shot-script-cell--vis">
            <input class="shot-title" placeholder="镜标题" value="${esc(s.title || "")}"/>
            <textarea class="shot-vis" rows="4" placeholder="画面描述">${esc(s.visual || "")}</textarea>
            <textarea class="shot-scene" rows="2" placeholder="场景（可空，默认同画面）">${esc(s.scene || "")}</textarea>
          </td>
          <td class="shot-script-cell">
            <textarea class="shot-motion" rows="3" placeholder="运镜 / 摄影机">${esc(s.motion || "")}</textarea>
            <textarea class="shot-performance" rows="2" placeholder="人物表演 / 口型">${esc(s.performance || "")}</textarea>
            <textarea class="shot-voices" rows="4" placeholder="对白/音色，每行：角色[onyx]{情绪}：台词@1.0s；无台词：角色[echo]：（说明）">${esc(voicesTxt)}</textarea>
            <input class="shot-ambience" placeholder="环境声 / BGM 提示" value="${esc(s.ambience || "")}"/>
            <textarea class="shot-continuity" rows="2" placeholder="剧情衔接（可选）">${esc(s.continuity || "")}</textarea>
          </td>
          <td class="shot-img-cell shot-img-cell--compact"><span class="shot-still-wrap">${renderShotStillImg(ep, s)}</span>
            <input type="file" class="shot-file-in" data-shot="${s.shot_num}" accept="image/*" title="上传分镜图"/>
            <button type="button" class="btn sm primary regen-shot" data-shot="${s.shot_num}">① 生成分镜图</button>
            <button type="button" class="btn sm gen-shot-still" data-shot="${s.shot_num}">快速出图（同①）</button>
            </td>
          <td class="shot-video-cell">${renderShotVideoBtn(ep, s)}</td>
          <td><button type="button" class="btn sm del-shot">删</button></td>
        </tr>`;
      })
      .join("")}
    </tbody></table>
    <p class="muted shot-editor-hint">H3 <strong>单镜</strong>带声视频 → 整集剪辑。对白示例：<code>旁白[nova]{克制}：我拼命加班三年…@0.6s</code>（nova=年轻女声；史诗男旁白可用 onyx）；无台词：<code>凌川[echo]：（本镜无对白，仅表演）</code></p>
    <button type="button" class="btn sm primary" id="addShotRow">+ 添加镜头</button>`;
}

function collectShotsFromEditor() {
  const rows = document.querySelectorAll("#shotsEditor tr[data-shot]");
  return Array.from(rows).map((tr) => ({
    shot_num: parseInt(tr.dataset.shot, 10),
    duration_sec: parseFloat(tr.querySelector(".shot-dur")?.value || 5),
    title: tr.querySelector(".shot-title")?.value || "",
    visual: tr.querySelector(".shot-vis")?.value || "",
    scene: tr.querySelector(".shot-scene")?.value || "",
    motion: tr.querySelector(".shot-motion")?.value || "",
    performance: tr.querySelector(".shot-performance")?.value || "",
    continuity: tr.querySelector(".shot-continuity")?.value || "",
    ambience: tr.querySelector(".shot-ambience")?.value || "",
    voices: parseVoicesFromEditor(tr.querySelector(".shot-voices")?.value || ""),
    image_path: tr.dataset.imagePath || "",
    video_path: tr.dataset.videoPath || "",
  }));
}

async function startJob(stageId, episodeId, params) {
  try {
    const body = { stage_id: stageId, episode_id: episodeId };
    if (params) body.params = params;
    const job = await api(`/api/projects/${state.currentProjectId}/jobs`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await refreshPipeline();
    if (state.episodeModalOpen && state.episodeModalId === episodeId) {
      await refreshEpisodeModal(episodeId);
    } else {
      state.currentTab = "jobs";
      await renderMain(true);
    }
    alert(`已提交：${stageLabel(stageId)}\n任务 ID：${job.id?.slice(0, 8)}…\nComfy 成片较久，请在本集窗口或「任务」页看进度（约每 ${POLL_MS / 1000} 秒刷新）。`);
  } catch (e) {
    alert(`提交失败：${e.message || e}`);
    await refreshPipeline();
    renderMain(true);
  }
}

function showNewProjectModal() {
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  modal.innerHTML = `<div class="modal-box">
    <h2>新建短剧项目</h2>
    <label>名称<input id="npName" value="我的短剧"/></label>
    <label>简介<textarea id="npDesc" rows="3"></textarea></label>
    <label>集数<input type="number" id="npEps" value="20" min="1" max="99"/></label>
    <label>宽<input type="number" id="npW" value="640" step="32"/></label>
    <label>高<input type="number" id="npH" value="360" step="32"/></label>
    <label>风格前缀<textarea id="npStyle" rows="2">国风仙侠，电影感，无文字无水印</textarea></label>
    <button class="btn primary" id="npCreate">创建</button>
    <button class="btn" id="npCancel">取消</button>
  </div>`;
  document.getElementById("npCancel").onclick = () => modal.classList.add("hidden");
  document.getElementById("npCreate").onclick = async () => {
    const p = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({
        name: document.getElementById("npName").value,
        description: document.getElementById("npDesc").value,
        episode_count: parseInt(document.getElementById("npEps").value, 10),
        width: parseInt(document.getElementById("npW").value, 10),
        height: parseInt(document.getElementById("npH").value, 10),
        style_prefix: document.getElementById("npStyle").value,
      }),
    });
    modal.classList.add("hidden");
    await loadProjects();
    openProject(p.id);
  };
}

async function init() {
  state.config = await api("/api/config");
  document.getElementById("configLine").textContent = `ComfyUI ${state.config.public_comfyui_url}`;
  document.getElementById("btnNewProject").onclick = showNewProjectModal;
  ensureEpisodeModalDelegates();
  await loadProjects();
  if (state.projects.length) openProject(state.projects[0].id);
  else {
    renderMain();
    startPoll();
  }
}

init();
