def test_entry_page_revalidates_to_discover_latest_assets(client):
    response = client.get('/')
    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert response.headers['cache-control'] == 'no-cache, max-age=0, must-revalidate'
    assert '/assets/' in response.text
    assert client.get('/api/health').json() == {'status': 'ok'}
