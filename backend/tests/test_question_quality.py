import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import db
from app.main import app
from app.providers import MockProvider, OpenAIProvider, OpenAIAPIError
from app.question_quality import QualityReview, quality_review

CASES = json.loads(
    (Path(__file__).parent / "fixtures/question_quality.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_explicit_trivia_and_useful_recall_local_rules(case):
    # Local guards do not establish the model's semantic quality.
    review = MockProvider().review_question(case)
    assert review["acceptable"] is case["acceptable"]
    if not case["acceptable"]:
        assert case["reason_code"] in [reason["code"] for reason in review["reasons"]]


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"acceptable": True, "reasons": [], "warnings": []},
        {**quality_review(), "acceptable": "true"},
        {
            **quality_review(),
            "checks": {"learning_value": False, "grounded": True, "clear": True},
        },
        {**quality_review(), "acceptable": False},
    ],
)
def test_malformed_review_is_not_a_pass(result):
    with pytest.raises(ValidationError):
        QualityReview.model_validate(result)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    with TestClient(app) as connection:
        yield connection


def material(count=2):
    return db.put(
        "materials",
        {
            "name": "業務手順.txt",
            "categories": ["管理"],
            "chunks": [
                {
                    "id": f"unit-{i}",
                    "text": f"対象{i}：重要な業務では規定の管理手順を実施する。",
                    "categories": ["管理"],
                }
                for i in range(count)
            ],
        },
    )


def candidate(
    body="管理の対象を何と呼びますか。", answer="業務記録", reference="SOURCE_1"
):
    return {
        "body": body,
        "question_type": "word",
        "answer": answer,
        "explanation": "業務記録を管理します。",
        "source_references": [{"source_key": reference}],
    }


def generate(client, item, count=1, job=False):
    response = client.post(
        "/api/generation-jobs/direct" if job else "/api/generate-direct",
        json={
            "material_ids": [item["id"]],
            "question_type": "word",
            "question_count": count,
        },
    )
    assert response.status_code == (202 if job else 200), response.text
    return db.get("generation_jobs", response.json()["id"]) if job else response.json()


def test_bad_topic_moves_to_another_source_and_only_good_evidence_is_saved(
    client, monkeypatch
):
    calls = []

    def create(self, recipe, chunks):
        calls.append(chunks[0]["text"])
        return [
            candidate("この資料の書類名は何ですか。", "統合報告書")
            if len(calls) == 1
            else candidate()
        ]

    monkeypatch.setattr(MockProvider, "generate_question_set", create)
    result = generate(client, material(2))
    assert result["accepted_count"] == 1 and result["rejected_count"] == 1
    assert len(calls) == 2 and calls[0] != calls[1]
    assert result["questions"][0]["source_references"][0]["id"] == "unit-1"
    assert result["sets"][0]["used_source_locations"][0]["id"] == "unit-1"
    assert result["questions"][0]["quality_review"]["acceptable"] is True
    assert len(db.all_items("questions")) == 7


def test_repair_reuses_evidence_with_specific_feedback(client, monkeypatch):
    calls = []

    def create(self, recipe, chunks):
        calls.append((chunks[0]["text"], recipe.get("ng_feedback", [])))
        return [
            candidate("業務記録を何と呼びますか。") if len(calls) == 1 else candidate()
        ]

    monkeypatch.setattr(MockProvider, "generate_question_set", create)
    result = generate(client, material(1))
    assert result["accepted_count"] == 1 and len(calls) == 2
    assert calls[0][0] == calls[1][0] and "正答" in calls[1][1][0]


def test_invalid_reference_is_repaired_not_silently_attached(client, monkeypatch):
    calls = []

    def create(self, recipe, chunks):
        calls.append(recipe)
        return [candidate(reference="invented" if len(calls) == 1 else "SOURCE_1")]

    monkeypatch.setattr(MockProvider, "generate_question_set", create)
    result = generate(client, material(1))
    assert result["accepted_count"] == 1 and result["rejected_count"] == 1
    assert "SOURCE_1" in calls[1]["ng_feedback"][0]


def test_all_rejected_finishes_without_empty_set(client, monkeypatch):
    monkeypatch.setattr(
        MockProvider,
        "generate_question_set",
        lambda *args: [candidate("グラフのメモリ数は何個ですか。", "5")],
    )
    job = generate(client, material(3), count=5, job=True)
    assert job["status"] == "partial" and job["completed"] == job["accepted_count"] == 0
    assert job["stop_reason"] == "candidate_exhausted" and job["attempted_count"] == 3
    assert (
        not job["set_ids"]
        and db.all_items("sets") == []
        and len(db.all_items("questions")) == 6
    )


def test_heading_only_finishes_without_model_calls(client, monkeypatch):
    item = material(1)
    item["chunks"][0]["text"] = "目次"
    db.put("materials", item)
    monkeypatch.setattr(
        MockProvider,
        "generate_question_set",
        lambda *args: pytest.fail("No generation expected"),
    )
    job = generate(client, item, job=True)
    assert job["status"] == "partial" and job["attempted_count"] == 0


