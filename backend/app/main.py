import json
import os
import re
import unicodedata
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from . import db
from .models import Question, Recipe, Generation, Grade
from .parser import parse, ALLOWED
from .providers import provider, PROMPT_VERSION
from .seed import seed


@asynccontextmanager
async def lifespan(app):
    db.migrate()
    seed()
    yield


app = FastAPI(title="昇格ラボ API", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def local_write_guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        from urllib.parse import urlsplit

        if urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse(
                status_code=403, content={"detail": "同一オリジンから操作してください"}
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def require(kind, id):
    item = db.get(kind, id)
    if item is None:
        raise HTTPException(404, "データが見つかりません")
    return item


def ai_call(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        # Do not return upstream request headers, keys or uploaded text.
        if isinstance(e, ValueError) and "OPENAI_API_KEY" in str(e):
            raise HTTPException(503, str(e))
        raise HTTPException(
            502, "AI処理に失敗しました。接続設定や応答形式を確認してください"
        ) from e


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "provider": provider().name,
        "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
        if provider().name == "openai"
        else None,
    }


@app.get("/api/bootstrap")
def bootstrap():
    return {
        **{
            k: db.all_items(k)
            for k in [
                "questions",
                "materials",
                "exams",
                "recipes",
                "categories",
                "sets",
            ]
        },
        "provider": provider().name,
    }


@app.post("/api/categories")
def category(data: dict):
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 100:
        raise HTTPException(422, "カテゴリ名を1〜100文字で指定してください")
    existing = next((c for c in db.all_items("categories") if c["name"] == name), None)
    return existing or db.put("categories", {"name": name})


@app.post("/api/questions")
def create_question(q: Question):
    return db.put("questions", {**q.model_dump(), "id": db.uid()})


@app.put("/api/questions/{id}")
def edit_question(id: str, q: Question):
    old = require("questions", id)
    db.put("history", {"question_id": id, "snapshot": old})
    return db.put("questions", {**old, **q.model_dump(), "id": id})


@app.get("/api/questions/{id}/history")
def history(id: str):
    return [x for x in db.all_items("history") if x["question_id"] == id]


@app.post("/api/recipes")
def create_recipe(r: Recipe):
    return db.put("recipes", {**r.model_dump(), "id": db.uid()})


@app.put("/api/recipes/{id}")
def edit_recipe(id: str, r: Recipe):
    require("recipes", id)
    return db.put("recipes", {**r.model_dump(), "id": id})


@app.post("/api/uploads")
def upload(
    file: UploadFile = File(...), kind: str = Form("materials"), year: str = Form("")
):
    if kind not in ("materials", "exams"):
        raise HTTPException(422, "登録先が不正です")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(
            415, "PDF / XLSX / PNG / JPEG / WebP / UTF-8 TXT に対応しています"
        )
    id = db.uid()
    directory = db.DATA / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (id + suffix)
    total = 0
    try:
        with path.open("wb") as dest:
            while block := file.file.read(1024 * 1024):
                total += len(block)
                if total > 20 * 1024 * 1024:
                    raise HTTPException(413, "ファイルは20MB以内にしてください")
                dest.write(block)
        chunks = parse(path)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception as e:
        path.unlink(missing_ok=True)
        raise HTTPException(
            422,
            "資料を解析できませんでした。形式・文字・OCR環境を確認してください。"
            + (str(e) if isinstance(e, ValueError) else ""),
        ) from e
    warnings = []
    try:
        categories = (
            ai_call(provider().classify_material, chunks) if kind == "materials" else []
        )
        questions = (
            ai_call(provider().analyze_past_exam, chunks) if kind == "exams" else []
        )
    except HTTPException:
        from .parser import analyze

        categories = ["未分類"]
        questions = analyze(chunks) if kind == "exams" else []
        warnings.append(
            "AI解析に失敗したため抽出結果を保存しました。手動で確認・修正してください。"
        )
    for c in chunks:
        c["categories"] = categories
    for q in questions:
        q.setdefault("id", db.uid())
    for cat in categories:
        category({"name": cat})
    return db.put(
        kind,
        {
            "id": id,
            "name": Path(file.filename).name,
            "file_path": path.name,
            "file_type": suffix,
            "version": 1,
            "status": "review",
            "year": year,
            "chunks": chunks,
            "categories": categories,
            "questions": questions,
            "warnings": warnings,
            "analysis_method": provider().name,
        },
    )


@app.put("/api/documents/{kind}/{id}")
def update_document(kind: str, id: str, data: dict):
    if kind not in ("materials", "exams"):
        raise HTTPException(404)
    old = require(kind, id)
    changes = {
        k: v
        for k, v in data.items()
        if k in ("name", "year", "categories", "chunks", "questions", "status")
    }
    chunks = changes.get("chunks", old["chunks"])
    if not isinstance(chunks, list) or any(
        not isinstance(c, dict)
        or not isinstance(c.get("text"), str)
        or not isinstance(c.get("id"), str)
        or not isinstance(c.get("categories", []), list)
        for c in chunks
    ):
        raise HTTPException(422, "チャンク形式が不正です")
    if not isinstance(changes.get("categories", []), list):
        raise HTTPException(422, "カテゴリは配列で指定してください")
    questions = changes.get("questions", old["questions"])
    if not isinstance(questions, list) or any(
        not isinstance(q, dict) or not isinstance(q.get("raw_text"), str)
        for q in questions
    ):
        raise HTTPException(422, "過去問にはraw_textが必要です")
    return db.put(kind, {**old, **changes, "version": old["version"] + 1})


@app.get("/api/documents/{kind}/{id}/file")
def source_file(kind: str, id: str):
    if kind not in ("materials", "exams"):
        raise HTTPException(404)
    item = require(kind, id)
    return FileResponse(
        db.DATA / "uploads" / item["file_path"],
        filename=item["name"],
        content_disposition_type="attachment",
    )


def generate(recipe, count=1):
    chunks = [
        {**c, "material_id": m["id"], "material_name": m["name"]}
        for m in db.all_items("materials")
        for c in m["chunks"]
        if recipe["category"] in c.get("categories", m["categories"])
    ]
    if not chunks:
        raise HTTPException(422, "対象カテゴリの資料を登録・分類してください")
    if sum(len(c["text"]) for c in chunks) > 100000:
        raise HTTPException(
            422, "対象資料が長すぎます。カテゴリを分割してください（10万文字以内）"
        )
    past = next(
        (
            q
            for e in db.all_items("exams")
            for q in e["questions"]
            if q.get("id") == recipe["reference_past_question_id"]
        ),
        {},
    )
    style = {
        k: past.get(k)
        for k in ["question_type", "structure_json", "style_profile_json"]
    }
    pending = []
    sets = []
    for n in range(count):
        set_id = db.uid()
        raw = ai_call(provider().generate_question_set, recipe, chunks, style)
        if len(raw) != recipe["major_count"] * recipe["sub_count"]:
            raise HTTPException(
                422, "生成問題数がレシピと一致しません。再生成してください"
            )
        for item in raw:
            refs = []
            warnings = list(item.get("warnings", []))
            for ref in item.get("source_references", []):
                chunk = next(
                    (c for c in chunks if c["id"] == ref.get("chunk_id")), None
                )
                if chunk:
                    refs.append(
                        {
                            k: chunk[k]
                            for k in [
                                "id",
                                "material_id",
                                "material_name",
                                "text",
                                "page_number",
                                "sheet_name",
                                "cell_range",
                            ]
                            if k in chunk
                        }
                    )
                else:
                    warnings.append("根拠チャンクが資料に存在しません")
            if not refs:
                warnings.append("資料との対応が弱い：有効な根拠を確認できません")
            if (
                past
                and SequenceMatcher(
                    None, item.get("body", ""), past["raw_text"]
                ).ratio()
                > 0.7
            ):
                warnings.append("過去問と文面が似すぎている可能性があります")
            if (
                abs(len(item.get("body", "")) - recipe["body_length"])
                > recipe["body_length"] * 0.6
            ):
                warnings.append("問題文の長さが指定から離れています")
            if provider().name == "openai":
                try:
                    warnings.extend(
                        ai_call(
                            provider().validate_question,
                            {"question": item, "sources": refs},
                        )
                    )
                except HTTPException:
                    warnings.append(
                        "AI品質チェックに失敗しました。内容を確認してください"
                    )
            try:
                q = Question(
                    **{
                        **item,
                        "id": db.uid(),
                        "category": recipe["category"],
                        "recipe_id": recipe["id"],
                        "question_set_id": set_id,
                        "score_weight": recipe["score_weight"],
                        "source_references": refs,
                        "warnings": warnings,
                    }
                ).model_dump()
            except (ValidationError, TypeError) as e:
                raise HTTPException(
                    422,
                    "生成結果に表示・採点できない不整合があります。再生成してください",
                ) from e
            if q["question_type"] != recipe["question_type"]:
                raise HTTPException(422, "生成問題形式がレシピと一致しません")
            q.update(
                {
                    "created_at": db.now(),
                    "updated_at": db.now(),
                    "provider": provider().name,
                    "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
                    if provider().name == "openai"
                    else "mock",
                    "prompt_version": PROMPT_VERSION,
                }
            )
            pending.append(q)
        sets.append(
            {
                "id": set_id,
                "name": recipe["name"] + f" / {n + 1}",
                "recipe_id": recipe["id"],
                "created_at": db.now(),
                "prompt_version": PROMPT_VERSION,
                "generation_status": "complete",
            }
        )
    # Commit the entire generation atomically, including question sets.
    with db.connect() as c:
        for kind, items in [("questions", pending), ("sets", sets)]:
            for item in items:
                c.execute(
                    "INSERT INTO entities VALUES (?,?,?)",
                    (kind, item["id"], json.dumps(item, ensure_ascii=False)),
                )
    return {"questions": pending, "sets": sets}


@app.post("/api/generate")
def generation(g: Generation):
    return generate(require("recipes", g.recipe_id), g.count)


@app.post("/api/questions/{id}/regenerate")
def regenerate(id: str):
    old = require("questions", id)
    r = {**require("recipes", old["recipe_id"]), "major_count": 1, "sub_count": 1}
    return generate(r)


def normalize(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold().rstrip("。.")


@app.post("/api/grade")
def grade(g: Grade):
    q = g.question.model_dump()
    if q["question_type"] == "short":
        return ai_call(provider().grade_short_answer, q, g.answer)
    correct = (
        g.answer == q["answer"]
        if q["question_type"] == "choice"
        else normalize(g.answer)
        in [normalize(a) for a in [q["answer"], *q["accepted_answers"]]]
    )
    return {
        "correct": correct,
        "score": 1 if correct else 0,
        "reason": "正解と一致しました。"
        if correct
        else "模範解答と解説を確認しましょう。",
        "reference": False,
    }


@app.get("/api/attempts")
def attempts():
    return db.all_items("attempts")


@app.put("/api/attempts/{id}")
def save_attempt(id: str, data: dict):
    if len(json.dumps(data)) > 4_000_000:
        raise HTTPException(413, "演習データが大きすぎます")
    revision = data.get("revision", 0)
    if not isinstance(revision, int) or revision < 0:
        raise HTTPException(422, "revisionは非負の整数です")
    # Atomic compare-and-write prevents delayed requests overwriting newer answers.
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT payload FROM entities WHERE kind=? AND id=?", ("attempts", id)
        ).fetchone()
        old = json.loads(row[0]) if row else None
        if old and old.get("revision", 0) > revision:
            return old
        value = {**data, "id": id, "updated_at": db.now()}
        c.execute(
            "INSERT INTO entities VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
            ("attempts", id, json.dumps(value, ensure_ascii=False)),
        )
    return value


DIST = Path(os.environ.get("FRONTEND_DIST", "../frontend/dist"))
if DIST.exists():

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(
            DIST / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(DIST / "index.html")
