import asyncio
import logging

import pytest

from app.config import settings
from conftest import FakeOpenAI
from app.services.ai.openai_compatible import OpenAICompatibleClient


@pytest.fixture()
def fake_openai(monkeypatch, work_dir, fake_openai_server):
    from PIL import Image

    FakeOpenAI.response_content = (
        '{"subject":"math_calculus","question_text":"求极限","first_error_step":"第2行漏掉负号",'
        '"recognition_confidence":0.9,"correction_confidence":0.8,"verification_status":"verified"}'
    )

    image_path = work_dir / "page.jpg"
    Image.new("RGB", (3000, 2000), "white").save(image_path, quality=95)

    monkeypatch.setattr(settings, "ai_base_url", fake_openai_server.url)
    monkeypatch.setattr(settings, "ai_api_key", "sk-test-secret-value")
    monkeypatch.setattr(settings, "ai_model", "test-vision-model")
    monkeypatch.setattr(settings, "ai_max_retries", 1)
    monkeypatch.setattr(settings, "ai_image_compress", True)
    monkeypatch.setattr(settings, "ai_image_max_edge", 800)
    yield {"path": str(image_path), "raw_size": image_path.stat().st_size}


def test_images_sent_as_vision_messages_and_compressed(fake_openai, caplog):
    with caplog.at_level(logging.INFO):
        result = asyncio.run(OpenAICompatibleClient().recognize_and_analyze(
            [{"path": fake_openai["path"], "mime_type": "image/jpeg", "image_type": "question"}], task_id=7,
        ))
    content = FakeOpenAI.requests[0]["messages"][0]["content"]
    image_parts = [part for part in content if part["type"] == "image_url"]
    assert len(image_parts) == 1, "图片必须作为标准 image_url 消息发送"
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert FakeOpenAI.requests[0]["model"] == "test-vision-model"
    assert result["first_error_step"] == "第2行漏掉负号"
    assert result["confidence"] == 0.8
    assert result["_meta"]["status_code"] == 200
    assert result["_meta"]["task_id"] == 7
    assert result["_meta"]["elapsed_ms"] >= 0
    sent = len(image_parts[0]["image_url"]["url"])
    assert sent < fake_openai["raw_size"], "发送前应压缩图片"
    logs = caplog.text
    assert "sk-test-secret-value" not in logs, "日志中绝不能出现API Key"
    assert "task=7" in logs


def test_retries_without_json_mode_when_provider_rejects_it(fake_openai):
    FakeOpenAI.reject_json_mode_once = True
    result = asyncio.run(OpenAICompatibleClient().recognize_and_analyze(
        [{"path": fake_openai["path"], "mime_type": "image/jpeg", "image_type": "question"}], task_id=8,
    ))
    assert len(FakeOpenAI.requests) == 2
    assert "response_format" in FakeOpenAI.requests[0]
    assert "response_format" not in FakeOpenAI.requests[1]
    assert result["subject"] == "math_calculus"


def test_scoped_recognition_keeps_result_sparse(fake_openai):
    FakeOpenAI.response_content = '{"question_text":"完整题干","question_latex":"x^2","subject":"math_calculus"}'
    result = asyncio.run(OpenAICompatibleClient().recognize_and_analyze(
        [{"path": fake_openai["path"], "mime_type": "image/jpeg", "image_type": "question"}],
        task_id=9, recognition_fields=["question_text"], recognition_context={"question_text": "旧摘要", "subject": "math_calculus"},
    ))
    assert result["question_text"] == "完整题干"
    assert "question_latex" not in result
    prompt = FakeOpenAI.requests[0]["messages"][0]["content"][0]["text"]
    assert "JSON 键只能是：question_text" in prompt
    assert "旧摘要" in prompt
