import json
import secrets
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from ..db import db, json_load, now_iso
from ..error_types import primary_error_type
from ..subjects import EDIT_OPTIONS, PICKER_OPTIONS, SUBJECTS, group_members, normalize_subject
from ..syllabus import normalize_knowledge, syllabus_catalog
from ..services.recognition import RECOGNITION_FIELDS

router = APIRouter(prefix="/api/questions", tags=["questions"])

SORTS = {
    "created_desc": "created_at DESC",
    "created_asc": "created_at ASC",
    "due_asc": "next_review_at IS NULL, next_review_at ASC",
    "mastery_asc": "mastery ASC, created_at DESC",
    "difficulty_desc": "difficulty DESC, created_at DESC",
}

EDITABLE_FIELDS = (
    "question_label", "subject", "section", "chapter", "question_type", "question_mode",
    "difficulty", "question_text", "question_latex", "user_solution_latex",
    "reference_solution_latex", "code_blocks", "first_error_step", "error_analysis",
    "correct_approach", "key_lesson", "review_prompt", "error_types", "knowledge_points",
    "tags", "uncertain_parts", "confidence", "needs_review", "is_typical",
)


class QuestionIn(BaseModel):
    question_label: str = ""
    subject: str = "unknown"
    section: str = ""
    chapter: str = ""
    question_type: str = ""
    question_mode: str = "unknown"
    difficulty: int = Field(3, ge=1, le=5)
    question_text: str = ""
    question_latex: str = ""
    user_solution_latex: str = ""
    reference_solution_latex: str = ""
    code_blocks: list = []
    first_error_step: str = ""
    error_analysis: str = ""
    correct_approach: str = ""
    key_lesson: str = ""
    review_prompt: str = ""
    error_types: list[str] = []
    knowledge_points: list[str] = []
    tags: list[str] = []
    uncertain_parts: list[str] = []
    confidence: float = 0
    needs_review: bool = False
    is_typical: bool = False
    task_id: int | None = None

    # AI 经常把「没有内容」写成 null，这里统一兜住，避免前端拿到 422 却只看到 [object Object]
    @field_validator(
        "question_label", "section", "chapter", "question_type", "question_mode",
        "question_text", "question_latex", "user_solution_latex", "reference_solution_latex",
        "first_error_step", "error_analysis", "correct_approach", "key_lesson", "review_prompt",
        mode="before",
    )
    @classmethod
    def text_default(cls, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @field_validator("subject", mode="before")
    @classmethod
    def subject_default(cls, value):
        return "unknown" if not value else value

    @field_validator("code_blocks", "error_types", "knowledge_points", "tags", "uncertain_parts", mode="before")
    @classmethod
    def list_default(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    @field_validator("difficulty", mode="before")
    @classmethod
    def difficulty_default(cls, value):
        try:
            return max(1, min(5, int(value)))
        except (TypeError, ValueError):
            return 3

    @field_validator("confidence", mode="before")
    @classmethod
    def confidence_default(cls, value):
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0


class ReanalysisImageEdit(BaseModel):
    image_id: int
    rotation: int = Field(0, description="0/90/180/270")
    x: float = Field(0, ge=0, lt=1)
    y: float = Field(0, ge=0, lt=1)
    width: float = Field(1, gt=0, le=1)
    height: float = Field(1, gt=0, le=1)

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError("旋转角度无效")
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("裁剪范围超出图片")
        return self


class ReanalysisIn(BaseModel):
    recognition_hint: str = Field(default="", max_length=4000)
    image_edits: list[ReanalysisImageEdit] = Field(default_factory=list, max_length=20)
    fields: list[str] = Field(default_factory=list, max_length=10)


def normalize(row):
    if not row:
        raise HTTPException(404, "错题不存在")
    value = dict(row)
    for key in ("code_blocks", "error_types", "knowledge_points", "tags", "uncertain_parts"):
        value[key] = json_load(value.get(key))
    value["error_types"] = [primary_error_type(
        value["error_types"],
        " ".join(str(value.get(key, "")) for key in ("first_error_step", "error_analysis", "correct_approach", "question_text")),
    )]
    normalize_knowledge(value)
    return value


@router.get("")
def list_questions(
    q: str = "",
    subject: str = "",
    chapter: str = "",
    knowledge_point: str = "",
    error_type: str = "",
    date_from: str = "",
    date_to: str = "",
    sort: str = "created_desc",
    limit: int = 100,
    offset: int = 0,
):
    clauses = ["deleted_at IS NULL"]
    args: list = []
    if q:
        clauses.append("(question_text LIKE ? OR question_latex LIKE ? OR error_analysis LIKE ? OR key_lesson LIKE ? OR correct_approach LIKE ?)")
        args += [f"%{q}%"] * 5
    if subject:
        group = group_members(subject)
        if group:
            clauses.append(f"subject IN ({','.join('?' * len(group))})")
            args.extend(group)
        else:
            clauses.append("subject=?"); args.append(normalize_subject(subject))
    if chapter:
        clauses.append("chapter=?"); args.append(chapter)
    if date_from:
        clauses.append("substr(datetime(created_at,'+8 hours'),1,10)>=?"); args.append(date_from)
    if date_to:
        clauses.append("substr(datetime(created_at,'+8 hours'),1,10)<=?"); args.append(date_to)
    if knowledge_point:
        clauses.append("EXISTS (SELECT 1 FROM json_each(q.knowledge_points) WHERE value=?)")
        args.append(knowledge_point)
    if error_type:
        clauses.append("EXISTS (SELECT 1 FROM json_each(q.error_types) WHERE value=?)")
        args.append(error_type)
    order = SORTS.get(sort, SORTS["created_desc"])
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM questions q WHERE {' AND '.join(clauses)}", args).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT q.*,
                   (SELECT COALESCE(json_group_array(t.name), '[]')
                    FROM question_tags qt JOIN tags t ON t.id=qt.tag_id
                    WHERE qt.question_id=q.id) AS tags,
                   (SELECT COUNT(*) FROM questions earlier
                    WHERE earlier.deleted_at IS NULL
                      AND substr(datetime(earlier.created_at,'+8 hours'), 1, 10) = substr(datetime(q.created_at,'+8 hours'), 1, 10)
                      AND (earlier.created_at < q.created_at OR (earlier.created_at = q.created_at AND earlier.id <= q.id))
                   ) AS daily_number
            FROM questions q
            WHERE {' AND '.join(clauses)}
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (*args, max(1, min(limit, 500)), max(0, offset)),
        ).fetchall()
    values = [normalize(r) for r in rows]
    return {"items": values, "total": total}


@router.get("/filters")
def filter_options():
    """给错题集筛选下拉框用：现有章节、知识点、错误标签和科目。"""
    with db() as conn:
        rows = [dict(x) for x in conn.execute("SELECT subject,chapter,knowledge_points,error_types FROM questions WHERE deleted_at IS NULL")]
    chapters: dict[str, int] = {}
    knowledge: dict[str, int] = {}
    errors: dict[str, int] = {}
    subjects: dict[str, int] = {}
    point_counts: dict[tuple[str, str], int] = {}
    for row in rows:
        subjects[row["subject"] or "unknown"] = subjects.get(row["subject"] or "unknown", 0) + 1
        if row["chapter"]:
            chapters[row["chapter"]] = chapters.get(row["chapter"], 0) + 1
        for name in json_load(row["knowledge_points"]):
            knowledge[str(name)] = knowledge.get(str(name), 0) + 1
            key = (row["subject"], str(name))
            point_counts[key] = point_counts.get(key, 0) + 1
        category = primary_error_type(
            json_load(row["error_types"]),
            " ".join(str(row.get(key, "")) for key in ("first_error_step", "error_analysis", "correct_approach", "question_text")),
        )
        errors[category] = errors.get(category, 0) + 1
    catalog = syllabus_catalog()
    for subject, entries in catalog.items():
        for entry in entries:
            entry["count"] = point_counts.get((subject, entry["name"]), 0)
    return {
        "subjects": sorted(subjects, key=lambda k: -subjects[k]),
        "chapters": sorted(chapters, key=lambda k: -chapters[k]),
        "knowledge_points": sorted(knowledge, key=lambda k: -knowledge[k]),
        "error_types": sorted(errors, key=lambda k: -errors[k]),
        "subject_options": PICKER_OPTIONS,
        "subject_edit_options": EDIT_OPTIONS,
        "syllabus": catalog,
    }


@router.post("")
def create_question(payload: QuestionIn):
    return save_question(payload)


@router.post("/{question_id}/confirm")
def confirm_question(question_id: int, payload: QuestionIn):
    # The confirmation page uses the task id in the URL. Resolve the question
    # through the task association so a task id cannot collide with an older
    # question id and overwrite the wrong row.
    existing_id = None
    if payload.task_id:
        with db() as conn:
            task = conn.execute("SELECT id FROM ai_tasks WHERE id=?", (payload.task_id,)).fetchone()
            if not task:
                raise HTTPException(404, "任务不存在")
            if not conn.execute("SELECT 1 FROM question_images WHERE task_id=? LIMIT 1", (payload.task_id,)).fetchone():
                raise HTTPException(422, "任务没有关联图片，无法确认错题")
            row = conn.execute(
                "SELECT question_id FROM question_images WHERE task_id=? AND question_id IS NOT NULL LIMIT 1",
                (payload.task_id,),
            ).fetchone()
        existing_id = row[0] if row else None
    if existing_id is None:
        with db() as conn:
            row = conn.execute("SELECT id FROM questions WHERE id=? AND deleted_at IS NULL", (question_id,)).fetchone()
        # Keep direct API callers compatible when no task association is sent.
        existing_id = row[0] if row and not payload.task_id else None
    return save_question(payload, existing_id)


def save_question(payload: QuestionIn, question_id: int | None = None, *, connection=None, version_label="保存修改"):
    data = payload.model_dump()
    should_sync_tags = question_id is None or "tags" in payload.model_fields_set
    data["subject"] = normalize_subject(data["subject"])
    normalize_knowledge(data)
    data["error_types"] = [primary_error_type(
        data["error_types"],
        " ".join(str(data.get(key, "")) for key in ("first_error_step", "error_analysis", "correct_approach", "question_text")),
    )]
    data["tags"] = _clean_tags(data["tags"], data["knowledge_points"], data["error_types"])
    now = now_iso()
    with (nullcontext(connection) if connection is not None else db()) as conn:
        if connection is None:
            conn.execute("BEGIN IMMEDIATE")
        if data.get('task_id') and conn.execute('SELECT 1 FROM practice_attempts WHERE task_id=?', (data['task_id'],)).fetchone():
            raise HTTPException(409, '这是重做记录，请在独立重做页面确认')
        if question_id and not conn.execute('SELECT 1 FROM questions WHERE id=? AND deleted_at IS NULL', (question_id,)).fetchone():
            raise HTTPException(404, '错题不存在或已移入回收站')
        before_snapshot = None
        if question_id:
            before_snapshot = _load_question_for_version(conn, question_id)
            if not should_sync_tags:
                data["tags"] = before_snapshot.get("tags", [])
            # Legacy rows had no version history. Capture their exact current
            # state once so the first correction is always reversible.
            if not conn.execute("SELECT 1 FROM question_versions WHERE question_id=? LIMIT 1", (question_id,)).fetchone():
                _insert_version(conn, question_id, before_snapshot, None, "初始版本")
        if question_id:
            conn.execute("UPDATE questions SET question_label=?,subject=?,section=?,chapter=?,question_type=?,question_mode=?,difficulty=?,question_text=?,question_latex=?,user_solution_latex=?,reference_solution_latex=?,code_blocks=?,first_error_step=?,error_analysis=?,correct_approach=?,key_lesson=?,review_prompt=?,error_types=?,knowledge_points=?,uncertain_parts=?,confidence=?,needs_review=?,is_typical=?,status='confirmed',updated_at=? WHERE id=?", (data["question_label"],data["subject"],data["section"],data["chapter"],data["question_type"],data["question_mode"],data["difficulty"],data["question_text"],data["question_latex"],data["user_solution_latex"],data["reference_solution_latex"],json.dumps(data["code_blocks"],ensure_ascii=False),data["first_error_step"],data["error_analysis"],data["correct_approach"],data["key_lesson"],data["review_prompt"],json.dumps(data["error_types"],ensure_ascii=False),json.dumps(data["knowledge_points"],ensure_ascii=False),json.dumps(data["uncertain_parts"],ensure_ascii=False),data["confidence"],int(data["needs_review"]),int(data["is_typical"]),now,question_id))
        else:
            cur = conn.execute("INSERT INTO questions(question_label,subject,section,chapter,question_type,question_mode,difficulty,question_text,question_latex,user_solution_latex,reference_solution_latex,code_blocks,first_error_step,error_analysis,correct_approach,key_lesson,review_prompt,error_types,knowledge_points,uncertain_parts,confidence,needs_review,is_typical,status,created_at,updated_at,next_review_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (data["question_label"],data["subject"],data["section"],data["chapter"],data["question_type"],data["question_mode"],data["difficulty"],data["question_text"],data["question_latex"],data["user_solution_latex"],data["reference_solution_latex"],json.dumps(data["code_blocks"],ensure_ascii=False),data["first_error_step"],data["error_analysis"],data["correct_approach"],data["key_lesson"],data["review_prompt"],json.dumps(data["error_types"],ensure_ascii=False),json.dumps(data["knowledge_points"],ensure_ascii=False),json.dumps(data["uncertain_parts"],ensure_ascii=False),data["confidence"],int(data["needs_review"]),int(data["is_typical"]),"confirmed",now,now,(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
            question_id = cur.lastrowid
            conn.execute("INSERT INTO review_cards(question_id,due_at) VALUES(?,?)", (question_id, (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
        if data.get("task_id"):
            conn.execute("UPDATE question_images SET question_id=? WHERE task_id=?", (question_id, data["task_id"]))
            # 任务回写为已确认，避免任务列表一直显示“等待确认”
            conn.execute("UPDATE ai_tasks SET status='confirmed', error=NULL, error_category=NULL, updated_at=? WHERE id=?", (now, data["task_id"]))
        if should_sync_tags:
            _sync_tags(conn, question_id, data["tags"], "ai" if data.get("task_id") else "manual")
        if question_id:
            _insert_version(conn, question_id, data, data.get("task_id"), version_label)
    return get_question(question_id) if connection is None else question_id


def _clean_tags(values: list[str], knowledge_points: list[str], error_types: list[str]) -> list[str]:
    excluded = set(knowledge_points) | set(error_types) | {"待确认"}
    cleaned: list[str] = []
    for value in values:
        name = str(value).strip().lstrip("#")[:24]
        if name and name not in excluded and name not in cleaned:
            cleaned.append(name)
    return cleaned[:8]


def _sync_tags(conn, question_id: int, names: list[str], kind: str) -> None:
    conn.execute("DELETE FROM question_tags WHERE question_id=?", (question_id,))
    for name in names:
        conn.execute("INSERT OR IGNORE INTO tags(name,kind,created_at) VALUES(?,?,?)", (name, kind, now_iso()))
        tag = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        conn.execute("INSERT INTO question_tags(question_id,tag_id) VALUES(?,?)", (question_id, tag[0]))


@router.get("/{question_id}")
def get_question(question_id: int):
    with db() as conn:
        row = conn.execute(
            """
            SELECT q.*,
                   (SELECT COALESCE(json_group_array(t.name), '[]')
                    FROM question_tags qt JOIN tags t ON t.id=qt.tag_id
                    WHERE qt.question_id=q.id) AS tags,
                   (SELECT COUNT(*) FROM questions earlier
                    WHERE earlier.deleted_at IS NULL
                      AND substr(datetime(earlier.created_at,'+8 hours'), 1, 10) = substr(datetime(q.created_at,'+8 hours'), 1, 10)
                      AND (earlier.created_at < q.created_at OR (earlier.created_at = q.created_at AND earlier.id <= q.id))
                   ) AS daily_number
            FROM questions q WHERE q.id=? AND q.deleted_at IS NULL
            """,
            (question_id,),
        ).fetchone()
        images = conn.execute("SELECT * FROM question_images WHERE question_id=?", (question_id,)).fetchall()
        reanalysis = conn.execute(
            """SELECT t.id,t.status,t.error,t.recognition_hint,t.recognition_fields,t.created_at,t.updated_at,
                      r.result_json,r.meta_json
               FROM ai_tasks t LEFT JOIN ai_results r ON r.task_id=t.id
               WHERE t.reanalysis_question_id=? ORDER BY t.id DESC LIMIT 20""",
            (question_id,),
        ).fetchall()
    value = normalize(row); value["images"] = [dict(x) for x in images]
    value["reanalysis_runs"] = []
    for item in reanalysis:
        run = dict(item)
        run["result"] = json_load(run.pop("result_json"), None)
        run["ai_meta"] = json_load(run.pop("meta_json"), None)
        run["recognition_fields"] = json_load(run.get("recognition_fields"), [])
        value["reanalysis_runs"].append(run)
    with db() as conn:
        versions = conn.execute(
            "SELECT id,source_task_id,label,created_at,snapshot_json FROM question_versions "
            "WHERE question_id=? ORDER BY id DESC", (question_id,)
        ).fetchall()
    value["versions"] = []
    for item in versions:
        version = dict(item)
        version["snapshot"] = json_load(version.pop("snapshot_json"), {})
        value["versions"].append(version)
    return value


@router.patch("/{question_id}")
def patch_question(question_id: int, payload: dict[str, Any] = Body(...)):
    """Partially update a question while preserving omitted fields."""
    allowed = set(QuestionIn.model_fields) - {"task_id"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise HTTPException(422, f"不支持的字段: {', '.join(unknown)}")
    current = get_question(question_id)
    merged = {name: current.get(name) for name in allowed}
    merged["task_id"] = None
    merged.update(payload)
    return save_question(QuestionIn.model_validate(merged), question_id)


@router.delete("/{question_id}")
def delete_question(question_id: int):
    with db() as conn:
        cur = conn.execute("UPDATE questions SET deleted_at=?,updated_at=? WHERE id=? AND deleted_at IS NULL", (now_iso(), now_iso(), question_id))
        if not cur.rowcount:
            raise HTTPException(404, "错题不存在或已移入回收站")
    return {"ok": True}


@router.post("/{question_id}/reanalyze")
def reanalyze(question_id: int, payload: ReanalysisIn | None = None):
    payload = payload or ReanalysisIn()
    unknown_fields = sorted(set(payload.fields) - RECOGNITION_FIELDS)
    if unknown_fields:
        raise HTTPException(422, f"不支持的重识别字段: {', '.join(unknown_fields)}")
    with db() as conn:
        question = conn.execute("SELECT * FROM questions WHERE id=? AND deleted_at IS NULL", (question_id,)).fetchone()
        if not question:
            raise HTTPException(404, "错题不存在")
        row = conn.execute("""
            SELECT qi.task_id
            FROM question_images qi
            WHERE qi.question_id=?
              AND qi.task_id NOT IN (SELECT task_id FROM practice_attempts)
            ORDER BY qi.id DESC
            LIMIT 1
        """, (question_id,)).fetchone()
        if not row:
            raise HTTPException(400, "该错题没有关联图片")
        parent_task_id = row[0]
        source_images = conn.execute(
            """SELECT * FROM question_images
               WHERE question_id=?
                 AND (task_id IS NULL OR task_id NOT IN (SELECT task_id FROM practice_attempts))
               ORDER BY id""", (question_id,)
        ).fetchall()
        source_ids = {int(item["id"]) for item in source_images}
        edits = {item.image_id: item.model_dump(exclude={"image_id"}) for item in payload.image_edits}
        unknown = sorted(set(edits) - source_ids)
        if unknown:
            raise HTTPException(422, "裁剪图片不属于这道错题")
        # When the user draws a region, send only those cropped source images
        # to the model. Without this, a full-page second image could make a
        # local correction silently re-run the entire question.
        selected_images = [image for image in source_images if int(image["id"]) in edits] or source_images
        context = dict(question)
        for key in ("code_blocks", "error_types", "knowledge_points", "tags", "uncertain_parts"):
            context[key] = json_load(context.get(key), [])
        context["tags"] = [row[0] for row in conn.execute(
            "SELECT t.name FROM question_tags qt JOIN tags t ON t.id=qt.tag_id WHERE qt.question_id=? ORDER BY t.name", (question_id,)
        ).fetchall()]
        context = _version_snapshot(context)
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO ai_tasks(
                 status,error,question_label,recognition_hint,recognition_fields,recognition_context,crop_ready,created_at,updated_at,
                 attempts,idempotency_key,reanalysis_question_id,reanalysis_parent_task_id
                   ) VALUES('pending_recognition',NULL,?,?,?,?,1,?,?,0,?,?,?)""",
            (question["question_label"], payload.recognition_hint.strip() or None,
             json.dumps(payload.fields, ensure_ascii=False), json.dumps(context, ensure_ascii=False), now, now,
              secrets.token_urlsafe(16), question_id, parent_task_id),
        )
        task_id = cur.lastrowid
        for image in selected_images:
            edit = edits.get(int(image["id"])) or json_load(image["edit_json"], None)
            copied = conn.execute(
                """INSERT INTO question_images(
                     question_id,task_id,image_type,original_path,compressed_path,mime_type,file_size,
                     created_at,question_label,edit_json,sha256
                   ) VALUES(NULL,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, image["image_type"], image["original_path"], image["compressed_path"], image["mime_type"],
                 image["file_size"], now, image["question_label"], json.dumps(edit, ensure_ascii=False) if edit else None,
                 image["sha256"]),
            )
            conn.execute("INSERT INTO ai_task_images(task_id,image_id) VALUES(?,?)", (task_id, copied.lastrowid))
    return {"task_id": task_id, "status": "pending_recognition", "question_id": question_id, "fields": payload.fields}


def _version_snapshot(question: dict[str, Any]) -> dict[str, Any]:
    """Keep only editable question fields in a rollback version."""
    return {name: question.get(name) for name in EDITABLE_FIELDS}


def _load_question_for_version(connection, question_id: int) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM questions WHERE id=? AND deleted_at IS NULL", (question_id,)).fetchone()
    if not row:
        raise HTTPException(404, "错题不存在或已移入回收站")
    value = dict(row)
    for key in ("code_blocks", "error_types", "knowledge_points", "uncertain_parts"):
        value[key] = json_load(value.get(key), [])
    value["tags"] = [item[0] for item in connection.execute(
        "SELECT t.name FROM question_tags qt JOIN tags t ON t.id=qt.tag_id WHERE qt.question_id=? ORDER BY t.name", (question_id,)
    ).fetchall()]
    return _version_snapshot(value)


def _insert_version(connection, question_id: int, question: dict[str, Any], source_task_id: int | None, label: str) -> int:
    snapshot = json.dumps(_version_snapshot(question), ensure_ascii=False)
    version = connection.execute(
        "INSERT INTO question_versions(question_id,source_task_id,snapshot_json,label,created_at) VALUES(?,?,?,?,?)",
        (question_id, source_task_id, snapshot, label, now_iso()),
    )
    return int(version.lastrowid)


@router.post("/{question_id}/reanalyze/{task_id}/apply")
def apply_reanalysis(question_id: int, task_id: int, fields: dict[str, list[str]] = Body(...)):
    requested = fields.get("fields", [])
    requested = list(dict.fromkeys(requested))
    unknown = sorted(set(requested) - RECOGNITION_FIELDS)
    if unknown:
        raise HTTPException(422, f"不支持的字段: {', '.join(unknown)}")
    if not requested:
        raise HTTPException(422, "至少选择一个要采纳的字段")
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT status,reanalysis_question_id,recognition_fields FROM ai_tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not task or task["reanalysis_question_id"] != question_id:
            raise HTTPException(404, "重识别任务不存在")
        if task["status"] not in {"reanalysis_completed", "analysis_completed", "recognition_completed"}:
            raise HTTPException(409, "重识别尚未完成")
        task_fields = set(json_load(task["recognition_fields"], []))
        if task_fields and not set(requested).issubset(task_fields):
            raise HTTPException(422, "只能采纳本次重识别请求的字段")
        result = conn.execute("SELECT result_json FROM ai_results WHERE task_id=?", (task_id,)).fetchone()
        new_result = json_load(result[0], {}) if result else {}
        missing = [name for name in requested if name not in new_result]
        if missing:
            raise HTTPException(422, "AI 未返回字段：" + ", ".join(missing))
        current = _load_question_for_version(conn, question_id)
        merged = {name: current.get(name) for name in EDITABLE_FIELDS}
        merged.update({name: new_result[name] for name in requested})
        saved_id = save_question(QuestionIn.model_validate(merged), question_id, connection=conn, version_label=f"重识别任务 #{task_id}")
        version = conn.execute("SELECT id FROM question_versions WHERE question_id=? ORDER BY id DESC LIMIT 1", (question_id,)).fetchone()
        conn.execute("UPDATE ai_tasks SET status='reanalysis_applied',updated_at=? WHERE id=?", (now_iso(), task_id))
    return {"question": get_question(saved_id), "task_id": task_id, "fields": requested, "version_id": version[0] if version else None}


@router.post("/{question_id}/versions/{version_id}/restore")
def restore_version(question_id: int, version_id: int):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute(
            "SELECT snapshot_json,source_task_id,label FROM question_versions WHERE id=? AND question_id=?",
            (version_id, question_id),
        ).fetchone()
        if not version:
            raise HTTPException(404, "识别版本不存在")
        snapshot = json_load(version["snapshot_json"], {})
        if not isinstance(snapshot, dict):
            raise HTTPException(422, "识别版本内容无效")
        current = _load_question_for_version(conn, question_id)
        merged = {name: current.get(name) for name in EDITABLE_FIELDS}
        merged.update({name: snapshot[name] for name in EDITABLE_FIELDS if name in snapshot})
        before_id = conn.execute("SELECT id FROM question_versions WHERE question_id=? ORDER BY id DESC LIMIT 1", (question_id,)).fetchone()
        saved_id = save_question(QuestionIn.model_validate(merged), question_id, connection=conn, version_label=f"回退到版本 #{version_id}")
    return {"question": get_question(saved_id), "restored_version_id": version_id, "backup_version_id": before_id[0] if before_id else None}
