"""Conservative duplicate suggestions; never merge automatically."""
import re
import unicodedata
import hashlib
from pathlib import Path
from difflib import SequenceMatcher

from ..db import json_load
from ..subjects import normalize_subject
from ..syllabus import UNCLASSIFIED, primary_knowledge_point


def fingerprint(text):
    # Keep operators and numbers: x+1 and x-1 must remain different.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).replace("$", "")


def candidates(conn, task_id, result=None):
    if conn.execute("SELECT 1 FROM practice_attempts WHERE task_id=?", (task_id,)).fetchone():
        return []
    # Older uploads predate hashes. Only hash files of a matching size, once.
    for row in conn.execute('''SELECT id,original_path FROM question_images WHERE sha256 IS NULL
        AND question_id IS NOT NULL AND file_size IN (SELECT file_size FROM question_images WHERE task_id=?)''', (task_id,)).fetchall():
        try:
            with Path(row['original_path']).open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            conn.execute('UPDATE question_images SET sha256=? WHERE id=?', (digest, row['id']))
        except OSError:
            continue
    image_ids = {row[0] for row in conn.execute(
        """SELECT DISTINCT older.question_id FROM question_images current
        JOIN question_images older ON current.sha256=older.sha256
        WHERE current.task_id=? AND current.sha256 IS NOT NULL AND older.task_id!=?
        AND older.question_id IS NOT NULL""", (task_id, task_id))}
    incoming = [fingerprint((result or {}).get(key)) for key in ('question_text', 'question_latex')]
    subject = (result or {}).get('subject')
    found = []
    for row in conn.execute("SELECT id,subject,chapter,question_label,created_at,question_text,question_latex FROM questions WHERE deleted_at IS NULL"):
        if conn.execute("SELECT 1 FROM question_images WHERE task_id=? AND question_id=?", (task_id, row['id'])).fetchone():
            continue
        score = 0.0
        reason = ''
        if row['id'] in image_ids:
            score, reason = 1.0, '原图相同（同页可能有多题，请核对题号）'
        if not subject or subject == 'unknown' or row['subject'] == subject:
            for key, source in zip(('question_text', 'question_latex'), incoming):
                target = fingerprint(row[key])
                if len(source) >= 8 and source == target:
                    score, reason = 1.0, '题目内容相同，请核对'
                elif min(len(source), len(target)) >= 24:
                    ratio = SequenceMatcher(None, source, target, autojunk=False).ratio()
                    if ratio >= .90 and ratio > score:
                        score, reason = ratio, '题目内容相似，请核对数字和条件'
        if reason:
            found.append({**dict(row), 'score': round(score, 3), 'reason': reason})
    return sorted(found, key=lambda item: (-item['score'], -item['id']))[:5]


def knowledge_point_candidates(conn, task_id, result=None, excluded_ids=None):
    """Find existing questions with the same canonical syllabus point.

    These are deliberately separate from exact duplicate candidates: a shared
    knowledge point is useful for grouping, but it is not safe to merge the
    questions automatically because their numbers and conditions may differ.
    """
    if conn.execute("SELECT 1 FROM practice_attempts WHERE task_id=?", (task_id,)).fetchone():
        return []
    value = result or {}
    subject = normalize_subject(value.get("subject"))
    if subject == "unknown":
        return []
    incoming_point = primary_knowledge_point(
        subject,
        value.get("knowledge_points"),
        " ".join(str(value.get(key) or "") for key in ("chapter", "question_text", "question_latex", "correct_approach")),
    )
    if incoming_point == UNCLASSIFIED:
        return []
    excluded = {int(item) for item in (excluded_ids or ())}
    found = []
    for row in conn.execute(
        "SELECT id,subject,chapter,question_label,created_at,question_text,question_latex,knowledge_points "
        "FROM questions WHERE deleted_at IS NULL AND subject=? ORDER BY id DESC",
        (subject,),
    ):
        if int(row["id"]) in excluded:
            continue
        points = json_load(row["knowledge_points"], [])
        point = primary_knowledge_point(
            row["subject"],
            points,
            " ".join(str(row[key] or "") for key in ("chapter", "question_text", "question_latex")),
        )
        if point != incoming_point:
            continue
        source = fingerprint(value.get("question_text"))
        target = fingerprint(row["question_text"])
        text_similarity = SequenceMatcher(None, source, target, autojunk=False).ratio() if min(len(source), len(target)) >= 12 else 0.0
        found.append({
            **dict(row),
            "score": round(0.65 + min(0.2, text_similarity * 0.2), 3),
            "match_type": "knowledge_point",
            "knowledge_point": incoming_point,
            "reason": f"可能是同一知识点：{incoming_point}，可归纳后再保留",
        })
    return sorted(found, key=lambda item: (-item["score"], -item["id"]))[:5]
