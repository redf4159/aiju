# ComfyUI 工作流 · 冰镜

## 服务器已有、可借鉴的文件

已同步到 `workflows/_server_ref/`（只读副本）：

| 文件 | 用途 |
|------|------|
| `workflows_upload_【图生视频】video_minimax_h3_i2v.json` | **单镜** 图生视频（MiniMax H3） |
| `workflows_upload_【文生视频】video_minimax_h3_t2v.json` | 文生视频 |
| `workflows_upload_图生视频_MiniMax_H3_N卡.json` | N 段图生视频编排 |
| `workflows_upload_图生视频_MiniMax_H3_蓝图.json` | 与官方蓝图同型 |
| `blueprints_Image to Video (MiniMax H3).json` | Comfy 内置蓝图格式 |

## 《冰镜》推荐用法

1. **整集成片（有声、无口型）**  
   使用自定义节点 **`短剧10镜 Image to Video (MiniMax H3)`**（`ComfyUI_Drama10`），粘贴 Web 制作台导出的 Prompt。  
   节点能力：自动分镜静帧 → H3 逐镜 I2V → **edge-tts** 对白/旁白 → BGM → 单 MP4。

2. **单镜精修**  
   用 `【图生视频】video_minimax_h3_i2v.json`：从 `assets/storyboards/EPxx/` 上传首帧。

3. **参数**  
   - 竖屏默认 **352×608**（与 Drama10 一致，可改）  
   - `shot_duration`：2–15s（Web 导出会算 grid 与中位时长）  
   - 先部署 `server_patch/` 再跑《冰镜》角色对白

## 新建工作流（手动在 ComfyUI 内另存为）

在 UI 中搭一条 **「冰镜·整集」** 模板：

```
[Load Checkpoint / MiniMax H3 模型组]
        ↓
[Drama10ShotMiniMaxH3]  ← prompt 接 Web 导出 TXT
        ↓
[Save Video]
```

另存为 `workflows/bingjing_episode_drama10.json` 后，可再接 API 队列（Web 端 `COMFYUI_SUBMIT` 后续启用）。

## 参考源码

`workflows/_server_ref_drama10_nodes.py`：Drama10 脚本格式（`01｜`、`视频提示词：`、`旁白：`、`对白：`）。
