"""Image edits, resumable confirmation and task browsing use isolated local data."""
import asyncio
import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.db import db, now_iso
from app.services.ai.openai_compatible import OpenAICompatibleClient
from app.services.images import edit_image
from app.services.worker import process_task


def pixels():
    image = Image.new('RGB', (100, 60), 'red')
    image.paste('blue', (50, 0, 100, 60))
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


def upload(client, raw=None, edits=None, **extra):
    return client.post('/api/tasks/upload', data={
        'image_types': 'question', 'image_edits': json.dumps(edits or []), **extra,
    }, files=[('files', ('photo.png', raw if raw is not None else pixels(), 'image/png'))])


def ready(task_id):
    version = now_iso()
    with db() as conn:
        conn.execute("UPDATE ai_tasks SET status='waiting_confirmation',updated_at=? WHERE id=?", (version, task_id))
        conn.execute('INSERT OR REPLACE INTO ai_results(task_id,result_json,created_at) VALUES(?,?,?)',
                     (task_id, json.dumps({'question_latex': 'x^2'}), version))
    return version


def test_rotation_crop_pixels_and_exif_preserve_original(work_dir):
    path = work_dir / 'photo.png'
    path.write_bytes(pixels())
    data, mime, _ = edit_image(path, {'rotation': 90, 'x': 0, 'y': .5, 'width': 1, 'height': .5})
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (60, 50)
        red, green, blue = image.getpixel((30, 25))
        assert blue > 240 and red < 10 and green < 10
    assert mime == 'image/jpeg' and path.read_bytes() == pixels()
    exif = Image.Exif()
    exif[274] = 6  # Camera orientation: clockwise 90 degrees.
    with Image.open(path) as image:
        image.save(work_dir / 'oriented.jpg', exif=exif)
    oriented, _, _ = edit_image(work_dir / 'oriented.jpg', {})
    assert Image.open(io.BytesIO(oriented)).size == (60, 100)


def test_upload_preview_and_ai_receive_same_crop(client, monkeypatch):
    captured = []

    async def recognize(self, images, task_id=None, question_label=''):
        captured.extend(await self._image_parts(images))
        return {'question_text': '裁剪测试', 'question_latex': 'x^2'}

    monkeypatch.setattr(OpenAICompatibleClient, 'recognize_and_analyze', recognize)
    edit = {'rotation': 90, 'x': 0, 'y': .5, 'width': 1, 'height': .5}
    response = upload(client, edits=[edit])
    assert response.status_code == 200
    task_id = response.json()['id']
    asyncio.run(process_task(task_id))
    task = client.get(f'/api/tasks/{task_id}').json()
    assert task['status'] == 'confirmed'
    image = task['images'][0]
    assert image['has_edit']
    endpoint = f"/api/tasks/images/{image['id']}"
    assert client.get(endpoint).content == pixels()
    preview = client.get(endpoint + '?edited=true')
    assert preview.status_code == 200
    assert Image.open(io.BytesIO(preview.content)).size == (60, 50)
    message = next(part for part in captured if part['type'] == 'image_url')
    sent = base64.b64decode(message['image_url']['url'].split(',', 1)[1])
    assert sent == preview.content
    with db() as conn:
        stored = conn.execute('SELECT original_path,edit_json FROM question_images WHERE id=?', (image['id'],)).fetchone()
    assert Path(stored['original_path']).read_bytes() == pixels()
    assert json.loads(stored['edit_json']) == edit


def test_original_thumbnail_is_cached_without_changing_source(client, work_dir):
    raw = pixels()
    task_id = upload(client, raw=raw).json()['id']
    image = client.get(f'/api/tasks/{task_id}').json()['images'][0]
    endpoint = f"/api/tasks/images/{image['id']}?thumbnail=true"
    first = client.get(endpoint)
    assert first.status_code == 200
    assert first.headers['cache-control'].startswith('public, max-age=31536000')
    with db() as conn:
        original = Path(conn.execute('SELECT original_path FROM question_images WHERE id=?', (image['id'],)).fetchone()[0])
    cache_files = list((work_dir / 'data' / 'thumbnail_cache').glob('*.jpg'))
    assert len(cache_files) == 1
    second = client.get(endpoint)
    assert second.content == first.content
    assert original.read_bytes() == raw


