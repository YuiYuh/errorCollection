from datetime import datetime, timezone, timedelta
from contextlib import nullcontext
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..db import db, now_iso
from ..timezone import day_bounds_utc

router = APIRouter(prefix="/api", tags=["reviews"])

class ReviewIn(BaseModel):
    rating: str
    note: str = ""


def _day_bounds() -> tuple[str, str]:
    start, end = day_bounds_utc()
    return start.isoformat(), end.isoformat()


def _review_rows(scope: str, knowledge_point: str | None = None) -> list[dict]:
    start, end = _day_bounds()
    due_clause = "rc.due_at<?" if scope == "overdue" else "rc.due_at>=? AND rc.due_at<?"
    args: list[str] = [start] if scope == "overdue" else [start, end]
    point_clause = ""
    if knowledge_point:
        point_clause = " AND EXISTS (SELECT 1 FROM json_each(q.knowledge_points) WHERE json_each.value=?)"
        args.append(knowledge_point)
    with db() as conn:
        rows = [dict(row) for row in conn.execute(
            f"""
            SELECT q.*, rc.due_at,
                   (SELECT COUNT(*) FROM questions earlier
                    WHERE earlier.deleted_at IS NULL
                      AND substr(datetime(earlier.created_at,'+8 hours'), 1, 10) = substr(datetime(q.created_at,'+8 hours'), 1, 10)
                      AND (earlier.created_at < q.created_at OR (earlier.created_at = q.created_at AND earlier.id <= q.id))
                   ) AS daily_number,
                   (SELECT MAX(l.reviewed_at) FROM review_logs l WHERE l.question_id=q.id) AS last_reviewed_at,
                   (SELECT COUNT(*) FROM review_logs l WHERE l.question_id=q.id AND l.rating='forgot') AS forgotten_count,
                   COALESCE(
                     (SELECT MAX(a.created_at) FROM practice_attempts a
                      WHERE a.question_id=q.id AND a.confirmed_at IS NOT NULL AND a.outcome IN ('wrong','partial')),
                     q.created_at
                   ) AS recent_error_at
            FROM questions q JOIN review_cards rc ON rc.question_id=q.id
            WHERE q.deleted_at IS NULL AND {due_clause}{point_clause}
            """,
            args,
        )]
        attempts = [dict(row) for row in conn.execute(
            "SELECT question_id, outcome, created_at FROM practice_attempts WHERE confirmed_at IS NOT NULL ORDER BY created_at DESC"
        )]
    by_question: dict[int, list[dict]] = {}
    for attempt in attempts:
        by_question.setdefault(int(attempt["question_id"]), []).append(attempt)
    for row in rows:
        streak = 0
        for attempt in by_question.get(int(row["id"]), []):
            if attempt.get("outcome") in {"wrong", "partial"}:
                streak += 1
            else:
                break
        row["consecutive_error_count"] = streak
        try:
            recent_error = datetime.fromisoformat(str(row.get("recent_error_at") or "").replace("Z", "+00:00"))
            recent_days = max(0.0, (datetime.now(timezone.utc) - recent_error).total_seconds() / 86400)
        except (TypeError, ValueError):
            recent_days = 365.0
        row["priority_score"] = round(
            (1 - float(row.get("mastery") or 0)) * 100
            + streak * 12
            + int(row.get("forgotten_count") or 0) * 8
            + max(0.0, 10 - recent_days)
            + (20 if scope == "overdue" else 0),
            2,
        )
    rows.sort(key=lambda row: (-row["priority_score"], row.get("due_at") or "", row["id"]))
    return rows

@router.get("/reviews/today")
def today_reviews(knowledge_point: str | None = None):
    rows = _review_rows("today", knowledge_point)
    return {"items": rows, "total": len(rows), "knowledge_point": knowledge_point or ""}

@router.get("/reviews/overdue")
def overdue_reviews(knowledge_point: str | None = None):
    rows = _review_rows("overdue", knowledge_point)
    return {"items": rows, "total": len(rows), "knowledge_point": knowledge_point or ""}

@router.post("/questions/{question_id}/review")
def review(question_id: int, payload: ReviewIn):
    with db() as conn:
        return apply_review(conn, question_id, payload)


def apply_review(connection, question_id: int, payload: ReviewIn):
    if payload.rating not in {"forgot", "hard", "remembered", "mastered"}:
        raise HTTPException(422, "rating必须为forgot、hard、remembered或mastered")
    intervals = {"forgot": 1, "hard": 2, "remembered": 5, "mastered": 14}
    with nullcontext(connection) as conn:
        q = conn.execute("SELECT mastery,review_count,correct_count,next_review_at FROM questions WHERE id=? AND deleted_at IS NULL", (question_id,)).fetchone()
        if not q: raise HTTPException(404, "错题不存在")
        now = datetime.now(timezone.utc)
        before = q["mastery"]
        after = max(0, min(1, before + {"forgot": -0.2, "hard": 0.05, "remembered": 0.15, "mastered": 0.3}[payload.rating]))
        due = (now + timedelta(days=intervals[payload.rating])).isoformat()
        overdue = 0
        if q["next_review_at"]:
            try:
                previous_due = datetime.fromisoformat(q["next_review_at"])
                if previous_due.tzinfo is None:
                    previous_due = previous_due.replace(tzinfo=timezone.utc)
                overdue = int(previous_due.astimezone(timezone.utc) < now)
            except ValueError:
                # 历史脏数据不应阻塞用户完成复习；下一次安排会写入规范时间。
                overdue = 0
        conn.execute("UPDATE questions SET mastery=?,review_count=review_count+1,correct_count=correct_count+?,next_review_at=?,updated_at=? WHERE id=?", (after, int(payload.rating in {"remembered","mastered"}), due, now_iso(), question_id))
        conn.execute("INSERT OR IGNORE INTO review_cards(question_id,due_at) VALUES(?,?)", (question_id, due))
        conn.execute("UPDATE review_cards SET due_at=?,interval_days=?,reps=reps+1,lapses=lapses+? WHERE question_id=?", (due, intervals[payload.rating], int(payload.rating=="forgot"), question_id))
        conn.execute("INSERT INTO review_logs(question_id,reviewed_at,rating,before_mastery,after_mastery,next_review_at,overdue,note) VALUES(?,?,?,?,?,?,?,?)", (question_id,now_iso(),payload.rating,before,after,due,overdue,payload.note))
    return {"question_id": question_id, "rating": payload.rating, "next_review_at": due, "mastery": after}

@router.get("/reviews/stats")
def review_stats():
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) total,
                   SUM(CASE WHEN COALESCE(rc.due_at, q.next_review_at)<=? THEN 1 ELSE 0 END) due,
                   AVG(q.mastery) mastery
            FROM questions q
            LEFT JOIN review_cards rc ON rc.question_id=q.id
            WHERE q.deleted_at IS NULL
            """,
            (now_iso(),),
        ).fetchone()
    return dict(row)
