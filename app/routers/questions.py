import json
from datetime import datetime, timedelta, timezone

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..db import db, json_load, now_iso
from ..error_types import primary_error_type
from ..subjects import EDIT_OPTIONS, PICKER_OPTIONS, SUBJECTS, group_members, normalize_subject
from ..syllabus import normalize_knowledge, syllabus_catalog

router = APIRouter(prefix="/api/questions", tags=["questions"])

SORTS = {
    "created_desc": "created_at DESC",
    "created_asc": "created_at ASC",
    "due_asc": "next_review_at IS NULL, next_review_at ASC",
    "mastery_asc": "mastery ASC, created_at DESC",
    "difficulty_desc": "difficulty DESC, created_at DESC",
}


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


def save_question(payload: QuestionIn, question_id: int | None = None):
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
    with db() as conn:
        if data.get('task_id') and conn.execute('SELECT 1 FROM practice_attempts WHERE task_id=?', (data['task_id'],)).fetchone():
            raise HTTPException(409, '这是重做记录，请在独立重做页面确认')
        if question_id and not conn.execute('SELECT 1 FROM questions WHERE id=? AND deleted_at IS NULL', (question_id,)).fetchone():
            raise HTTPException(404, '错题不存在或已移入回收站')
        if question_id:
            conn.execute("UPDATE questions SET question_label=?,subject=?,section=?,chapter=?,question_type=?,question_mode=?,difficulty=?,question_text=?,question_latex=?,user_solution_latex=?,reference_solution_latex=?,code_blocks=?,first_error_step=?,error_analysis=?,correct_approach=?,key_lesson=?,review_prompt=?,error_types=?,knowledge_points=?,uncertain_parts=?,confidence=?,is_typical=?,status='confirmed',updated_at=? WHERE id=?", (data["question_label"],data["subject"],data["section"],data["chapter"],data["question_type"],data["question_mode"],data["difficulty"],data["question_text"],data["question_latex"],data["user_solution_latex"],data["reference_solution_latex"],json.dumps(data["code_blocks"],ensure_ascii=False),data["first_error_step"],data["error_analysis"],data["correct_approach"],data["key_lesson"],data["review_prompt"],json.dumps(data["error_types"],ensure_ascii=False),json.dumps(data["knowledge_points"],ensure_ascii=False),json.dumps(data["uncertain_parts"],ensure_ascii=False),data["confidence"],int(data["is_typical"]),now,question_id))
        else:
            cur = conn.execute("INSERT INTO questions(question_label,subject,section,chapter,question_type,question_mode,difficulty,question_text,question_latex,user_solution_latex,reference_solution_latex,code_blocks,first_error_step,error_analysis,correct_approach,key_lesson,review_prompt,error_types,knowledge_points,uncertain_parts,confidence,is_typical,status,created_at,updated_at,next_review_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (data["question_label"],data["subject"],data["section"],data["chapter"],data["question_type"],data["question_mode"],data["difficulty"],data["question_text"],data["question_latex"],data["user_solution_latex"],data["reference_solution_latex"],json.dumps(data["code_blocks"],ensure_ascii=False),data["first_error_step"],data["error_analysis"],data["correct_approach"],data["key_lesson"],data["review_prompt"],json.dumps(data["error_types"],ensure_ascii=False),json.dumps(data["knowledge_points"],ensure_ascii=False),json.dumps(data["uncertain_parts"],ensure_ascii=False),data["confidence"],int(data["is_typical"]),"confirmed",now,now,(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
            question_id = cur.lastrowid
            conn.execute("INSERT INTO review_cards(question_id,due_at) VALUES(?,?)", (question_id, (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
        if data.get("task_id"):
            conn.execute("UPDATE question_images SET question_id=? WHERE task_id=?", (question_id, data["task_id"]))
            # 任务回写为已确认，避免任务列表一直显示“等待确认”
            conn.execute("UPDATE ai_tasks SET status='confirmed', error=NULL, updated_at=? WHERE id=?", (now, data["task_id"]))
        if should_sync_tags:
            _sync_tags(conn, question_id, data["tags"], "ai" if data.get("task_id") else "manual")
    return get_question(question_id)


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
    value = normalize(row); value["images"] = [dict(x) for x in images]; return value


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
def reanalyze(question_id: int):
    with db() as conn:
        row = conn.execute("""
            SELECT qi.task_id
            FROM question_images qi
            WHERE qi.question_id=?
              AND qi.task_id NOT IN (SELECT task_id FROM practice_attempts)
            ORDER BY qi.id DESC
            LIMIT 1
        """, (question_id,)).fetchone()
        if not row: raise HTTPException(400, "该错题没有关联图片")
        conn.execute("UPDATE ai_tasks SET status='pending_recognition',error=NULL,updated_at=? WHERE id=?", (now_iso(), row[0]))
    return {"task_id": row[0], "status": "pending_recognition"}