@pytest.mark.parametrize('edits', [
    'not json', '{}', '[0]', '[false]', '[null,null]',
    '[{"rotation":45}]', '[{"x":0.7,"width":0.5}]',
    '[{"height":0}]', '[{"x":NaN}]', '[{"width":Infinity}]',
])
def test_invalid_crop_rejected_before_creating_task(client, edits):
    response = client.post('/api/tasks/upload', data={'image_edits': edits},
                           files=[('files', ('x.png', pixels(), 'image/png'))])
    assert response.status_code == 422
    assert client.get('/api/tasks?include_practice=true').json()['total'] == 0
    with db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM question_images').fetchone()[0] == 0


def test_bad_image_crop_fails_but_original_and_retry_survive(client):
    raw = b'undecodable original retained'
    task_id = upload(client, raw=raw, edits=[{'rotation': 90}]).json()['id']
    asyncio.run(process_task(task_id))
    task = client.get(f'/api/tasks/{task_id}').json()
    assert task['status'] == 'failed'
    endpoint = f"/api/tasks/images/{task['images'][0]['id']}"
    assert client.get(endpoint).content == raw
    assert client.get(endpoint + '?edited=true').status_code == 422
    assert client.post(f'/api/tasks/{task_id}/retry').status_code == 200
    assert client.get(endpoint).content == raw


def test_retry_can_send_user_recognition_hint_to_ai(client, monkeypatch):
    hints = []

    async def recognize(self, images, task_id=None, question_label='', recognition_hint=''):
        hints.append(recognition_hint)
        return {'question_text': '按提示重新识别后的题目'}

    monkeypatch.setattr(OpenAICompatibleClient, 'recognize_and_analyze', recognize)
    task_id = upload(client).json()['id']
    asyncio.run(process_task(task_id))
    response = client.post(f'/api/tasks/{task_id}/retry', json={
        'recognition_hint': '题干第二行的 2π 不要识别成 π',
    })
    assert response.status_code == 200
    assert response.json()['recognition_hint'] == '题干第二行的 2π 不要识别成 π'
    asyncio.run(process_task(task_id))
    assert hints == ['', '题干第二行的 2π 不要识别成 π']


def test_upload_can_send_recognition_hint_on_first_attempt(client, monkeypatch):
    hints = []

    async def recognize(self, images, task_id=None, question_label='', recognition_hint=''):
        hints.append(recognition_hint)
        return {'question_text': '按上传提示识别出的题目'}

    monkeypatch.setattr(OpenAICompatibleClient, 'recognize_and_analyze', recognize)
    hint = '题目中的方框表示矩阵，不是空白；分数线分子为 x+1。'
    response = upload(client, recognition_hint=hint)
    assert response.status_code == 200
    task_id = response.json()['id']
    assert client.get(f'/api/tasks/{task_id}').json()['recognition_hint'] == hint
    asyncio.run(process_task(task_id))
    assert hints == [hint]


def test_saved_latex_draft_resumes_and_does_not_confirm(client):
    task_id = upload(client).json()['id']
    version = ready(task_id)
    endpoint = f'/api/tasks/{task_id}/draft'
    draft = {'subject': 'math_calculus', 'question_text': '修正后的题目', 'question_latex': r'\frac{x}{2}'}
    body = {'result': draft, 'task_version': version}
    assert client.put(endpoint, json=body).status_code == 200
    task = client.get(f'/api/tasks/{task_id}').json()
    assert task['draft'] == draft
    assert task['result']['question_latex'] == 'x^2'
    assert task['status'] == 'waiting_confirmation'
    assert client.get('/api/questions').json()['total'] == 0
    # An explicit confirmation stores the edited result.
    confirmed = client.post(f'/api/questions/{task_id}/confirm', json={**draft, 'task_id': task_id})
    assert confirmed.status_code == 200
    assert confirmed.json()['question_latex'] == draft['question_latex']
    assert client.put(endpoint, json=body).status_code == 409


