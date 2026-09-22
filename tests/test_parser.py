from app.services.ai.parser import parse_result
from app.routers.stats import _rate


def test_invalid_json_is_explicitly_uncertain():
    result = parse_result("not-json")
    assert result["confidence"] == 0
    assert result["uncertain_parts"]


def test_parser_preserves_408_code_blocks():
    result = parse_result({"subject": "ds", "code_blocks": ["for(i=0;i<n;i++)"], "difficulty": 9})
    assert result["subject"] == "ds"
    assert result["code_blocks"] == ["for(i=0;i<n;i++)"]
    assert result["difficulty"] == 5


def test_overall_confidence_uses_weakest_link():
    result = parse_result({"recognition_confidence": 0.95, "correction_confidence": 0.4, "confidence": 0.99})
    assert result["confidence"] == 0.4
    assert any("纠错结论置信度" in item for item in result["uncertain_parts"])


def test_explicit_zero_recognition_confidence_is_not_ignored():
    result = parse_result({"recognition_confidence": 0, "correction_confidence": 0.9})
    assert result["confidence"] == 0
    assert any("图像识别置信度" in item for item in result["uncertain_parts"])


def test_field_confidence_is_normalized_and_older_results_get_fallbacks():
    result = parse_result({
        "subject": "math_calculus",
        "knowledge_points": ["极限"],
        "question_text": "求极限",
        "user_solution_latex": "x\\to 0",
        "reference_solution_latex": "0",
        "recognition_confidence": 0.9,
        "correction_confidence": 0.7,
        "field_confidence": {"question": 1.4, "formula": "bad", "solution": 0.2},
    })
    assert result["field_confidence"] == {
        "question": 1.0,
        "formula": 0.9,
        "solution": 0.2,
        "reference": 0.7,
        "classification": 0.7,
    }


def test_subject_alias_is_normalized():
    assert parse_result({"subject": "数据结构"})["subject"] == "ds"


def test_math1_is_split_into_three_parts():
    assert parse_result({"subject": "高等数学"})["subject"] == "math_calculus"
    assert parse_result({"subject": "高数"})["subject"] == "math_calculus"
    assert parse_result({"subject": "线性代数"})["subject"] == "math_linear"
    assert parse_result({"subject": "线代"})["subject"] == "math_linear"
    assert parse_result({"subject": "概率论与数理统计"})["subject"] == "math_probability"
    assert parse_result({"subject": "概率论"})["subject"] == "math_probability"


def test_broad_math1_is_flagged_as_not_subdivided():
    result = parse_result({"subject": "数学一"})
    assert result["subject"] == "math1"
    assert any("未细分" in item for item in result["uncertain_parts"])


def test_408_parts_are_not_collapsed_into_one_subject():
    assert parse_result({"subject": "计算机组成原理"})["subject"] == "coa"
    assert parse_result({"subject": "操作系统"})["subject"] == "os"
    assert parse_result({"subject": "计算机网络"})["subject"] == "cn"
    assert parse_result({"subject": "408"})["subject"] == "unknown"


def test_parser_turns_nulls_into_defaults():
    """AI 会把「没有内容」写成 null，落库前必须归一化。"""
    result = parse_result({
        "subject": "math_calculus",
        "recognition_confidence": 0.9,
        "correction_confidence": 0.9,
        "first_error_step": None,
        "error_analysis": None,
        "review_prompt": None,
        "code_blocks": None,
        "error_types": "符号错误，审题错误",
        "uncertain_parts": None,
    })
    assert result["first_error_step"] == ""
    assert result["error_analysis"] == ""
    assert result["review_prompt"] == ""
    assert result["code_blocks"] == []
    assert result["uncertain_parts"] == []
    assert result["error_types"] == ["计算与符号"]


def test_parser_accepts_fenced_json_from_compatible_models():
    result = parse_result('```json\n{"subject":"math_calculus","question_text":"求极限"}\n```')
    assert result["subject"] == "math_calculus"
    assert result["question_text"] == "求极限"


def test_parser_keeps_408_code_out_of_latex():
    result = parse_result({
        "subject": "ds",
        "question_latex": "for (i = 0; i < n; i++) { a[i] = 0; }",
        "reference_solution_latex": "| 状态 | 输入 | 输出 |\n|---|---|---|\n| S0 | 0 | S1 |",
        "recognition_confidence": 0.9,
        "correction_confidence": 0.9,
    })
    assert result["question_latex"] == ""
    assert result["reference_solution_latex"] == ""
    assert any("[题目原文]" in block for block in result["code_blocks"])
    assert any("[参考答案]" in block for block in result["code_blocks"])
    assert any("按原文保留" in item for item in result["uncertain_parts"])


def test_parser_keeps_code_out_of_latex_even_when_model_only_says_408():
    """模型偶尔只返回“408”，仍不能把代码/表格送进公式渲染。"""
    result = parse_result({
        "subject": "408",
        "question_latex": "for (i = 0; i < n; i++) { a[i] = 0; }",
        "recognition_confidence": 0.9,
        "correction_confidence": 0.9,
    })
    assert result["question_latex"] == ""
    assert result["subject"] == "unknown"
    assert result["code_blocks"]


def test_accuracy_rate_counts_partial_as_half():
    assert _rate(correct=3, partial=2, total=8) == 0.5
    assert _rate(correct=0, partial=0, total=0) == 0.0


def test_parser_normalizes_object_fields_and_text_difficulty():
    result = parse_result({
        "subject": "math_calculus",
        "question_text": {"recognized": "求极限"},
        "error_types": [{"name": "符号错误"}],
        "difficulty": "hard",
    })
    assert result["question_text"] == '{"recognized": "求极限"}'
    assert result["error_types"] == ["计算与符号"]
    assert result["difficulty"] == 4


def test_parser_uses_method_category_when_no_error_is_visible():
    result = parse_result({
        "error_types": [],
        "error_analysis": "未发现用户存在实质性错误，但需要复习二重积分的解题路径。",
        "correct_approach": "使用极坐标计算积分。",
    })
    assert result["error_types"] == ["方法选择"]


def test_parser_uses_one_syllabus_point_for_math_synonyms():
    result = parse_result({
        "subject": "math_calculus", "chapter": "多元函数积分学",
        "knowledge_points": ["极坐标变换", "二重积分的对称性"],
        "question_text": "利用对称性计算二重积分",
    })
    assert result["knowledge_points"] == ["二重积分"]
    assert result["chapter"] == "多元函数积分学"


def test_parser_uses_one_syllabus_point_for_408():
    result = parse_result({"subject": "ds", "knowledge_points": ["二叉树遍历"]})
    assert result["knowledge_points"] == ["树与二叉树"]
    assert result["chapter"] == "树与二叉树"


def test_408_protocol_prose_stays_in_solution_with_inline_formula():
    solution = '（1）DNS协议报文按 UDP→IP→以太网封装。\n（2）MAC地址为 FF-FF-FF-FF-FF-FF，数量为 \\(2\\)。'
    result = parse_result({'subject': 'cn', 'user_solution_latex': solution})
    assert result['user_solution_latex'] == solution
    assert result['code_blocks'] == []


def test_protocol_diagram_is_preserved_as_code_block():
    diagram = '客户端 -> 服务器: SYN\n客户端 <- 服务器: SYN+ACK\n客户端 -> 服务器: ACK'
    result = parse_result({'subject': 'cn', 'reference_solution_latex': diagram})
    assert result['reference_solution_latex'] == ''
    assert any(diagram in block for block in result['code_blocks'])
