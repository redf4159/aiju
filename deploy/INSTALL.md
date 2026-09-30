# 《冰境》制作台 · 新服务器安装指南

本仓库为 **Web 制作台 + ComfyUI Drama10 节点参考 + 分集 JSON 模板**。不含 ComfyUI 本体、MiniMax 权重与 OhMyGPT 密钥。

## 1. 目录约定（与线上一致）

| 路径 | 说明 |
|------|------|
| `/home/linux/bingjing/web` | 本仓库 `web/` 内容（`app`、`data/ep*.json`、`requirements.txt`） |
| `/home/linux/ComfyUI` | ComfyUI 安装目录 |
| `/home/linux/ComfyUI/custom_nodes/ComfyUI_Drama10/drama10_nodes.py` | 由 `comfyui/workflows/_server_ref_drama10_nodes.py` 覆盖部署 |
| `/home/linux/bingjing/web/data` | SQLite `studio.db`、项目 `projects/`（**不随 git 覆盖**） |

## 2. 依赖

- Ubuntu 22.04+，Python 3.10+（推荐与线上一致：`/home/linux/comfyui-venv`）
- ffmpeg、ffprobe（混音必需）
- ComfyUI + MiniMax H3 节点（图生视频）
- 可选：edge-tts 或 OhMyGPT（TTS / 分镜 API）

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg git
python3 -m venv /home/linux/comfyui-venv
source /home/linux/comfyui-venv/bin/activate
pip install -r /home/linux/bingjing/web/requirements.txt
```

## 3. 部署 Web

```bash
git clone <你的仓库地址> /tmp/bingjing-src
rsync -a --delete /tmp/bingjing-src/web/app/ /home/linux/bingjing/web/app/
cp /tmp/bingjing-src/web/requirements.txt /home/linux/bingjing/web/
mkdir -p /home/linux/bingjing/web/data
# 首次：复制模板剧本（若 data 为空）
cp -n /tmp/bingjing-src/web/data/ep*.json /home/linux/bingjing/web/data/ 2>/dev/null || true
```

安装 systemd 用户服务（按需改 `WEB_USER` / `WEB_PASSWORD` / 端口）：

```bash
mkdir -p ~/.config/systemd/user
cp /tmp/bingjing-src/deploy/bingjing-web.service ~/.config/systemd/user/
# 编辑 Environment：OhMyGPT Key 建议在制作台 UI 保存，勿写进 git
systemctl --user daemon-reload
systemctl --user enable --now bingjing-web.service
```

首次启动后访问制作台 → **全局制作 → OhMyGPT 密钥** 保存 API Key。

## 4. 部署 Drama10 节点

```bash
cp /tmp/bingjing-src/comfyui/workflows/_server_ref_drama10_nodes.py \
  /home/linux/ComfyUI/custom_nodes/ComfyUI_Drama10/drama10_nodes.py
systemctl --user restart bingjing-comfy.service   # 若有
```

## 5. 环境变量要点（见 `deploy/bingjing-web.service`）

- `BINGJING_DATA=/home/linux/bingjing/web/data`
- `COMFYUI_ROOT=/home/linux/ComfyUI`
- `STORYBOARD_IMAGE_SIZE=1536x864`（OhMyGPT 出图最小像素）
- `DRAMA10_WEB_AUDIO_ONLY=1`（Comfy 单镜无声，Web 独占混音）
- `DRAMA10_AMB_MIX_GAIN` / `DRAMA10_BGM_MIX_GAIN`（环境音量）

## 6. 同步更新（开发机）

Windows：`deploy/sync_to_server.ps1`（勿把 `data/studio.db` 同步上去）。

---

**安全**：仓库内不含 SSH 密码、OhMyGPT Key；上级目录 `duanju/_*.py` 运维脚本请勿提交 git。
