# Drama10 · 《冰镜》语音补丁

服务器原 `drama10_nodes.py` 的 TTS 角色偏 demo（如「凌川」）。本补丁为 **卡索 / 樱空释 / 蝶澈 / 皇北城** 等增加 `edge-tts` 声线，并解析 `对白：角色「台词」`。

## 安装（在服务器 ComfyUI 机器上，需维护窗口）

1. 备份  
   `cp custom_nodes/ComfyUI_Drama10/drama10_nodes.py drama10_nodes.py.bak`

2. 在 `drama10_nodes.py` 的 `VOICE_MAP` 中 **追加**（勿删原有键）：

```python
"卡索": ("zh-CN-YunxiNeural", "-10%"),
"樱空释": ("zh-CN-YunjianNeural", "-12%"),
"蝶澈": ("zh-CN-XiaoyiNeural", "-8%"),
"皇北城": ("zh-CN-YunyangNeural", "-15%"),
"大臣": ("zh-CN-YunfengNeural", "-5%"),
"潮雾使": ("zh-CN-YunjianNeural", "-20%"),
```

3. 在 `_parse_voice_lines` 末尾 `return lines` 之前追加：

```python
    for m in re.finditer(
        r"(?:^|\n)\s*对白\s*[：:]\s*(.*?)(?=\n\s*(?:旁白|声音|神秘声音|视频提示词|画面)\s*[：:]|\Z)",
        block,
        re.S,
    ):
        chunk = m.group(1)
        for qm in re.finditer(
            r"([\u4e00-\u9fffA-Za-z·]{2,10})[「\"“](.+?)[」\"”]",
            chunk,
            re.S,
        ):
            add(qm.group(1), qm.group(2))
```

4. 重启 ComfyUI（由运维在合适时间执行）。

未打补丁时：对白可能被当成默认声线或无法解析，建议先只跑 **EP01** 试听。
