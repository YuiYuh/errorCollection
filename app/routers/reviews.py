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

@router.get("/reviews/today")
def today_reviews():
    start, end = _day_bounds()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT q.*, rc.due_at,
                   (SELECT COUNT(*) FROM questions earlier
                    WHERE earlier.deleted_at IS NULL
                      AND substr(datetime(earlier.created_at,'+8 hours'), 1, 10) = substr(datetime(q.created_at,'+8 hours'), 1, 10)
                      AND (earlier.created_at < q.created_at OR (earlier.created_at = q.created_at AND earlier.id <= q.id))
                   ) AS daily_number
            FROM questions q JOIN review_cards rc ON rc.question_id=q.id
            WHERE q.deleted_at IS NULL AND rc.due_at>=? AND rc.due_at<?
            ORDER BY rc.due_at
            """,
            (start, end),
        ).fetchall()
    return {"items": [dict(x) for x in rows], "total": len(rows)}

@router.get("/reviews/overdue")
def overdue_reviews():
    start, _ = _day_bounds()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT q.*, rc.due_at,
                   (SELECT COUNT(*) FROM questions earlier
                    WHERE earlier.deleted_at IS NULL
                      AND substr(datetime(earlier.created_at,'+8 hours'), 1, 10) = substr(datetime(q.created_at,'+8 hours'), 1, 10)
                      AND (earlier.created_at < q.created_at OR (earlier.created_at = q.created_at AND earlier.id <= q.id))
                   ) AS daily_number
            FROM questions q JOIN review_cards rc ON rc.question_id=q.id
            WHERE q.deleted_at IS NULL AND rc.due_at<?
            ORDER BY rc.due_at
            """,
            (start,),
        ).fetchall()
    return {"items": [dict(x) for x in rows], "total": len(rows)}

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
