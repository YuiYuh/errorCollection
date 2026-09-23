import copy
import json
import re
from typing import Any

from ...config import settings
from ...error_types import primary_error_type
from ...subjects import SUBJECTS, normalize_subject
from ...syllabus import normalize_knowledge

FIELDS = {
    "question_label": "",
    "subject": "unknown", "section": "", "chapter": "", "question_type": "",
    "question_mode": "unknown", "question_text": "", "question_latex": "",
    "user_solution_latex": "", "reference_solution_latex": "", "code_blocks": [],
    "first_error_step": "", "error_analysis": "", "correct_approach": "",
    "error_types": [], "knowledge_points": [], "tags": [], "key_lesson": "", "review_prompt": "",
    "difficulty": 3, "is_typical": False, "needs_priority_review": False,
    "uncertain_parts": [], "confidence": 0.0,
    "recognition_confidence": 0.0, "correction_confidence": 0.0,
    "field_confidence": {},
    "verification_status": "unverified", "verification_notes": "",
}

FIELD_CONFIDENCE_DEFAULTS = ("question", "formula", "solution", "reference", "classification")

LATEX_FIELDS = ("question_latex", "user_solution_latex", "reference_solution_latex")
NON_MATH_SUBJECTS = {"ds", "coa", "os", "cn"}


def _auto_tags(result: dict[str, Any]) -> list[str]:
    """Small fallback for providers that omit the optional tags field."""
    text = " ".join(str(result.get(key) or "") for key in (
        "question_text", "question_latex", "user_solution_latex", "correct_approach", "error_analysis",
    ))
    candidates = (
        ("对称性", "对称"), ("极坐标", "极坐标"), ("参数方程", "参数方程"),
        ("递归", "递归"), ("二叉树", "二叉树"), ("动态规划", "动态规划"),
        ("边界条件", "边界"), ("归纳法", "归纳"), ("反证法", "反证"),
        ("链表", "链表"), ("最短路径", "最短路径"), ("排序", "排序"),
        ("死锁", "死锁"), ("分页", "分页"), ("缓存", "缓存"),
        ("TCP", "TCP"), ("UDP", "UDP"),
    )
    excluded = set(result.get("knowledge_points") or []) | set(result.get("error_types") or [])
    return [label for label, marker in candidates if marker.lower() in text.lower() and label not in excluded][:3]


def _decode_json(raw: str) -> Any:
    """Decode normal JSON and the fenced JSON some compatible models return."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json\n"):
            text = text[5:].lstrip()
    return json.loads(text)


def _text_value(value: Any, default: str = "") -> str:
    """Keep model fields JSON-safe and renderable even when the model uses an object."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _looks_like_code_or_structure(value: Any) -> bool:
    """Detect content that must stay readable text instead of going through KaTeX."""
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    markers = (
        "```", "#include", "public static", "int main", "void ", "struct ",
        "class ", "for(", "for (", "while(", "while (", "if(", "if (",
        "switch(", "switch (", "return ",
        "┌", "└", "├", "│",
    )
    if any(marker in text for marker in markers):
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    # Protocol names and a single encapsulation chain are prose, not diagrams.
    if sum(bool(re.search(r'->|<-|=>|→|←|↔', line)) for line in lines) >= 2:
        return True
    if len(lines) >= 2 and any("|" in line or "\t" in line for line in lines):
        return True
    # A code-like block usually contains several statements separated by semicolons.
    return len(lines) >= 2 and text.count(";") >= 2 and not re.search(r"\\(?:frac|sum|int|lim|sqrt)", text)


def _preserve_non_math_fields(result: dict[str, Any]) -> None:
    """Move accidental code/table/structure output out of LaTeX fields."""
    blocks = result.get("code_blocks")
    if not isinstance(blocks, list):
        blocks = []
        result["code_blocks"] = blocks
    for field in LATEX_FIELDS:
        value = result.get(field)
        if not _looks_like_code_or_structure(value):
            continue
        label = {"question_latex": "题目原文", "user_solution_latex": "我的解题过程", "reference_solution_latex": "参考答案"}[field]
        block = f"[{label}]\n{value.strip()}"
        if block not in blocks:
            blocks.append(block)
        if field == "question_latex" and not result.get("question_text"):
            result["question_text"] = value
        result[field] = ""
        result["uncertain_parts"] = [*result.get("uncertain_parts", []), f"{label}包含代码/结构内容，已按原文保留"]


def _strip_formula_wrapper(value: str) -> str:
    text = value.strip()
    for opening, closing in ((r"\(", r"\)"), (r"\[", r"\]"), ("$$", "$$"), ("$", "$")):
        if text.startswith(opening) and text.endswith(closing):
            return text[len(opening):-len(closing)].strip()
    return text


