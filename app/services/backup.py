"""数据库备份与整包导出：只读原库，不会移动或删除任何用户数据。"""
import json
import logging
import os
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..db import db, json_load

log = logging.getLogger(__name__)


def stamp() -> str:
    # Include microseconds so repeated backups in one second never overwrite
    # one another or make the listing point at an older partial file.
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def create_backup(tag: str = "manual") -> Path:
    settings.ensure_dirs()
    target = settings.backup_dir / f"wrongbook-{tag}-{stamp()}.db"
    temporary = target.with_suffix(target.suffix + ".tmp")
    with db() as conn:
        dest = sqlite3.connect(temporary)
        try:
            conn.backup(dest)
        finally:
            dest.close()
    os.replace(temporary, target)
    return target


def list_backups() -> list[dict]:
    settings.ensure_dirs()
    items = []
    candidates = [path for path in settings.backup_dir.glob("wrongbook-*.db") if path.stat().st_size > 0]
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        stat = path.stat()
        items.append({
            "name": path.name,
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return items


def last_backup_time() -> datetime | None:
    backups = list_backups()
    if not backups:
        return None
    return datetime.fromisoformat(backups[0]["created_at"])


def maybe_auto_backup() -> Path | None:
    """按 AI 自动备份间隔创建备份；未到间隔则什么都不做。"""
    hours = settings.auto_backup_hours
    if hours <= 0:
        return None
    last = last_backup_time()
    now = datetime.now(timezone.utc)
    if last and (now - last).total_seconds() < hours * 3600:
        return None
    target = create_backup("auto")
    _prune_auto_backups()
    log.info("auto backup created file=%s", target.name)
    return target


def _prune_auto_backups() -> None:
    keep = max(1, settings.auto_backup_keep)
    files = sorted(settings.backup_dir.glob("wrongbook-auto-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        try:
            path.unlink()
        except OSError:
            log.warning("could not remove old auto backup file=%s", path.name)


def export_bundle() -> dict:
    """导出 zip：题目 JSON + 关联的原始图片，文件名保持可追溯。"""
    settings.ensure_dirs()
    target = settings.export_dir / f"wrongbook-export-{stamp()}.zip"
    with db() as conn:
        questions = [dict(x) for x in conn.execute("SELECT * FROM questions WHERE deleted_at IS NULL ORDER BY id")]
        images = [dict(x) for x in conn.execute("SELECT id,question_id,task_id,image_type,original_path,mime_type,file_size,edit_json FROM question_images")]
        raw_tasks = [dict(x) for x in conn.execute("SELECT id,status,error,created_at,updated_at,attempts FROM ai_tasks")]
    payload_questions = []
    for row in questions:
        row = dict(row)
        for key in ("code_blocks", "error_types", "knowledge_points", "uncertain_parts"):
            row[key] = json_load(row.get(key))
        payload_questions.append(row)
    attached = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("questions.json", json.dumps(payload_questions, ensure_ascii=False, indent=2))
        archive.writestr("tasks.json", json.dumps(raw_tasks, ensure_ascii=False, indent=2))
        archive.writestr("images.json", json.dumps(images, ensure_ascii=False, indent=2))
        for row in images:
            path = Path(row["original_path"] or "")
            if not path.exists():
                continue
            quiz = row["question_id"] or f"task{row['task_id']}"
            archive.write(path, f"images/{quiz}_{row['id']}_{path.name}")
            attached += 1
    return {
        "path": str(target),
        "questions": len(payload_questions),
        "images": attached,
        "size": target.stat().st_size,
    }
