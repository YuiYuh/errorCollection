"""代理解析：让服务器端 AI 请求能走本机系统代理，密钥仍然只存在于服务端。"""
import logging

from ...config import settings

log = logging.getLogger(__name__)


def read_system_proxy() -> str | None:
    """读取 Windows 系统代理设置，仅在启用时返回。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if not enabled:
                return None
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()
    except OSError:
        return None
    if not server:
        return None
    if "=" in server:  # 形如 http=host:port;https=host:port
        parts = dict(item.split("=", 1) for item in server.split(";") if "=" in item)
        server = parts.get("https") or parts.get("http") or ""
    if not server:
        return None
    return server if "://" in server else f"http://{server}"


def resolve_ai_proxy() -> str | None:
    configured = (settings.ai_proxy or "").strip()
    if not configured or configured.lower() in {"none", "off", "false"}:
        return None
    if configured.lower() == "auto":
        proxy = read_system_proxy()
        log.info("AI proxy auto-detected=%s", proxy or "none")
        return proxy
    return configured if "://" in configured else f"http://{configured}"
