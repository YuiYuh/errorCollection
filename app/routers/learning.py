"""Practice history, knowledge quizzes, weekly reports and reversible deletion."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import db, json_load, now_iso
from ..subjects import group_members, normalize_subject
from .questions import get_question
from .reviews import ReviewIn, apply_review

router = APIRouter(prefix='/api/learning', tags=['learning'])


def active_question(conn, question_id):
    row = conn.execute('SELECT * FROM questions WHERE id=? AND deleted_at IS NULL', (question_id,)).fetchone()
    if not row:
        raise HTTPException(404, '错题不存在或已移入回收站')
    return dict(row)


def attempt_rows(conn, question_id):
    rows = conn.execute('''SELECT p.*, t.status,t.error,r.result_json FROM practice_attempts p
        JOIN ai_tasks t ON t.id=p.task_id LEFT JOIN ai_results r ON r.task_id=t.id
        WHERE p.question_id=? ORDER BY p.id DESC''', (question_id,)).fetchall()
    values = []
    for row in rows:
        value = dict(row)
        value['result'] = json_load(value.pop('result_json'), {})
        value['images'] = [dict(image) for image in conn.execute('SELECT id,image_type FROM question_images WHERE task_id=?', (value['task_id'],))]
        values.append(value)
    return values


@router.get('/practice/{question_id}')
def practice(question_id: int):
    with db() as conn:
        q = active_question(conn, question_id)
        # Only the question is returned here: opening practice must not reveal answers.
        safe = {key: q[key] for key in ('id', 'subject', 'chapter', 'question_text', 'question_latex', 'difficulty', 'created_at')}
        safe['images'] = [dict(row) for row in conn.execute("""SELECT id FROM question_images
            WHERE question_id=? AND image_type='question' AND task_id NOT IN (SELECT task_id FROM practice_attempts)""", (question_id,))]
        return {'question': safe, 'attempts': attempt_rows(conn, question_id)}


class ConfirmAttempt(BaseModel):
    outcome: Literal['correct', 'partial', 'wrong', 'uncertain']
    same_error: bool | None = None
    rating: Literal['forgot', 'hard', 'remembered', 'mastered']
    note: str = Field('', max_length=2000)


@router.post('/attempts/{attempt_id}/confirm')
def confirm_attempt(attempt_id: int, payload: ConfirmAttempt):
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        p = conn.execute('SELECT p.*,t.status FROM practice_attempts p JOIN ai_tasks t ON t.id=p.task_id WHERE p.id=?', (attempt_id,)).fetchone()
        if not p:
            raise HTTPException(404, '作答记录不存在')
        active_question(conn, p['question_id'])
        if p['confirmed_at']:
            return {'ok': True, 'already_confirmed': True}
        if p['status'] != 'waiting_confirmation':
            raise HTTPException(409, 'AI 尚未完成，暂不能确认')
        same = None if payload.outcome == 'uncertain' else False if payload.outcome == 'correct' else payload.same_error
        conn.execute('UPDATE practice_attempts SET outcome=?,same_error=?,review_rating=?,note=?,confirmed_at=? WHERE id=?',
                     (payload.outcome, same, payload.rating, payload.note, now_iso(), attempt_id))
        apply_review(conn, p['question_id'], ReviewIn(rating=payload.rating, note=f'重做 #{attempt_id}：{payload.outcome}'))
        conn.execute("UPDATE ai_tasks SET status='confirmed',updated_at=? WHERE id=?", (now_iso(), p['task_id']))
    return {'ok': True}


class AttachDuplicate(BaseModel):
    question_id: int


@router.post('/duplicates/{task_id}/attach')
def attach_duplicate(task_id: int, payload: AttachDuplicate):
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        active_question(conn, payload.question_id)
        prior = conn.execute('SELECT * FROM practice_attempts WHERE task_id=?', (task_id,)).fetchone()
        if prior:
            if prior['question_id'] != payload.question_id:
                raise HTTPException(409, '这次上传已关联另一道题')
            return {'attempt_id': prior['id'], 'question_id': payload.question_id}
        task = conn.execute('SELECT * FROM ai_tasks WHERE id=?', (task_id,)).fetchone()
        if not task or task['status'] != 'waiting_confirmation':
            raise HTTPException(409, '请等待识别完成后再归入旧题')
        # A page may contain multiple questions. Only the user chooses the target.
        attempt_id = conn.execute("INSERT INTO practice_attempts(question_id,task_id,source,created_at) VALUES(?,?,'duplicate',?)", (payload.question_id, task_id, now_iso())).lastrowid
        conn.execute('UPDATE question_images SET question_id=? WHERE task_id=?', (payload.question_id, task_id))
        conn.execute("UPDATE ai_tasks SET status='confirmed',updated_at=? WHERE id=?", (now_iso(), task_id))
    return {'attempt_id': attempt_id, 'question_id': payload.question_id}


class ExamIn(BaseModel):
    subject: str = ''
    knowledge_point: str = Field(..., min_length=1, max_length=120)
    count: int = Field(5, ge=1, le=20)


def ranked_questions(conn, subject='', knowledge_point=''):
    args = []
    where = ['q.deleted_at IS NULL']
    if subject:
        members = group_members(subject) or [normalize_subject(subject)]
        where.append(f"q.subject IN ({','.join('?' for _ in members)})")
        args.extend(members)
    if knowledge_point:
        where.append('EXISTS (SELECT 1 FROM json_each(q.knowledge_points) WHERE value=?)')
        args.append(knowledge_point)
    return [dict(row) for row in conn.execute(f'''SELECT q.id,q.subject,q.chapter,q.created_at,q.knowledge_points,q.mastery,
        COALESCE(rc.lapses,0) lapses,
        (SELECT COUNT(*) FROM practice_attempts p WHERE p.question_id=q.id AND p.confirmed_at IS NOT NULL AND p.outcome IN ('wrong','partial')) failures,
        (SELECT MAX(reviewed_at) FROM review_logs l WHERE l.question_id=q.id) last_review_at
        FROM questions q LEFT JOIN review_cards rc ON rc.question_id=q.id
        WHERE {' AND '.join(where)} ORDER BY (failures+COALESCE(rc.lapses,0)) DESC,
        COALESCE(last_review_at,q.created_at) ASC,q.mastery ASC,q.id ASC''', args)]


@router.post('/exams')
def create_exam(payload: ExamIn):
    with db() as conn:
        rows = ranked_questions(conn, payload.subject, payload.knowledge_point)[:payload.count]
        if not rows:
            raise HTTPException(400, '这个知识点还没有可组卷的错题')
        exam_id = conn.execute('INSERT INTO study_exams(subject,knowledge_point,created_at) VALUES(?,?,?)', (payload.subject, payload.knowledge_point, now_iso())).lastrowid
        conn.executemany('INSERT INTO study_exam_questions(exam_id,question_id,position) VALUES(?,?,?)', [(exam_id, row['id'], i) for i, row in enumerate(rows)])
    return get_exam(exam_id)


@router.get('/exams')
def exams():
    with db() as conn:
        return {'items': [dict(row) for row in conn.execute('SELECT * FROM study_exams ORDER BY id DESC LIMIT 50')]}


@router.get('/exams/{exam_id}')
def get_exam(exam_id: int):
    with db() as conn:
        row = conn.execute('SELECT * FROM study_exams WHERE id=?', (exam_id,)).fetchone()
        if not row:
            raise HTTPException(404, '试卷不存在')
        items = [dict(item) for item in conn.execute('''SELECT q.id,q.subject,q.chapter,q.created_at,q.deleted_at,
            (SELECT outcome FROM practice_attempts p WHERE p.exam_id=e.exam_id AND p.question_id=q.id AND confirmed_at IS NOT NULL ORDER BY p.id DESC LIMIT 1) outcome
            FROM study_exam_questions e JOIN questions q ON q.id=e.question_id WHERE e.exam_id=? ORDER BY e.position''', (exam_id,))]
        active = [item for item in items if not item['deleted_at']]
        completed = sum(item['outcome'] is not None for item in active)
        return {**dict(row), 'items': items, 'total': len(active), 'completed': completed, 'correct': sum(item['outcome'] == 'correct' for item in active)}


@router.get('/trash')
def trash():
    with db() as conn:
        return {'items': [dict(row) for row in conn.execute('SELECT id,subject,chapter,created_at,deleted_at FROM questions WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC')]}


@router.post('/trash/{question_id}/restore')
def restore(question_id: int):
    with db() as conn:
        if not conn.execute('SELECT 1 FROM questions WHERE id=?', (question_id,)).fetchone():
            raise HTTPException(404, '错题不存在')
        conn.execute('UPDATE questions SET deleted_at=NULL,updated_at=? WHERE id=?', (now_iso(), question_id))
    return get_question(question_id)


@router.get('/weekly')
def weekly(offset: int = 0):
    # Calendar weeks in the user's UTC+8 locale, queried using stored UTC timestamps.
    offset = max(0, min(offset, 52))
    local_now = datetime.now(timezone(timedelta(hours=8)))
    start = (local_now - timedelta(days=local_now.weekday(), weeks=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    start_s, end_s, previous_s = (d.astimezone(timezone.utc).isoformat() for d in (start, end, start - timedelta(days=7)))
    with db() as conn:
        questions = [dict(row) for row in conn.execute('SELECT * FROM questions WHERE deleted_at IS NULL')]
        attempts = [dict(row) for row in conn.execute('''SELECT p.*,r.result_json FROM practice_attempts p
            JOIN questions q ON q.id=p.question_id LEFT JOIN ai_results r ON r.task_id=p.task_id
            WHERE p.confirmed_at IS NOT NULL AND q.deleted_at IS NULL AND p.confirmed_at<? ORDER BY p.confirmed_at,p.id''', (end_s,))]
        review_count = conn.execute('''SELECT COUNT(*) FROM review_logs l JOIN questions q ON q.id=l.question_id
            WHERE q.deleted_at IS NULL AND l.reviewed_at>=? AND l.reviewed_at<?''', (start_s, end_s)).fetchone()[0]
        recommendations = ranked_questions(conn)[:5]
    by_id = {q['id']: q for q in questions}
    current = [a for a in attempts if a['confirmed_at'] >= start_s]
    previous = [a for a in attempts if previous_s <= a['confirmed_at'] < start_s]
    new = [q for q in questions if start_s <= q['created_at'] < end_s]
    knowledge = defaultdict(set)
    errors = Counter()
    for q in new:
        for point in json_load(q['knowledge_points']):
            knowledge[(q['subject'], str(point))].add(q['id'])
        errors.update(json_load(q['error_types'])[:1])
    for a in current:
        if a['outcome'] not in ('wrong', 'partial'):
            continue
        q = by_id[a['question_id']]
        for point in json_load(q['knowledge_points']):
            knowledge[(q['subject'], str(point))].add(q['id'])
        result = json_load(a['result_json'], {})
        errors.update((result.get('error_types') or json_load(q['error_types']))[:1])
    prior_outcomes = {}
    improved = set()
    for a in attempts:
        prior = prior_outcomes.get(a['question_id'])
        if a['confirmed_at'] >= start_s and a['outcome'] == 'correct' and prior in ('wrong', 'partial'):
            improved.add(a['question_id'])
        if a['outcome'] != 'uncertain':
            prior_outcomes[a['question_id']] = a['outcome']
    def result_summary(rows):
        known = [a for a in rows if a['outcome'] != 'uncertain']
        return {'total': len(rows), 'graded': len(known), 'correct': sum(a['outcome'] == 'correct' for a in known),
                'accuracy': sum(a['outcome'] == 'correct' for a in known) / len(known) if known else None}
    return {'start': start.date().isoformat(), 'end': (end - timedelta(days=1)).date().isoformat(), 'new_questions': len(new),
            'review_count': review_count, 'practice': result_summary(current), 'previous_practice': result_summary(previous),
            'improved_questions': len(improved), 'repeated_errors': sum(a['same_error'] == 1 for a in current),
            'knowledge': [{'subject': subject, 'name': name, 'count': len(ids)} for (subject, name), ids in sorted(knowledge.items(), key=lambda item: -len(item[1]))],
            'errors': [{'name': name, 'count': count} for name, count in errors.most_common()],
            'recommendations': recommendations, 'recommendations_scope': 'current',
            'basis': '薄弱知识点按本周新增错题和重做未通过的题目去重；错误类型按发生次数。正确率仅统计已确认重做，排除不确定，自评复习不计入。'}
