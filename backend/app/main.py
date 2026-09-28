import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
import threading
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import db
from .answers import answer_appears_in_body, numeric_value_key, strip_numeric_units
from .dedupe import (
    compact_question,
    is_exact_duplicate,
    shares_source,
    source_locations_overlap,
    unused_source_locations,
)
from .models import Generation, Grade, Question, Recipe
from .parser import ALLOWED, MAX_CHUNK_CHARS, parse, split_chunks
from .providers import PROMPT_VERSION, OpenAIAPIError, provider
from .question_quality import has_substantive_source_text
from .seed import seed


@asynccontextmanager
async def lifespan(app):
    db.migrate()
    seed()
    purged_questions = db.purge_deleted_questions()
    if purged_questions:
        logger.info("Permanently removed %s previously deleted questions", purged_questions)
    for job in db.all_items("generation_jobs"):
        if job.get("status") in ("queued", "running"):
            db.put(
                "generation_jobs",
                {
                    **job,
                    "status": "failed",
                    "error": "サーバー再起動で生成が中断されました。保存済みの問題を確認してください。",
                },
            )
    yield


app = FastAPI(title="昇格ラボ API", version="1.0.0", lifespan=lifespan)
logger = logging.getLogger(__name__)

MAX_ANALYSIS_BATCH_CHARS = 48_000
MAX_GENERATION_BATCH_CHARS = 60_000
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_DUPLICATE_RETRIES = 2
SOURCE_LINES_PER_QUESTION = 4
GENERATION_LOCK = threading.Lock()


