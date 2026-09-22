"""全链路验证：上传 → 后台队列 → 视觉API(本地假接口) → 待确认 → 确认入库 → 正确率统计。"""
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from conftest import FakeOpenAI

ANALYSIS = (
    '{"subject":"math_calculus","chapter":"极限","question_text":"求极限","question_latex":"\\\\lim_{x\\\\to0}\\\\frac{\\\\sin x}{x}",'
    '"first_error_step":"第2行漏掉负号","error_analysis":"符号错误","correct_approach":"先化简再取极限",'
    '"knowledge_points":["极限"],"error_types":["符号错误"],"uncertain_parts":[],'
    '"recognition_confidence":0.92,"correction_confidence":0.88,"verification_status":"verified"}'
)


@pytest.fixture()
def ai_client(monkeypatch, work_dir, fake_openai_server):
    for name in ("data_dir", "upload_dir", "backup_dir", "export_dir", "log_dir"):
        monkeypatch.setattr(settings, name, work_dir / name.replace("_dir", ""))
    monkeypatch.setattr(settings, "ai_base_url", fake_openai_server.url)
    monkeypatch.setattr(settings, "ai_api_key", "sk-e2e-secret")
    monkeypatch.setattr(settings, "ai_model", "e2e-vision-model")
    monkeypatch.setattr(settings, "ai_max_retries", 0)
    FakeOpenAI.response_content = ANALYSIS
    from app.main import app

    with TestClient(app) as client:
        yield client, fake_openai_server


def wait_for_task(client: TestClient, task_id: int, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    task = {}
    while time.time() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"waiting_confirmation", "failed", "cancelled"}:
            return task
        time.sleep(0.5)
    return task


def test_full_pipeline_from_upload_to_confirmed_question(ai_client, caplog):
    client, provider = ai_client
    buffer = io.BytesIO()
    Image.new("RGB", (800, 1000), "white").save(buffer, format="JPEG")
    response = client.post(
        "/api/tasks/upload",
        files=[("files", ("q.jpg", buffer.getvalue(), "image/jpeg"))],
        data={"image_types": "question"},
    )
    assert response.status_code == 200
    task_id = response.json()["id"]

    task = wait_for_task(client, task_id)
    assert task["status"] == "waiting_confirmation", task
    assert task["result"]["first_error_step"] == "第2行漏掉负号"
    assert task["ai_meta"]["model"] == "e2e-vision-model"

    # 关键证据：模型确实收到标准视觉消息
    content = provider.requests[-1]["messages"][0]["content"]
    assert any(part["type"] == "image_url" for part in content)
    assert provider.requests[-1]["model"] == "e2e-vision-model"
    assert "sk-e2e-secret" not in caplog.text

    confirm = client.post(
        f"/api/questions/{task_id}/confirm",
        json={**task["result"], "task_id": task_id},
    )
    assert confirm.status_code == 200, confirm.text
    question = confirm.json()
    assert question["question_text"] == "求极限"
    assert question["status"] == "confirmed"

    listed = client.get("/api/questions").json()
    assert listed["total"] == 1
    assert client.get("/api/reviews/today").json()["total"] == 0  # 明天才到期

    assert client.post(
        f"/api/tasks/{task_id}/feedback",
        json={"recognition_rating": "correct", "correction_rating": "correct"},
    ).status_code == 200
    accuracy = client.get("/api/stats/ai-accuracy").json()
    assert accuracy["recognition"]["rate"] == 1.0
    assert accuracy["correction"]["rate"] == 1.0
    assert accuracy["confirmed_questions"] == 1
