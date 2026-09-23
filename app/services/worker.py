import asyncio
import json
import logging
import re
import inspect
from collections import defaultdict

from ..db import db, json_load, now_iso
from ..config import settings
from .ai.openai_compatible import OpenAICompatibleClient, error_category
from .backup import maybe_auto_backup

log = logging.getLogger(__name__)
_BATCH_LOCKS: dict[str, asyncio.Lock] = {}

# 这些状态代表任务正在被处理；进程重启后它们一定是孤儿，必须重新排队
ORPHAN_STATUSES = ("recognizing", "pending_analysis", "analyzing", "recognition_completed", "analysis_completed")


def _clamp_region(region: dict) -> dict | None:
    try:
        image_index = int(region.get("image_index"))
        x = max(0.0, min(1.0, float(region.get("x", 0))))
        y = max(0.0, min(1.0, float(region.get("y", 0))))
        width = max(0.0, min(1.0 - x, float(region.get("width", 0))))
        height = max(0.0, min(1.0 - y, float(region.get("height", 0))))
    except (TypeError, ValueError):
        return None
    if image_index < 0 or width < 0.01 or height < 0.01:
        return None
    # A tiny margin prevents OCR from clipping a superscript, option marker or
    # punctuation at the model's predicted boundary.
    margin_x, margin_y = min(0.015, x), min(0.015, y)
    right_margin = min(0.015, 1.0 - (x + width))
    bottom_margin = min(0.015, 1.0 - (y + height))
    return {
        "image_index": image_index,
        "rotation": 0,
        "x": max(0.0, x - margin_x),
        "y": max(0.0, y - margin_y),
        "width": min(1.0, width + margin_x + right_margin),
        "height": min(1.0, height + margin_y + bottom_margin),
    }


def _label_key(label: str) -> str:
    """Return a stable key for labels such as ``2``, ``第2题`` and ``第 2 题``."""
    text = str(label or "").strip().replace(" ", "")
    match = re.fullmatch(r"第?(\d+)题?", text)
    return f"第{match.group(1)}题" if match else text.casefold()


async def _prepare_multi_batch(task_id: int, batch_id: str) -> None:
    """Locate and persist per-question crops for a multi-page upload."""
    lock = _BATCH_LOCKS.setdefault(batch_id, asyncio.Lock())
    async with lock:
        await _prepare_multi_batch_locked(task_id, batch_id)


