# 漫剧工作台 · 换机 / 换 GPU 部署指南

仓库：[https://github.com/redf4159/aiju](https://github.com/redf4159/aiju)

本仓库包含：

| 目录 | 内容 |
|------|------|
| `web/` | FastAPI 制作台（分集脚本、分镜出图、成片、资产） |
| `comfyui/workflows/` | Drama10 节点参考 + 常用工作流 JSON |
| `deploy/` | systemd 单元、环境变量模板、同步脚本 |
| 根目录文档 / `04`～`07` | 剧本与分镜素材（可选同步） |

**不包含**：ComfyUI 本体、MiniMax / LTX 权重、`studio.db` / `projects/` 业务数据、OhMyGPT / Comfy Token 等密钥。

## 推荐架构

```
[预览 / Web 机]                         [GPU / Comfy 机]
  bingjing-web :8097  ──隧道/内网──►  ComfyUI :8000
  Nginx / 域名                          MiniMax H3 + Drama10 节点
  /opt/ai-forge/bingjing/web            /home/linux/ComfyUI
```

两台可合为一台（本机 `COMFYUI_URL=http://127.0.0.1:8000`）。

## 1. 克隆仓库

```bash
git clone https://github.com/redf4159/aiju.git /tmp/aiju
# 更新已有部署：
# cd /tmp/aiju && git pull
```

## 2. 部署 Web 制作台（预览机）

```bash
APP=/opt/ai-forge/bingjing/web
VENV=/opt/ai-forge/bingjing/venv
sudo mkdir -p "$APP" /opt/ai-forge/bingjing
sudo apt-get update && sudo apt-get install -y ffmpeg git python3-venv
python3 -m venv "$VENV"
source "$VENV/bin/activate"
pip install -U pip
pip install -r /tmp/aiju/web/requirements.txt

rsync -a --delete /tmp/aiju/web/app/ "$APP/app/"
cp /tmp/aiju/web/requirements.txt "$APP/"
mkdir -p "$APP/data"
# 仅模板 JSON；切勿用 --delete 清掉已有 studio.db / projects/
cp -n /tmp/aiju/web/data/*.json "$APP/data/" 2>/dev/null || true
```

systemd：

```bash
# 预览机路径示例
sudo cp /tmp/aiju/deploy/bingjing-web.preview.service /etc/systemd/system/bingjing-web.service
# 或 GPU 本机一体化：用 deploy/bingjing-web.service（WorkingDirectory=/home/linux/bingjing/web）
sudo nano /etc/systemd/system/bingjing-web.service   # 改 WEB_*、COMFYUI_URL
# 也可参考 deploy/env.example
sudo systemctl daemon-reload
sudo systemctl enable --now bingjing-web.service
```

浏览器打开制作台 → **全局制作** 填写 OhMyGPT / Comfy Token（写入本地 DB，不进 git）。

### 迁移业务数据（换 Web 机时）

在旧机打包（**含密钥配置**，妥善保管）：

```bash
tar czf bingjing-data.tgz -C /opt/ai-forge/bingjing/web data/studio.db data/projects
```

新机解压到同路径后 `systemctl restart bingjing-web`。

## 3. 部署 ComfyUI（GPU 机）

1. 按官方方式安装 ComfyUI + 对应 CUDA / venv（本仓库不打包权重）。
2. 安装 MiniMax H3、Drama10 等所需 custom_nodes。
3. 覆盖 Drama10 节点：

```bash
COMFY=/home/linux/ComfyUI
cp /tmp/aiju/comfyui/workflows/_server_ref_drama10_nodes.py \
  "$COMFY/custom_nodes/ComfyUI_Drama10/drama10_nodes.py"
```

4. systemd 用户或系统单元示例：`deploy/bingjing-comfy.service`

```bash
# 用户级示例
mkdir -p ~/.config/systemd/user
cp /tmp/aiju/deploy/bingjing-comfy.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bingjing-comfy.service
```

5. 确认 `http://<GPU>:8000` 可访问；Web 机将 `COMFYUI_URL` 指到该地址或 SSH 隧道（如 `127.0.0.1:18000`）。

工作流 JSON 参考：`comfyui/workflows/_server_ref/`（可按需导入 Comfy，非必须覆盖）。

## 4. 环境变量要点

见 `deploy/env.example` 与 `deploy/bingjing-web*.service`：

| 变量 | 含义 |
|------|------|
| `COMFYUI_URL` | 制作台访问 Comfy 的地址 |
| `COMFYUI_ROOT` | Comfy 根目录（读 output / 日志） |
| `STORYBOARD_BACKEND` | `image_api` / Comfy 静帧等 |
| `STORYBOARD_IMAGE_*` | 第三方出图网关 |
| `STORYBOARD_STALE_SEC` | 出图无心跳自动中止（默认约 420s） |
| `DRAMA10_WEB_AUDIO_ONLY=0` | 成片优先 H3 原生音轨 |

## 5. 本地 Windows 同步代码

`deploy/sync_to_server.ps1`（勿同步 `data/studio.db`、`projects/`）。

日常推荐：服务器 `git pull` + `rsync app/` + `systemctl restart bingjing-web`。

## 6. 安全清单

- 仓库已忽略：`web/data/studio.db`、`web/data/projects/`、`.env`、`web/_*.py` 临时运维脚本
- **不要**把含 SSH 密码的 `VPN/_*.py` 或真实 `Environment=...KEY=` 提交进 git
- 换机后立刻改 `WEB_PASSWORD` 与面板内 API Key

---

当前预览对照（会随环境变化，以实际机器为准）：

| 项 | 值 |
|----|-----|
| 制作台 | https://dj.preview.3swl.com/ |
| 预览机代码 | `/opt/ai-forge/bingjing/web`（`bingjing-web` → `:8097`） |
| GPU Comfy | 按 `COMFYUI_URL` / 隧道配置 |
