import pymupdf
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.providers import OpenAIAPIError, OpenAIProvider


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


def test_openai_api_error_message_is_preserved(monkeypatch):
    class ErrorResponse:
        status_code = 400

        def raise_for_status(self):
            import httpx

            request = httpx.Request("POST", "https://api.openai.com/v1/responses")
            response = httpx.Response(
                self.status_code,
                request=request,
                json={"error": {"message": "The selected model cannot process this file."}},
            )
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

        def json(self):
            return {"error": {"message": "The selected model cannot process this file."}}

    class ErrorClient(Client):
        def post(self, url, headers, json):
            return ErrorResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr("app.providers.httpx.Client", ErrorClient)
    try:
        OpenAIProvider().classify_material([{"text": "資料"}])
    except OpenAIAPIError as exc:
        assert "HTTP 400" in str(exc)
        assert "cannot process this file" in str(exc)
    else:
        raise AssertionError("Expected the OpenAI API error to be surfaced")


class VisionResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"pages":[{"page_number":1,"text":"正しい日本語の本文"}]}',
                        }
                    ]
                }
            ]
        }


class VisionClient:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def post(self, url, headers, json):
        assert url == "https://api.openai.com/v1/responses"
        content = json["input"][0]["content"]
        assert content[0]["type"] == "input_file"
        assert content[0]["filename"] == "source.pdf"
        assert content[0]["file_data"].startswith("data:application/pdf;base64,")
        assert content[0]["detail"] == "high"
        assert content[1] == {
            "type": "input_text",
            "text": "このPDFをページ単位で正確に文字起こしし、JSON形式で返してください。",
        }
        return VisionResponse()


def test_openai_multimodal_pdf_input(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr("app.providers.httpx.Client", VisionClient)
    path = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    chunks = OpenAIProvider().extract_pdf(path)
    assert chunks[0]["page_number"] == 1
    assert chunks[0]["text"] == "正しい日本語の本文"


def test_multimodal_pdf_is_sent_in_small_page_groups(tmp_path, monkeypatch):
    path = tmp_path / "long.pdf"
    with pymupdf.open() as pdf:
        for _ in range(7):
            pdf.new_page()
        pdf.save(path)
    calls = []

    def ask(self, instruction, data=None, input_items=None):
        calls.append(input_items)
        return {"pages": [{"page_number": 1, "text": "確認した内容"}]}

    monkeypatch.setattr(OpenAIProvider, "ask", ask)
    chunks = OpenAIProvider().extract_pdf(path)
    assert len(calls) == 2
    assert [chunk["page_number"] for chunk in chunks] == [1, 6]


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
