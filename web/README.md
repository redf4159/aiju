# 冰镜 · Web 制作台（仅线上运行）

制作台部署在服务器 **`/home/linux/bingjing/web`**，不在本地启动服务。

## 功能（v0.2）

- **多短剧项目管理**：新建 / 切换项目
- **项目级**：总纲、人物库、场景库、分集剧本说明、全流程状态
- **集级**：本集剧本、分镜脚本、镜头表、分镜图上传、成片路径、Drama10 导出
- **任务队列**：`storyboard_images` / `video` 经 ComfyUI API 提交 **Drama10** 节点（Bearer token）；全站 GPU 任务串行
- 数据：`/home/linux/bingjing/web/data/studio.db` + `data/projects/{id}/`

## 访问地址

- 公网：**http://175.155.64.171:61951**（对内 `8001`，与 `漫剧.txt` 第二路一致）
- 登录：**sansuo** / **sansuo2026**（HTTP Basic，与 ComfyUI 一致；可改环境变量 `WEB_USER` / `WEB_PASSWORD`）
- 服务器本机：`http://127.0.0.1:8001`

## 运维命令（SSH 登录后）

```bash
systemctl --user status bingjing-web.service
systemctl --user restart bingjing-web.service
tail -f /home/linux/bingjing/web.log
```

手动启动：`/home/linux/bingjing/start_web.sh`

## 环境变量（服务器）

| 变量 | 默认 | 说明 |
|------|------|------|
| `COMFYUI_URL` | `http://127.0.0.1:8000` | 同机 ComfyUI |
| `COMFYUI_ROOT` | `/home/linux/ComfyUI` | 读取 output / input |
| `COMFYUI_TOKEN` | 空 | 可选；默认读 `COMFYUI_ROOT/login/PASSWORD` 首行 |
| `PUBLIC_COMFYUI_URL` | 见 systemd | 页面展示用外网 ComfyUI |
| `WEB_PUBLIC_URL` | 空 | 页面展示制作台公网 URL |
| `WEB_USER` | `sansuo` | 制作台 HTTP 基本认证用户名 |
| `WEB_PASSWORD` | `sansuo2026` | 制作台密码 |

## 源码同步

在开发机执行项目内部署脚本（或 SFTP 同步 `web/` 到服务器）后，在服务器上 `systemctl --user restart bingjing-web.service`。
