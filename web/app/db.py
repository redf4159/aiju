# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

try:
    from zoneinfo import ZoneInfo

    _BJ_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    _BJ_TZ = timezone(timedelta(hours=8))

WEB_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("BINGJING_DATA", str(WEB_ROOT / "data")))
DB_PATH = DATA_ROOT / "studio.db"

DATA_ROOT.mkdir(parents=True, exist_ok=True)


def _utcnow() -> str:
    """业务时间戳：北京时间（Asia/Shanghai，UTC+8）。函数名保留兼容旧调用。"""
    return datetime.now(_BJ_TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


@contextmanager
def connect() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate_schema_only() -> None:
    """仅建表/加列，不写入或修改业务数据（部署脚本专用）。"""
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                episode_count INTEGER DEFAULT 20,
                width INTEGER DEFAULT 640,
                height INTEGER DEFAULT 360,
                style_prefix TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS characters (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                episode_id TEXT,
                name TEXT NOT NULL,
                role_note TEXT DEFAULT '',
                visual_prompt TEXT DEFAULT '',
                ref_image TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS scenes (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                episode_id TEXT,
                code TEXT DEFAULT '',
                name TEXT NOT NULL,
                visual_prompt TEXT DEFAULT '',
                ref_image TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                ep_num INTEGER NOT NULL,
                title TEXT DEFAULT '',
                outline TEXT DEFAULT '',
                script_md TEXT DEFAULT '',
                storyboard_md TEXT DEFAULT '',
                drama10_prompt TEXT DEFAULT '',
                video_path TEXT DEFAULT '',
                pipeline_json TEXT DEFAULT '{}',
                UNIQUE(project_id, ep_num)
            );
            CREATE TABLE IF NOT EXISTS shots (
                id TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL,
                shot_num INTEGER NOT NULL,
                duration_sec REAL DEFAULT 5,
                visual TEXT DEFAULT '',
                motion TEXT DEFAULT '',
                voices_json TEXT DEFAULT '[]',
                image_path TEXT DEFAULT '',
                video_path TEXT DEFAULT '',
                FOREIGN KEY (episode_id) REFERENCES episodes(id),
                UNIQUE(episode_id, shot_num)
            );
            CREATE TABLE IF NOT EXISTS project_docs (
                project_id TEXT PRIMARY KEY,
                outline_md TEXT DEFAULT '',
                series_scripts_note TEXT DEFAULT '',
                pipeline_json TEXT DEFAULT '{}',
                novel_md TEXT DEFAULT '',
                script_meta_json TEXT DEFAULT '{}',
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS props (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                episode_id TEXT,
                name TEXT NOT NULL,
                role_note TEXT DEFAULT '',
                visual_prompt TEXT DEFAULT '',
                ref_image TEXT DEFAULT '',
                ref_images_json TEXT DEFAULT '[]',
                episodes_json TEXT DEFAULT '[]',
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                episode_id TEXT,
                stage_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                progress REAL DEFAULT 0,
                message TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
            """
        )
        _migrate_schema(conn)


def init_db() -> None:
    migrate_schema_only()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(project_docs)").fetchall()}
    if "generation_prefs_json" not in cols:
        conn.execute(
            "ALTER TABLE project_docs ADD COLUMN generation_prefs_json TEXT DEFAULT '{}'"
        )
    if "novel_md" not in cols:
        conn.execute("ALTER TABLE project_docs ADD COLUMN novel_md TEXT DEFAULT ''")
    if "script_meta_json" not in cols:
        conn.execute(
            "ALTER TABLE project_docs ADD COLUMN script_meta_json TEXT DEFAULT '{}'"
        )
    jcols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "params_json" not in jcols:
        conn.execute("ALTER TABLE jobs ADD COLUMN params_json TEXT DEFAULT '{}'")
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(characters)").fetchall()}
    if "voice_desc" not in ccols:
        conn.execute("ALTER TABLE characters ADD COLUMN voice_desc TEXT DEFAULT ''")
    if "voice_samples_json" not in ccols:
        conn.execute(
            "ALTER TABLE characters ADD COLUMN voice_samples_json TEXT DEFAULT '[]'"
        )
    if "ref_images_json" not in ccols:
        conn.execute("ALTER TABLE characters ADD COLUMN ref_images_json TEXT DEFAULT '[]'")
    if "tts_voice" not in ccols:
        conn.execute("ALTER TABLE characters ADD COLUMN tts_voice TEXT DEFAULT ''")
    if "episodes_json" not in ccols:
        conn.execute("ALTER TABLE characters ADD COLUMN episodes_json TEXT DEFAULT '[]'")
    scols_sc = {r[1] for r in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    if "episodes_json" not in scols_sc:
        conn.execute("ALTER TABLE scenes ADD COLUMN episodes_json TEXT DEFAULT '[]'")
    if "ref_images_json" not in scols_sc:
        conn.execute("ALTER TABLE scenes ADD COLUMN ref_images_json TEXT DEFAULT '[]'")
    if "role_note" not in scols_sc:
        conn.execute("ALTER TABLE scenes ADD COLUMN role_note TEXT DEFAULT ''")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS props (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            episode_id TEXT,
            name TEXT NOT NULL,
            role_note TEXT DEFAULT '',
            visual_prompt TEXT DEFAULT '',
            ref_image TEXT DEFAULT '',
            ref_images_json TEXT DEFAULT '[]',
            episodes_json TEXT DEFAULT '[]',
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS studio_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT
        )
        """
    )
    scols = {r[1] for r in conn.execute("PRAGMA table_info(shots)").fetchall()}
    for col, ddl in (
        ("title", "TEXT DEFAULT ''"),
        ("scene", "TEXT DEFAULT ''"),
        ("performance", "TEXT DEFAULT ''"),
        ("continuity", "TEXT DEFAULT ''"),
        ("ambience", "TEXT DEFAULT ''"),
        ("ref_images_json", "TEXT DEFAULT '[]'"),
    ):
        if col not in scols:
            conn.execute(f"ALTER TABLE shots ADD COLUMN {col} {ddl}")


def project_dir(project_id: str) -> Path:
    p = DATA_ROOT / "projects" / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)
