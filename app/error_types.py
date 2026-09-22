"""Stable, broad error categories used by AI, manual edits, and statistics."""

from typing import Iterable


ERROR_TYPE_OPTIONS = (
    "概念理解",
    "方法选择",
    "计算与符号",
    "条件与边界",
    "审题与信息提取",
    "推理与论证",
    "记忆与规范",
    "代码与实现",
)

_ALIASES = {
    "概念理解": "概念理解", "概念错误": "概念理解", "概念不清": "概念理解", "定义理解": "概念理解",
    "方法选择": "方法选择", "方法错误": "方法选择", "解题方法": "方法选择", "思路错误": "方法选择", "思路与方法": "方法选择",
    "计算与符号": "计算与符号", "符号错误": "计算与符号", "计算错误": "计算与符号", "运算错误": "计算与符号",
    "条件与边界": "条件与边界", "边界条件": "条件与边界", "条件错误": "条件与边界", "边界错误": "条件与边界",
    "审题与信息提取": "审题与信息提取", "审题错误": "审题与信息提取", "信息提取": "审题与信息提取",
    "推理与论证": "推理与论证", "推理错误": "推理与论证", "证明错误": "推理与论证", "逻辑错误": "推理与论证",
    "记忆与规范": "记忆与规范", "记忆错误": "记忆与规范", "公式记忆": "记忆与规范", "规范错误": "记忆与规范",
    "代码与实现": "代码与实现", "代码错误": "代码与实现", "实现错误": "代码与实现", "编程错误": "代码与实现",
}

_KEYWORDS = (
    ("代码与实现", ("代码", "程序", "语法", "指针", "实现", "编译", "算法实现")),
    ("条件与边界", ("边界", "条件", "定义域", "初值", "范围", "越界", "溢出", "临界")),
    ("审题与信息提取", ("审题", "题意", "看错", "漏看", "信息")),
    ("计算与符号", ("符号", "计算", "运算", "化简", "求导", "积分", "代数")),
    ("推理与论证", ("证明", "推导", "推理", "逻辑", "论证")),
    ("记忆与规范", ("记忆", "背诵", "规范", "协议", "公式")),
    ("概念理解", ("概念", "定义", "性质", "定理", "原理", "知识点")),
)


def primary_error_type(values: Iterable[object] | None, evidence: str = "") -> str:
    """Return exactly one broad category, preferring the model's explicit choice."""
    for value in values or ():
        text = str(value).strip()
        if text in _ALIASES:
            return _ALIASES[text]

    text = " ".join([*(str(value) for value in values or ()), evidence]).lower()
    if ("未发现" in text and "实质性错误" in text) or "没有实质性错误" in text:
        return "方法选择"
    for category, keywords in _KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    # A question kept in the wrong-book without a visible mistake is still best
    # grouped by the problem-solving approach it should reinforce.
    return "方法选择"
