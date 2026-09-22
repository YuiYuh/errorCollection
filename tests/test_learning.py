"""Learning flows use an isolated DB and mocked AI; never spend provider credit."""
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from app.db import db, now_iso
from app.services.ai.openai_compatible import OpenAICompatibleClient


def question(client, **extra):
    response = client.post('/api/questions', json={
        'subject': 'math_calculus', 'question_text': '求函数 f(x)=x^2 在 x=1 处的导数',
        'question_latex': r"f'(1)=2", 'knowledge_points': ['极限'],
        'error_types': ['计算与符号'], 'correct_approach': '历史答案不得在独立作答页面自动展开', **extra,
    })
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, **data):
    stream = io.BytesIO()
    Image.new('RGB', (24, 24), 'white').save(stream, format='PNG')
    response = client.post('/api/tasks/upload', data={'image_types': 'solution', **data},
                           files=[('files', ('answer.png', stream.getvalue(), 'image/png'))])
    assert response.status_code == 200, response.text
    return response.json()


def ready(task_id, outcome='wrong'):
    result = {'outcome': outcome, 'same_error': True, 'error_types': ['计算与符号'], 'first_error_step': '符号有误'}
    with db() as conn:
        conn.execute("UPDATE ai_tasks SET status='waiting_confirmation' WHERE id=?", (task_id,))
        conn.execute('INSERT OR REPLACE INTO ai_results(task_id,result_json,created_at) VALUES(?,?,?)',
                     (task_id, json.dumps(result), now_iso()))


def test_practice_confirm_is_idempotent_and_survives_missing_card(client):
    q = question(client)
    safe = client.get(f"/api/learning/practice/{q['id']}").json()['question']
    assert 'correct_approach' not in safe and 'reference_solution_latex' not in safe
    task = upload(client, practice_question_id=q['id'])
    assert task['id'] not in [item['id'] for item in client.get('/api/tasks').json()['items']]
    with db() as conn:
        conn.execute('DELETE FROM review_cards WHERE question_id=?', (q['id'],))
    ready(task['id'])
    endpoint = f"/api/learning/attempts/{task['attempt_id']}/confirm"
    body = {'outcome': 'wrong', 'same_error': True, 'rating': 'forgot'}
    assert client.post(endpoint, json=body).status_code == 200
    assert client.post(endpoint, json=body).json()['already_confirmed']
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409
    with db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM review_logs').fetchone()[0] == 1
        assert conn.execute('SELECT lapses FROM review_cards').fetchone()[0] == 1
    history = client.get(f"/api/learning/practice/{q['id']}").json()['attempts']
    assert history[0]['outcome'] == 'wrong' and history[0]['confirmed_at']


def test_exam_membership_progress_and_recovery(client):
    q = question(client)
    other = question(client, subject='ds', knowledge_points=['图'])
    response = client.post('/api/learning/exams', json={'knowledge_point': q['knowledge_points'][0], 'subject': '数学一', 'count': 5})
    assert response.status_code == 200, response.text
    exam = response.json()
    assert exam['total'] == 1 and exam['completed'] == 0
    task = upload(client, practice_question_id=q['id'], exam_id=exam['id'])
    ready(task['id'])
    assert client.post(f"/api/learning/attempts/{task['attempt_id']}/confirm", json={'outcome': 'correct', 'rating': 'remembered'}).status_code == 200
    saved = client.get(f"/api/learning/exams/{exam['id']}").json()
    assert saved['completed'] == saved['correct'] == 1
    assert client.get('/api/learning/exams').json()['items'][0]['id'] == exam['id']
    assert client.post('/api/tasks/upload', data={'practice_question_id': other['id'], 'exam_id': exam['id']}, files=[('files', ('x.png', b'x', 'image/png'))]).status_code == 422
    assert client.delete(f"/api/questions/{q['id']}").status_code == 200
    assert client.get(f"/api/learning/exams/{exam['id']}").json()['total'] == 0
    assert client.get('/api/learning/trash').json()['items'][0]['id'] == q['id']
    assert client.get(f"/api/questions/{q['id']}").status_code == 404
    assert client.post(f"/api/learning/trash/{q['id']}/restore").status_code == 200
    assert client.get(f"/api/learning/exams/{exam['id']}").json()['completed'] == 1
    with db() as conn:
        path = conn.execute('SELECT original_path FROM question_images WHERE task_id=?', (task['id'],)).fetchone()[0]
    assert Path(path).is_file()


