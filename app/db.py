import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import settings
from .error_types import primary_error_type
from .syllabus import normalize_knowledge
from .subjects import normalize_subject


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS questions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL, section TEXT, chapter TEXT,
 question_type TEXT, question_mode TEXT, difficulty INTEGER, question_text TEXT,
 question_latex TEXT, user_solution_latex TEXT, reference_solution_latex TEXT,
 code_blocks TEXT NOT NULL DEFAULT '[]', first_error_step TEXT, error_analysis TEXT,
 correct_approach TEXT, key_lesson TEXT, review_prompt TEXT, error_types TEXT NOT NULL DEFAULT '[]',
 knowledge_points TEXT NOT NULL DEFAULT '[]', uncertain_parts TEXT NOT NULL DEFAULT '[]',
 confidence REAL, is_typical INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'confirmed', needs_review INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, next_review_at TEXT,
 review_count INTEGER NOT NULL DEFAULT 0, correct_count INTEGER NOT NULL DEFAULT 0,
 mastery REAL NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'upload', deleted_at TEXT,
 question_label TEXT
);
CREATE TABLE IF NOT EXISTS question_images (
 id INTEGER PRIMARY KEY AUTOINCREMENT, question_id INTEGER, task_id INTEGER, image_type TEXT NOT NULL,
 original_path TEXT NOT NULL, compressed_path TEXT, mime_type TEXT, file_size INTEGER,
 created_at TEXT NOT NULL, question_label TEXT,
 FOREIGN KEY(question_id) REFERENCES questions(id), FOREIGN KEY(task_id) REFERENCES ai_tasks(id)
);
CREATE TABLE IF NOT EXISTS knowledge_points (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL, name TEXT NOT NULL, chapter TEXT, created_at TEXT NOT NULL, UNIQUE(subject,name));
CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'manual', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS question_tags (question_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY(question_id,tag_id), FOREIGN KEY(question_id) REFERENCES questions(id), FOREIGN KEY(tag_id) REFERENCES tags(id));
CREATE TABLE IF NOT EXISTS review_cards (question_id INTEGER PRIMARY KEY, due_at TEXT NOT NULL, interval_days REAL NOT NULL DEFAULT 1, ease REAL NOT NULL DEFAULT 2.5, reps INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(question_id) REFERENCES questions(id));
CREATE TABLE IF NOT EXISTS review_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, question_id INTEGER NOT NULL, reviewed_at TEXT NOT NULL, rating TEXT NOT NULL, before_mastery REAL, after_mastery REAL, next_review_at TEXT, overdue INTEGER NOT NULL DEFAULT 0, note TEXT, FOREIGN KEY(question_id) REFERENCES questions(id));
CREATE TABLE IF NOT EXISTS ai_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, error TEXT, error_category TEXT, question_label TEXT, recognition_hint TEXT, multi_batch_id TEXT, crop_ready INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT UNIQUE, reanalysis_question_id INTEGER, reanalysis_parent_task_id INTEGER);
CREATE TABLE IF NOT EXISTS ai_task_images (task_id INTEGER NOT NULL, image_id INTEGER NOT NULL, PRIMARY KEY(task_id,image_id), FOREIGN KEY(task_id) REFERENCES ai_tasks(id), FOREIGN KEY(image_id) REFERENCES question_images(id));
CREATE TABLE IF NOT EXISTS ai_results (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL UNIQUE, result_json TEXT NOT NULL, raw_response TEXT, meta_json TEXT, created_at TEXT NOT NULL, FOREIGN KEY(task_id) REFERENCES ai_tasks(id));
CREATE TABLE IF NOT EXISTS ai_feedback (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, recognition_rating TEXT NOT NULL,
 correction_rating TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL,
 FOREIGN KEY(task_id) REFERENCES ai_tasks(id)
);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_questions_due ON questions(next_review_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON ai_tasks(status);
CREATE INDEX IF NOT EXISTS idx_feedback_task ON ai_feedback(task_id);
CREATE TABLE IF NOT EXISTS study_exams (
 id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL, knowledge_point TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_exam_questions (
 exam_id INTEGER NOT NULL REFERENCES study_exams(id), question_id INTEGER NOT NULL REFERENCES questions(id),
 position INTEGER NOT NULL, PRIMARY KEY(exam_id,question_id)
);
CREATE TABLE IF NOT EXISTS practice_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, question_id INTEGER NOT NULL REFERENCES questions(id),
 task_id INTEGER NOT NULL UNIQUE REFERENCES ai_tasks(id), exam_id INTEGER REFERENCES study_exams(id),
 source TEXT NOT NULL DEFAULT 'practice', created_at TEXT NOT NULL, confirmed_at TEXT,
 outcome TEXT, same_error INTEGER, review_rating TEXT, note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_practice_question ON practice_attempts(question_id,created_at);
CREATE TABLE IF NOT EXISTS task_drafts (
 task_id INTEGER PRIMARY KEY REFERENCES ai_tasks(id), result_json TEXT NOT NULL,
 task_version TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""

MIGRATIONS = (
    "ALTER TABLE question_images ADD COLUMN edit_json TEXT",
    "ALTER TABLE question_images ADD COLUMN sha256 TEXT",
    "ALTER TABLE ai_results ADD COLUMN meta_json TEXT",
    "ALTER TABLE questions ADD COLUMN question_label TEXT",
    "ALTER TABLE question_images ADD COLUMN question_label TEXT",
    "ALTER TABLE ai_tasks ADD COLUMN question_label TEXT",
    "ALTER TABLE ai_tasks ADD COLUMN recognition_hint TEXT",
    "ALTER TABLE ai_tasks ADD COLUMN multi_batch_id TEXT",
    "ALTER TABLE ai_tasks ADD COLUMN crop_ready INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE ai_tasks ADD COLUMN reanalysis_question_id INTEGER",
    "ALTER TABLE ai_tasks ADD COLUMN reanalysis_parent_task_id INTEGER",
    "ALTER TABLE ai_tasks ADD COLUMN error_category TEXT",
    "ALTER TABLE questions ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0",
)


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for statement in MIGRATIONS:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass  # 字段已存在，属于正常情况
        conn.execute('CREATE INDEX IF NOT EXISTS idx_images_sha256 ON question_images(sha256)')
        # 旧数据也归并到固定大类，避免统计随着历史细标签不断碎片化。
        rows = conn.execute("SELECT id,error_types,first_error_step,error_analysis,correct_approach,question_text FROM questions WHERE deleted_at IS NULL").fetchall()
        for row in rows:
            evidence = " ".join(str(row[key] or "") for key in ("first_error_step", "error_analysis", "correct_approach", "question_text"))
            category = primary_error_type(json_load(row["error_types"]), evidence)
            conn.execute("UPDATE questions SET error_types=? WHERE id=?", (json.dumps([category], ensure_ascii=False), row["id"]))
        # Once per catalog version; retain original labels for traceability.
        migration_key = "syllabus_v1_migrated"
        if not conn.execute("SELECT 1 FROM app_settings WHERE key=?", (migration_key,)).fetchone():
            for row in conn.execute("SELECT * FROM questions").fetchall():
                data = dict(row)
                conn.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES(?,?)", (
                    f"syllabus_v1_original_{data['id']}",
                    json.dumps({key: data[key] for key in ("subject", "chapter", "knowledge_points")}, ensure_ascii=False),
                ))
                data["subject"] = normalize_subject(data["subject"])
                data["knowledge_points"] = json_load(data["knowledge_points"])
                normalize_knowledge(data)
                conn.execute("UPDATE questions SET subject=?,chapter=?,knowledge_points=? WHERE id=?", (
                    data["subject"], data["chapter"], json.dumps(data["knowledge_points"], ensure_ascii=False), data["id"],
                ))
            conn.execute("INSERT INTO app_settings(key,value) VALUES(?,?)", (migration_key, now_iso()))


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def json_load(value: str | None, default=None):
    try:
        return json.loads(value) if value else (default if default is not None else [])
    except json.JSONDecodeError:
        return default if default is not None else []
