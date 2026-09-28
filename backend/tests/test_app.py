import io
import socket
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import db
from app import main as main_module
from app.main import app
from app.parser import MAX_CHUNK_CHARS
from app.providers import OpenAIAPIError


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    with TestClient(app) as c:
        yield c


def test_seed_validation_history(client):
    qs = client.get("/api/bootstrap").json()["questions"]
    assert len(qs) == 6
    q = qs[0]
    assert (
        client.post("/api/questions", json={**q, "answer": "不存在"}).status_code == 422
    )
    for _ in range(2):
        assert (
            client.put("/api/questions/" + q["id"], json=q).json()["status"]
            == "active"
        )
    assert len(client.get("/api/questions/" + q["id"] + "/history").json()) == 2


def test_grading(client):
    qs = client.get("/api/bootstrap").json()["questions"]
    for q, answer in [(qs[2], " 粗 利 。"), (qs[3], "検証")]:
        assert client.post("/api/grade", json={"question": q, "answer": answer}).json()[
            "correct"
        ]
    assert (
        client.post("/api/grade", json={"question": qs[0], "answer": "違う"}).json()[
            "correct"
        ]
        is False
    )
    assert (
        client.post("/api/grade", json={"question": qs[4], "answer": "回答"}).json()[
            "score"
        ]
        is None
    )


def test_attempt_stale_write(client):
    assert (
        client.put(
            "/api/attempts/a", json={"revision": 5, "answers": {"q": "new"}}
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/attempts/a", json={"revision": 4, "answers": {"q": "old"}}
        ).json()["answers"]["q"]
        == "new"
    )
    assert db.get("attempts", "a")["answers"] == {"q": "new"}


def test_full_workflow(client):
    r = client.post(
        "/api/uploads",
        files={
            "file": (
                "material.txt",
                "個人情報は所定の手順に沿って管理する。漏えい時には速やかに報告する。".encode(),
            )
        },
        data={"kind": "materials"},
    )
    assert r.status_code == 200, r.text
    m = r.json()
    m["categories"] = ["コンプライアンス"]
    m["chunks"][0]["categories"] = m["categories"]
    assert client.put("/api/documents/materials/" + m["id"], json=m).status_code == 200
    manual_question = client.post(
        "/api/questions",
        json={
            "body": "報告が必要な場面を説明してください。",
            "question_type": "short",
            "answer": "漏えい時",
        },
    )
    assert manual_question.status_code == 200
    assert manual_question.json()["question_type"] == "short"
    recipe = client.get("/api/bootstrap").json()["recipes"][0]
    assert client.put("/api/recipes/" + recipe["id"], json=recipe).status_code == 200
    r = client.post("/api/generate", json={"recipe_id": recipe["id"], "count": 2})
    assert r.status_code == 200, r.text
    assert len(r.json()["questions"]) == 6
    q = r.json()["questions"][0]
    assert q["source_references"][0]["material_id"] == m["id"] and q["warnings"]
    assert client.post("/api/questions/" + q["id"] + "/regenerate").status_code == 200
    assert (
        client.get("/api/documents/materials/" + m["id"] + "/file")
        .content.decode()
        .startswith("個人情報")
    )


def test_past_exam_registration_is_removed(client):
    assert "exams" not in client.get("/api/bootstrap").json()
    assert client.get("/api/documents/exams/old-id").status_code == 404
    upload = client.post(
        "/api/uploads",
        files={"file": ("old-exam.txt", "本文".encode())},
        data={"kind": "exams"},
    )
    assert upload.status_code == 422
    url = client.post(
        "/api/urls", json={"url": "https://example.com", "kind": "exams"}
    )
    assert url.status_code == 422


def test_long_material_is_split_and_analyzed_in_bounded_batches(client, monkeypatch):
    calls = []

    def classify(self, chunks):
        calls.append(sum(len(chunk["text"]) for chunk in chunks))
        return ["長文規程"]

    monkeypatch.setattr("app.providers.MockProvider.classify_material", classify)
    long_text = ("業務手順を確認して報告する。\n" * 8_000).encode()
    r = client.post(
        "/api/uploads",
        files={"file": ("long.txt", long_text)},
        data={"kind": "materials"},
    )
    assert r.status_code == 200, r.text
    material = r.json()
    assert material["chunking"]["auto_split"] is True
    assert len(material["chunks"]) > 1
    assert all(len(c["text"]) <= MAX_CHUNK_CHARS for c in material["chunks"])
    assert len(calls) == material["chunking"]["analysis_batch_count"] > 1
    assert max(calls) <= 48_000
    assert all(c["categories"] == ["長文規程"] for c in material["chunks"])


