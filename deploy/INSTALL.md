# 漫剧工作台 · 新服务器安装指南

仓库：[https://github.com/redf4159/aiju](https://github.com/redf4159/aiju)

本仓库包含：**Web 漫剧制作台**、**ComfyUI Drama10 节点参考**、**部署单元**、分集文档与 JSON 模板。  
不含 ComfyUI 本体、MiniMax 权重、OhMyGPT / Comfy Token 等密钥。

## 线上对照（当前预览）

| 项 | 值 |
|----|-----|
| 制作台 | https://dj.preview.3swl.com/ |
| 预览机代码 | `/opt/ai-forge/bingjing/web`（systemd `bingjing-web` → `:8097`） |
| GPU / Comfy | H3 `175.155.64.171`，Comfy `:8000`，经隧道到预览机 `127.0.0.1:18000` |
| Drama10 节点 | H3 `/home/linux/ComfyUI/custom_nodes/ComfyUI_Drama10/drama10_nodes.py` |

旧文档中的 `/home/linux/bingjing/web:8001` 为 H3 本机制作台路径，仍可用同一套 `web/` 部署。

## 1. 目录约定

| 路径 | 说明 |
|------|------|
| `<BINGJING>/web` | 本仓库 `web/`（`app/`、`requirements.txt`、可选 `data/ep*.json`） |
| ComfyUI | 如 `/home/linux/ComfyUI` |
| Drama10 | 用 `comfyui/workflows/_server_ref_drama10_nodes.py` 覆盖 `custom_nodes/ComfyUI_Drama10/drama10_nodes.py` |
| `web/data` | `studio.db`、`projects/`（**不进 git，部署勿覆盖**） |

## 2. 依赖

- Ubuntu 22.04+，Python 3.10+
- ffmpeg / ffprobe
- ComfyUI + MiniMax H3（图生视频 + 原生音轨）
- 可选：edge-tts / OhMyGPT（分镜 API、脚本 LLM）

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg git
python3 -m venv /opt/ai-forge/bingjing/venv   # 或 /home/linux/comfyui-venv
source /opt/ai-forge/bingjing/venv/bin/activate
pip install -r web/requirements.txt
```

## 3. 部署 Web

```bash
git clone https://github.com/redf4159/aiju.git /tmp/aiju
APP=/opt/ai-forge/bingjing/web   # 或 /home/linux/bingjing/web
mkdir -p "$APP"
rsync -a --delete /tmp/aiju/web/app/ "$APP/app/"
cp /tmp/aiju/web/requirements.txt "$APP/"
mkdir -p "$APP/data"
cp -n /tmp/aiju/web/data/ep*.json "$APP/data/" 2>/dev/null || true
cp -n /tmp/aiju/web/data/gpu_vram_test_ep*.json "$APP/data/" 2>/dev/null || true
```

systemd 示例见 `deploy/bingjing-web.service`（按需改 `WorkingDirectory`、`ExecStart` 的 venv、端口、`WEB_USER`/`WEB_PASSWORD`、`COMFYUI_URL`）。

```bash
# 预览机示例（系统级）
cp /tmp/aiju/deploy/bingjing-web.service /etc/systemd/system/
# 编辑 Environment 后：
systemctl daemon-reload
systemctl enable --now bingjing-web.service
```

首次登录制作台 → **制作设置** 配置 OhMyGPT / Comfy Token（勿写入 git）。

## 4. 部署 Drama10 节点（H3）

```bash
cp /tmp/aiju/comfyui/workflows/_server_ref_drama10_nodes.py \
  /home/linux/ComfyUI/custom_nodes/ComfyUI_Drama10/drama10_nodes.py
# 可选：web/scripts/h3_native_audio_patch.py 为增量补丁说明（节点文件已含原生音轨逻辑）
systemctl --user restart bingjing-comfy.service
```

成片策略：**只用 MiniMax H3 原生音轨**；禁止 Web TTS 混音；画面禁止烧录字幕。

## 5. 环境变量要点

见 `deploy/bingjing-web.service`：

- `COMFYUI_URL`：本机 `http://127.0.0.1:8000` 或隧道 `http://127.0.0.1:18000`
- `COMFYUI_ROOT` / `BINGJING_DATA`
- `DRAMA10_WEB_AUDIO_ONLY=0`（由 H3 出原生声；Web 不再盖轨）
- `DRAMA10_HUM_FALLBACK_REMUX=0`（禁止无声时 Web 补音）
- 读 Comfy 日志 ETA（可选）：`COMFY_LOG_SSH_*`、`COMFY_LOG_PATH`

## 6. 本地同步

Windows：`deploy/sync_to_server.ps1`（勿同步 `data/studio.db` / `projects/`）。

---

**安全**：仓库不含 SSH 密码与 API Key；`web/_*.py` 临时运维脚本已在 `.gitignore`。
