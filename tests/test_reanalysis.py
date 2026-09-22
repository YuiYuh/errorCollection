import asyncio
import io

from PIL import Image

from app.services.ai.openai_compatible import OpenAICompatibleClient
from app.services.worker import process_task


def photo() -> bytes:
    image = Image.new("RGB", (80, 50), "white")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def test_reanalysis_isolated_and_applies_selected_fields(client, monkeypatch):
    calls = []

    async def recognize(self, images, task_id=None, question_label="", recognition_hint=""):
        calls.append((task_id, recognition_hint, images[0].get("edit", {})))
        if len(calls) == 1:
            return {"question_text": "旧题目", "question_latex": "x^2", "knowledge_points": ["极限"]}
        return {"question_text": "新题目", "question_latex": "x^3", "knowledge_points": ["导数"]}

    monkeypatch.setattr(OpenAICompatibleClient, "recognize_and_analyze", recognize)
    uploaded = client.post("/api/tasks/upload", data={"image_types": "question"}, files=[("files", ("q.png", photo(), "image/png"))])
    parent_task = uploaded.json()["id"]
    asyncio.run(process_task(parent_task))
    parent = client.get(f"/api/tasks/{parent_task}").json()
    question_id = client.get("/api/tasks?include_practice=true").json()["items"][0]["question_id"]
    question = client.get(f"/api/questions/{question_id}").json()
    image_id = question["images"][0]["id"]

    created = client.post(f"/api/questions/{question['id']}/reanalyze", json={
        "recognition_hint": "第二行公式识别错了",
        "image_edits": [{"image_id": image_id, "x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}],
    })
    assert created.status_code == 200
    reanalysis_task = created.json()["task_id"]
    assert reanalysis_task != parent_task
    assert client.get(f"/api/tasks/{parent_task}").json()["result"]["question_text"] == "旧题目"

    asyncio.run(process_task(reanalysis_task))
    result_task = client.get(f"/api/tasks/{reanalysis_task}").json()
    assert result_task["status"] == "reanalysis_completed"
    assert calls[1][1] == "第二行公式识别错了"
    assert calls[1][2]["x"] == 0.1

    history = client.get(f"/api/questions/{question['id']}").json()["reanalysis_runs"]
    assert history[0]["result"]["question_text"] == "新题目"
    applied = client.post(f"/api/questions/{question['id']}/reanalyze/{reanalysis_task}/apply", json={"fields": ["question_text"]})
    assert applied.status_code == 200
    assert applied.json()["question"]["question_text"] == "新题目"
    assert applied.json()["question"]["question_latex"] == "x^2"
