from app.providers import OpenAIProvider
from app.main import app
from app import db
from fastapi.testclient import TestClient


class Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": '{"categories":["情報管理"]}'}
                    ]
                }
            ]
        }


class Client:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def post(self, url, headers, json):
        assert url == "https://api.openai.com/v1/responses"
        assert headers["Authorization"] == "Bearer test-not-a-real-key"
        assert json["store"] is False
        assert json["text"]["format"]["type"] == "json_object"
        return Response()


def test_openai_adapter_transport(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr("app.providers.httpx.Client", Client)
    assert OpenAIProvider().classify_material([{"text": "資料"}]) == ["情報管理"]


def test_invalid_generation_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    with TestClient(app) as client:
        material = client.post(
            "/api/uploads", files={"file": ("m.txt", "これは資料の本文です。".encode())}
        ).json()
        material["chunks"][0]["categories"] = ["コンプライアンス"]
        client.put("/api/documents/materials/" + material["id"], json=material)
        monkeypatch.setattr(
            "app.providers.MockProvider.generate_question_set",
            lambda *args: [{"body": "x", "choices": ["a"], "answer": "b"}] * 3,
        )
        assert (
            client.post(
                "/api/generate", json={"recipe_id": "sample-recipe"}
            ).status_code
            == 422
        )
        assert len(db.all_items("questions")) == 6
        assert db.all_items("sets") == []