def test_draft_rejects_stale_task_version(client):
    task_id = upload(client).json()['id']
    version = ready(task_id)
    body = {'result': {'question_latex': 'x'}, 'task_version': version}
    assert client.put(f'/api/tasks/{task_id}/draft', json=body).status_code == 200
    with db() as conn:
        conn.execute('UPDATE ai_tasks SET updated_at=? WHERE id=?', ('new-version', task_id))
    assert client.get(f'/api/tasks/{task_id}').json()['draft'] is None
    assert client.put(f'/api/tasks/{task_id}/draft', json=body).status_code == 409


def test_task_center_practice_filter_and_pagination(client):
    q = client.post('/api/questions', json={'question_text': '题目'}).json()
    normal = upload(client).json()['id']
    practice = upload(client, practice_question_id=q['id']).json()['id']
    ready(normal)
    with db() as conn:
        conn.execute("UPDATE ai_tasks SET status='failed',error='测试错误' WHERE id=?", (practice,))
    default = client.get('/api/tasks').json()
    assert default['total'] == 1 and default['items'][0]['id'] == normal
    all_tasks = client.get('/api/tasks?include_practice=true&limit=1').json()
    assert all_tasks['total'] == 2 and len(all_tasks['items']) == 1
    assert all_tasks['items'][0]['id'] == practice
    assert all_tasks['items'][0]['practice_question_id'] == q['id']
    second = client.get('/api/tasks?include_practice=true&limit=1&offset=1').json()
    assert second['items'][0]['id'] == normal
    assert client.get('/api/tasks?include_practice=true&status=failed').json()['total'] == 1
    assert client.get('/api/tasks?include_practice=true&status=waiting_confirmation').json()['total'] == 1
    assert client.get('/api/tasks?include_practice=true&status=active').json()['total'] == 0


def test_batch_task_actions_only_touch_allowed_statuses(client):
    retryable = upload(client).json()['id']
    active = upload(client).json()['id']
    failed = upload(client).json()['id']
    with db() as conn:
        conn.execute("UPDATE ai_tasks SET status='failed' WHERE id=?", (retryable,))
        conn.execute("UPDATE ai_tasks SET status='failed' WHERE id=?", (failed,))
    result = client.post('/api/tasks/batch-retry', json={'task_ids': [retryable, active, 99999]})
    assert result.status_code == 200
    assert result.json()['updated'] == [retryable]
    assert len(result.json()['skipped']) == 2
    result = client.post('/api/tasks/batch-cancel', json={'task_ids': [active, failed]})
    assert result.status_code == 200
    assert result.json()['updated'] == [active]
    assert len(result.json()['skipped']) == 1


def test_unresolved_tasks_are_not_buried_by_newer_archived_tasks(client):
    pending = upload(client).json()['id']
    ready(pending)
    failed = upload(client).json()['id']
    with db() as conn:
        conn.execute("UPDATE ai_tasks SET status='failed' WHERE id=?", (failed,))
        for _ in range(25):
            conn.execute("INSERT INTO ai_tasks(status,created_at,updated_at) VALUES('confirmed',?,?)", (now_iso(), now_iso()))
        conn.execute("INSERT INTO ai_tasks(status,created_at,updated_at) VALUES('cancelled',?,?)", (now_iso(), now_iso()))
    unresolved = client.get('/api/tasks?status=unresolved').json()
    assert unresolved['total'] == 2
    assert {item['id'] for item in unresolved['items']} == {pending, failed}
    archived = client.get('/api/tasks?status=archived&limit=20').json()
    assert archived['total'] == 26 and len(archived['items']) == 20
    assert all(item['status'] in ('confirmed', 'cancelled') for item in archived['items'])
    assert len(client.get('/api/tasks?status=archived&offset=20').json()['items']) == 6
    assert client.post(f'/api/questions/{pending}/confirm', json={'question_text': '题目', 'task_id': pending}).status_code == 200
    assert client.get('/api/tasks?status=unresolved').json()['total'] == 1
    assert client.get('/api/tasks?status=archived').json()['total'] == 27
