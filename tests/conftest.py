import json
import shutil
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


class FakeOpenAI(BaseHTTPRequestHandler):
    """本地假的 OpenAI 兼容接口，用于验证请求内容而不消耗真实额度。"""

    requests: list[dict] = []
    auth_headers: list[str] = []
    response_content: str = "{}"
    reject_json_mode_once = False

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeOpenAI.requests.append(body)
        FakeOpenAI.auth_headers.append(self.headers.get("Authorization", ""))
        if FakeOpenAI.reject_json_mode_once:
            FakeOpenAI.reject_json_mode_once = False
            message = b'{"error":{"message":"response_format is not supported"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            return
        response = json.dumps({
            "choices": [{"message": {"content": FakeOpenAI.response_content}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *args):
        return


class FakeProvider:
    def __init__(self, url: str):
        self.url = url

    @property
    def requests(self) -> list[dict]:
        return FakeOpenAI.requests


@pytest.fixture()
def fake_openai_server():
    server = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeOpenAI.requests = []
    FakeOpenAI.auth_headers = []
    FakeOpenAI.reject_json_mode_once = False
    FakeOpenAI.response_content = "{}"
    yield FakeProvider(f"http://127.0.0.1:{server.server_port}/v1")
    server.shutdown()


@pytest.fixture()
def work_dir():
    # 沙箱环境不允许写系统临时目录，测试文件放在项目内的临时目录并及时清理。
    # 每个测试用独立子目录，避免 SQLite 文件被占用时清理失败导致数据串场。
    root = Path(__file__).resolve().parent.parent / ".test-tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / uuid.uuid4().hex[:10]
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def client(monkeypatch, work_dir):
    """使用独立临时目录的接口客户端，不会碰到真实数据库和图片。"""
    from fastapi.testclient import TestClient

    from app.config import settings

    for name in ("data_dir", "upload_dir", "backup_dir", "export_dir", "log_dir"):
        monkeypatch.setattr(settings, name, work_dir / name.replace("_dir", ""))
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_image_compress", True)
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
