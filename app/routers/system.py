import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException
from ..config import settings
from ..db import db, now_iso
from ..services import backup as backup_service

router = APIRouter(prefix="/api/system", tags=["system"])

@router.get("/status")
def status():
    settings.ensure_dirs()
    with db() as conn:
        tasks = conn.execute("SELECT SUM(status IN ('uploaded','pending','pending_recognition','recognizing','pending_analysis','analyzing')) pending,SUM(status='failed') failed FROM ai_tasks").fetchone()
        due = conn.execute(
            """
            SELECT COUNT(*)
            FROM questions q
            LEFT JOIN review_cards rc ON rc.question_id=q.id
            WHERE q.deleted_at IS NULL AND COALESCE(rc.due_at, q.next_review_at)<=?
            """,
            (now_iso(),),
        ).fetchone()[0]
    disk = shutil.disk_usage(settings.data_dir)
    disk_ok = disk.free > 500 * 1024 * 1024
    tailscale = tailscale_state()
    last_backup = backup_service.last_backup_time()
    return {
        "backend": True,
        "database": True,
        "database_path": str(settings.db_path),
        "api_key_configured": bool(settings.ai_api_key),
        "ai_model": settings.ai_model,
        "ai_base_url": settings.ai_base_url,
        "disk_free_bytes": disk.free,
        "disk_ok": disk_ok,
        "disk_message": "" if disk_ok else f"磁盘剩余空间不足 500MB（当前 {disk.free / 1024 / 1024:.0f}MB），请立即清理，否则可能无法保存新图片",
        "pending_tasks": tasks[0] or 0,
        "failed_tasks": tasks[1] or 0,
        "today_reviews": due,
        "tailscale_running": tailscale["running"],
        "tailscale_https": tailscale["https"],
        "tailscale_message": tailscale["message"],
        "last_backup_at": last_backup.isoformat() if last_backup else None,
        "auto_backup_hours": settings.auto_backup_hours,
    }


def tailscale_state() -> dict:
    """读取 Tailscale 状态；受限进程无法访问命名管道时降级检查服务。"""
    exe = shutil.which("tailscale")
    if not exe:
        return {"running": False, "https": False, "message": "未检测到 tailscale 命令，手机可能无法通过 HTTPS 访问"}
    try:
        result = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"running": False, "https": False, "message": f"Tailscale 状态检查失败：{type(exc).__name__}"}
    if result.returncode != 0:
        # Tailscale 的本地状态接口受 Windows ACL 保护。Web 服务通常以普通
        # 用户运行，会被拒绝访问；此时 Windows 服务运行且已配置 HTTPS 域名
        # 表明 Serve 可以继续为本机服务提供入口。
        try:
            service = subprocess.run(
                ["sc", "query", "Tailscale"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            service = None
        configured = bool(settings.tailscale_hostname and settings.tailscale_serve)
        if service and service.returncode == 0 and "RUNNING" in service.stdout and configured:
            hostname = settings.tailscale_hostname
            return {
                "running": True,
                "https": True,
                "message": f"HTTPS 已配置：https://{hostname}",
            }
        return {"running": False, "https": False, "message": "Tailscale 未登录或未连接，请先在电脑上打开 Tailscale"}
    import json as _json
    try:
        data = _json.loads(result.stdout or "{}")
    except ValueError:
        data = {}
    online = data.get("BackendState") == "Running" and bool(data.get("Self", {}).get("Online", True))
    return {
        "running": online,
        "https": online,
        "message": "" if online else "Tailscale 未连接，手机暂时无法访问",
    }

@router.get("/config")
def config():
    return {"ai_base_url": settings.ai_base_url, "ai_model": settings.ai_model, "ai_key_configured": bool(settings.ai_api_key), "tailscale_hostname": settings.tailscale_hostname}


@router.post("/ai-selftest")
async def ai_selftest(cases: int = 3):
    """渲染已知答案的算式图片并让模型识别，用于量化当前模型的读图正确率。"""
    if not settings.ai_api_key:
        raise HTTPException(400, "AI_API_KEY未配置，无法执行识别自检")
    from ..services.ai.selftest import run_selftest

    return await run_selftest(max(1, min(cases, 5)))

@router.post("/backup")
def backup():
    target = backup_service.create_backup("manual")
    return {"path": str(target), "created_at": now_iso()}


@router.get("/backups")
def backups():
    return {"items": backup_service.list_backups(), "auto_backup_hours": settings.auto_backup_hours}


@router.post("/export-bundle")
def export_bundle():
    return backup_service.export_bundle()

@router.get("/integrity-check")
@router.post("/integrity-check")
def integrity():
    issues=[]
    with db() as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok": issues.append("数据库完整性检查失败")
        for row in conn.execute("SELECT id,original_path FROM question_images"):
            if not Path(row[1]).exists(): issues.append(f"图片丢失: {row[0]}")
        unfinished = conn.execute("SELECT COUNT(*) FROM ai_tasks WHERE status IN ('uploaded','pending_recognition','recognizing','analyzing')").fetchone()[0]
    return {"ok": not issues, "issues": issues, "unfinished_tasks": unfinished}

@router.get("/export")
def export():
    import json
    settings.export_dir.mkdir(parents=True, exist_ok=True); path=settings.export_dir / f"questions-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    with db() as conn:
        rows=[dict(x) for x in conn.execute("SELECT * FROM questions WHERE deleted_at IS NULL")]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "count": len(rows)}