def parse_result(raw: str | dict[str, Any]) -> dict[str, Any]:
    try:
        value = _decode_json(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {**copy.deepcopy(FIELDS), "uncertain_parts": ["AI返回不是合法JSON"], "confidence": 0.0}
    if not isinstance(value, dict):
        value = {}
    result = {**copy.deepcopy(FIELDS), **value}
    # AI 常把「没有内容」写成 null，归一化成默认值，避免下游接口和数据库出现 null
    for key, default in FIELDS.items():
        if result.get(key) is None:
            result[key] = default
    for key in ("code_blocks", "error_types", "knowledge_points", "tags", "uncertain_parts"):
        if isinstance(result[key], str):
            parts = [item.strip() for item in result[key].replace("，", ",").split(",") if item.strip()]
            result[key] = parts
        elif isinstance(result[key], list):
            result[key] = [_text_value(item).strip() for item in result[key] if _text_value(item).strip()]
        else:
            result[key] = []
    result["error_types"] = [primary_error_type(
        result["error_types"],
        " ".join(str(result.get(key, "")) for key in ("first_error_step", "error_analysis", "correct_approach", "question_text")),
    )]
    for key in FIELDS:
        if key not in ("subject", "difficulty", "is_typical", "needs_priority_review", "code_blocks", "error_types", "knowledge_points", "tags", "uncertain_parts", "confidence", "recognition_confidence", "correction_confidence", "field_confidence"):
            result[key] = _text_value(result.get(key), FIELDS[key])
    # Some vision providers emit a bare zero when no standalone formula was
    # found. It is not useful as a supplementary formula when the full stem
    # is already present, and would otherwise render as “补充公式 0”.
    if result.get("question_text", "").strip() and _strip_formula_wrapper(result.get("question_latex", "")) == "0":
        result["question_latex"] = ""
    original_subject = str(result.get("subject", "unknown"))
    result["subject"] = normalize_subject(original_subject)
    if result["subject"] == "unknown" and original_subject.strip().lower() not in {"unknown", ""}:
        result["uncertain_parts"] = [*result.get("uncertain_parts", []), "科目无法确认"]
    if result["subject"] == "math1":
        result["uncertain_parts"] = [*result.get("uncertain_parts", []), "只判断到数学一，未细分到高等数学 / 线性代数 / 概率论与数理统计"]
    if result["subject"] not in SUBJECTS:
        result["subject"] = "unknown"
    normalize_knowledge(result)
    result["tags"] = [str(item).strip().lstrip("#")[:24] for item in result.get("tags", []) if str(item).strip()]
    result["tags"] = list(dict.fromkeys(result["tags"]))[:8] or _auto_tags(result)
    _preserve_non_math_fields(result)
    difficulty = result.get("difficulty")
    try:
        difficulty = int(float(difficulty))
    except (TypeError, ValueError):
        difficulty = {"easy": 2, "简单": 2, "medium": 3, "中等": 3, "hard": 4, "困难": 4, "very hard": 5, "很难": 5}.get(str(difficulty).strip().lower(), 3)
    result["difficulty"] = max(1, min(5, difficulty))
    for key in ("confidence", "recognition_confidence", "correction_confidence"):
        try:
            result[key] = max(0.0, min(1.0, float(result.get(key) or 0)))
        except (TypeError, ValueError):
            result[key] = 0.0
    # Keep field-level confidence optional for older providers while exposing a
    # stable shape to the confirmation page. Missing values inherit the most
    # relevant overall score; explicit values are clamped to [0, 1].
    raw_field_confidence = result.get("field_confidence")
    if not isinstance(raw_field_confidence, dict):
        raw_field_confidence = {}
    defaults = {
        "question": result["recognition_confidence"] if result.get("question_text") or result.get("question_latex") else 0.0,
        "formula": result["recognition_confidence"] if result.get("question_latex") or result.get("user_solution_latex") or result.get("reference_solution_latex") else 0.0,
        "solution": result["correction_confidence"] if result.get("user_solution_latex") or result.get("first_error_step") or result.get("error_analysis") else 0.0,
        "reference": result["correction_confidence"] if result.get("reference_solution_latex") else 0.0,
        "classification": min(result["recognition_confidence"], result["correction_confidence"]) if result.get("subject") not in {"unknown", "math1"} and result.get("knowledge_points") else 0.0,
    }
    field_confidence: dict[str, float] = {}
    for field in FIELD_CONFIDENCE_DEFAULTS:
        try:
            score = float(raw_field_confidence.get(field, defaults[field]))
        except (TypeError, ValueError):
            score = defaults[field]
        field_confidence[field] = max(0.0, min(1.0, score))
    result["field_confidence"] = field_confidence
    supplied = [result[key] for key in ("recognition_confidence", "correction_confidence") if key in value and value.get(key) is not None]
    if supplied:
        # 综合置信度取识别与纠错中最弱的一环，避免“看得清但判错”被高估。
        result["confidence"] = min(supplied)
    threshold = settings.ai_confidence_threshold
    if result["recognition_confidence"] < threshold:
        result["uncertain_parts"] = [*result.get("uncertain_parts", []), f"图像识别置信度低于{int(threshold * 100)}%，请人工核对题目与公式"]
    if result["correction_confidence"] < threshold:
        result["uncertain_parts"] = [*result.get("uncertain_parts", []), f"纠错结论置信度低于{int(threshold * 100)}%，请人工核对第一处错误"]
    if result["verification_status"] not in {"verified", "partially_verified", "unverified"}:
        result["verification_status"] = "unverified"
    return result
