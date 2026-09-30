"""Real-model smoke test in a disposable container, using synthetic materials only."""
import json
from fastapi.testclient import TestClient
from app import db
from app.main import app

with TestClient(app) as client:
    item = db.put('materials', {
        'name': '架空の経営・業務資料.txt', 'categories': ['経営・管理'], 'version': 1,
        'chunks': [
            {'id': 'strategy', 'text': '当社の中期計画では、2030年度の海外売上構成比目標を40%とする。', 'categories': ['経営・管理']},
            {'id': 'expenses', 'text': '経費精算では領収書の提出が必要であり、部門長の承認を受ける。', 'categories': ['経営・管理']},
            {'id': 'incident', 'text': '情報漏えいが発生した場合は直ちに上長へ報告し、記録は5年間保存する。', 'categories': ['経営・管理']},
        ],
    })
    response = client.post('/api/generate-direct', json={
        'material_ids': [item['id']], 'question_type': 'choice', 'question_count': 3,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['accepted_count'] >= 2, result
    for question in result['questions']:
        assert question['quality_review']['acceptable'] is True
        assert all(question['quality_review']['checks'].values())
        assert question['source_references'][0]['material_id'] == item['id']
        assert client.post('/api/grade', json={'question': question, 'answer': question['answer']}).json()['correct']
    print(json.dumps({
        'requested': result['requested_count'], 'accepted': result['accepted_count'],
        'rejected': result['rejected_count'], 'attempted': result['attempted_count'],
        'stop_reason': result['stop_reason'], 'checks': 'all passed',
        'tested_concepts': [question['tested_concept'] for question in result['questions']],
    }, ensure_ascii=False))
