import httpx
import pytest

from app.config import settings
from app.services.ai.network import resolve_ai_proxy
from app.services.ai.openai_compatible import _friendly_error


@pytest.mark.parametrize(
    "configured,expected",
    [
        ("", None),
        ("none", None),
        ("off", None),
        ("http://127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("socks5://127.0.0.1:7891", "socks5://127.0.0.1:7891"),
    ],
)
def test_proxy_setting_is_normalized(monkeypatch, configured, expected):
    monkeypatch.setattr(settings, "ai_proxy", configured)
    assert resolve_ai_proxy() == expected


def test_auto_proxy_uses_system_setting(monkeypatch):
    monkeypatch.setattr(settings, "ai_proxy", "auto")
    monkeypatch.setattr("app.services.ai.network.read_system_proxy", lambda: "http://127.0.0.1:7890")
    assert resolve_ai_proxy() == "http://127.0.0.1:7890"


def test_connection_error_does_not_assume_vpn_is_required():
    message = _friendly_error(httpx.ConnectTimeout("timed out"))
    assert "中转站" in message and "原图已保存" in message
    assert "VPN" not in message


def test_permission_failure_is_distinguished_from_provider_timeout():
    blocked = httpx.ConnectError('connection failed')
    blocked.__cause__ = OSError('[WinError 10013] socket access denied')
    assert '系统网络权限限制' in _friendly_error(blocked)
    assert '响应超时' in _friendly_error(httpx.ReadTimeout('timed out'))


def test_http_status_messages_are_actionable():
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    unauthorized = httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))
    not_found = httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
    assert "API Key 无效" in _friendly_error(unauthorized)
    assert "AI_MODEL" in _friendly_error(not_found)
