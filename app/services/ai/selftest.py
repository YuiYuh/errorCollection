"""视觉能力自检：渲染已知答案的算式图片，验证当前模型能否正确读图。"""
import json
import logging
import random
import re
from pathlib import Path

from ...config import settings
from .openai_compatible import OpenAICompatibleClient

log = logging.getLogger(__name__)
PROMPT = (
    "读出图片中的算式，并给出计算结果。只输出JSON："
    '{"expression":"算式原文","value":"计算结果"}。不要解释，不要输出其他文字。'
)


def _font(size: int):
    from PIL import ImageFont

    for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_expression(expression: str, path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (900, 320), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 60), expression, fill="black", font=_font(96))
    draw.text((60, 210), "请读出上面的算式", fill="#444444", font=_font(28))
    image.save(path, format="PNG")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).replace("×", "*").replace("÷", "/").replace("−", "-")


def _make_case(rng: random.Random) -> tuple[str, str]:
    a, b = rng.randint(11, 89), rng.randint(2, 9)
    if rng.random() < 0.5:
        return f"{a} * {b}", str(a * b)
    return f"{a} + {b}", str(a + b)


async def run_selftest(cases: int = 3, task_id: int | None = None) -> dict:
    folder = settings.data_dir / "selftest"
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random()
    client = OpenAICompatibleClient()
    results = []
    try:
        for index in range(cases):
            expression, expected = _make_case(rng)
            path = folder / f"case-{index + 1}.png"
            render_expression(expression, path)
            item = {"expression": expression, "expected": expected, "ok": False, "answer": "", "error": ""}
            try:
                response = await client.ask_json(PROMPT, str(path), "image/png", task_id=task_id)
                raw = response["content"]
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    value = {"expression": raw, "value": raw}
                item["answer"] = f"{value.get('expression', '')} = {value.get('value', '')}"
                got_expression = _normalize(value.get("expression", ""))
                got_value = _normalize(value.get("value", ""))
                item["ok"] = _normalize(expression) in got_expression and got_value == expected
                item["elapsed_ms"] = response["meta"]["elapsed_ms"]
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            results.append(item)
    finally:
        for path in folder.glob("case-*.png"):
            path.unlink(missing_ok=True)
    passed = sum(1 for item in results if item["ok"])
    return {
        "cases": len(results),
        "passed": passed,
        "accuracy": round(passed / len(results), 4) if results else 0.0,
        "model": settings.ai_model,
        "results": results,
    }
