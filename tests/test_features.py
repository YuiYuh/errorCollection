"""新增功能验证：错题筛选、统计分布、备份、整包导出。"""
import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.db import db
from app.timezone import day_bounds_utc


def make_question(client: TestClient, **overrides) -> dict:
    payload = {
        "subject": "math_calculus",
        "chapter": "极限",
        "question_text": "求极限",
        "knowledge_points": ["极限", "洛必达"],
        "error_types": ["计算与符号"],
        "difficulty": 3,
    }
    payload.update(overrides)
    response = client.post("/api/questions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_filter_by_subject_group_and_alias(client):
    make_question(client, subject="math_calculus")
    make_question(client, subject="ds", chapter="图", knowledge_points=["图的遍历"])
    assert client.get("/api/questions", params={"subject": "数学一"}).json()["total"] == 1
    assert client.get("/api/questions", params={"subject": "高等数学"}).json()["total"] == 1
    assert client.get("/api/questions", params={"subject": "math_calculus"}).json()["total"] == 1
    assert client.get("/api/questions", params={"subject": "线性代数"}).json()["total"] == 0
    assert client.get("/api/questions", params={"subject": "408"}).json()["total"] == 1
    assert client.get("/api/questions").json()["total"] == 2


def test_math1_group_covers_all_three_parts(client):
    make_question(client, subject="math_calculus", chapter="极限")
    make_question(client, subject="math_linear", chapter="特征值")
    make_question(client, subject="math_probability", chapter="条件概率")
    make_question(client, subject="os", chapter="进程")
    assert client.get("/api/questions", params={"subject": "数学一"}).json()["total"] == 3
    assert client.get("/api/questions", params={"subject": "高等数学"}).json()["total"] == 1
    assert client.get("/api/questions", params={"subject": "线性代数"}).json()["total"] == 1
    assert client.get("/api/questions", params={"subject": "概率论与数理统计"}).json()["total"] == 1


def test_filter_options_expose_split_subjects(client):
    options = client.get("/api/questions/filters").json()
    values = [item["value"] for item in options["subject_options"]]
    assert values[:5] == ["math1", "math_calculus", "math_linear", "math_probability", "408"]
    edit_values = [item["value"] for item in options["subject_edit_options"]]
    assert "math_calculus" in edit_values and "math1" in edit_values


def test_filters_by_chapter_knowledge_error_and_date(client):
    make_question(client, knowledge_points=["极限"], error_types=["计算与符号"])
    make_question(client, chapter="图", knowledge_points=["图的遍历"], error_types=["条件与边界"], subject="ds")
    assert client.get("/api/questions", params={"chapter": "图"}).json()["total"] == 1
    assert client.get("/api/questions", params={"knowledge_point": "函数、极限、连续"}).json()["total"] == 1
    assert client.get("/api/questions", params={"knowledge_point": "不存在的知识点"}).json()["total"] == 0
    assert client.get("/api/questions", params={"error_type": "条件与边界"}).json()["total"] == 1
    assert client.get("/api/questions", params={"date_from": "2000-01-01", "date_to": "2999-01-01"}).json()["total"] == 2
    assert client.get("/api/questions", params={"date_from": "2999-01-01"}).json()["total"] == 0


def test_search_covers_correct_approach(client):
    make_question(client, correct_approach="先用等价无穷小替换")
    assert client.get("/api/questions", params={"q": "等价无穷小"}).json()["total"] == 1


def test_filter_options_are_aggregated(client):
    make_question(client, chapter="极限")
    make_question(client, subject="ds", chapter="图", knowledge_points=["图的遍历"], error_types=["条件与边界"])
    options = client.get("/api/questions/filters").json()
    assert set(options["chapters"]) == {"函数、极限、连续", "图"}
    assert "图" in options["knowledge_points"]
    assert "条件与边界" in options["error_types"]
    assert options["subjects"]


def test_distribution_endpoint_returns_all_sections(client):
    make_question(client, error_types=["计算与符号"])
    data = client.get("/api/stats/distribution").json()
    assert {item["name"] for item in data["knowledge_points"]} == {"函数、极限、连续"}
    assert [item["name"] for item in data["error_types"]] == ["计算与符号"]
    assert data["subjects"][0]["count"] == 1
    assert data["trend"]
    assert "repeat_questions" in data
    assert "repeat_knowledge_points" in data
    assert "repeat_syllabus_points" in data


def test_distribution_returns_actionable_knowledge_point_statistics(client):
    old = make_question(client, knowledge_points=["极限"])
    recent = make_question(client, knowledge_points=["极限"])
    with db() as conn:
        conn.execute("UPDATE questions SET created_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(days=10)).isoformat(), old["id"]))
        conn.execute("UPDATE questions SET created_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), recent["id"]))
    data = client.get("/api/stats/distribution").json()
    point = next(item for item in data["knowledge_point_stats"] if item["name"] == "函数、极限、连续")
    assert point["error_count"] == 2
    assert point["question_count"] == 2
    assert point["consecutive_error_count"] == 2
    assert point["recent_error_at"].startswith((datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat())
    assert data["weekly_priority"][0]["name"] == "函数、极限、连续"
    assert len(data["weekly_priority"]) <= 5


def test_distribution_groups_repeat_questions_by_knowledge_point(client):
    first = make_question(client, knowledge_points=["函数、极限、连续"])
    second = make_question(client, knowledge_points=["函数、极限、连续"])
    with db() as conn:
        conn.execute("UPDATE review_cards SET lapses=2 WHERE question_id=?", (first["id"],))
        conn.execute("UPDATE questions SET mastery=0.2 WHERE id=?", (second["id"],))
    data = client.get("/api/stats/distribution").json()
    point = next(item for item in data["repeat_knowledge_points"] if item["name"] == "函数、极限、连续")
    syllabus_point = next(item for item in data["repeat_syllabus_points"] if item["name"] == "函数、极限、连续")
    assert point["count"] == 2
    assert point["repeat_count"] == 2
    assert [item["id"] for item in point["questions"]] == [first["id"], second["id"]]
    assert syllabus_point["name"] == point["name"]
    assert syllabus_point["details"][0]["parent_name"] == point["name"]


def test_distribution_splits_broad_points_into_actionable_details(client):
    graph = make_question(
        client,
        subject="ds",
        chapter="图",
        knowledge_points=["图"],
        question_text="使用 Dijkstra 算法求带权图的最短路径",
    )
    matrix = make_question(
        client,
        subject="math_linear",
        chapter="矩阵",
        knowledge_points=["矩阵"],
        question_text="利用逆矩阵和伴随矩阵求解线性方程组",
    )
    with db() as conn:
        conn.execute("UPDATE questions SET mastery=0.2 WHERE id IN (?, ?)", (graph["id"], matrix["id"]))
    data = client.get("/api/stats/distribution").json()
    graph_point = next(item for item in data["repeat_knowledge_points"] if item["name"] == "图")
    matrix_point = next(item for item in data["repeat_knowledge_points"] if item["name"] == "矩阵")
    assert [item["name"] for item in graph_point["details"]] == ["最短路径"]
    assert [item["name"] for item in matrix_point["details"]] == ["逆矩阵"]
    assert graph_point["details"][0]["parent_name"] == "图"


def test_distribution_does_not_render_legacy_subject_as_knowledge_point(client):
    question = make_question(
        client,
        subject="math_calculus",
        chapter="函数、极限、连续",
        question_text="利用洛必达法则求极限",
    )
    with db() as conn:
        conn.execute(
            "UPDATE questions SET knowledge_points=?, mastery=0.2 WHERE id=?",
            ('["高等数学"]', question["id"]),
        )
    data = client.get("/api/stats/distribution").json()
    names = {item["name"] for item in data["repeat_knowledge_points"]}
    assert "高等数学" not in names
    point = next(item for item in data["repeat_knowledge_points"] if item["name"] == "函数、极限、连续")
    assert [item["name"] for item in point["details"]] == ["洛必达法则"]


def test_distribution_recovers_legacy_pending_point_from_question_evidence(client):
    question = make_question(
        client,
        subject="ds",
        chapter="图",
        knowledge_points=["待确认"],
        question_text="使用 Dijkstra 算法求带权图的最短路径",
    )
    with db() as conn:
        conn.execute("UPDATE questions SET mastery=0.2 WHERE id=?", (question["id"],))
    data = client.get("/api/stats/distribution").json()
    graph = next(item for item in data["repeat_knowledge_points"] if item["name"] == "图")
    assert [item["name"] for item in graph["details"]] == ["最短路径"]


def test_distribution_recovers_empty_point_from_question_evidence(client):
    question = make_question(
        client,
        subject="math_linear",
        chapter="矩阵",
        knowledge_points=[],
        question_text="利用逆矩阵和伴随矩阵求解线性方程组",
    )
    with db() as conn:
        conn.execute("UPDATE questions SET knowledge_points=? WHERE id=?", ("[]", question["id"]))
        conn.execute("UPDATE questions SET mastery=0.2 WHERE id=?", (question["id"],))
    data = client.get("/api/stats/distribution").json()
    matrix = next(item for item in data["repeat_knowledge_points"] if item["name"] == "矩阵")
    assert [item["name"] for item in matrix["details"]] == ["逆矩阵"]


def test_distribution_keeps_malformed_knowledge_points_in_fallback_group(client):
    question = make_question(client, chapter="", question_text="", knowledge_points=[])
    with db() as conn:
        conn.execute("UPDATE questions SET knowledge_points=? WHERE id=?", ('["", "   "]', question["id"]))
        conn.execute("UPDATE questions SET mastery=0.2 WHERE id=?", (question["id"],))
    data = client.get("/api/stats/distribution").json()
    point = next(item for item in data["repeat_knowledge_points"] if item["name"] == "待确认")
    assert point["count"] == 1
    assert point["questions"][0]["id"] == question["id"]
    assert all(item["name"] != "待确认" for item in data["repeat_syllabus_points"])


def test_syllabus_merges_synonyms_and_lists_historical_questions(client):
    first = make_question(client, knowledge_points=["二重积分的对称性"], question_text="利用对称性计算二重积分")
    second = make_question(client, knowledge_points=["极坐标变换"], question_text="用极坐标变换求二重积分")
    assert first["knowledge_points"] == ["二重积分"]
    assert second["knowledge_points"] == ["二重积分"]
    found = client.get("/api/questions", params={"knowledge_point": "二重积分", "subject": "math_calculus"}).json()
    assert found["total"] == 2
    catalog = client.get("/api/questions/filters").json()["syllabus"]["math_calculus"]
    item = next(point for point in catalog if point["name"] == "二重积分")
    assert item == {"chapter": "多元函数积分学", "name": "二重积分", "count": 2}


def test_ai_and_manual_tags_are_saved_and_can_be_removed(client):
    question = make_question(client, tags=["对称性", "极坐标"])
    assert question["tags"] == ["对称性", "极坐标"]
    updated = client.patch(f"/api/questions/{question['id']}", json={
        "subject": question["subject"], "question_text": question["question_text"],
        "knowledge_points": question["knowledge_points"], "error_types": question["error_types"],
        "tags": ["极坐标", "易错"],
    }).json()
    assert updated["tags"] == ["极坐标", "易错"]


def test_patch_question_preserves_omitted_fields(client):
    question = make_question(
        client,
        chapter="极限",
        question_text="原题目",
        knowledge_points=["极限"],
        tags=["保留标签"],
    )
    response = client.patch(f"/api/questions/{question['id']}", json={"key_lesson": "新的教训"})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["key_lesson"] == "新的教训"
    assert updated["chapter"] == question["chapter"]
    assert updated["question_text"] == question["question_text"]
    assert updated["knowledge_points"] == question["knowledge_points"]
    assert updated["tags"] == question["tags"]


def test_review_rating_updates_schedule_and_records_overdue(client):
    question = make_question(client)
    with db() as conn:
        due = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        conn.execute("UPDATE questions SET next_review_at=? WHERE id=?", (due, question["id"]))
        conn.execute("UPDATE review_cards SET due_at=? WHERE question_id=?", (due, question["id"]))
    response = client.post(f"/api/questions/{question['id']}/review", json={"rating": "forgot"})
    assert response.status_code == 200, response.text
    assert response.json()["rating"] == "forgot"
    with db() as conn:
        log = conn.execute("SELECT overdue,rating FROM review_logs WHERE question_id=?", (question["id"],)).fetchone()
    assert dict(log) == {"overdue": 1, "rating": "forgot"}


def test_empty_error_type_is_assigned_to_a_broad_category(client):
    question = make_question(client, error_types=[])
    assert question["error_types"] == ["方法选择"]
    distribution = client.get("/api/stats/distribution").json()
    assert distribution["error_types"] == [{"name": "方法选择", "count": 1}]


def test_manual_backup_and_listing(client):
    make_question(client)
    created = client.post("/api/system/backup").json()
    assert Path(created["path"]).exists()
    listed = client.get("/api/system/backups").json()
    assert listed["items"]
    assert listed["items"][0]["size"] > 0


def test_export_bundle_contains_questions_and_images(client):
    buffer = io.BytesIO()
    Image.new("RGB", (400, 500), "white").save(buffer, format="JPEG")
    task_id = client.post(
        "/api/tasks/upload",
        files=[("files", ("q.jpg", buffer.getvalue(), "image/jpeg"))],
        data={"image_types": "question"},
    ).json()["id"]
    result = client.post(
        f"/api/questions/{task_id}/confirm",
        json={"subject": "math1", "question_text": "求极限", "task_id": task_id},
    ).json()
    assert result["id"]
    bundle = client.post("/api/system/export-bundle").json()
    path = Path(bundle["path"])
    assert path.exists()
    assert bundle["questions"] == 1
    assert bundle["images"] == 1
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert "questions.json" in names
        assert any(name.startswith("images/") for name in names)


def test_status_reports_disk_backup_and_tailscale(client, monkeypatch):
    monkeypatch.setattr("app.routers.system.tailscale_state", lambda: {"running": True, "https": True, "message": ""})
    data = client.get("/api/system/status").json()
    assert data["disk_ok"] is True
    assert data["disk_message"] == ""
    assert data["tailscale_running"] is True
    assert "auto_backup_hours" in data
    assert data["ai_base_url"].startswith("http")


def test_auto_backup_skips_when_disabled(monkeypatch, client):
    from app.services import backup as backup_service

    monkeypatch.setattr(settings, "auto_backup_hours", 0)
    assert backup_service.maybe_auto_backup() is None


def test_integrity_check_accepts_get_and_post(client):
    for method in (client.get, client.post):
        data = method("/api/system/integrity-check").json()
        assert data["ok"] is True
        assert data["issues"] == []


def test_today_and_overdue_reviews_are_separated(client):
    today = make_question(client, question_text="今天复习")
    overdue = make_question(client, question_text="逾期复习")
    start, _ = day_bounds_utc()
    with db() as conn:
        conn.execute("UPDATE review_cards SET due_at=? WHERE question_id=?", ((start + timedelta(hours=1)).isoformat(), today["id"]))
        conn.execute("UPDATE review_cards SET due_at=? WHERE question_id=?", ((start - timedelta(days=1)).isoformat(), overdue["id"]))
    assert [item["question_text"] for item in client.get("/api/reviews/today").json()["items"]] == ["今天复习"]
    assert [item["question_text"] for item in client.get("/api/reviews/overdue").json()["items"]] == ["逾期复习"]
    overview = client.get("/api/stats/overview").json()
    assert overview["today_due"] == 1
    assert overview["overdue"] == 1
