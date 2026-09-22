"""图片预处理：发送给视觉模型前按配置压缩，原图始终保留在磁盘。"""
import io
import logging
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)


def edit_image(path, edit):
    """Apply EXIF orientation, clockwise rotation, then normalized crop. Never overwrite source."""
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        image = image.rotate(-edit.get('rotation', 0), expand=True)
        x, y = edit.get('x', 0), edit.get('y', 0)
        w, h = edit.get('width', 1), edit.get('height', 1)
        left, top = int(x * image.width), int(y * image.height)
        right, bottom = round((x + w) * image.width), round((y + h) * image.height)
        if right <= left or bottom <= top:
            raise ValueError('裁剪范围太小，请重新选择')
        image = image.crop((left, top, right, bottom))
        if settings.ai_image_compress:
            image.thumbnail((settings.ai_image_max_edge, settings.ai_image_max_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=95)
        return buffer.getvalue(), 'image/jpeg', True


def prepare_for_ai(path: str | Path, mime_type: str | None = None) -> tuple[bytes, str, bool]:
    """返回 (字节, mime, 是否已压缩)。任何失败都退回原始字节，不影响识别。"""
    source = Path(path)
    raw = source.read_bytes()
    if not settings.ai_image_compress:
        return raw, mime_type or "image/jpeg", False
    if source.suffix.lower() in {".heic", ".heif"}:
        # Pillow 默认不带 HEIC 解码器，交给模型端处理原始数据。
        return raw, mime_type or "image/heic", False
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return raw, mime_type or "image/jpeg", False
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            longest = max(image.size)
            if longest > settings.ai_image_max_edge:
                ratio = settings.ai_image_max_edge / longest
                image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=settings.ai_image_jpeg_quality, optimize=True)
        compressed = buffer.getvalue()
        if len(compressed) >= len(raw):
            return raw, mime_type or "image/jpeg", False
        return compressed, "image/jpeg", True
    except Exception as exc:
        log.warning("image compression skipped path=%s error=%s", source.name, type(exc).__name__)
        return raw, mime_type or "image/jpeg", False