class URLImport(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    kind: Literal["materials"] = "materials"


class DirectGeneration(BaseModel):
    recipe_id: str = Field(default="", max_length=64)
    material_ids: list[str] = Field(min_length=1, max_length=1000)
    question_type: str = "choice"
    question_count: int = Field(default=5, ge=1, le=1000)
    difficulty: str = "標準"


def checked_public_url(url):
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as e:
        raise HTTPException(422, "URLのポートが不正です") from e
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or port not in (None, 80, 443):
        raise HTTPException(422, "公開WebページのHTTP/HTTPS URLを指定してください")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise HTTPException(422, "URLのホスト名を解決できません") from e
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise HTTPException(422, "ローカル・非公開アドレスのURLは登録できません")
    chosen = next((item for item in addresses if item[0] == socket.AF_INET), addresses[0])
    return parsed, chosen[4][0]


def download_public_url(url, destination):
    current = url
    with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False, trust_env=False) as client:
        for _ in range(5):
            parsed, address = checked_public_url(current)
            host = parsed.hostname.encode("idna").decode("ascii")
            host_header = host + (f":{parsed.port}" if parsed.port else "")
            address = f"[{address}]" if ":" in address else address
            pinned_url = parsed._replace(netloc=address + (f":{parsed.port}" if parsed.port else ""), fragment="").geturl()
            try:
                with client.stream("GET", pinned_url, headers={"Host": host_header, "User-Agent": "ExamStudio/1.0", "Accept": "text/html,application/pdf,application/vnd.openxmlformats-officedocument.*,text/plain,*/*"}, extensions={"sni_hostname": host}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        target = response.headers.get("location")
                        if not target:
                            raise HTTPException(422, "転送先のないURLです")
                        current = urljoin(current, target)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    suffix = Path(urlsplit(current).path).suffix.lower()
                    mime_suffix = {
                        "text/html": ".html", "application/xhtml+xml": ".html",
                        "text/plain": ".txt", "application/pdf": ".pdf",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                        "application/vnd.ms-excel": ".xls",
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                        "application/vnd.ms-powerpoint": ".ppt",
                    }.get(content_type)
                    if suffix not in ALLOWED:
                        suffix = mime_suffix or (".html" if content_type.startswith("text/html") else "")
                    if suffix not in ALLOWED:
                        raise HTTPException(415, "URLの内容は対応形式ではありません")
                    total = 0
                    with destination.open("wb") as out:
                        for block in response.iter_bytes(1024 * 1024):
                            total += len(block)
                            if total > MAX_UPLOAD_BYTES:
                                raise HTTPException(413, "URLの資料は100MB以内にしてください")
                            out.write(block)
                    if not total:
                        raise HTTPException(422, "URLの内容が空です")
                    name = Path(urlsplit(current).path).name or urlsplit(current).hostname
                    return name + suffix if not Path(name).suffix else name, suffix
            except httpx.HTTPError as e:
                raise HTTPException(422, "URLを取得できませんでした。公開設定とURLを確認してください") from e
    raise HTTPException(422, "URLの転送が多すぎます")


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
    except OpenAIAPIError as e:
        raise HTTPException(502, str(e)) from e
    except Exception as e:
        # Do not return upstream request headers, keys or uploaded text.
        if isinstance(e, ValueError) and "OPENAI_API_KEY" in str(e):
            raise HTTPException(503, str(e))
        raise HTTPException(
            502, "AI処理に失敗しました。接続設定や応答形式を確認してください"
        ) from e


def chunk_batches(chunks, max_chars):
    batches = []
    current = []
    current_size = 0
    for chunk in chunks:
        size = len(chunk.get("text", ""))
        if current and current_size + size > max_chars:
            batches.append(current)
            current = []
            current_size = 0
        current.append(chunk)
        current_size += size
    if current:
        batches.append(current)
    return batches


def column_number(value):
    number = 0
    for character in value:
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def normalize_spreadsheet_chunk(chunk):
    """Recover row boundaries from older XLSX extracts that kept cell newlines."""
    match = re.fullmatch(
        r"([A-Z]+)(\d+):([A-Z]+)(\d+)", str(chunk.get("cell_range") or "")
    )
    if not match or chunk.get("line_start") is not None:
        return chunk
    first_column, first_row, last_column, last_row = match.groups()
    first_row, last_row = int(first_row), int(last_row)
    expected_rows = last_row - first_row + 1
    lines = str(chunk.get("text", "")).splitlines()
    if len(lines) < expected_rows:
        # Legacy extraction stripped blank rows at the edges, so their original
        # offsets cannot be recovered safely from text alone.
        return {
            **chunk,
            "line_start": first_row,
            "line_end": last_row,
            "location_ambiguous": True,
        }
    if len(lines) == expected_rows:
        return {**chunk, "line_start": first_row, "line_end": last_row}

    tab_count = column_number(last_column) - column_number(first_column)
    if tab_count == 0:
        return {
            **chunk,
            "line_start": first_row,
            "line_end": last_row,
            "location_ambiguous": True,
        }
    rows = []
    for line in lines:
        if len(rows) < expected_rows and (not rows or line.count("\t") >= tab_count):
            rows.append(line)
        elif rows:
            rows[-1] = rows[-1] + " " + line.strip()
    if len(rows) != expected_rows:
        return {
            **chunk,
            "line_start": first_row,
            "line_end": last_row,
            "location_ambiguous": True,
        }
    return {
        **chunk,
        "text": "\n".join(rows),
        "line_start": first_row,
        "line_end": last_row,
    }


def normalize_legacy_source_segments(chunks):
    """Restore distinct offsets for pre-line-metadata segments."""
    groups = {}
    for index, chunk in enumerate(chunks):
        source_id = chunk.get("source_chunk_id")
        if source_id and chunk.get("line_start") is None:
            key = (
                str(source_id),
                str(chunk.get("sheet_name") or ""),
                str(chunk.get("cell_range") or ""),
            )
            groups.setdefault(key, []).append((index, chunk))

    starts = {indices[0][0]: (key, indices) for key, indices in groups.items()}
    skipped = {index for items in groups.values() for index, _ in items}
    result = []
    next_line = 1
    for index, chunk in enumerate(chunks):
        if index in starts:
            (source_id, sheet_name, cell_range), items = starts[index]
            ordered = sorted(
                items,
                key=lambda item: (
                    item[1].get("segment_index")
                    if isinstance(item[1].get("segment_index"), int)
                    else item[0]
                ),
            )
            if sheet_name:
                merged = {
                    **ordered[0][1],
                    "id": source_id,
                    "text": "\n".join(item.get("text", "") for _, item in ordered),
                }
                for key in ("segment_index", "segment_count", "line_start", "line_end"):
                    merged.pop(key, None)
                result.append(merged)
            else:
                has_page_lines = bool(
                    ordered[0][1].get("page_number")
                    or ordered[0][1].get("slide_number")
                )
                cursor = 1 if has_page_lines else next_line
                for _, item in ordered:
                    line_count = max(1, len(str(item.get("text", "")).splitlines()))
                    line_start = cursor
                    line_end = line_start + line_count - 1
                    result.append(
                        {**item, "line_start": line_start, "line_end": line_end}
                    )
                    cursor = line_end + 1
                if not has_page_lines:
                    next_line = max(next_line, cursor)
            continue
        if index in skipped:
            continue
        result.append(chunk)
        if not any(chunk.get(key) for key in ("page_number", "slide_number", "sheet_name", "cell_range")):
            start = chunk.get("line_start")
            end = chunk.get("line_end")
            if start is not None and end is not None:
                next_line = max(next_line, int(end) + 1)
    return result


def material_chunks_with_locations(material, chunks):
    source_chunks = []
    next_line = 1
    is_extracted_line_source = str(material.get("file_type") or "").lower() in {
        ".html", ".htm", ".pptx", ".ppt", ".png", ".jpg", ".jpeg", ".webp"
    }
    for original in normalize_legacy_source_segments(chunks):
        original = normalize_spreadsheet_chunk(original) if original.get("sheet_name") else original
        chunk = {
            **original,
            "material_id": material.get("id", ""),
            "material_name": material.get("name", ""),
            "file_type": material.get("file_type", ""),
            "line_basis": "extracted"
            if is_extracted_line_source or original.get("ocr_confidence") is not None
            else "document",
            **({"source_url": material["source_url"]} if material.get("source_url") else {}),
        }
        if chunk.get("line_start") is None:
            cell_range = str(chunk.get("cell_range") or "")
            match = re.search(r"[A-Z]+(\d+)", cell_range)
            if match:
                chunk["line_start"] = int(match.group(1))
            elif chunk.get("page_number") or chunk.get("slide_number") or chunk.get("sheet_name"):
                chunk["line_start"] = 1
            else:
                chunk["line_start"] = next_line
        if chunk.get("line_end") is None:
            chunk["line_end"] = chunk["line_start"] + max(
                0, len(str(chunk.get("text", "")).splitlines()) - 1
            )
        if not any(chunk.get(key) for key in ("page_number", "slide_number", "sheet_name", "cell_range")):
            next_line = max(next_line, int(chunk["line_end"]) + 1)
        source_chunks.append(chunk)
    return source_chunks


def unique_strings(values):
    result = []
    for value in values:
        if value is None:
            continue
        value = str(value).strip()
        if value and value not in result:
            result.append(value)
    return result


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
    def summary(item):
        chunks = item.get("chunks", [])
        chunking = {
            **item.get("chunking", {}),
            "generation_batch_count": len(
                chunk_batches(chunks, MAX_GENERATION_BATCH_CHARS)
            ),
            "generation_location_count": len(
                question_source_locations(
                    split_chunks(material_chunks_with_locations(item, chunks))
                )
            ),
        }
        return {
            **item,
            "chunks": [{k: value for k, value in chunk.items() if k != "text"} for chunk in chunks],
            "chunking": chunking,
        }

    return {
        **{
            k: [summary(item) for item in db.all_items(k)] if k == "materials" else db.all_items(k)
            for k in [
                "questions",
                "materials",
                "recipes",
                "categories",
                "sets",
            ]
        },
        "provider": provider().name,
    }


@app.get("/api/documents/{kind}/{id}")
def get_document(kind: str, id: str):
    if kind != "materials":
        raise HTTPException(404)
    return require(kind, id)


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
    updated = q.model_dump()
    if any(updated.get(key) != old.get(key) for key in ("body", "answer")):
        for key in ("tested_concept", "answer_target", "question_goal"):
            if updated.get(key) == old.get(key):
                updated[key] = ""
    return db.put("questions", {**old, **updated, "id": id})


@app.delete("/api/questions/{id}")
def delete_question(id: str):
    if not db.delete_question(id):
        raise HTTPException(404, "問題が見つかりません")
    return {"id": id, "deleted": True}


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
    file: UploadFile = File(...),  # noqa: B008 - FastAPI request declaration
    kind: Literal["materials"] = Form("materials"),
    analysis_method: str = Form("standard"),
):
    if analysis_method not in ("standard", "multimodal"):
        raise HTTPException(422, "解析方式が不正です")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(
            415, "PDF / Excel / PowerPoint / HTML / PNG / JPEG / WebP / UTF-8 TXT に対応しています"
        )
    if analysis_method == "multimodal" and suffix != ".pdf":
        raise HTTPException(422, "LLMマルチモーダル解析はPDFで利用してください")
    if analysis_method == "multimodal" and provider().name != "openai":
        raise HTTPException(
            503,
            "LLMマルチモーダル解析にはLLM_PROVIDER=openaiとOPENAI_API_KEYが必要です",
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
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "ファイルは100MB以内にしてください")
                dest.write(block)
        extracted_chunks = (
            ai_call(provider().extract_pdf, path)
            if analysis_method == "multimodal"
            else parse(path)
        )
        chunks = split_chunks(extracted_chunks)
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
    analysis_batches = chunk_batches(chunks, MAX_ANALYSIS_BATCH_CHARS)
    try:
        classification_batches = analysis_batches
        if provider().name == "openai" and len(analysis_batches) > 3:
            classification_batches = sample_source_batches(analysis_batches, 3)
        categories = []
        for batch in classification_batches:
            batch_categories = unique_strings(
                ai_call(provider().classify_material, batch)
            )
            for c in batch:
                c["categories"] = batch_categories
            categories.extend(batch_categories)
        categories = unique_strings(categories) or ["未分類"]
        for c in chunks:
            c["categories"] = categories
        if len(classification_batches) < len(analysis_batches):
            warnings.append("長い資料のカテゴリは一部の範囲から推定しました。必要に応じて修正してください。")
    except HTTPException:
        categories = ["未分類"]
        for c in chunks:
            c["categories"] = categories
        warnings.append(
            "AI解析に失敗したため抽出結果を保存しました。手動で確認・修正してください。"
        )
    for cat in categories:
        category({"name": cat})
    return db.put(
        "materials",
        {
            "id": id,
            "name": Path(file.filename).name,
            "file_path": path.name,
            "file_type": suffix,
            "version": 1,
            "status": "review",
            "chunks": chunks,
            "categories": categories,
            "warnings": warnings,
            "analysis_method": analysis_method,
            "analysis_provider": provider().name,
            "chunking": {
                "max_chars": MAX_CHUNK_CHARS,
                "source_chunk_count": len(extracted_chunks),
                "chunk_count": len(chunks),
                "analysis_batch_count": len(analysis_batches),
                "auto_split": len(chunks) > len(extracted_chunks),
            },
        },
    )


