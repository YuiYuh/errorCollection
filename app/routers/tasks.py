import mimetypes
import hashlib
import secrets
import json
import re
from typing import Literal
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator, ValidationError
from ..services.images import edit_image

from ..config import settings
from ..db import db, json_load, now_iso
from ..services.worker import process_task
from ..syllabus import normalize_knowledge
from ..services.duplicates import candidates

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
RATINGS = {"correct", "partial", "wrong"}


class FeedbackIn(BaseModel):
    recognition_rating: str
    correction_rating: str
    note: str = ""


class RetryIn(BaseModel):
    question_label: str | None = None
    recognition_hint: str | None = Field(default=None, max_length=4000)


class BatchTaskIn(BaseModel):
    """任务中心批量操作的输入，限制批量大小避免误操作和超长请求。"""
    task_ids: list[int] = Field(min_length=1, max_length=50)


class DraftIn(BaseModel):
    result: dict
    task_version: str


def _question_labels(question_label: str, question_labels: str, multi_question: bool) -> list[str]:
    """将多题选择框中的题号拆成独立任务标签。

    支持“1,2,3”“第1题、第2题”和“1-5”这几种手机端容易输入的格式。
    标签数量限制在 20 个，避免一次误操作压垮识别队列。
    """
    single = question_label.strip()[:60]
    if not multi_question:
        return [single]
    raw = question_labels.strip()
    if not raw:
        raise HTTPException(422, "多题纠错至少选择一个题号")
    labels: list[str] = []
    for token in re.split(r"[,，、;；\n]+", raw):
        token = token.strip()
        if not token:
            continue
        match = re.fullmatch(r"(?:第\s*)?(\d+)\s*[-~至到]\s*(?:第\s*)?(\d+)(?:题)?", token)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if end < start or end - start > 19:
                raise HTTPException(422, "题号范围最多只能包含 20 道题")
            labels.extend(f"第{number}题" for number in range(start, end + 1))
        else:
            number = re.fullmatch(r"第?\s*(\d+)\s*题?", token)
            labels.append(f"第{number.group(1)}题" if number else token[:60])
    labels = list(dict.fromkeys(labels))
    if not labels:
        raise HTTPException(422, "多题纠错至少选择一个题号")
    if len(labels) > 20:
        raise HTTPException(422, "一次最多选择 20 道题")
    return labels


def _same_question_label(left: str | None, right: str | None) -> bool:
    """Treat ``2`` and ``第2题`` as the same label for multi-task retries."""
    a, b = (str(left or "").replace(" ", "") for left in (left, right))
    a_num = re.fullmatch(r"第?(\d+)题?", a)
    b_num = re.fullmatch(r"第?(\d+)题?", b)
    if a_num and b_num:
        return a_num.group(1) == b_num.group(1)
    return a == b


