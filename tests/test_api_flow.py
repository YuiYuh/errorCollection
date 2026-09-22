import asyncio
import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.services.worker import process_task
from app.config import settings
from app.db import db


def jpeg_bytes(size=(600, 800)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def upload(client: TestClient, filename: str = "question.jpg", types: str = "question") -> int:
    response = client.post(
        "/api/tasks/upload",
        files=[("files", (filename, jpeg_bytes(), "image/jpeg"))],
        data={"image_types": types},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_upload_saves_original_image_and_serves_it(client):
    task_id = upload(client)
    task = client.get(f"/api/tasks/{task_id}").json()
    assert len(task["images"]) == 1
    image_id = task["images"][0]["id"]
    assert client.get(f"/api/tasks/images/{image_id}").status_code == 200


def test_failed_upload_removes_files_empty_folders_and_database_rows(client):
    response = client.post(
        "/api/tasks/upload",
        files=[
            ("files", ("question.jpg", jpeg_bytes(), "image/jpeg")),
            ("files", ("bad.txt", b"not an image", "text/plain")),
        ],
        data={"image_types": ["question", "question"]},
    )
    assert response.status_code == 415
    assert not list(Path(settings.upload_dir).rglob("*"))
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM question_images").fetchone()[0] == 0


def test_missing_api_key_fails_task_but_keeps_image(client):
    task_id = upload(client)
    asyncio.run(process_task(task_id))
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "failed"
    assert "AI_API_KEY" in task["error"]
    assert client.get(f"/api/tasks/images/{task['images'][0]['id']}").status_code == 200


def test_review_feedback_drives_accuracy_stats(client):
    task_id = upload(client)
    assert client.post(
        f"/api/tasks/{task_id}/feedback",
        json={"recognition_rating": "correct", "correction_rating": "partial"},
    ).status_code == 200
    stats = client.get("/api/stats/ai-accuracy").json()
    assert stats["reviewed_tasks"] == 1
    assert stats["recognition"]["correct"] == 1
    assert stats["recognition"]["rate"] == 1.0
    assert stats["correction"]["partial"] == 1
    assert stats["correction"]["rate"] == 0.5


def test_feedback_rejects_unknown_rating(client):
    task_id = upload(client)
    assert client.post(
        f"/api/tasks/{task_id}/feedback",
        json={"recognition_rating": "perfect", "correction_rating": "correct"},
    ).status_code == 422


def test_task_payload_exposes_ai_meta_field(client):
    task_id = upload(client)
    task = client.get(f"/api/tasks/{task_id}").json()
    assert "ai_meta" in task


def test_worker_passes_readable_image_paths_to_client(client, monkeypatch):
    """回归：后台任务曾把 original_path 当成 path 传给AI客户端，导致配置Key后必然失败。"""
    from app.services.ai import openai_compatible

    captured = {}

    async def fake_recognize(self, images, task_id=None, question_label=""):
        captured["images"] = images
        captured["question_label"] = question_label
        return {"subject": "math1", "first_error_step": "x", "_meta": {"model": "m"}, "_raw_response": None}

    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "recognize_and_analyze", fake_recognize)
    task_id = upload(client)
    asyncio.run(process_task(task_id))
    assert captured["images"], "任务应当把图片传给AI客户端"
    assert all(item["path"] for item in captured["images"])
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "confirmed"


def test_question_label_flows_from_upload_to_question(client, monkeypatch):
    from app.services.ai import openai_compatible

    captured = {}

    async def fake_recognize(self, images, task_id=None, question_label=""):
        captured["label"] = question_label
        return {"subject": "math_calculus", "question_label": question_label, "first_error_step": "x", "_meta": {"model": "m"}, "_raw_response": None}

    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "recognize_and_analyze", fake_recognize)
    response = client.post(
        "/api/tasks/upload",
        files=[("files", ("q.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"image_types": "question", "question_label": "第3题"},
    )
    task_id = response.json()["id"]
    assert client.get(f"/api/tasks/{task_id}").json()["question_label"] == "第3题"
    asyncio.run(process_task(task_id))
    assert captured["label"] == "第3题"
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["result"]["question_label"] == "第3题"
    question = client.post(
        f"/api/questions/{task_id}/confirm",
        json={**task["result"], "question_label": "第3题", "task_id": task_id},
    ).json()
    assert question["question_label"] == "第3题"
    assert client.get("/api/questions").json()["items"][0]["question_label"] == "第3题"


def test_multi_question_upload_creates_independent_tasks_sharing_originals(client):
    response = client.post(
        "/api/tasks/upload",
        files=[("files", ("page.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"image_types": "question", "multi_question": "true", "question_labels": "1, 3-4"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["task_count"] == 3
    assert len(data["ids"]) == 3
    tasks = [client.get(f"/api/tasks/{task_id}").json() for task_id in data["ids"]]
    assert [task["question_label"] for task in tasks] == ["第1题", "第3题", "第4题"]
    assert all(len(task["images"]) == 1 for task in tasks)
    assert len({task["images"][0]["id"] for task in tasks}) == 3
    with db() as conn:
        paths = conn.execute("SELECT original_path FROM question_images ORDER BY id").fetchall()
    assert len({row["original_path"] for row in paths}) == 1


def test_multi_question_tasks_are_analyzed_with_their_own_labels(client, monkeypatch):
    from app.services.ai import openai_compatible

    labels = []

    async def fake_recognize(self, images, task_id=None, question_label=""):
        labels.append(question_label)
        return {"subject": "math_calculus", "question_text": f"题目 {question_label}"}

    async def fake_locate(self, images, question_labels, task_id=None):
        return {"questions": [{"question_label": label, "regions": [{"image_index": 0, "x": 0.05, "y": 0.1, "width": 0.8, "height": 0.2}]} for label in question_labels]}

    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "recognize_and_analyze", fake_recognize)
    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "locate_questions", fake_locate)
    response = client.post(
        "/api/tasks/upload",
        files=[("files", ("page.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"image_types": "question", "multi_question": "true", "question_labels": "2,4"},
    )
    task_ids = response.json()["ids"]
    for task_id in task_ids:
        asyncio.run(process_task(task_id))
    assert labels == ["第2题", "第4题"]
    assert all(client.get(f"/api/tasks/{task_id}").json()["status"] == "confirmed" for task_id in task_ids)


def test_multi_question_failure_keeps_other_results_and_retries_only_failed(client, monkeypatch):
    from app.services.ai import openai_compatible

    async def fake_locate(self, images, question_labels, task_id=None):
        return {"questions": [{"question_label": label, "regions": [{"image_index": 0, "x": 0.05, "y": 0.1, "width": 0.8, "height": 0.2}]} for label in question_labels]}

    async def fake_recognize(self, images, task_id=None, question_label=""):
        if question_label == "第2题":
            raise RuntimeError("第2题识别失败")
        return {"subject": "math_calculus", "question_label": question_label, "question_text": question_label}

    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "locate_questions", fake_locate)
    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "recognize_and_analyze", fake_recognize)
    response = client.post(
        "/api/tasks/upload",
        files=[("files", ("page.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"image_types": "question", "multi_question": "true", "question_labels": "2,4"},
    )
    task_ids = response.json()["ids"]
    for task_id in task_ids:
        asyncio.run(process_task(task_id))
    first = client.get(f"/api/tasks/{task_ids[0]}").json()
    second = client.get(f"/api/tasks/{task_ids[1]}").json()
    assert first["status"] == "failed"
    assert second["status"] == "confirmed"
    assert first["batch"]["progress_label"] == "已完成 1/2 题，失败 1 题"
    assert first["batch"]["failed_ids"] == [task_ids[0]]
    retry = client.post(f"/api/tasks/batches/{first['multi_batch_id']}/retry-failed")
    assert retry.status_code == 200
    assert retry.json()["updated"] == [task_ids[0]]
    assert client.get(f"/api/tasks/{task_ids[1]}").json()["status"] == "confirmed"
    assert client.get(f"/api/tasks/{task_ids[0]}").json()["status"] == "pending_recognition"


def test_multi_question_locator_failure_is_reused_by_waiting_tasks(client, monkeypatch):
    from app.services.ai import openai_compatible

    calls = 0

    async def fail_locate(self, images, question_labels, task_id=None):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        raise RuntimeError("定位接口不可用")

    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "locate_questions", fail_locate)
    response = client.post(
        "/api/tasks/upload",
        files=[("files", ("page.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"image_types": "question", "multi_question": "true", "question_labels": "9,10"},
    )
    task_ids = response.json()["ids"]
    async def run_batch():
        await asyncio.gather(*(process_task(task_id) for task_id in task_ids))

    asyncio.run(run_batch())
    assert calls == 1
    assert all(client.get(f"/api/tasks/{task_id}").json()["status"] == "failed" for task_id in task_ids)


def test_multi_question_confirmation_is_independent_per_task(client, monkeypatch):
    from app.services.ai import openai_compatible

    async def fake_recognize(self, images, task_id=None, question_label=""):
        return {"subject": "math_calculus", "question_text": f"完整题目 {question_label}", "question_label": question_label}

    async def fake_locate(self, images, question_labels, task_id=None):
        return {"questions": [{"question_label": label, "regions": [{"image_index": 0, "x": 0.05, "y": 0.1, "width": 0.8, "height": 0.2}]} for label in question_labels]}

    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "recognize_and_analyze", fake_recognize)
    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "locate_questions", fake_locate)
    response = client.post(
        "/api/tasks/upload",
        files=[("files", ("page.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"image_types": "question", "multi_question": "true", "question_labels": "2,4"},
    )
    task_ids = response.json()["ids"]
    for task_id in task_ids:
        asyncio.run(process_task(task_id))
    first = client.get(f"/api/tasks/{task_ids[0]}").json()
    saved = client.post(f"/api/questions/{task_ids[0]}/confirm", json={**first["result"], "task_id": task_ids[0]}).json()
    assert saved["question_label"] == "第2题"
    assert client.get(f"/api/tasks/{task_ids[0]}").json()["status"] == "confirmed"
    assert client.get(f"/api/tasks/{task_ids[1]}").json()["status"] == "confirmed"
    assert client.get("/api/questions").json()["total"] == 2


def test_multi_question_crops_each_task_to_its_located_page(client, monkeypatch):
    from app.services.ai import openai_compatible

    captured = {}

    async def fake_locate(self, images, question_labels, task_id=None):
        return {"questions": [
            {"question_label": "2", "regions": [{"image_index": 0, "x": 0.10, "y": 0.10, "width": 0.30, "height": 0.20}]},
            {"question_label": "第4题", "regions": [{"image_index": 1, "x": 0.40, "y": 0.20, "width": 0.25, "height": 0.30}]},
        ]}

    async def fake_recognize(self, images, task_id=None, question_label=""):
        captured[question_label] = images
        return {"subject": "math_calculus", "question_label": question_label, "question_text": question_label}

    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "locate_questions", fake_locate)
    monkeypatch.setattr(openai_compatible.OpenAICompatibleClient, "recognize_and_analyze", fake_recognize)
    response = client.post(
        "/api/tasks/upload",
        files=[
            ("files", ("page-1.jpg", jpeg_bytes(), "image/jpeg")),
            ("files", ("page-2.jpg", jpeg_bytes(), "image/jpeg")),
        ],
        data={"image_types": ["question", "question"], "multi_question": "true", "question_labels": "2,4"},
    )
    task_ids = response.json()["ids"]
    for task_id in task_ids:
        asyncio.run(process_task(task_id))

    assert [len(captured["第2题"]), len(captured["第4题"])] == [1, 1]
    assert captured["第2题"][0]["path"] != captured["第4题"][0]["path"]
    with db() as conn:
        rows = conn.execute("SELECT task_id,image_type,edit_json FROM question_images ORDER BY task_id,id").fetchall()
    first = [row for row in rows if row["task_id"] == task_ids[0]]
    second = [row for row in rows if row["task_id"] == task_ids[1]]
    assert len(first) == len(second) == 1
    assert first[0]["edit_json"] != second[0]["edit_json"]


def test_multi_question_upload_requires_labels_and_limits_ranges(client):
    files = [("files", ("page.jpg", jpeg_bytes(), "image/jpeg"))]
    missing = client.post("/api/tasks/upload", files=files, data={"multi_question": "true", "question_labels": ""})
    assert missing.status_code == 422
    too_many = client.post("/api/tasks/upload", files=files, data={"multi_question": "true", "question_labels": "1-21"})
    assert too_many.status_code == 422


def test_task_list_shows_recent_tasks_with_images(client):
    task_id = upload(client)
    items = client.get("/api/tasks").json()["items"]
    assert items
    assert items[0]["id"] == task_id
    assert len(items[0]["images"]) == 1
    assert items[0]["question_id"] is None


def test_confirm_marks_task_as_confirmed(client):
    task_id = upload(client)
    client.post(
        f"/api/questions/{task_id}/confirm",
        json={"subject": "math_calculus", "question_text": "求极限", "task_id": task_id},
    )
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "confirmed"
    listed = client.get("/api/tasks").json()["items"][0]
    assert listed["status"] == "confirmed"
    assert listed["question_id"]


def test_task_links_use_question_id_not_image_id(client):
    # Diverge both sequences, including a multi-image task. A truthiness check
    # misses the bug because an image id also looks like a valid question id.
    client.post('/api/questions', json={'question_text': 'unrelated'})
    response = client.post(
        '/api/tasks/upload',
        files=[('files', (f'{i}.jpg', jpeg_bytes(), 'image/jpeg')) for i in range(3)],
    )
    task_id = response.json()['id']
    question = client.post(
        f'/api/questions/{task_id}/confirm',
        json={'task_id': task_id, 'question_text': 'linked question'},
    ).json()
    listed = client.get('/api/tasks').json()['items'][0]
    assert listed['question_id'] == question['id']
    detail = client.get(f"/api/questions/{listed['question_id']}")
    assert detail.status_code == 200
    assert detail.json()['question_text'] == 'linked question'
    assert len(detail.json()['images']) == 3


def test_questions_include_stable_daily_numbers(client):
    first = client.post('/api/questions', json={'question_text': 'first'}).json()
    second = client.post('/api/questions', json={'question_text': 'second'}).json()
    listed = {item['id']: item for item in client.get('/api/questions').json()['items']}
    assert listed[first['id']]['daily_number'] == 1
    assert listed[second['id']]['daily_number'] == 2
    assert client.get(f"/api/questions/{second['id']}").json()['daily_number'] == 2


def test_deleted_question_has_no_task_detail_link(client):
    task_id = upload(client)
    question = client.post(
        f'/api/questions/{task_id}/confirm', json={'task_id': task_id},
    ).json()
    client.delete(f"/api/questions/{question['id']}")
    listed = client.get('/api/tasks').json()['items'][0]
    assert listed['question_id'] is None
    assert client.get(f"/api/tasks/images/{listed['images'][0]['id']}").status_code == 200


def test_retry_can_set_question_label_afterwards(client):
    task_id = upload(client)
    response = client.post(f"/api/tasks/{task_id}/retry", json={"question_label": "第3题"})
    assert response.status_code == 200
    body = response.json()
    assert body["question_label"] == "第3题"
    assert body["status"] == "pending_recognition"
    assert client.get(f"/api/tasks/{task_id}").json()["question_label"] == "第3题"


def test_retry_without_body_keeps_existing_label(client):
    task_id = client.post(
        "/api/tasks/upload",
        files=[("files", ("q.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"image_types": "question", "question_label": "第5题"},
    ).json()["id"]
    response = client.post(f"/api/tasks/{task_id}/retry")
    assert response.status_code == 200
    assert response.json()["question_label"] == "第5题"


def test_confirm_accepts_nulls_from_ai_result(client):
    """回归：AI 把「没有错误」写成 null 时，确认接口曾返回 422，前端只显示 [object Object]。"""
    task_id = upload(client)
    payload = {
        "question_label": "第3题",
        "subject": "math_calculus",
        "first_error_step": None,
        "reference_solution_latex": None,
        "error_types": None,
        "knowledge_points": "二重积分，对称性",
        "difficulty": None,
        "confidence": None,
        "task_id": task_id,
    }
    response = client.post(f"/api/questions/{task_id}/confirm", json=payload)
    assert response.status_code == 200, response.text
    question = response.json()
    assert question["first_error_step"] == ""
    assert question["knowledge_points"] == ["二重积分"]
    assert question["chapter"] == "多元函数积分学"
    assert question["difficulty"] == 3
    assert question["confidence"] == 0


def test_confirm_rejects_string_for_list_field_gracefully(client):
    task_id = upload(client)
    response = client.post(
        f"/api/questions/{task_id}/confirm",
        json={"subject": "ds", "error_types": "条件与边界", "task_id": task_id},
    )
    assert response.status_code == 200
    assert response.json()["error_types"] == ["条件与边界"]


def test_confirm_task_id_collision_does_not_overwrite_existing_question(client):
    """任务 ID 和旧错题 ID 相同也必须创建/更新任务自己的错题。"""
    first_task = upload(client)
    first = client.post(
        f"/api/questions/{first_task}/confirm",
        json={"subject": "math_calculus", "question_text": "旧题", "task_id": first_task},
    ).json()
    second_task = upload(client)
    # 在独立测试库里第二个任务通常为 2，而旧错题为 1；显式构造同 ID
    # 关系所覆盖的场景：确认接口只能依据 task_id 查找关联记录。
    old_question_id = first["id"]
    response = client.post(
        f"/api/questions/{old_question_id}/confirm",
        json={"subject": "ds", "question_text": "新题", "task_id": second_task},
    )
    assert response.status_code == 200
    rows = client.get("/api/questions").json()["items"]
    assert {row["question_text"] for row in rows} == {"旧题", "新题"}