async def _prepare_multi_batch_locked(task_id: int, batch_id: str) -> None:
    with db() as conn:
        tasks = conn.execute("SELECT id,question_label FROM ai_tasks WHERE multi_batch_id=? ORDER BY id", (batch_id,)).fetchall()
        if not tasks:
            raise RuntimeError("多题任务组不存在")
        source_task = tasks[0]["id"]
        fresh = conn.execute("SELECT crop_ready FROM ai_tasks WHERE multi_batch_id=?", (batch_id,)).fetchall()
        if fresh and all(bool(row["crop_ready"]) for row in fresh):
            return
        # The first task may have already attempted localization and failed
        # while another task in the same batch was waiting on this lock. Reuse
        # that terminal error instead of sending the same locator request again.
        failed = conn.execute(
            "SELECT error FROM ai_tasks WHERE multi_batch_id=? AND status='failed' ORDER BY id LIMIT 1",
            (batch_id,),
        ).fetchone()
        if failed:
            raise RuntimeError(failed["error"] or "多题任务定位失败，请重试")
        source_rows = conn.execute(
            "SELECT qi.* FROM question_images qi JOIN ai_task_images ati ON qi.id=ati.image_id WHERE ati.task_id=? ORDER BY qi.id",
            (source_task,),
        ).fetchall()
    if not source_rows:
        raise RuntimeError("多题任务没有原图")
    source_images = [
        {"id": row["id"], "path": row["original_path"], "mime_type": row["mime_type"], "image_type": row["image_type"]}
        for row in source_rows
    ]
    labels = [row["question_label"] or "" for row in tasks]
    located = await OpenAICompatibleClient().locate_questions(source_images, labels, task_id=task_id)
    by_label: dict[str, list[dict]] = defaultdict(list)
    invalid_image_indexes: list[int] = []
    for item in located.get("questions", []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("question_label") or "").strip()
        for region in item.get("regions", []) if isinstance(item.get("regions"), list) else []:
            if isinstance(region, dict):
                try:
                    image_index = int(region.get("image_index"))
                except (TypeError, ValueError):
                    image_index = -1
                if image_index < 0 or image_index >= len(source_rows):
                    invalid_image_indexes.append(image_index)
                    continue
            clean = _clamp_region(region) if isinstance(region, dict) else None
            if clean is not None:
                by_label[_label_key(label)].append(clean)
    if invalid_image_indexes:
        raise RuntimeError("AI 返回了不存在的图片页码，无法安全裁剪；请重试或重新拍摄")
    missing = [label for label in labels if not by_label.get(_label_key(label))]
    if missing:
        raise RuntimeError("无法定位题目：" + "、".join(missing) + "。请检查题号或重新拍摄清晰的整页照片")

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # A task can be processed through the API while the background worker
        # is handling the same batch.  Once another caller has finished the
        # crop transaction, its per-task image rows are already reduced; do
        # not run the locator a second time against that reduced source.
        fresh = conn.execute("SELECT crop_ready FROM ai_tasks WHERE multi_batch_id=?", (batch_id,)).fetchall()
        if fresh and all(bool(row["crop_ready"]) for row in fresh):
            return
        for task in tasks:
            label = task["question_label"] or ""
            rows = conn.execute(
                "SELECT qi.id FROM question_images qi JOIN ai_task_images ati ON qi.id=ati.image_id WHERE ati.task_id=? ORDER BY qi.id",
                (task["id"],),
            ).fetchall()
            regions_by_image: dict[int, list[dict]] = defaultdict(list)
            for region in by_label[_label_key(label)]:
                regions_by_image[int(region["image_index"])].append(region)
            for image_index, row in enumerate(rows):
                regions = regions_by_image.get(image_index, [])
                if not regions:
                    conn.execute("DELETE FROM ai_task_images WHERE task_id=? AND image_id=?", (task["id"], row["id"]))
                    conn.execute("DELETE FROM question_images WHERE id=?", (row["id"],))
                    continue
                # If a question spans multiple boxes on one page, use their union.
                x1 = min(item["x"] for item in regions)
                y1 = min(item["y"] for item in regions)
                x2 = max(item["x"] + item["width"] for item in regions)
                y2 = max(item["y"] + item["height"] for item in regions)
                edit = _clamp_region({"image_index": image_index, "x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1})
                conn.execute("UPDATE question_images SET edit_json=?,question_label=? WHERE id=?", (json.dumps(edit, ensure_ascii=False), label, row["id"]))
            conn.execute("UPDATE ai_tasks SET crop_ready=1,updated_at=? WHERE id=?", (now_iso(), task["id"]))


def requeue_orphans() -> int:
    placeholders = ",".join("?" * len(ORPHAN_STATUSES))
    with db() as conn:
        cur = conn.execute(
            f"UPDATE ai_tasks SET status='pending_recognition', error=?, error_category=?, updated_at=? WHERE status IN ({placeholders})",
            ("服务重启，任务已自动重新排队", "server", now_iso(), *ORPHAN_STATUSES),
        )
        count = cur.rowcount
    if count:
        log.info("requeued %d interrupted task(s) after restart", count)
    return count


async def process_task(task_id: int) -> None:
    with db() as conn:
        task = conn.execute("SELECT * FROM ai_tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return
        question_label = task["question_label"] or ""
        recognition_hint = task["recognition_hint"] or ""
        recognition_fields = json_load(task["recognition_fields"], [])
        recognition_context = json_load(task["recognition_context"], {})
        multi_batch_id = task["multi_batch_id"]
        crop_ready = bool(task["crop_ready"])
        reanalysis_question_id = task["reanalysis_question_id"]
    if multi_batch_id and not crop_ready:
        try:
            await _prepare_multi_batch(task_id, multi_batch_id)
        except Exception as exc:
            message = str(exc)[:1000]
            category = error_category(exc)
            with db() as conn:
                conn.execute(
                    "UPDATE ai_tasks SET status='failed',error=?,error_category=?,updated_at=? WHERE multi_batch_id=? AND status!='cancelled'",
                    (message, category, now_iso(), multi_batch_id),
                )
            log.warning("multi-page crop failed batch=%s error=%s", multi_batch_id, message)
            return
    with db() as conn:
        rows = conn.execute("SELECT qi.* FROM question_images qi JOIN ai_task_images ati ON qi.id=ati.image_id WHERE ati.task_id=?", (task_id,)).fetchall()
        images = [
            {"id": row["id"], "path": row["original_path"], "mime_type": row["mime_type"], "image_type": row["image_type"], "edit": json_load(row['edit_json'], {})}
            for row in rows
        ]
        attempt = conn.execute('SELECT p.question_id,q.* FROM practice_attempts p JOIN questions q ON q.id=p.question_id WHERE p.task_id=?', (task_id,)).fetchone()
        claimed = conn.execute("UPDATE ai_tasks SET status='recognizing', attempts=attempts+1, error_category=NULL, updated_at=? WHERE id=? AND status IN ('uploaded','pending','pending_recognition','failed')", (now_iso(), task_id))
        if not claimed.rowcount:
            return
        if attempt:
            original_images = conn.execute('''SELECT * FROM question_images WHERE question_id=?
                AND image_type='question' AND task_id NOT IN (SELECT task_id FROM practice_attempts)''', (attempt['question_id'],)).fetchall()
            images = [{'id': row['id'], 'path': row['original_path'], 'mime_type': row['mime_type'], 'image_type': 'question', 'edit': json_load(row['edit_json'], {})} for row in original_images] + images
    try:
        with db() as conn:
            conn.execute("UPDATE ai_tasks SET status='pending_analysis', updated_at=? WHERE id=?", (now_iso(), task_id))
            conn.execute("UPDATE ai_tasks SET status='analyzing', updated_at=? WHERE id=?", (now_iso(), task_id))
        if attempt:
            result = await OpenAICompatibleClient().analyze_attempt(images, dict(attempt), task_id)
        else:
            # Keep third-party adapters and old test doubles compatible while
            # the built-in client receives the frozen context for scoped runs.
            kwargs = {"task_id": task_id, "question_label": question_label}
            if recognition_hint:
                kwargs["recognition_hint"] = recognition_hint
            if recognition_fields:
                kwargs["recognition_fields"] = recognition_fields
                parameters = inspect.signature(OpenAICompatibleClient.recognize_and_analyze).parameters
                if "recognition_context" in parameters:
                    kwargs["recognition_context"] = recognition_context
            result = await OpenAICompatibleClient().recognize_and_analyze(images, **kwargs)
        raw = result.pop("_raw_response", None)
        meta = result.pop("_meta", None)
        if meta:
            log.info("task %s analysis finished elapsed_ms=%s attempts=%s", task_id, meta.get("elapsed_ms"), meta.get("attempts"))
        with db() as conn:
            current = conn.execute('SELECT status FROM ai_tasks WHERE id=?', (task_id,)).fetchone()
            if not current or current['status'] == 'cancelled':
                return
            conn.execute("INSERT OR REPLACE INTO ai_results(task_id,result_json,raw_response,created_at,meta_json) VALUES(?,?,?,?,?)", (task_id, json.dumps(result, ensure_ascii=False), json.dumps(raw, ensure_ascii=False) if raw else None, now_iso(), json.dumps(meta, ensure_ascii=False) if meta else None))
            conn.execute("UPDATE ai_tasks SET status='recognition_completed', error_category=NULL, updated_at=? WHERE id=?", (now_iso(), task_id))
            conn.execute("UPDATE ai_tasks SET status='analysis_completed', error_category=NULL, updated_at=? WHERE id=?", (now_iso(), task_id))
        # Archive every successful result immediately.  A low score is retained
        # as a review marker and never blocks the user's upload queue.
        from ..routers.questions import QuestionIn, save_question
        score = result.get("confidence")
        try:
            score_value = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score_value = 0.0
        if not reanalysis_question_id:
            result["needs_review"] = score_value < settings.ai_confidence_threshold
            result["task_id"] = task_id
        with db() as conn:
            conn.execute("UPDATE ai_results SET result_json=? WHERE task_id=?", (json.dumps(result, ensure_ascii=False), task_id))
        if reanalysis_question_id:
            # A reanalysis is deliberately isolated from the original wrong
            # question.  Its result is shown for comparison and is only
            # written to the question when the user explicitly applies fields.
            with db() as conn:
                conn.execute("UPDATE ai_tasks SET status='reanalysis_completed', error=NULL, error_category=NULL, updated_at=? WHERE id=?", (now_iso(), task_id))
            return
        if not attempt:
            save_question(QuestionIn.model_validate(result))
        else:
            with db() as conn:
                conn.execute("UPDATE ai_tasks SET status='waiting_confirmation', error=NULL, error_category=NULL, updated_at=? WHERE id=?", (now_iso(), task_id))
            return
        with db() as conn:
            conn.execute("UPDATE ai_tasks SET status='confirmed', error=NULL, error_category=NULL, updated_at=? WHERE id=?", (now_iso(), task_id))
    except Exception as exc:
        category = error_category(exc)
        with db() as conn:
            conn.execute("UPDATE ai_tasks SET status='failed', error=?, error_category=?, updated_at=? WHERE id=? AND status!='cancelled'", (str(exc)[:1000], category, now_iso(), task_id))
        log.warning("task %s failed error=%s message=%s", task_id, type(exc).__name__, str(exc)[:300])


class TaskWorker:
    def __init__(self) -> None:
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.concurrency = max(1, int(settings.ai_concurrency))
        self.semaphore = asyncio.Semaphore(self.concurrency)

    async def _process_with_limit(self, task_id: int) -> None:
        async with self.semaphore:
            await process_task(task_id)

    async def run(self) -> None:
        ticks = 0
        while not self.stop_event.is_set():
            with db() as conn:
                rows = conn.execute("SELECT id FROM ai_tasks WHERE status IN ('pending_recognition','uploaded','pending') ORDER BY id LIMIT ?", (self.concurrency,)).fetchall()
            if rows:
                await asyncio.gather(*(self._process_with_limit(row["id"]) for row in rows))
            ticks += 1
            if ticks == 1 or ticks % 150 == 0:  # 启动时检查一次，之后约每 5 分钟检查
                try:
                    await asyncio.to_thread(maybe_auto_backup)
                except Exception:
                    log.warning("auto backup check failed", exc_info=True)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        # 每次启动都重建事件，否则同一个进程内重启后队列不会再消费任务。
        self.stop_event = asyncio.Event()
        await asyncio.to_thread(requeue_orphans)
        self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            await self.task