@router.put('/{task_id}/draft')
def save_draft(task_id: int, payload: DraftIn):
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        task = conn.execute('SELECT status,updated_at FROM ai_tasks WHERE id=?', (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, '任务不存在')
        if task['status'] != 'waiting_confirmation' or task['updated_at'] != payload.task_version:
            raise HTTPException(409, '任务已更新，请重新打开结果再编辑')
        serialized = json.dumps(payload.result, ensure_ascii=False)
        if len(serialized) > 500_000:
            raise HTTPException(413, '草稿内容过长')
        conn.execute('INSERT OR REPLACE INTO task_drafts(task_id,result_json,task_version,updated_at) VALUES(?,?,?,?)',
                     (task_id, serialized, payload.task_version, now_iso()))
    return {'ok': True}


class ImageEdit(BaseModel):
    rotation: Literal[0, 90, 180, 270] = 0
    x: float = Field(0, ge=0, lt=1)
    y: float = Field(0, ge=0, lt=1)
    width: float = Field(1, gt=0, le=1)
    height: float = Field(1, gt=0, le=1)

    @model_validator(mode='after')
    def bounds(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError('裁剪范围超出图片')
        return self


@router.get("/images/{image_id}")
def get_image(image_id: int, edited: bool = False):
    with db() as conn:
        row = conn.execute("SELECT original_path,mime_type,edit_json FROM question_images WHERE id=?", (image_id,)).fetchone()
    if not row or not Path(row[0]).exists():
        raise HTTPException(404, "图片不存在")
    if edited and row['edit_json']:
        try:
            data, mime, _ = edit_image(row[0], json_load(row['edit_json'], {}))
            return Response(data, media_type=mime)
        except Exception:
            raise HTTPException(422, '图片无法裁剪，请查看原图或重新选择图片')
    return FileResponse(row[0], media_type=row[1] or "application/octet-stream")


@router.post("/upload")
async def upload_task(
    files: list[UploadFile] = File(...),
    image_types: list[str] = Form(default=[]),
    question_label: str = Form(default=""),
    question_labels: str = Form(default=""),
    multi_question: bool = Form(default=False),
    practice_question_id: int | None = Form(default=None),
    exam_id: int | None = Form(default=None),
    image_edits: str = Form(default='[]'),
):
    if not files:
        raise HTTPException(400, "至少上传一张图片")
    try:
        edits = json.loads(image_edits)
        if not isinstance(edits, list) or (edits and len(edits) != len(files)):
            raise ValueError()
        edits = [ImageEdit.model_validate(item).model_dump() if item is not None else None for item in edits]
    except (ValueError, TypeError, ValidationError):
        raise HTTPException(422, '图片裁剪参数无效，请重新选择范围')
    labels = _question_labels(question_label, question_labels, multi_question)
    if practice_question_id is not None and len(labels) > 1:
        raise HTTPException(422, "独立重做一次只能对应一道题")
    stamp = now_iso().replace(":", "").replace("+00:00", "Z")
    written_paths: list[Path] = []
    written_folders: set[Path] = set()
    task_ids: list[int] = []
    multi_batch_id = secrets.token_urlsafe(16) if len(labels) > 1 else None
    try:
        with db() as conn:
            if practice_question_id is not None:
                if not conn.execute("SELECT 1 FROM questions WHERE id=? AND deleted_at IS NULL", (practice_question_id,)).fetchone():
                    raise HTTPException(404, '错题不存在或已移入回收站')
                if exam_id is not None and not conn.execute("SELECT 1 FROM study_exam_questions WHERE exam_id=? AND question_id=?", (exam_id, practice_question_id)).fetchone():
                    raise HTTPException(422, '这道题不属于当前试卷')
            elif exam_id is not None:
                raise HTTPException(422, '组卷作答必须指定题目')
            # 先读取并校验文件。多题模式下原图只落盘一次，多个任务共享同一
            # 个 question_images 内容副本，确认后仍能分别归档到不同错题。
            file_data: list[tuple[UploadFile, str, bytes, str]] = []
            for upload in files:
                mime = upload.content_type or mimetypes.guess_type(upload.filename or "")[0] or "application/octet-stream"
                if mime not in ALLOWED:
                    raise HTTPException(415, f"不支持的图片格式: {mime}")
                data = await upload.read()
                if len(data) > settings.ai_image_max_bytes * 2:
                    raise HTTPException(413, "图片过大")
                ext = Path(upload.filename or "image.jpg").suffix.lower() or ".jpg"
                file_data.append((upload, mime, data, ext))
            # 所有任务共享同一组图片文件；每个任务保留自己的图片记录和编辑参数。
            saved_files: list[tuple[Path, str, bytes]] = []
            for label_index, label in enumerate(labels):
                task_key = secrets.token_urlsafe(16)
                cur = conn.execute(
                    "INSERT INTO ai_tasks(status,question_label,multi_batch_id,crop_ready,created_at,updated_at,idempotency_key) VALUES('uploaded',?,?,0,?,?,?)",
                    (label or None, multi_batch_id, now_iso(), now_iso(), task_key),
                )
                task_id = cur.lastrowid
                task_ids.append(task_id)
                for idx, (_upload, mime, data, ext) in enumerate(file_data):
                    if label_index == 0:
                        folder = settings.upload_dir / stamp / str(task_id)
                        folder.mkdir(parents=True, exist_ok=True)
                        written_folders.add(folder)
                        path = folder / f"{idx:02d}_{secrets.token_hex(4)}{ext}"
                        path.write_bytes(data)
                        written_paths.append(path)
                        saved_files.append((path, mime, data))
                    else:
                        path, _saved_mime, _saved_data = saved_files[idx]
                    image_type = image_types[idx] if idx < len(image_types) else "other"
                    img = conn.execute("INSERT INTO question_images(task_id,image_type,question_label,original_path,mime_type,file_size,created_at) VALUES(?,?,?,?,?,?,?)", (task_id, image_type, label or None, str(path), mime, len(data), now_iso()))
                    conn.execute('UPDATE question_images SET sha256=? WHERE id=?', (hashlib.sha256(data).hexdigest(), img.lastrowid))
                    if edits and edits[idx]:
                        conn.execute('UPDATE question_images SET edit_json=? WHERE id=?', (json.dumps(edits[idx]), img.lastrowid))
                    conn.execute("INSERT INTO ai_task_images(task_id,image_id) VALUES(?,?)", (task_id, img.lastrowid))
                if practice_question_id is not None:
                    # 多题模式不能绑定独立重做；该分支已在校验前保持单题语义。
                    attempt_id = conn.execute('INSERT INTO practice_attempts(question_id,task_id,exam_id,created_at) VALUES(?,?,?,?)', (practice_question_id, task_id, exam_id, now_iso())).lastrowid
                    conn.execute('UPDATE question_images SET question_id=? WHERE task_id=?', (practice_question_id, task_id))
    except Exception:
        for path in written_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        for folder in sorted(written_folders, key=lambda item: len(item.parts), reverse=True):
            try:
                folder.rmdir()
                stamp_folder = folder.parent
                stamp_folder.rmdir()
            except OSError:
                # A concurrent upload may still use the timestamp directory.
                pass
        raise
    return {"id": task_ids[0], "ids": task_ids, "task_count": len(task_ids), "status": "uploaded", "image_count": len(files), "attempt_id": attempt_id if practice_question_id is not None else None}


@router.get("")
def list_tasks(limit: int = 20, offset: int = 0, include_practice: bool = False, status: str = ''):
    """最近的任务列表：刷新页面后也能看到进行中或失败的任务。"""
    with db() as conn:
        where, args = [], []
        if not include_practice:
            where.append('t.id NOT IN (SELECT task_id FROM practice_attempts)')
        if status == 'active':
            where.append("t.status NOT IN ('confirmed','cancelled','failed','waiting_confirmation')")
        elif status == 'unresolved':
            where.append("t.status NOT IN ('confirmed','cancelled')")
        elif status == 'archived':
            where.append("t.status IN ('confirmed','cancelled')")
        elif status:
            where.append('t.status=?')
            args.append(status)
        condition = ' AND '.join(where) or '1=1'
        total = conn.execute(f'SELECT COUNT(*) FROM ai_tasks t WHERE {condition}', args).fetchone()[0]
        tasks = conn.execute(
            f"SELECT t.id,t.status,t.error,t.question_label,t.created_at,t.updated_at,t.attempts,p.question_id practice_question_id,p.exam_id FROM ai_tasks t LEFT JOIN practice_attempts p ON p.task_id=t.id WHERE {condition} ORDER BY t.id DESC LIMIT ? OFFSET ?",
            (*args, max(1, min(limit, 100)), max(0, offset)),
        ).fetchall()
        images = conn.execute("SELECT id,task_id,image_type,(edit_json IS NOT NULL) has_edit FROM question_images ORDER BY id").fetchall()
        confirmed = conn.execute(
            """
            SELECT qi.task_id, qi.question_id
            FROM question_images qi
            JOIN questions q ON q.id = qi.question_id AND q.deleted_at IS NULL
            WHERE qi.question_id IS NOT NULL
            ORDER BY qi.id DESC
            """
        ).fetchall()
    by_task: dict[int, list] = {}
    for row in images:
        by_task.setdefault(row["task_id"], []).append({"id": row["id"], "image_type": row["image_type"], "has_edit": bool(row["has_edit"])})
    question_of = {row["task_id"]: row["question_id"] for row in confirmed}
    return {
        "total": total,
        "items": [
            {
                **dict(task),
                "images": by_task.get(task["id"], []),
                "question_id": question_of.get(task["id"]),
            }
            for task in tasks
        ]
    }


def task_payload(task_id: int):
    with db() as conn:
        task = conn.execute("SELECT * FROM ai_tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        result = conn.execute("SELECT result_json,meta_json FROM ai_results WHERE task_id=?", (task_id,)).fetchone()
        images = conn.execute("SELECT id,image_type,mime_type,file_size,(edit_json IS NOT NULL) has_edit FROM question_images WHERE task_id=?", (task_id,)).fetchall()
        duplicates = candidates(conn, task_id, json_load(result[0], {}) if result else {})
        attempt = conn.execute('SELECT id,question_id FROM practice_attempts WHERE task_id=?', (task_id,)).fetchone()
        draft = conn.execute('SELECT result_json FROM task_drafts WHERE task_id=? AND task_version=?', (task_id, task['updated_at'])).fetchone()
    parsed = json_load(result[0]) if result else None
    if isinstance(parsed, dict):
        normalize_knowledge(parsed)
    return {
        **dict(task),
        "result": parsed,
        "ai_meta": json_load(result[1], {}) if result else None,
        "images": [dict(i) for i in images],
        "duplicates": duplicates,
        "practice_attempt": dict(attempt) if attempt else None,
        "draft": json_load(draft[0], {}) if draft else None,
    }


@router.get("/{task_id}")
def get_task(task_id: int):
    return task_payload(task_id)


@router.post("/{task_id}/recognize")
@router.post("/{task_id}/analyze")
async def run_task(task_id: int):
    task_payload(task_id)
    await process_task(task_id)
    return task_payload(task_id)


@router.post("/{task_id}/retry")
async def retry_task(task_id: int, payload: RetryIn | None = None):
    with db() as conn:
        current = conn.execute("SELECT status,question_label,multi_batch_id FROM ai_tasks WHERE id=?", (task_id,)).fetchone()
        if not current:
            raise HTTPException(404, "任务不存在")
        if conn.execute("SELECT 1 FROM practice_attempts WHERE task_id=? AND (confirmed_at IS NOT NULL OR source='duplicate')", (task_id,)).fetchone():
            raise HTTPException(409, '已确认的作答不能覆盖，请开始一次新的重做')
        if payload is not None and payload.question_label is not None:
            # 允许事后补题号再重跑，例如照片里有多道题但上传时忘了填
            new_label = payload.question_label.strip()[:60] or None
            if current["multi_batch_id"] and not _same_question_label(new_label, current["question_label"]):
                raise HTTPException(422, "多题任务的题号属于同一批次，不能单独修改；请重新上传整页并重新选择题号")
            conn.execute("UPDATE ai_tasks SET question_label=? WHERE id=?", (new_label, task_id))
        if payload is not None and payload.recognition_hint is not None:
            # 用户指出的识别问题只作为本次重识别提示，不改写原图或旧结果。
            conn.execute("UPDATE ai_tasks SET recognition_hint=? WHERE id=?", (payload.recognition_hint.strip()[:4000] or None, task_id))
        conn.execute("UPDATE ai_tasks SET status='pending_recognition',error=NULL,updated_at=? WHERE id=?", (now_iso(), task_id))
    return task_payload(task_id)


@router.post("/batch-retry")
def batch_retry_tasks(payload: BatchTaskIn):
    """只批量重试失败或已取消的分析任务；待确认任务必须人工核对。"""
    task_ids = list(dict.fromkeys(payload.task_ids))
    updated: list[int] = []
    skipped: list[dict[str, object]] = []
    now = now_iso()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for task_id in task_ids:
            row = conn.execute("SELECT status FROM ai_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                skipped.append({"id": task_id, "reason": "任务不存在"})
                continue
            if conn.execute("SELECT 1 FROM practice_attempts WHERE task_id=? AND (confirmed_at IS NOT NULL OR source='duplicate')", (task_id,)).fetchone():
                skipped.append({"id": task_id, "reason": "已确认的重做不能覆盖"})
                continue
            if row["status"] not in {"failed", "cancelled"}:
                skipped.append({"id": task_id, "reason": "只有失败或已取消任务可以批量重试"})
                continue
            conn.execute("UPDATE ai_tasks SET status='pending_recognition',error=NULL,updated_at=? WHERE id=?", (now, task_id))
            updated.append(task_id)
    return {"updated": updated, "skipped": skipped, "count": len(updated)}


@router.post("/batch-cancel")
def batch_cancel_tasks(payload: BatchTaskIn):
    """批量取消仍在队列或分析中的任务，等待确认和已结束任务不会被取消。"""
    task_ids = list(dict.fromkeys(payload.task_ids))
    updated: list[int] = []
    skipped: list[dict[str, object]] = []
    now = now_iso()
    terminal = {"confirmed", "cancelled", "failed", "waiting_confirmation"}
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for task_id in task_ids:
            row = conn.execute("SELECT status FROM ai_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                skipped.append({"id": task_id, "reason": "任务不存在"})
                continue
            if row["status"] in terminal:
                skipped.append({"id": task_id, "reason": "任务已经结束或等待确认"})
                continue
            conn.execute("UPDATE ai_tasks SET status='cancelled',error=NULL,updated_at=? WHERE id=?", (now, task_id))
            updated.append(task_id)
    return {"updated": updated, "skipped": skipped, "count": len(updated)}


@router.post("/{task_id}/feedback")
def submit_feedback(task_id: int, payload: FeedbackIn):
    if payload.recognition_rating not in RATINGS or payload.correction_rating not in RATINGS:
        raise HTTPException(422, "rating必须为correct、partial或wrong")
    with db() as conn:
        if not conn.execute("SELECT id FROM ai_tasks WHERE id=?", (task_id,)).fetchone():
            raise HTTPException(404, "任务不存在")
        conn.execute("DELETE FROM ai_feedback WHERE task_id=?", (task_id,))
        conn.execute(
            "INSERT INTO ai_feedback(task_id,recognition_rating,correction_rating,note,created_at) VALUES(?,?,?,?,?)",
            (task_id, payload.recognition_rating, payload.correction_rating, payload.note, now_iso()),
        )
    return {"ok": True, "task_id": task_id}


@router.get("/{task_id}/feedback")
def get_feedback(task_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM ai_feedback WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


@router.delete("/{task_id}")
def delete_task(task_id: int):
    with db() as conn:
        if not conn.execute("SELECT id FROM ai_tasks WHERE id=?", (task_id,)).fetchone():
            raise HTTPException(404, "任务不存在")
        conn.execute("UPDATE ai_tasks SET status='cancelled',updated_at=? WHERE id=?", (now_iso(), task_id))
    return {"ok": True}