@app.post("/api/urls")
def import_url(request: URLImport):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "download"
        name, suffix = download_public_url(request.url.strip(), path)
        with path.open("rb") as source:
            item = upload(
                UploadFile(file=source, filename=Path(name).stem + suffix),
                kind="materials",
                analysis_method="standard",
            )
    return db.put("materials", {**item, "source_url": request.url.strip()})


@app.put("/api/documents/{kind}/{id}")
def update_document(kind: str, id: str, data: dict):
    if kind != "materials":
        raise HTTPException(404)
    old = require(kind, id)
    changes = {
        k: v
        for k, v in data.items()
        if k in ("name", "categories", "chunks", "status")
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
    submitted_chunk_count = len(chunks)
    chunks = split_chunks(
        [normalize_spreadsheet_chunk(chunk) if chunk.get("sheet_name") else chunk for chunk in chunks]
    )
    changes["chunks"] = chunks
    chunking = {
        **old.get("chunking", {}),
        "max_chars": MAX_CHUNK_CHARS,
        "chunk_count": len(chunks),
        "analysis_batch_count": len(
            chunk_batches(chunks, MAX_ANALYSIS_BATCH_CHARS)
        ),
        "auto_split": old.get("chunking", {}).get("auto_split", False)
        or len(chunks) > submitted_chunk_count,
    }
    return db.put(
        kind,
        {**old, **changes, "chunking": chunking, "version": old["version"] + 1},
    )


@app.delete("/api/documents/{kind}/{id}")
def delete_document(kind: str, id: str):
    if kind != "materials":
        raise HTTPException(404)
    item = require(kind, id)
    if not db.delete(kind, id):
        raise HTTPException(404)

    file_path = item.get("file_path")
    if isinstance(file_path, str) and file_path and Path(file_path).name == file_path:
        shared = any(
            other.get("file_path") == file_path
            for other in db.all_items("materials")
        )
        upload_dir = (db.DATA / "uploads").resolve()
        source_path = (upload_dir / file_path).resolve()
        if not shared and source_path.parent == upload_dir:
            try:
                source_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove uploaded source file for %s", id)
    return {"id": id, "deleted": True}


@app.get("/api/documents/{kind}/{id}/file")
def source_file(kind: str, id: str):
    if kind != "materials":
        raise HTTPException(404)
    item = require(kind, id)
    return FileResponse(
        db.DATA / "uploads" / item["file_path"],
        filename=item["name"],
        content_disposition_type="attachment",
    )


def generation_sources(recipe, source_chunk_ids=None):
    materials = db.all_items("materials")
    selected_ids = recipe.get("material_ids") or []
    if selected_ids:
        available_ids = {m["id"] for m in materials}
        missing = [id for id in selected_ids if id not in available_ids]
        if missing:
            raise HTTPException(
                422, "選択した資料が見つかりません。レシピの使用資料を更新してください"
            )
        materials = [m for m in materials if m["id"] in selected_ids]
    source_chunks = []
    for material in materials:
        eligible = [
            chunk
            for chunk in material["chunks"]
            if recipe.get("all_categories")
            or recipe["category"] in chunk.get("categories", material["categories"])
        ]
        source_chunks.extend(material_chunks_with_locations(material, eligible))
    chunks = split_chunks(source_chunks)
    allowed_chunks = set(source_chunk_ids or [])
    if allowed_chunks:
        chunks = [
            c
            for c in chunks
            if c["id"] in allowed_chunks
            or c.get("source_chunk_id") in allowed_chunks
            or any(c["id"].startswith(f"{allowed_id}:lines:") for allowed_id in allowed_chunks)
        ]
    if not chunks:
        raise HTTPException(
            422,
            "選択した資料に対象カテゴリの範囲がありません。資料またはカテゴリを確認してください",
        )
    question_sources = question_source_locations(chunks)
    if not question_sources:
        raise HTTPException(
            422,
            "出題できる説明本文がありません。見出し・ページ番号・短いラベルだけでなく、規則・定義・条件などを含む資料範囲を選んでください。",
        )
    return question_sources


def source_location_chunks(chunks):
    """Turn extracted text into non-overlapping, addressable line ranges."""
    result = []
    for chunk in chunks:
        if chunk.get("location_ambiguous"):
            candidate = {
                **chunk,
                "text": str(chunk.get("text", "")).strip(),
            }
            if candidate["text"] and not any(
                source_locations_overlap(candidate, existing) for existing in result
            ):
                result.append(candidate)
            continue
        lines = str(chunk.get("text", "")).splitlines()
        if not lines:
            continue
        first_line = int(chunk.get("line_start", 1))
        for offset in range(0, len(lines), SOURCE_LINES_PER_QUESTION):
            group = lines[offset : offset + SOURCE_LINES_PER_QUESTION]
            text = "\n".join(group).strip()
            if not text:
                continue
            line_start = first_line + offset
            line_end = line_start + len(group) - 1
            if len(lines) <= SOURCE_LINES_PER_QUESTION:
                chunk_id = chunk["id"]
            else:
                chunk_id = f"{chunk['id']}:lines:{line_start}-{line_end}"
            candidate = {
                **chunk,
                "id": chunk_id,
                "text": text,
                "line_start": line_start,
                "line_end": line_end,
            }
            cell_range = str(chunk.get("cell_range") or "")
            cell_match = re.fullmatch(r"([A-Z]+)\d+:([A-Z]+)\d+", cell_range)
            if cell_match:
                candidate["cell_range"] = (
                    f"{cell_match.group(1)}{line_start}:"
                    f"{cell_match.group(2)}{line_end}"
                )
            # Very long physical lines can cross parser segments. Keep only one
            # source unit for a page/line so two questions cannot cite fragments
            # of the same line as if they were distinct locations.
            if any(source_locations_overlap(candidate, existing) for existing in result):
                continue
            result.append(candidate)
    return result


def question_source_locations(chunks):
    """Keep locatable ranges that contain material worth examining."""
    return [
        chunk
        for chunk in source_location_chunks(chunks)
        if has_substantive_source_text(chunk.get("text"))
    ]


def _interleave_source_batches(batches, offset=0):
    batches = [batch for batch in batches if batch]
    if not batches:
        return []
    result = []
    start = offset % len(batches)
    for row in range(max(map(len, batches))):
        for step in range(len(batches)):
            batch = batches[(start + step) % len(batches)]
            if row < len(batch):
                result.append(batch[row])
    return result


def select_source_units(source_batches, questions, question_count, offset):
    """Prefer unseen evidence ranges, then reuse ranges instead of failing."""
    all_batches = [list(batch) for batch in source_batches if batch]
    if not all_batches:
        raise HTTPException(409, "問題の根拠にできる資料本文がありません")

    fresh_batches = [unused_source_locations(batch, questions) for batch in all_batches]
    fresh = _interleave_source_batches(fresh_batches, offset)
    fresh_ids = {str(chunk.get("id") or "") for chunk in fresh}
    previously_used = [
        [chunk for chunk in batch if str(chunk.get("id") or "") not in fresh_ids]
        for batch in all_batches
    ]
    reused = _interleave_source_batches(previously_used, offset)

    if fresh:
        candidates = fresh + reused
    else:
        candidates = _interleave_source_batches(all_batches, offset)
    if not candidates:
        raise HTTPException(409, "問題の根拠にできる資料本文がありません")
    return [candidates[index % len(candidates)] for index in range(question_count)]


def sample_source_batches(batches, sample_count):
    if len(batches) <= sample_count:
        return batches
    indexes = [int(i * len(batches) / sample_count) for i in range(sample_count)]
    return [batches[i] for i in indexes]


def _ng_question(question):
    compact = compact_question(question, max_body=320)
    return {
        "body": compact["body"],
        "answer": compact["answer"],
        "tested_concept": compact["tested_concept"],
        "answer_target": compact["answer_target"],
        "question_goal": compact["question_goal"],
        "question_type": compact["question_type"],
    }


def questions_for_source_batch(
    source_batch, questions, limit=12, extra_questions=()
):
    source = {
        "source_references": [
            {
                key: chunk[key]
                for key in (
                    "id",
                    "material_id",
                    "material_name",
                    "page_number",
                    "line_start",
                    "line_end",
                    "slide_number",
                    "sheet_name",
                    "cell_range",
                    "text",
                )
                if key in chunk
            }
            for chunk in source_batch
        ]
    }
    related = []
    for question in questions:
        references = [
            reference
            for reference in question.get("source_references") or []
            if isinstance(reference, dict)
        ]
        overlaps = any(
            source_locations_overlap(chunk, reference)
            for chunk in source["source_references"]
            for reference in references
        )
        if overlaps or shares_source(source, question):
            related.append((overlaps, question))
    related.sort(
        key=lambda pair: (
            pair[0],
            str(pair[1].get("created_at") or ""),
        ),
        reverse=True,
    )
    result = []
    seen = set()
    for question in [*(item[1] for item in related[:limit]), *extra_questions[-12:]]:
        compact = _ng_question(question)
        key = (
            compact["body"].casefold(),
            (compact["answer_target"] or compact["answer"]).casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(compact)
    return result


def duplicate_question(candidate, existing_questions):
    return next(
        (
            question
            for question in existing_questions
            if is_exact_duplicate(candidate, question)
        ),
        None,
    )


def regenerate_question_candidate(
    recipe, known_questions, rejected_questions, source_unit, feedback=()
):
    call_recipe = {
        **recipe,
        "major_count": 1,
        "sub_count": 1,
        "ng_questions": questions_for_source_batch(
            [source_unit],
            known_questions,
            extra_questions=rejected_questions,
        ),
        "ng_feedback": unique_strings(feedback),
    }
    source_for_model = [
        {
            "id": "SOURCE_1",
            "source_key": "SOURCE_1",
            "text": str(source_unit.get("text") or ""),
        }
    ]
    replacement = ai_call(
        provider().generate_question_set, call_recipe, source_for_model
    )
    if len(replacement) != 1:
        raise HTTPException(422, "問題の作り直しに失敗しました。再生成してください")
    return replacement[0]


def ensure_question_format(item, source_chunks):
    if item.get("question_type") != "blank":
        return item
    body = str(item.get("body", ""))
    answers = [part.strip() for part in str(item.get("answer", "")).split(" / ")]
    has_blank = bool(re.search(r"（\s*）|\(\s*\)|_{2,}|＿{2,}|【\s*】|〔\s*〕", body))
    leaked_answer = False
    for answer in answers:
        if answer and answer in body:
            body = body.replace(answer, "（　）", 1)
            leaked_answer = True
    if not has_blank and not leaked_answer:
        answer = answers[0] if answers else ""
        if not answer:
            return item
        source_sentence = next(
            (
                sentence.strip()
                for chunk in source_chunks
                for sentence in re.split(r"(?<=[。！？])|\n", chunk["text"])
                if answer in sentence and len(sentence.strip()) <= 2000
            ),
            "",
        )
        body = (
            source_sentence.replace(answer, "（　）", 1) + "　空欄に入る語句を答えてください。"
            if source_sentence
            else body + "\n空欄（　）に入る語句を答えてください。"
        )
    elif not leaked_answer:
        return item
    return {
        **item,
        "body": body,
        "warnings": [
            *(item.get("warnings") if isinstance(item.get("warnings"), list) else []),
            "穴埋め形式に合わせて問題文を補正しました。",
        ],
    }


def prepare_numeric_answer(item):
    """Store numeric answers without units and tell learners units are optional."""
    if item.get("question_type") == "choice":
        return item
    answer = str(item.get("answer") or "")
    if numeric_value_key(answer) is None:
        return item
    body = str(item.get("body") or "").rstrip()
    if "単位は不要" not in body:
        body += "\n数値のみで回答してください（単位は不要です）。"
    return {
        **item,
        "body": body,
        "answer": strip_numeric_units(answer),
    }


def generate(
    recipe,
    count=1,
    source_chunk_ids=None,
    persist_recipe=False,
    source_question_offset=0,
    set_name_offset=0,
):
    # Selection, model calls, duplicate checks, and the final DB commit form one
    # critical section so concurrent tabs cannot spend tokens on the same range.
    with GENERATION_LOCK:
        return _generate(
            recipe,
            count=count,
            source_chunk_ids=source_chunk_ids,
            persist_recipe=persist_recipe,
            source_question_offset=source_question_offset,
            set_name_offset=set_name_offset,
        )


def _generate(
    recipe,
    count=1,
    source_chunk_ids=None,
    persist_recipe=False,
    source_question_offset=0,
    set_name_offset=0,
):
    chunks = generation_sources(recipe, source_chunk_ids)
    source_batches = chunk_batches(chunks, MAX_GENERATION_BATCH_CHARS)
    existing_questions = db.all_items("questions")
    material_ids = {str(chunk.get("material_id") or "") for chunk in chunks}
    source_history = [
        {"source_references": locations}
        for generation_set in db.all_items("sets")
        if material_ids & {
            str(material_id) for material_id in generation_set.get("material_ids", [])
        }
        and isinstance(
            locations := generation_set.get("used_source_locations"), list
        )
        and locations
    ]
    pending = []
    sets = []
    for n in range(count):
        set_id = db.uid()
        question_count = recipe["major_count"] * recipe["sub_count"]
        known_questions = [*existing_questions, *source_history, *pending]
        selected_units = select_source_units(
            source_batches,
            known_questions,
            question_count,
            source_question_offset + n * question_count,
        )
        generated = []
        generated_candidates = []
        for source_unit in selected_units:
            known_questions = [*existing_questions, *pending]
            source_for_model = [
                {
                    "id": "SOURCE_1",
                    "source_key": "SOURCE_1",
                    "text": str(source_unit.get("text") or ""),
                }
            ]
            call_recipe = {
                **recipe,
                "major_count": 1,
                "sub_count": 1,
                "ng_questions": questions_for_source_batch(
                    [source_unit],
                    known_questions,
                    extra_questions=generated_candidates,
                ),
            }
            batch_result = ai_call(
                provider().generate_question_set, call_recipe, source_for_model
            )
            if len(batch_result) != 1:
                raise HTTPException(
                    422, "生成問題数がレシピと一致しません。再生成してください"
                )
            generated.append((batch_result[0], [source_unit]))
            generated_candidates.append(batch_result[0])
        if len(generated) != question_count:
            raise HTTPException(
                422, "生成問題数がレシピと一致しません。再生成してください"
            )
        used_chunks = {c["id"]: c for c in selected_units}
        scope_warning = ""
        if len(used_chunks) < len(selected_units):
            scope_warning = "問題数が資料範囲数を上回るため、一部の資料範囲を再利用しました。"
        elif len(chunks) > len(used_chunks):
            scope_warning = (
                f"選択資料の全{len(chunks)}範囲中、"
                f"{len(used_chunks)}範囲をこのセットで使用しました。"
                "複数セットを生成すると対象範囲を分散します。"
            )
        for item_index, (item, item_chunks) in enumerate(generated):
            rejected = []
            q = None
            for attempt in range(MAX_DUPLICATE_RETRIES + 1):
                if not isinstance(item, dict):
                    rejected.append({"body": str(item)[:320]})
                    if attempt >= MAX_DUPLICATE_RETRIES:
                        raise HTTPException(
                            422,
                            "問題形式の候補を作れませんでした。生成条件を確認してください。",
                        )
                    item = regenerate_question_candidate(
                        recipe,
                        [*existing_questions, *pending],
                        rejected,
                        item_chunks[0],
                        ["問題をJSONオブジェクトで返してください"],
                    )
                    continue
                item = prepare_numeric_answer(
                    ensure_question_format(item, item_chunks)
                )
                raw_warnings = item.get("warnings")
                warnings = (
                    [str(warning) for warning in raw_warnings if str(warning).strip()]
                    if isinstance(raw_warnings, list)
                    else []
                )
                if scope_warning:
                    warnings.append(scope_warning)
                source_unit = item_chunks[0]
                source_references = item.get("source_references")
                if not isinstance(source_references, list):
                    source_references = []
                reported_ids = {
                    str(ref.get("source_key") or ref.get("chunk_id"))
                    for ref in source_references
                    if isinstance(ref, dict)
                    and (ref.get("source_key") or ref.get("chunk_id"))
                }
                if "SOURCE_1" not in reported_ids:
                    warnings.append(
                        "生成結果に根拠IDがなかったため、入力した資料範囲を根拠に設定しました。"
                    )
                refs = [
                    {
                        key: source_unit[key]
                        for key in [
                            "id",
                            "material_id",
                            "material_name",
                            "file_type",
                            "line_basis",
                            "text",
                            "page_number",
                            "line_start",
                            "line_end",
                            "char_start",
                            "char_end",
                            "slide_number",
                            "sheet_name",
                            "cell_range",
                            "source_url",
                        ]
                        if key in source_unit
                    }
                ]
                body_length = len(str(item.get("body") or ""))
                if (
                    abs(body_length - recipe["body_length"])
                    > recipe["body_length"] * 0.6
                ):
                    warnings.append("問題文の長さが指定から離れています")
                try:
                    q = Question(
                        **{
                            **item,
                            "parent": f"第{item_index // recipe['sub_count'] + 1}問",
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
                    rejected.append(item)
                    if attempt >= MAX_DUPLICATE_RETRIES:
                        raise HTTPException(
                            422,
                            "問題形式の候補を作れませんでした。生成条件を確認してください。",
                        ) from e
                    item = regenerate_question_candidate(
                        recipe,
                        [*existing_questions, *pending],
                        rejected,
                        source_unit,
                        ["必須項目と選択肢・正解の整合性を満たすJSONを返してください"],
                    )
                    continue
                if q["question_type"] != recipe["question_type"]:
                    rejected.append(q)
                    if attempt >= MAX_DUPLICATE_RETRIES:
                        raise HTTPException(
                            422,
                            "生成問題形式がレシピと一致しません。生成条件を確認してください。",
                        )
                    item = regenerate_question_candidate(
                        recipe,
                        [*existing_questions, *pending],
                        rejected,
                        source_unit,
                        [f"question_typeは{recipe['question_type']}にしてください"],
                    )
                    continue

                answer_forms = [q["answer"], *q["accepted_answers"]]
                if any(
                    answer_appears_in_body(q["body"], answer_form)
                    for answer_form in answer_forms
                ):
                    rejected.append(q)
                    if attempt >= MAX_DUPLICATE_RETRIES:
                        raise HTTPException(
                            422,
                            "問題文に正解が含まれない候補を作れませんでした。資料範囲を変えて再試行してください。",
                        )
                    item = regenerate_question_candidate(
                        recipe,
                        [*existing_questions, *pending],
                        rejected,
                        source_unit,
                        [
                            "問題文に正答または正答と同じ数値が含まれています。正解を本文に出さずに問う問題へ作り直してください。"
                        ],
                    )
                    continue

                known_questions = [*existing_questions, *pending]
                duplicate = duplicate_question(q, known_questions)
                if duplicate:
                    rejected.append(q)
                    if attempt >= MAX_DUPLICATE_RETRIES:
                        raise HTTPException(
                            409,
                            "既存問題と重複しない候補を作れませんでした。生成条件または資料範囲を変えて再試行してください。今回の生成分は保存していません。",
                        )
                    item = regenerate_question_candidate(
                        recipe,
                        known_questions,
                        rejected,
                        source_unit,
                        ["問題文と正解が既存問題と同じです。資料中の別の事実を問う問題にしてください"],
                    )
                    continue

                q["warnings"] = warnings
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
                break
            pending.append(q)
        sets.append(
            {
                "id": set_id,
                "name": recipe["name"] + f" / {set_name_offset + n + 1}",
                "recipe_id": recipe["id"],
                "created_at": db.now(),
                "prompt_version": PROMPT_VERSION,
                "generation_status": "complete",
                "question_count": question_count,
                "generation_scope": {
                    "flow": recipe.get("generation_flow", "recipe"),
                    "category": recipe["category"],
                    "all_categories": recipe.get("all_categories", False),
                    "question_type": recipe["question_type"],
                    "difficulty": recipe["difficulty"],
                },
                "material_ids": list(
                    dict.fromkeys(c["material_id"] for c in chunks)
                ),
                "material_names": list(
                    dict.fromkeys(c["material_name"] for c in chunks)
                ),
                "source_chunk_count": len(chunks),
                "used_chunk_count": len(used_chunks),
                "used_source_locations": [
                    {
                        key: source_unit[key]
                        for key in (
                            "id",
                            "material_id",
                            "material_name",
                            "page_number",
                            "line_start",
                            "line_end",
                            "char_start",
                            "char_end",
                            "slide_number",
                            "sheet_name",
                            "cell_range",
                        )
                        if key in source_unit
                    }
                    for source_unit in used_chunks.values()
                ],
            }
        )
    # Commit the entire generation atomically, including question sets.
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        stored_questions = [
            json.loads(row[0])
            for row in c.execute(
                "SELECT payload FROM entities WHERE kind=?", ("questions",)
            )
        ]
        commit_scope = list(stored_questions)
        for question in pending:
            if any(is_exact_duplicate(question, stored) for stored in commit_scope):
                raise HTTPException(
                    409,
                    "保存直前に既存問題との重複が見つかりました。生成し直してください。今回の生成分は保存していません。",
                )
            commit_scope.append(question)
        records = [("questions", pending), ("sets", sets)]
        if persist_recipe:
            records.append(("recipes", [recipe]))
        for kind, items in records:
            for item in items:
                c.execute(
                    "INSERT INTO entities VALUES (?,?,?)",
                    (kind, item["id"], json.dumps(item, ensure_ascii=False)),
                )
    return {"questions": pending, "sets": sets}


@app.post("/api/generate")
def generation(g: Generation):
    recipe = require("recipes", g.recipe_id)
    if g.material_ids is not None:
        recipe = {**recipe, "material_ids": g.material_ids}
    return generate(recipe, g.count)


def prepare_direct_generation(request: DirectGeneration):
    if len(set(request.material_ids)) != len(request.material_ids) or any(not id.strip() for id in request.material_ids):
        raise HTTPException(422, "使用する資料を選び直してください")
    if request.question_type not in ("choice", "blank", "word", "short"):
        raise HTTPException(422, "問題形式を選び直してください")
    if request.difficulty not in ("基礎", "標準", "応用"):
        raise HTTPException(422, "難易度を選び直してください")
    recipe_id = request.recipe_id.strip() or db.uid()
    recipe = Recipe(
        id=recipe_id,
        name="資料から作成",
        category="資料から作成",
        all_categories=True,
        material_ids=request.material_ids,
        question_type=request.question_type,
        major_count=1,
        sub_count=min(request.question_count, 20),
        difficulty=request.difficulty,
    ).model_dump()
    recipe.update({
        "created_at": db.now(),
        "updated_at": db.now(),
        "generation_flow": "direct",
    })
    existing_recipe = next(
        (item for item in db.all_items("recipes") if item["id"] == recipe_id),
        None,
    )
    if existing_recipe and any(
        existing_recipe.get(key) != recipe.get(key)
        for key in ["material_ids", "question_type", "difficulty"]
    ):
        raise HTTPException(409, "生成条件が変わりました。最初から作成してください")
    selected_materials = set(request.material_ids)
    current_scope = {
        "flow": "direct",
        "category": "資料から作成",
        "all_categories": True,
        "question_type": request.question_type,
        "difficulty": request.difficulty,
    }
    legacy_recipe_ids = {
        item["id"]
        for item in db.all_items("recipes")
        if item.get("category") == "資料から作成"
        and item.get("all_categories") is True
        and set(item.get("material_ids") or []) == selected_materials
        and item.get("question_type") == request.question_type
        and item.get("difficulty") == request.difficulty
    }
    previous_sets = [
        item
        for item in db.all_items("sets")
        if item.get("material_ids")
        and set(item["material_ids"]) == selected_materials
        and (
            item.get("generation_scope") == current_scope
            or (
                not item.get("generation_scope")
                and item.get("recipe_id") in legacy_recipe_ids
            )
        )
    ]
    previous_question_counts = {}
    for item in db.all_items("questions"):
        question_set_id = item.get("question_set_id")
        previous_question_counts[question_set_id] = previous_question_counts.get(
            question_set_id, 0
        ) + 1
    previous_question_count = sum(
        set_item.get("question_count", previous_question_counts.get(set_item["id"], 0))
        for set_item in previous_sets
    )
    return recipe, existing_recipe is None, previous_question_count, len(previous_sets)


def run_direct_generation(request: DirectGeneration):
    recipe, persist_recipe, previous_question_count, previous_set_count = prepare_direct_generation(request)
    return generate(
        recipe,
        persist_recipe=persist_recipe,
        source_question_offset=previous_question_count,
        set_name_offset=previous_set_count,
    )


@app.post("/api/generate-direct")
def generate_direct(request: DirectGeneration):
    if request.question_count > 20:
        raise HTTPException(422, "一度に同期生成できるのは20問までです")
    return run_direct_generation(request)


def save_generation_job(job_id, **changes):
    current_job = db.get("generation_jobs", job_id)
    if not current_job:
        return None
    return db.put("generation_jobs", {**current_job, **changes})


def fail_generation_job(job_id, error):
    save_generation_job(job_id, status="failed", error=str(error))


def run_direct_generation_job(job_id, payload):
    save_generation_job(job_id, status="running", error="")
    completed = 0
    set_ids = []
    try:
        recipe_id = payload["recipe_id"]
        while completed < payload["question_count"]:
            count = min(20, payload["question_count"] - completed)
            request = DirectGeneration.model_validate(
                {**payload, "recipe_id": recipe_id, "question_count": count}
            )
            result = run_direct_generation(request)
            if not recipe_id and result["sets"]:
                recipe_id = result["sets"][0]["recipe_id"]
            completed += len(result["questions"])
            set_ids.extend(item["id"] for item in result["sets"])
            save_generation_job(
                job_id,
                completed=completed,
                set_ids=list(dict.fromkeys(set_ids)),
            )
        save_generation_job(job_id, status="complete", completed=payload["question_count"])
    except HTTPException as error:
        fail_generation_job(job_id, error.detail)
    except Exception:
        logger.exception("Direct generation job %s failed", job_id)
        fail_generation_job(job_id, "生成処理に失敗しました。条件を確認して再試行してください")


def run_recipe_generation_job(job_id, recipe, set_count):
    save_generation_job(job_id, status="running", error="")
    set_ids = []
    try:
        for index in range(set_count):
            result = generate(recipe, count=1, set_name_offset=index)
            set_ids.extend(item["id"] for item in result["sets"])
            save_generation_job(
                job_id,
                completed=index + 1,
                set_ids=list(dict.fromkeys(set_ids)),
            )
        save_generation_job(job_id, status="complete", completed=set_count)
    except HTTPException as error:
        fail_generation_job(job_id, error.detail)
    except Exception:
        logger.exception("Recipe generation job %s failed", job_id)
        fail_generation_job(job_id, "生成処理に失敗しました。条件を確認して再試行してください")


def create_generation_job(kind, total):
    return db.put(
        "generation_jobs",
        {
            "id": db.uid(),
            "kind": kind,
            "status": "queued",
            "total": total,
            "completed": 0,
            "set_ids": [],
            "error": "",
        },
    )


@app.get("/api/generation-jobs")
def generation_jobs():
    jobs = db.all_items("generation_jobs")
    jobs.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return jobs[:20]


@app.get("/api/generation-jobs/{job_id}")
def generation_job(job_id: str):
    return require("generation_jobs", job_id)


@app.post("/api/generation-jobs/direct", status_code=202)
def queue_direct_generation(request: DirectGeneration, background_tasks: BackgroundTasks):
    recipe_id = request.recipe_id.strip() or db.uid()
    payload = {**request.model_dump(), "recipe_id": recipe_id}
    job = create_generation_job("direct", request.question_count)
    background_tasks.add_task(run_direct_generation_job, job["id"], payload)
    return job


@app.post("/api/generation-jobs/recipe", status_code=202)
def queue_recipe_generation(request: Generation, background_tasks: BackgroundTasks):
    recipe = require("recipes", request.recipe_id)
    if request.material_ids is not None:
        recipe = {**recipe, "material_ids": request.material_ids}
    job = create_generation_job("recipe", request.count)
    background_tasks.add_task(run_recipe_generation_job, job["id"], recipe, request.count)
    return job


@app.post("/api/generation-jobs/questions/{id}/regenerate", status_code=202)
def queue_question_regeneration(id: str, background_tasks: BackgroundTasks):
    old = require("questions", id)
    recipe = {**require("recipes", old["recipe_id"]), "major_count": 1, "sub_count": 1}
    material_ids = [
        reference.get("material_id")
        for reference in old.get("source_references", [])
    ]
    recipe["material_ids"] = unique_strings(material_ids) or recipe.get("material_ids", [])
    job = create_generation_job("recipe", 1)
    background_tasks.add_task(run_recipe_generation_job, job["id"], recipe, 1)
    return job


@app.post("/api/questions/{id}/regenerate")
def regenerate(id: str):
    old = require("questions", id)
    r = {**require("recipes", old["recipe_id"]), "major_count": 1, "sub_count": 1}
    material_ids = [
        ref.get("material_id") for ref in old.get("source_references", [])
    ]
    r["material_ids"] = unique_strings(material_ids) or r.get("material_ids", [])
    return generate(r)


def normalize(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold().rstrip("。.")


@app.post("/api/grade")
def grade(g: Grade):
    q = g.question.model_dump()
    if q["question_type"] == "short":
        expected_numeric = numeric_value_key(q["answer"])
        submitted_numeric = numeric_value_key(g.answer)
        if expected_numeric is not None and expected_numeric == submitted_numeric:
            return {
                "correct": True,
                "score": 1,
                "reason": "数値が一致しました。単位の入力は不要です。",
                "reference": False,
            }
        return ai_call(provider().grade_short_answer, q, g.answer)
    answer_forms = [q["answer"], *q["accepted_answers"]]
    submitted_numeric = numeric_value_key(g.answer)
    numeric_match = submitted_numeric is not None and any(
        numeric_value_key(answer) == submitted_numeric for answer in answer_forms
    )
    correct = (
        g.answer == q["answer"]
        if q["question_type"] == "choice"
        else numeric_match
        or normalize(g.answer) in [normalize(answer) for answer in answer_forms]
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
        value, _ = db.sanitize_attempt(value, c)
        if data.get("questions") and not value.get("questions"):
            c.execute("DELETE FROM entities WHERE kind=? AND id=?", ("attempts", id))
            return value
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
