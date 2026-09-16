import io

import pytest
from app import db
from app.main import app
from app.parser import MAX_CHUNK_CHARS
from fastapi.testclient import TestClient
from openpyxl import Workbook


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
    for status in ["deleted", "active"]:
        q["status"] = status
        assert (
            client.put("/api/questions/" + q["id"], json=q).json()["status"] == status
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
    exam = client.post(
        "/api/uploads",
        files={
            "file": (
                "exam.txt",
                "第1問\n情報管理について選びなさい。\nA. 報告する\nB. 隠す\n正解：A\n第2問\n（1）確認する。".encode(),
            )
        },
        data={"kind": "exams"},
    ).json()
    assert len(exam["questions"]) == 2 and exam["questions"][0]["answer"] == "A"
    recipe = client.get("/api/bootstrap").json()["recipes"][0]
    recipe["reference_past_question_id"] = exam["questions"][0]["id"]
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

    def generate(self, call_recipe, chunks, style):
        call_sizes.append(sum(len(chunk["text"]) for chunk in chunks))
        return original(self, call_recipe, chunks, style)

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
                    "text": "旧データの長文。" * 20_000,
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

    def generate(self, call_recipe, chunks, style):
        sizes.append(max(len(c["text"]) for c in chunks))
        return original(self, call_recipe, chunks, style)

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
