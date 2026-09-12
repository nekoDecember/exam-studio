import io
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from app import db
from app.main import app


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
