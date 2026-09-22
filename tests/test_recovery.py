"""队列恢复：服务重启后，卡在识别中的任务必须重新排队，不能永久卡死。"""
import asyncio
import io

from fastapi.testclient import TestClient
from PIL import Image

from app.db import db, now_iso
from app.services.worker import ORPHAN_STATUSES, requeue_orphans


def jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 500), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def make_task(client: TestClient) -> int:
    return client.post(
        "/api/tasks/upload",
        files=[("files", ("q.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"image_types": "question"},
    ).json()["id"]


def test_orphaned_tasks_are_requeued(client):
    task_id = make_task(client)
    with db() as conn:
        conn.execute("UPDATE ai_tasks SET status='analyzing' WHERE id=?", (task_id,))
    assert requeue_orphans() == 1
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "pending_recognition"
    assert "重新排队" in task["error"]


def test_all_in_flight_statuses_are_covered(client):
    ids = [make_task(client) for _ in ORPHAN_STATUSES]
    with db() as conn:
        for task_id, status in zip(ids, ORPHAN_STATUSES):
            conn.execute("UPDATE ai_tasks SET status=? WHERE id=?", (status, task_id))
    assert requeue_orphans() == len(ORPHAN_STATUSES)
    for task_id in ids:
        assert client.get(f"/api/tasks/{task_id}").json()["status"] == "pending_recognition"


def test_finished_tasks_are_not_requeued(client):
    done = make_task(client)
    failed = make_task(client)
    with db() as conn:
        conn.execute("UPDATE ai_tasks SET status='waiting_confirmation' WHERE id=?", (done,))
        conn.execute("UPDATE ai_tasks SET status='failed', error='原来的错误' WHERE id=?", (failed,))
    assert requeue_orphans() == 0
    assert client.get(f"/api/tasks/{done}").json()["status"] == "waiting_confirmation"
    assert client.get(f"/api/tasks/{failed}").json()["error"] == "原来的错误"


def test_retry_works_for_stuck_task(client):
    task_id = make_task(client)
    with db() as conn:
        conn.execute("UPDATE ai_tasks SET status='analyzing', updated_at=? WHERE id=?", (now_iso(), task_id))
    response = client.post(f"/api/tasks/{task_id}/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "pending_recognition"
