# -*- coding: utf-8 -*-
"""部署/重启后恢复磁盘上的分镜图与 Drama10 脚本（避免 DB 与 projects 目录脱节）。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db

# 冰镜种子项目固定 ID：与服务器上已有 assets 目录对齐，避免每次 seed 换新 UUID
CANONICAL_BINGJING_PROJECT_ID = "78be2a4b-282c-4e4b-8a3e-18f4d076149b"
# 历史种子 ID（兼容旧路径恢复）
LEGACY_BINGJING_PROJECT_IDS = frozenset(
    {
        CANONICAL_BINGJING_PROJECT_ID,
        "d7e69d56-8592-42a9-8fb3-febe68b8f64b",
    }
)


def is_bingjing_project_id(project_id: str) -> bool:
    return (project_id or "").strip() in LEGACY_BINGJING_PROJECT_IDS


def _projects_root() -> Path:
    return db.DATA_ROOT / "projects"


def _episode_dir(project_id: str, ep_num: int) -> Path:
    return db.project_dir(project_id) / "episodes" / f"ep{ep_num:02d}"


def _score_episode_assets(ep_dir: Path, ep_num: int) -> int:
    if not ep_dir.is_dir():
        return 0
    shots_dir = ep_dir / "shots"
    n_png = len(list(shots_dir.glob("shot_*.png"))) if shots_dir.is_dir() else 0
    score = n_png * 10
    if (ep_dir / "storyboard_collage.png").is_file():
        score += 5
    export = ep_dir.parent.parent / "export" / f"EP{ep_num:02d}_drama10.txt"
    if export.is_file():
        score += 3
    return score


def _best_project_root_for_episode(ep_num: int, prefer_id: str) -> Optional[Path]:
    root = _projects_root()
    if not root.is_dir():
        return None
    best: Optional[Path] = None
    best_score = 0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        ep_dir = d / "episodes" / f"ep{ep_num:02d}"
        score = _score_episode_assets(ep_dir, ep_num)
        if d.name == prefer_id and score > 0:
            score += 1000
        if score > best_score:
            best_score = score
            best = d
    return best if best_score > 0 else None


def _copy_episode_assets(src_ep: Path, dst_ep: Path, ep_num: int) -> None:
    dst_ep.mkdir(parents=True, exist_ok=True)
    src_collage = src_ep / "storyboard_collage.png"
    if src_collage.is_file():
        shutil.copy2(src_collage, dst_ep / "storyboard_collage.png")
    src_shots = src_ep / "shots"
    if src_shots.is_dir():
        dst_shots = dst_ep / "shots"
        dst_shots.mkdir(parents=True, exist_ok=True)
        for p in src_shots.glob("shot_*.png"):
            dest = dst_shots / p.name
            if not dest.is_file() or dest.stat().st_size < p.stat().st_size:
                shutil.copy2(p, dest)
    src_export = src_ep.parent.parent / "export" / f"EP{ep_num:02d}_drama10.txt"
    if src_export.is_file():
        dst_export_dir = dst_ep.parent.parent / "export"
        dst_export_dir.mkdir(parents=True, exist_ok=True)
        dst_file = dst_export_dir / src_export.name
        if not dst_file.is_file() or dst_file.stat().st_size < src_export.stat().st_size:
            shutil.copy2(src_export, dst_file)


def save_drama10_export_file(project_id: str, ep_num: int, prompt: str) -> Path:
    out_dir = db.project_dir(project_id) / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"EP{ep_num:02d}_drama10.txt"
    text = (prompt or "").strip()
    if text:
        path.write_text(text, encoding="utf-8")
    return path


def read_drama10_export_file(project_id: str, ep_num: int) -> str:
    path = db.project_dir(project_id) / "export" / f"EP{ep_num:02d}_drama10.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    # 仅冰镜种子项目允许从其它目录找回；其它项目禁止串读，避免台词/风格污染
    if is_bingjing_project_id(project_id):
        alt = _best_project_root_for_episode(ep_num, project_id)
        if alt:
            path = alt / "export" / f"EP{ep_num:02d}_drama10.txt"
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
    return ""


def _rehydrate_drama10_from_disk(project_id: str, episode_id: str, ep_num: int, current: str) -> None:
    if (current or "").strip():
        return
    text = read_drama10_export_file(project_id, ep_num)
    if len(text) > 20:
        with db.connect() as conn:
            conn.execute(
                "UPDATE episodes SET drama10_prompt=? WHERE id=?",
                (text, episode_id),
            )


def ensure_episode_assets(project_id: str, episode_id: str, ep_num: int, n_shots: int) -> None:
    if n_shots <= 0:
        _rehydrate_drama10_from_disk(project_id, episode_id, ep_num, "")
        return
    target_root = db.project_dir(project_id)
    target_ep = _episode_dir(project_id, ep_num)
    target_score = _score_episode_assets(target_ep, ep_num)
    need = n_shots - (
        len(list((target_ep / "shots").glob("shot_*.png")))
        if (target_ep / "shots").is_dir()
        else 0
    )
    if need <= 0 and target_score >= n_shots * 10:
        _rehydrate_drama10_from_disk(
            project_id,
            episode_id,
            ep_num,
            "",
        )
        return
    # 跨项目拷贝仅用于冰镜种子恢复；新项目（如显卡压测）不得吞并其它剧资产
    if is_bingjing_project_id(project_id):
        source_root = _best_project_root_for_episode(ep_num, project_id)
        if source_root and source_root.resolve() != target_root.resolve():
            src_ep = source_root / "episodes" / f"ep{ep_num:02d}"
            src_score = _score_episode_assets(src_ep, ep_num)
            if src_score > target_score:
                _copy_episode_assets(src_ep, target_ep, ep_num)
    _rehydrate_drama10_from_disk(project_id, episode_id, ep_num, "")


def _shot_count(episode_id: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM shots WHERE episode_id=?", (episode_id,)
        ).fetchone()
    return int(row["c"]) if row else 0


def recover_production_data_on_startup() -> None:
    from . import services

    projects: List[Dict[str, Any]] = services.list_projects()
    if not projects:
        return
    for proj in projects:
        pid = proj["id"]
        for ep in services.list_episodes(pid):
            eid = ep["id"]
            ep_num = int(ep["ep_num"])
            n = _shot_count(eid)
            ensure_episode_assets(pid, eid, ep_num, n)
            if n > 0:
                services.get_episode(eid)
            else:
                _rehydrate_drama10_from_disk(
                    pid, eid, ep_num, ep.get("drama10_prompt") or ""
                )