def test_duplicates_legacy_hash_attach_without_changing_original(client):
    q = question(client)
    original = upload(client)
    with db() as conn:
        conn.execute('UPDATE question_images SET question_id=?,sha256=NULL WHERE task_id=?', (q['id'], original['id']))
    task = upload(client)
    suggestions = client.get(f"/api/tasks/{task['id']}").json()['duplicates']
    assert suggestions[0]['id'] == q['id'] and suggestions[0]['score'] == 1
    ready(task['id'])
    endpoint = f"/api/learning/duplicates/{task['id']}/attach"
    assert client.post(endpoint, json={'question_id': q['id']}).status_code == 200
    assert client.post(endpoint, json={'question_id': q['id']}).status_code == 200
    assert client.get('/api/questions').json()['total'] == 1
    assert client.get(f"/api/questions/{q['id']}").json()['question_text'] == q['question_text']
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409
    assert client.get('/api/learning/weekly').json()['practice']['total'] == 0
    with db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM practice_attempts WHERE task_id=?', (task['id'],)).fetchone()[0] == 1


def test_weekly_excludes_uncertain_and_counts_improvement(client):
    q = question(client)
    start = datetime.now(timezone(timedelta(hours=8)))
    start = (start - timedelta(days=start.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    for outcome, when in [('wrong', start - timedelta(hours=1)), ('correct', start + timedelta(seconds=1)), ('uncertain', start + timedelta(seconds=2))]:
        task = upload(client, practice_question_id=q['id'])
        ready(task['id'], outcome)
        assert client.post(f"/api/learning/attempts/{task['attempt_id']}/confirm", json={'outcome': outcome, 'same_error': True, 'rating': 'hard'}).status_code == 200
        with db() as conn:
            conn.execute('UPDATE practice_attempts SET confirmed_at=? WHERE id=?', (when.astimezone(timezone.utc).isoformat(), task['attempt_id']))
    report = client.get('/api/learning/weekly').json()
    assert report['practice'] == {'total': 2, 'graded': 1, 'correct': 1, 'accuracy': 1.0}
    assert report['previous_practice']['accuracy'] == 0
    assert report['improved_questions'] == 1 and report['repeated_errors'] == 0
    assert client.get('/api/learning/weekly?offset=1').json()['practice']['total'] == 1


def test_worker_routes_practice_to_new_analysis_and_preserves_original(client, monkeypatch):
    q = question(client)
    captured = []

    async def analyze(self, images, original, task_id):
        captured.append((images, original, task_id))
        return {'outcome': 'wrong', 'same_error': True, 'first_error_step': '本次符号错误'}

    monkeypatch.setattr(OpenAICompatibleClient, 'analyze_attempt', analyze)
    task = upload(client, practice_question_id=q['id'])
    result = client.post(f"/api/tasks/{task['id']}/analyze")
    assert result.status_code == 200 and result.json()['status'] == 'waiting_confirmation'
    assert result.json()['result']['outcome'] == 'wrong'
    assert captured[0][1]['id'] == q['id'] and captured[0][2] == task['id']
    assert client.get(f"/api/questions/{q['id']}").json()['first_error_step'] == q['first_error_step']


def test_worker_includes_original_question_image_in_practice_analysis(client, monkeypatch):
    q = question(client)
    original = upload(client)
    with db() as conn:
        original_image = conn.execute('SELECT id FROM question_images WHERE task_id=?', (original['id'],)).fetchone()['id']
        conn.execute('UPDATE question_images SET question_id=?,image_type=? WHERE task_id=?', (q['id'], 'question', original['id']))
    captured = []

    async def analyze(self, images, original, task_id):
        captured.append(images)
        return {'outcome': 'wrong', 'same_error': True}

    monkeypatch.setattr(OpenAICompatibleClient, 'analyze_attempt', analyze)
    task = upload(client, practice_question_id=q['id'])
    assert client.post(f"/api/tasks/{task['id']}/analyze").status_code == 200
    assert {image['id'] for image in captured[0]} >= {original_image}


def test_text_duplicate_suggestion_is_subject_scoped(client):
    q = question(client)
    task = upload(client)
    ready(task['id'])
    with db() as conn:
        conn.execute('UPDATE ai_results SET result_json=? WHERE task_id=?', (json.dumps({'question_text': q['question_text'], 'subject': 'math_calculus'}), task['id']))
    assert client.get(f"/api/tasks/{task['id']}").json()['duplicates'][0]['id'] == q['id']
    with db() as conn:
        conn.execute('UPDATE ai_results SET result_json=? WHERE task_id=?', (json.dumps({'question_text': q['question_text'], 'subject': 'ds'}), task['id']))
    assert client.get(f"/api/tasks/{task['id']}").json()['duplicates'] == []