def test_generation_uses_only_selected_materials(client):
    materials = []
    for name, content in [
        ("selected.txt", "選択した資料にだけある正しい記述。"),
        ("excluded.txt", "選択していない資料にだけある記述。"),
    ]:
        material = client.post(
            "/api/uploads",
            files={"file": (name, content.encode())},
            data={"kind": "materials"},
        ).json()
        material["categories"] = ["対象"]
        material["chunks"][0]["categories"] = ["対象"]
        client.put("/api/documents/materials/" + material["id"], json=material)
        materials.append(material)

    recipe = client.get("/api/bootstrap").json()["recipes"][0]
    recipe.update({"category": "対象", "material_ids": [materials[0]["id"]]})
    assert client.put("/api/recipes/" + recipe["id"], json=recipe).status_code == 200

    result = client.post(
        "/api/generate", json={"recipe_id": recipe["id"], "count": 1}
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert {
        ref["material_id"]
        for question in payload["questions"]
        for ref in question["source_references"]
    } == {materials[0]["id"]}
    assert payload["sets"][0]["material_ids"] == [materials[0]["id"]]
    assert materials[1]["name"] not in payload["sets"][0]["material_names"]


def test_generation_splits_long_source_across_provider_calls(client, monkeypatch):
    material = client.post(
        "/api/uploads",
        files={
            "file": (
                "long.txt",
                ("長い資料の根拠文です。\n" * 15_000).encode(),
            )
        },
        data={"kind": "materials"},
    ).json()
    material["categories"] = ["対象"]
    for chunk in material["chunks"]:
        chunk["categories"] = ["対象"]
    client.put("/api/documents/materials/" + material["id"], json=material)
    recipe = client.get("/api/bootstrap").json()["recipes"][0]
    recipe.update(
        {
            "category": "対象",
            "material_ids": [material["id"]],
            "major_count": 1,
            "sub_count": 3,
        }
    )
    client.put("/api/recipes/" + recipe["id"], json=recipe)

    original = __import__(
        "app.providers", fromlist=["MockProvider"]
    ).MockProvider.generate_question_set
    call_sizes = []

    def generate(self, call_recipe, chunks):
        call_sizes.append(sum(len(chunk["text"]) for chunk in chunks))
        return original(self, call_recipe, chunks)

    monkeypatch.setattr("app.providers.MockProvider.generate_question_set", generate)
    result = client.post("/api/generate", json={"recipe_id": recipe["id"]})
    assert result.status_code == 200, result.text
    assert len(call_sizes) > 1
    assert max(call_sizes) <= 60_000
    assert len(result.json()["questions"]) == 3


def test_generation_also_splits_legacy_unbounded_chunks(client, monkeypatch):
    material = db.put(
        "materials",
        {
            "name": "legacy.txt",
            "categories": ["対象"],
            "chunks": [
                {
                    "id": "legacy-chunk",
                    "text": "従業員は事故発生時に上長へ報告し、記録を保存する。\n" * 20_000,
                    "categories": ["対象"],
                }
            ],
            "questions": [],
        },
    )
    recipe = client.get("/api/bootstrap").json()["recipes"][0]
    recipe.update(
        {
            "category": "対象",
            "material_ids": [material["id"]],
            "major_count": 1,
            "sub_count": 3,
        }
    )
    client.put("/api/recipes/" + recipe["id"], json=recipe)
    sizes = []
    original = __import__(
        "app.providers", fromlist=["MockProvider"]
    ).MockProvider.generate_question_set

    def generate(self, call_recipe, chunks):
        sizes.append(max(len(c["text"]) for c in chunks))
        return original(self, call_recipe, chunks)

    monkeypatch.setattr("app.providers.MockProvider.generate_question_set", generate)
    response = client.post("/api/generate", json={"recipe_id": recipe["id"]})
    assert response.status_code == 200, response.text
    assert sizes and max(sizes) <= MAX_CHUNK_CHARS
    assert response.json()["questions"][0]["source_references"][0][
        "id"
    ].startswith("legacy-chunk:segment:")


def test_generation_rejects_missing_selected_material(client):
    recipe = client.get("/api/bootstrap").json()["recipes"][0]
    recipe["material_ids"] = ["missing-material"]
    client.put("/api/recipes/" + recipe["id"], json=recipe)
    response = client.post("/api/generate", json={"recipe_id": recipe["id"]})
    assert response.status_code == 422
    assert "見つかりません" in response.json()["detail"]


def test_multimodal_upload_uses_selected_method(client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr(
        "app.providers.OpenAIProvider.extract_pdf",
        lambda self, path: [
            {
                "id": "vision-chunk",
                "text": "画像から復元した本文",
                "categories": [],
                "page_number": 1,
            }
        ],
    )
    monkeypatch.setattr(
        "app.providers.OpenAIProvider.classify_material",
        lambda self, chunks: ["情報管理"],
    )
    r = client.post(
        "/api/uploads",
        files={"file": ("source.pdf", b"%PDF-1.4 test")},
        data={"kind": "materials", "analysis_method": "multimodal"},
    )
    assert r.status_code == 200, r.text
    document = r.json()
    assert document["analysis_method"] == "multimodal"
    assert document["analysis_provider"] == "openai"
    assert document["chunks"][0]["text"] == "画像から復元した本文"
    assert document["chunks"][0]["categories"] == ["情報管理"]


def test_multimodal_upload_requires_openai(client):
    r = client.post(
        "/api/uploads",
        files={"file": ("source.pdf", b"%PDF-1.4 test")},
        data={"kind": "materials", "analysis_method": "multimodal"},
    )
    assert r.status_code == 503


def test_xlsx_and_invalid(client):
    wb = Workbook()
    wb.active.title = "規程"
    wb.active.append(["部署", "内容"])
    wb.active.append(["総務", "報告する"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(
        "/api/uploads",
        files={"file": ("test.xlsx", buf.getvalue())},
        data={"kind": "materials"},
    )
    assert r.status_code == 200, r.text
    chunk = r.json()["chunks"][0]
    assert (
        chunk["sheet_name"] == "規程"
        and chunk["cell_range"] == "A1:B2"
        and "総務" in chunk["text"]
    )
    assert (
        client.post("/api/uploads", files={"file": ("bad.exe", b"test")}).status_code
        == 415
    )
    assert (
        client.post("/api/uploads", files={"file": ("bad.pdf", b"bad")}).status_code
        == 422
    )


def test_url_powerpoint_and_direct_generation(client, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.215.14", 443))])
    real_client = httpx.Client
    def respond(request):
        assert request.url.host == "93.184.215.14"
        assert request.headers["host"] == "example.com"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<article><h1>評価基準</h1><p>報告は当日中に行う。</p></article>",
        )
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(main_module.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    web = client.post("/api/urls", json={"url": "https://example.com/guide"})
    assert web.status_code == 200, web.text
    assert web.json()["source_url"] == "https://example.com/guide"
    assert "報告は当日中" in web.json()["chunks"][0]["text"]
    summary = next(item for item in client.get("/api/bootstrap").json()["materials"] if item["id"] == web.json()["id"])
    assert "text" not in summary["chunks"][0]
    assert client.get("/api/documents/materials/" + web.json()["id"]).json()["chunks"][0]["text"]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="p" xmlns:a="a"><a:t>管理職は部下の目標を確認する。</a:t></p:sld>')
    slides = client.post("/api/uploads", files={"file": ("training.pptx", buffer.getvalue())})
    assert slides.status_code == 200, slides.text
    assert slides.json()["chunks"][0]["slide_number"] == 1

    for kind in ("choice", "blank", "word", "short"):
        generated = client.post("/api/generate-direct", json={
            "material_ids": [web.json()["id"], slides.json()["id"]],
            "question_type": kind,
            "question_count": 2,
        })
        assert generated.status_code == 200, generated.text
        questions = generated.json()["questions"]
        assert len(questions) == 2 and all(q["question_type"] == kind for q in questions)
        assert all(q["source_references"] for q in questions)
        assert any(ref.get("slide_number") == 1 for q in questions for ref in q["source_references"])
        if kind in ("blank", "word"):
            assert all("（　）" in q["body"] for q in questions)


def test_url_rejects_private_addresses(client):
    response = client.post("/api/urls", json={"url": "http://127.0.0.1/internal"})
    assert response.status_code == 422
    assert "非公開" in response.json()["detail"]


def test_direct_blank_generation_keeps_requested_shape(client, monkeypatch):
    material = client.post(
        "/api/uploads",
        files={"file": ("policy.txt", "事故は直ちに上長へ報告する。".encode())},
    ).json()
    monkeypatch.setattr("app.providers.MockProvider.generate_question_set", lambda self, recipe, chunks: [{
        "body": "事故の際、誰へ報告しますか。",
        "question_type": "blank",
        "choices": [],
        "answer": "上長",
        "source_references": [{"chunk_id": chunks[0]["id"]}],
    }])
    response = client.post("/api/generate-direct", json={
        "material_ids": [material["id"]], "question_type": "blank", "question_count": 1,
    })
    assert response.status_code == 200, response.text
    question = response.json()["questions"][0]
    assert "（　）" in question["body"]
    assert question["answer"] == "上長"


def test_direct_generation_batches_can_continue_across_source(client):
    text = "\n".join(
        f"範囲{index:05}の運用手順を読み、担当者へ確認してから記録します。"
        for index in range(7000)
    )
    material = client.post(
        "/api/uploads", files={"file": ("large.txt", text.encode())}
    ).json()
    payload = {
        "recipe_id": "bulk-generation-review",
        "material_ids": [material["id"]],
        "question_type": "word",
        "question_count": 1,
    }
    first = client.post("/api/generate-direct", json=payload)
    second = client.post("/api/generate-direct", json=payload)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_question = first.json()["questions"][0]
    second_question = second.json()["questions"][0]
    assert first_question["question_set_id"] != second_question["question_set_id"]
    assert first_question["source_references"][0]["id"] != second_question["source_references"][0]["id"]
    assert len([r for r in db.all_items("recipes") if r["id"] == payload["recipe_id"]]) == 1


def test_repeated_generation_covers_all_source_batches_in_question_count(client):
    chunks = []
    for index in range(47 * 5):
        prefix = f"重要用語{index:04}が資料に記載されています。"
        chunks.append({
            "id": f"source-{index}",
            "text": prefix + "補足" * ((12_000 - len(prefix) - 1) // 2) + "。",
            "categories": ["対象"],
        })
    material = db.put(
        "materials",
        {"name": "large-library.txt", "categories": ["対象"], "chunks": chunks, "questions": []},
    )
    recipe_id = "large-library-generation"
    references = set()
    for run_index, question_count in enumerate((20, 20, 7)):
        response = client.post("/api/generate-direct", json={
            "recipe_id": recipe_id,
            "material_ids": [material["id"]],
            "question_type": "word",
            "question_count": question_count,
        })
        assert response.status_code == 200, response.text
        payload = response.json()
        references.update(
            reference["id"]
            for question in payload["questions"]
            for reference in question["source_references"]
        )
        if run_index == 0:
            changed_conditions = client.post("/api/generate-direct", json={
                "material_ids": [material["id"]],
                "question_type": "blank",
                "question_count": 1,
                "difficulty": "基礎",
            })
            assert changed_conditions.status_code == 200, changed_conditions.text
            changed_source_id = changed_conditions.json()["questions"][0][
                "source_references"
            ][0]["id"]
            assert changed_source_id not in references
            for question in payload["questions"]:
                db.delete("questions", question["id"])

    assert len(references) == 47
    assert {f"source-{index}" for index in range(0, 47 * 5, 5)} <= references


def test_registered_material_can_be_deleted_with_original_file(client):
    material = client.post(
        "/api/uploads",
        files={"file": ("removable.txt", "保存していた本文です。".encode())},
    ).json()
    generated = client.post("/api/generate-direct", json={
        "material_ids": [material["id"]],
        "question_type": "word",
        "question_count": 1,
    })
    assert generated.status_code == 200, generated.text
    question = generated.json()["questions"][0]
    original = db.DATA / "uploads" / material["file_path"]
    assert original.exists()

    response = client.delete(f"/api/documents/materials/{material['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == {"id": material["id"], "deleted": True}
    assert db.get("materials", material["id"]) is None
    saved_question = db.get("questions", question["id"])
    assert saved_question["source_references"][0]["material_id"] == material["id"]
    assert saved_question["source_references"][0]["text"]
    assert not original.exists()
    assert client.get(f"/api/documents/materials/{material['id']}").status_code == 404
    assert client.delete("/api/documents/materials/missing").status_code == 404


def test_multimodal_upload_returns_openai_api_reason(client, monkeypatch):
    class FailingOpenAI:
        name = "openai"

        def extract_pdf(self, path):
            raise OpenAIAPIError(400, "Unsupported PDF input for this request")

    monkeypatch.setattr(main_module, "provider", lambda: FailingOpenAI())
    response = client.post(
        "/api/uploads",
        files={"file": ("scan.pdf", b"synthetic pdf bytes")},
        data={"analysis_method": "multimodal"},
    )
    assert response.status_code == 502
    assert "HTTP 400" in response.json()["detail"]
    assert "Unsupported PDF input" in response.json()["detail"]


def test_guards(client):
    assert (
        client.post(
            "/api/categories",
            headers={"origin": "https://evil.example"},
            json={"name": "x"},
        ).status_code
        == 403
    )
    assert (
        client.post("/api/generate", json={"recipe_id": "sample-recipe"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/generate", json={"recipe_id": "sample-recipe", "count": 100}
        ).status_code
        == 422
    )
