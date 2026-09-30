# -*- coding: utf-8 -*-
"""制作流水线阶段定义（项目级 + 集级）."""

from __future__ import annotations

from typing import Any, Dict, List

PROJECT_STAGES: List[Dict[str, str]] = [
    {"id": "outline", "label": "总纲 / 世界观", "scope": "project"},
    {"id": "characters", "label": "人物设定", "scope": "project"},
    {"id": "scenes", "label": "场景设定", "scope": "project"},
    {"id": "series_scripts", "label": "分集剧本（全剧）", "scope": "project"},
]

EPISODE_STAGES: List[Dict[str, str]] = [
    {"id": "episode_script", "label": "本集剧本", "scope": "episode"},
    {"id": "episode_characters", "label": "本集人物", "scope": "episode"},
    {"id": "episode_scenes", "label": "本集场景", "scope": "episode"},
    {"id": "storyboard_script", "label": "分镜脚本", "scope": "episode"},
    {"id": "storyboard_images", "label": "分镜图片", "scope": "episode"},
    {"id": "video", "label": "本集成片", "scope": "episode"},
]

ALL_STAGES = PROJECT_STAGES + EPISODE_STAGES

STATUS_ORDER = ("pending", "running", "done", "failed", "skipped")


def empty_pipeline() -> Dict[str, str]:
    return {s["id"]: "pending" for s in ALL_STAGES}
