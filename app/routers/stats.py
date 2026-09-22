from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from ..db import db, json_load, now_iso
from ..timezone import LOCAL_TZ
from ..subjects import SUBJECTS, normalize_subject
from ..syllabus import UNCLASSIFIED, detail_knowledge_point, primary_knowledge_point, syllabus_points

router = APIRouter(prefix="/api/stats", tags=["stats"])

@router.get("/overview")
def overview():
    now = datetime.now(timezone.utc)
    local_start = now.astimezone(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start = local_start.astimezone(timezone.utc)
    end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM questions WHERE deleted_at IS NULL").fetchone()[0]
        due = conn.execute("SELECT COUNT(*) FROM questions q JOIN review_cards rc ON rc.question_id=q.id WHERE q.deleted_at IS NULL AND rc.due_at<=?", (now.isoformat(),)).fetchone()[0]
        overdue = conn.execute("SELECT COUNT(*) FROM questions q JOIN review_cards rc ON rc.question_id=q.id WHERE q.deleted_at IS NULL AND rc.due_at<?", (start.isoformat(),)).fetchone()[0]
        today_due = conn.execute("SELECT COUNT(*) FROM questions q JOIN review_cards rc ON rc.question_id=q.id WHERE q.deleted_at IS NULL AND rc.due_at>=? AND rc.due_at<?", (start.isoformat(), end.isoformat())).fetchone()[0]
        mastery = conn.execute("SELECT AVG(mastery) FROM questions WHERE deleted_at IS NULL").fetchone()[0] or 0
        subjects = [dict(x) for x in conn.execute("SELECT subject,COUNT(*) count FROM questions WHERE deleted_at IS NULL GROUP BY subject")]
    return {"total": total, "due": due, "today_due": today_due, "overdue": overdue, "average_mastery": mastery, "subjects": subjects}

@router.get("/subjects")
def subjects():
    with db() as conn: return [dict(x) for x in conn.execute("SELECT subject,COUNT(*) count,AVG(mastery) mastery FROM questions WHERE deleted_at IS NULL GROUP BY subject")]

@router.get("/knowledge-points")
def knowledge_points():
    with db() as conn: return [dict(x) for x in conn.execute("SELECT value name,COUNT(*) count FROM questions, json_each(questions.knowledge_points) WHERE deleted_at IS NULL GROUP BY value ORDER BY count DESC")]

@router.get("/error-types")
def error_types():
    with db() as conn:
        return [dict(x) for x in conn.execute(
            "SELECT value name,COUNT(*) count FROM questions, json_each(questions.error_types) WHERE deleted_at IS NULL GROUP BY value ORDER BY count DESC"
        )]

@router.get("/trends")
def trends():
    with db() as conn: return [dict(x) for x in conn.execute("SELECT substr(datetime(created_at,'+8 hours'),1,10) day,COUNT(*) count FROM questions WHERE deleted_at IS NULL GROUP BY day ORDER BY day DESC LIMIT 90")]


def _rate(correct: int, partial: int, total: int) -> float:
    if not total:
        return 0.0
    # 部分正确按一半计入正确率
    return round((correct + partial * 0.5) / total, 4)


@router.get("/ai-accuracy")
def ai_accuracy():
    with db() as conn:
        rows = [dict(x) for x in conn.execute("SELECT recognition_rating,correction_rating FROM ai_feedback")]
        analyzed = conn.execute("SELECT COUNT(*) FROM ai_results").fetchone()[0]
        confirmed = conn.execute("SELECT COUNT(*) FROM questions WHERE deleted_at IS NULL").fetchone()[0]
    by_recognition = {k: 0 for k in ("correct", "partial", "wrong")}
    by_correction = {k: 0 for k in ("correct", "partial", "wrong")}
    for row in rows:
        by_recognition[row["recognition_rating"]] = by_recognition.get(row["recognition_rating"], 0) + 1
        by_correction[row["correction_rating"]] = by_correction.get(row["correction_rating"], 0) + 1
    reviewed = len(rows)
    return {
        "reviewed_tasks": reviewed,
        "analyzed_tasks": analyzed,
        "confirmed_questions": confirmed,
        "recognition": {**by_recognition, "rate": _rate(by_recognition["correct"], by_recognition["partial"], reviewed)},
        "correction": {**by_correction, "rate": _rate(by_correction["correct"], by_correction["partial"], reviewed)},
    }


@router.get("/distribution")
def distribution(days: int = 30):
    """统计页一次性取回：知识点、错误类型、科目、难度和最近趋势。"""
    days = max(1, min(days, 365))
    with db() as conn:
        knowledge = [dict(x) for x in conn.execute(
            "SELECT value name,COUNT(*) count FROM questions, json_each(questions.knowledge_points) WHERE deleted_at IS NULL GROUP BY value ORDER BY count DESC"
        )]
        errors = [dict(x) for x in conn.execute(
            "SELECT value name,COUNT(*) count FROM questions, json_each(questions.error_types) WHERE deleted_at IS NULL GROUP BY value ORDER BY count DESC LIMIT 30"
        )]
        subjects = [dict(x) for x in conn.execute(
            "SELECT subject name,COUNT(*) count FROM questions WHERE deleted_at IS NULL GROUP BY subject ORDER BY count DESC"
        )]
        difficulty = [dict(x) for x in conn.execute(
            "SELECT difficulty name,COUNT(*) count FROM questions WHERE deleted_at IS NULL GROUP BY difficulty ORDER BY difficulty"
        )]
        trend = [dict(x) for x in conn.execute(
            "SELECT substr(datetime(created_at,'+8 hours'),1,10) day,COUNT(*) count FROM questions WHERE deleted_at IS NULL AND datetime(created_at,'+8 hours')>=datetime('now','+8 hours',?) GROUP BY day ORDER BY day",
            (f"-{days} day",),
        )]
        repeat_rows = [dict(x) for x in conn.execute(
            """
            SELECT q.id,q.subject,q.chapter,q.question_label,q.created_at,q.review_count,q.correct_count,
                   q.question_text,q.question_latex,q.correct_approach,q.error_analysis,
                   q.knowledge_points,q.mastery,COALESCE(rc.lapses,0) lapses,
                   (SELECT COALESCE(json_group_array(t.name), '[]')
                    FROM question_tags qt JOIN tags t ON t.id=qt.tag_id
                    WHERE qt.question_id=q.id) AS tags,
                   (SELECT COUNT(*) FROM practice_attempts p
                    WHERE p.question_id=q.id AND p.confirmed_at IS NOT NULL
                      AND p.outcome IN ('wrong','partial')) failures,
                   (SELECT COUNT(*) FROM questions earlier
                    WHERE earlier.deleted_at IS NULL
                      AND substr(datetime(earlier.created_at,'+8 hours'), 1, 10) = substr(datetime(q.created_at,'+8 hours'), 1, 10)
                      AND (earlier.created_at < q.created_at OR (earlier.created_at = q.created_at AND earlier.id <= q.id))
                   ) AS daily_number
            FROM questions q LEFT JOIN review_cards rc ON rc.question_id=q.id
            WHERE q.deleted_at IS NULL AND (
                COALESCE(rc.lapses,0)>0 OR q.mastery<0.4 OR
                (SELECT COUNT(*) FROM practice_attempts p
                 WHERE p.question_id=q.id AND p.confirmed_at IS NOT NULL
                   AND p.outcome IN ('wrong','partial'))>0
            )
            ORDER BY (COALESCE(rc.lapses,0) + failures) DESC,q.mastery ASC
            """
        )]
        repeat_knowledge_points = {}
        for row in repeat_rows:
            repeat_count = int(row.get("failures") or 0) + int(row.get("lapses") or 0)
            # Keep low-mastery questions in the weak-point view even before their
            # first explicit repeat, while giving actual repeats higher priority.
            if repeat_count == 0 and float(row.get("mastery") or 0) >= 0.4:
                continue
            raw_points = json_load(row.get("knowledge_points"), [])
            if not isinstance(raw_points, list):
                raw_points = [raw_points]
            evidence = " ".join(str(row.get(field) or "") for field in (
                "chapter", "question_text", "question_latex", "correct_approach", "error_analysis",
            ))
            content_evidence = " ".join(str(row.get(field) or "") for field in (
                "question_text", "question_latex", "correct_approach", "error_analysis",
            ))
            # Older records may contain a subject label, an empty value, or
            # the old pending marker in knowledge_points. Those values are
            # not evidence of a topic, so classify from the question content
            # again before falling back to the stored chapter.
            raw_values = [str(value).strip() for value in raw_points if str(value).strip()]
            subject_labels = {label for key, label in SUBJECTS.items() if key != "unknown"}
            usable_values = [
                value for value in raw_values
                if value != UNCLASSIFIED
                and value not in subject_labels
                and normalize_subject(value) == "unknown"
            ]
            chapter = str(row.get("chapter") or "").strip()
            # For legacy rows whose stored value is empty/pending/a subject
            # label, the chapter is the trusted parent and evidence chooses
            # only the display-level detail below it.
            if not usable_values and chapter in syllabus_points(row["subject"]):
                point = chapter
            else:
                point = primary_knowledge_point(row["subject"], usable_values, content_evidence)
                if point == UNCLASSIFIED and chapter in syllabus_points(row["subject"]):
                    point = chapter
            points = [point]
            for point in points:
                name = point
                key = (row["subject"], name)
                group = repeat_knowledge_points.setdefault(key, {
                    "name": name,
                    "subject": row["subject"],
                    "count": 0,
                    "repeat_count": 0,
                    "mastery": 0.0,
                    "questions": [],
                    "details": {},
                    "_question_ids": set(),
                })
                if row["id"] in group["_question_ids"]:
                    continue
                group["_question_ids"].add(row["id"])
                group["count"] += 1
                group["repeat_count"] += repeat_count
                group["mastery"] += float(row.get("mastery") or 0)
                question = {
                    "id": row["id"],
                    "subject": row["subject"],
                    "chapter": row["chapter"],
                    "question_label": row["question_label"],
                    "created_at": row["created_at"],
                    "mastery": row["mastery"],
                    "failures": row["failures"],
                    "lapses": row["lapses"],
                    "daily_number": row["daily_number"],
                }
                group["questions"].append(question)
                detail_name = detail_knowledge_point(
                    row["subject"], point, evidence,
                    json_load(row.get("tags"), []),
                )
                detail = group["details"].setdefault(detail_name, {
                    "name": detail_name,
                    "parent_name": name,
                    "subject": row["subject"],
                    "count": 0,
                    "repeat_count": 0,
                    "mastery": 0.0,
                    "questions": [],
                })
                detail["count"] += 1
                detail["repeat_count"] += repeat_count
                detail["mastery"] += float(row.get("mastery") or 0)
                detail["questions"].append(question)
        repeat_knowledge = []
        for group in repeat_knowledge_points.values():
            group.pop("_question_ids", None)
            group["mastery"] = round(group["mastery"] / group["count"], 4) if group["count"] else 0
            group["questions"].sort(key=lambda item: (
                -(int(item.get("failures") or 0) + int(item.get("lapses") or 0)),
                float(item.get("mastery") or 0),
                item["id"],
            ))
            group["questions"] = group["questions"][:5]
            details = []
            for detail in group["details"].values():
                detail["mastery"] = round(detail["mastery"] / detail["count"], 4) if detail["count"] else 0
                detail["questions"].sort(key=lambda item: (
                    -(int(item.get("failures") or 0) + int(item.get("lapses") or 0)),
                    float(item.get("mastery") or 0),
                    item["id"],
                ))
                detail["questions"] = detail["questions"][:5]
                details.append(detail)
            details.sort(key=lambda item: (-item["repeat_count"], item["mastery"], -item["count"], item["name"]))
            group["details"] = details
            repeat_knowledge.append(group)
        repeat_knowledge.sort(key=lambda item: (-item["repeat_count"], item["mastery"], -item["count"], item["name"]))
        # An unclassified value is useful for migration diagnostics, but it is
        # not a knowledge point users can act on from the home page.
        repeat_syllabus = [item for item in repeat_knowledge if item["name"] != UNCLASSIFIED]
    return {
        "knowledge_points": knowledge,
        "error_types": errors,
        "subjects": subjects,
        "difficulty": difficulty,
        "trend": trend,
        "repeat_questions": repeat_rows[:20],
        # Keep the canonical syllabus grouping explicit for the home page.
        # ``details`` remains available as an optional subdivision inside each
        # formal syllabus point, but it is not the point's display name.
        "repeat_syllabus_points": repeat_syllabus,
        "repeat_knowledge_points": repeat_knowledge,
    }
