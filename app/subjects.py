"""科目分类。

数学一拆成高等数学 / 线性代数 / 概率论与数理统计三部分，408 拆成数据结构 /
计算机组成原理 / 操作系统 / 计算机网络四科，避免把所有内容混在一个大类里。
"""
SUBJECTS: dict[str, str] = {
    "math_calculus": "高等数学",
    "math_linear": "线性代数",
    "math_probability": "概率论与数理统计",
    "math1": "数学一（未细分）",
    "ds": "数据结构",
    "coa": "计算机组成原理",
    "os": "操作系统",
    "cn": "计算机网络",
    "unknown": "未确认",
}

# 合并筛选用的分组键，键和值都可以出现在筛选参数里
GROUPS: dict[str, tuple[str, ...]] = {
    "math1": ("math_calculus", "math_linear", "math_probability"),
    "408": ("ds", "coa", "os", "cn"),
}

GROUP_LABELS: dict[str, str] = {
    "math1": "数学一（三科）",
    "408": "408（四科）",
}

ALIASES: dict[str, str] = {
    "数学一": "math1", "数学1": "math1", "math1": "math1",
    "高等数学": "math_calculus", "高数": "math_calculus", "微积分": "math_calculus",
    "数学分析": "math_calculus", "calculus": "math_calculus", "math_calculus": "math_calculus",
    "线性代数": "math_linear", "线代": "math_linear",
    "linearalgebra": "math_linear", "linear_algebra": "math_linear", "math_linear": "math_linear",
    "概率论与数理统计": "math_probability", "概率论": "math_probability", "概率统计": "math_probability",
    "数理统计": "math_probability", "probability": "math_probability", "math_probability": "math_probability",
    "数据结构": "ds", "408数据结构": "ds", "ds": "ds",
    "计算机组成原理": "coa", "组成原理": "coa", "计算机组成": "coa", "coa": "coa",
    "操作系统": "os", "os": "os",
    "计算机网络": "cn", "网络": "cn", "cn": "cn",
    "408": "408", "考研408": "408",
}


def normalize_subject(value: str | None) -> str:
    """把 AI 或用户输入的各种写法归一成内部科目代码，无法识别时返回 unknown。"""
    text = str(value or "").strip()
    if not text:
        return "unknown"
    key = text.lower().replace(" ", "").replace("-", "_")
    return ALIASES.get(text) or ALIASES.get(key) or (key if key in SUBJECTS or key in GROUPS else "unknown")


def group_members(key: str) -> tuple[str, ...] | None:
    """返回分组包含的科目；不是分组则返回 None。"""
    normalized = normalize_subject(key)
    if normalized in GROUPS:
        return GROUPS[normalized]
    return None


# 筛选下拉框：先给可合并的“大类”，再给具体科目
PICKER_OPTIONS: list[dict[str, str]] = [
    {"value": "math1", "label": "数学一（三科）"},
    {"value": "math_calculus", "label": "高等数学"},
    {"value": "math_linear", "label": "线性代数"},
    {"value": "math_probability", "label": "概率论与数理统计"},
    {"value": "408", "label": "408（四科）"},
    {"value": "ds", "label": "数据结构"},
    {"value": "coa", "label": "计算机组成原理"},
    {"value": "os", "label": "操作系统"},
    {"value": "cn", "label": "计算机网络"},
]

# 确认页 / 编辑页可选的具体科目，不含合并大类
EDIT_OPTIONS: list[dict[str, str]] = [
    {"value": key, "label": label} for key, label in SUBJECTS.items() if key != "unknown"
]