def test_partial_set_counts_actual_questions(client, monkeypatch):
    calls = []

    def create(self, recipe, chunks):
        calls.append(chunks)
        return [
            candidate()
            if len(calls) == 1
            else candidate("この資料のタイトルは何ですか。", "報告書")
        ]

    monkeypatch.setattr(MockProvider, "generate_question_set", create)
    job = generate(client, material(2), count=4, job=True)
    assert job["status"] == "partial" and job["accepted_count"] == job["completed"] == 1
    saved = db.get("sets", job["set_ids"][0])
    assert saved["question_count"] == 1 and saved["requested_count"] == 4
    assert (
        saved["generation_status"] == "partial"
        and len(saved["used_source_locations"]) == 1
    )


def test_budget_is_shared_across_twenty_question_batches(client, monkeypatch):
    calls = []

    def create(self, recipe, chunks):
        index = len(calls)
        calls.append(index)
        return [candidate(f"管理対象{index}の対応方針は何ですか。", f"記録保全{index}")]

    def review(self, data):
        return (
            quality_review()
            if len(calls) <= 20
            else quality_review(
                [{"code": "ambiguous_target", "message": "対象を明確にしてください"}]
            )
        )

    monkeypatch.setattr(MockProvider, "generate_question_set", create)
    monkeypatch.setattr(MockProvider, "review_question", review)
    job = generate(client, material(100), count=21, job=True)
    assert (
        len(calls) == 63
        and job["attempted_count"] == 63
        and job["rejected_count"] == 43
    )
    assert (
        job["status"] == "partial" and job["accepted_count"] == job["completed"] == 20
    )
    assert job["stop_reason"] == "attempt_limit" and len(job["set_ids"]) == 1


def test_review_failure_does_not_save_unreviewed_batch(client, monkeypatch):
    item, calls = material(2), []

    def create(self, recipe, chunks):
        calls.append(chunks)
        return [
            candidate(f"管理対象{len(calls)}の対応は何ですか。", f"記録{len(calls)}")
        ]

    def review(self, data):
        return quality_review() if len(calls) == 1 else {"acceptable": True}

    monkeypatch.setattr(MockProvider, "generate_question_set", create)
    monkeypatch.setattr(MockProvider, "review_question", review)
    response = client.post(
        "/api/generate-direct",
        json={
            "material_ids": [item["id"]],
            "question_type": "word",
            "question_count": 2,
        },
    )
    assert (
        response.status_code == 502
        and len(db.all_items("questions")) == 6
        and db.all_items("sets") == []
    )


def test_upstream_failure_keeps_earlier_saved_batch(client, monkeypatch):
    calls = []

    def create(self, recipe, chunks):
        index = len(calls)
        calls.append(index)
        if index == 20:
            raise OpenAIAPIError(429, "rate limit")
        return [candidate(f"管理対象{index}の方針は何ですか。", f"記録保全{index}")]

    monkeypatch.setattr(MockProvider, "generate_question_set", create)
    job = generate(client, material(30), count=21, job=True)
    assert job["status"] == "failed" and job["accepted_count"] == 20
    assert (
        len(job["set_ids"]) == 1
        and len(db.all_items("questions")) == 26
        and "429" in job["error"]
    )


def test_recipe_and_regeneration_use_quality_gate(client, monkeypatch):
    item = material(1)
    db.put(
        "recipes",
        {
            "id": "quality-recipe",
            "name": "品質確認",
            "category": "管理",
            "material_ids": [item["id"]],
            "question_type": "word",
            "major_count": 2,
            "sub_count": 2,
            "body_length": 50,
            "choice_count": 4,
            "blank_count": 1,
            "difficulty": "標準",
            "score_weight": 1,
        },
    )
    monkeypatch.setattr(
        MockProvider,
        "generate_question_set",
        lambda *args: [candidate("この資料のタイトルは何ですか。", "報告書")],
    )
    response = client.post(
        "/api/generation-jobs/recipe", json={"recipe_id": "quality-recipe", "count": 2}
    )
    job = db.get("generation_jobs", response.json()["id"])
    assert (
        job["status"] == "partial"
        and job["requested_count"] == 8
        and job["accepted_count"] == 0
    )
    question = db.put(
        "questions",
        {
            **candidate(),
            "recipe_id": "quality-recipe",
            "source_references": [{"material_id": item["id"]}],
        },
    )
    response = client.post(f"/api/questions/{question['id']}/regenerate")
    assert response.status_code == 200 and response.json()["accepted_count"] == 0
    response = client.post(
        f"/api/generation-jobs/questions/{question['id']}/regenerate"
    )
    assert db.get("generation_jobs", response.json()["id"])["status"] == "partial"


def test_openai_review_uses_three_checks_and_allows_recall(monkeypatch):
    instructions = []

    def ask(self, instruction, data):
        instructions.append(instruction)
        return quality_review()

    monkeypatch.setattr(OpenAIProvider, "ask", ask)
    assert OpenAIProvider().review_question(CASES[-1])["acceptable"]
    assert all(
        key in instructions[0]
        for key in ["learning_value", "grounded", "clear", "直接想起"]
    )


def test_lan_origin_write_guard(client):
    headers = {"Host": "192.168.1.5:8080", "Origin": "http://192.168.1.5:8080"}
    question = {
        "body": "管理の対象は何ですか。",
        "question_type": "word",
        "answer": "記録",
    }
    assert (
        client.post("/api/questions", json=question, headers=headers).status_code == 200
    )
    headers["Origin"] = "http://another-host:8080"
    assert (
        client.post("/api/questions", json=question, headers=headers).status_code == 403
    )
