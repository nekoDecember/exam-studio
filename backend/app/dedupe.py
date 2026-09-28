"""Question identity and lightweight retrieval helpers for generation deduplication."""

import re
import unicodedata
from collections.abc import Iterable


def normalize_duplicate_text(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"（\s*）|\(\s*\)|＿+|_{2,}|【\s*】|〔\s*〕", " 空欄 ", text)
    return re.sub(r"[\s\W_]+", "", text)


def _answer_targets(question):
    values = [
        question.get("answer_target"),
        question.get("answer"),
        *(question.get("accepted_answers") or []),
    ]
    return {value for item in values if (value := normalize_duplicate_text(item))}


def _source_ids(question):
    chunk_ids = set()
    material_ids = set()
    for reference in question.get("source_references") or []:
        if not isinstance(reference, dict):
            continue
        chunk_id = reference.get("id") or reference.get("chunk_id")
        if chunk_id:
            chunk_ids.add(str(chunk_id))
        if reference.get("material_id"):
            material_ids.add(str(reference["material_id"]))
    return chunk_ids, material_ids


def shares_source(left, right):
    left_chunks, left_materials = _source_ids(left)
    right_chunks, right_materials = _source_ids(right)
    return bool(left_chunks & right_chunks or left_materials & right_materials)


def source_locations_overlap(left, right):
    """Return whether two source references identify the same or unknown location."""
    def location_chunk_id(value):
        return re.split(r":lines:", str(value or ""), maxsplit=1)[0]

    def parent_chunk_id(value):
        return re.split(r":segment:", location_chunk_id(value), maxsplit=1)[0]

    def normalized_source_lines(reference):
        return {
            line
            for raw_line in str(reference.get("text") or "").splitlines()
            if len(line := re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw_line)).casefold()) >= 8
        }

    left_material = str(left.get("material_id") or "")
    right_material = str(right.get("material_id") or "")
    if left_material and right_material:
        if left_material != right_material:
            return False
    else:
        left_name = str(left.get("material_name") or left.get("source_url") or "")
        right_name = str(right.get("material_name") or right.get("source_url") or "")
        if left_name and right_name and left_name != right_name:
            return False
        if not left_name or not right_name:
            left_id = location_chunk_id(left.get("id") or left.get("chunk_id"))
            right_id = location_chunk_id(right.get("id") or right.get("chunk_id"))
            if not left_id or not right_id:
                return False
            if left_id != right_id and (
                parent_chunk_id(left_id) != parent_chunk_id(right_id)
                or (":segment:" in left_id and ":segment:" in right_id)
            ):
                return False

    for key in ("page_number", "slide_number", "sheet_name"):
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value is not None and right_value is not None and left_value != right_value:
            return False

    left_start = left.get("line_start")
    left_end = left.get("line_end", left_start)
    right_start = right.get("line_start")
    right_end = right.get("line_end", right_start)
    if left_start is None or right_start is None:
        # Legacy questions still carry the original chunk ID. Consume that
        # chunk, rather than blocking every other chunk in the same material.
        left_id = location_chunk_id(left.get("id") or left.get("chunk_id"))
        right_id = location_chunk_id(right.get("id") or right.get("chunk_id"))
        if left_id and right_id:
            if left_id == right_id:
                return True
            if parent_chunk_id(left_id) == parent_chunk_id(right_id):
                # A root ID consumes all its segments; two distinct segment IDs
                # remain separate source locations.
                return ":segment:" not in left_id or ":segment:" not in right_id
            return False
        # With no usable location identity, conservatively block the matching
        # page/sheet/material.
        return True
    try:
        lines_overlap = int(left_start) <= int(right_end) and int(right_start) <= int(left_end)
    except (TypeError, ValueError):
        return True
    if not lines_overlap:
        return bool(normalized_source_lines(left) & normalized_source_lines(right))
    if (
        not left.get("cell_range")
        and not right.get("cell_range")
        and int(left_start) == int(left_end) == int(right_start) == int(right_end)
    ):
        left_char_start = left.get("char_start")
        left_char_end = left.get("char_end", left_char_start)
        right_char_start = right.get("char_start")
        right_char_end = right.get("char_end", right_char_start)
        if all(value is not None for value in (left_char_start, left_char_end, right_char_start, right_char_end)):
            return int(left_char_start) <= int(right_char_end) and int(right_char_start) <= int(left_char_end)
    return True


def unused_source_locations(chunks, questions):
    references = [
        reference
        for question in questions
        for reference in question.get("source_references") or []
        if isinstance(reference, dict)
    ]
    return [
        chunk
        for chunk in chunks
        if not any(source_locations_overlap(chunk, reference) for reference in references)
    ]


def is_exact_duplicate(left, right):
    left_body = normalize_duplicate_text(left.get("body"))
    right_body = normalize_duplicate_text(right.get("body"))
    return bool(
        left_body
        and left_body == right_body
        and _answer_targets(left)
        and _answer_targets(left) & _answer_targets(right)
    )


def _character_ngrams(value, size=3):
    text = normalize_duplicate_text(value)
    if not text:
        return set()
    if len(text) < size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _token_set(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return {
        token
        for token in re.findall(r"[a-z0-9]+|[\u3040-\u30ff\u3400-\u9fff々ー]{2,}", text)
        if len(token) >= 2
    }


def question_text_similarity(left, right):
    left_body = left.get("body", "")
    right_body = right.get("body", "")
    left_grams = _character_ngrams(left_body)
    right_grams = _character_ngrams(right_body)
    if not left_grams or not right_grams:
        return 0.0
    gram_score = 2 * len(left_grams & right_grams) / (len(left_grams) + len(right_grams))
    left_tokens = _token_set(left_body)
    right_tokens = _token_set(right_body)
    token_score = (
        len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if left_tokens and right_tokens
        else 0.0
    )
    return max(gram_score, token_score)


def related_questions(question, existing: Iterable[dict], limit=5):
    """Return likely semantic neighbors, without treating shared source alone as a duplicate."""
    concept = normalize_duplicate_text(question.get("tested_concept"))
    targets = _answer_targets(question)
    ranked = []
    for other in existing:
        if other.get("id") and other.get("id") == question.get("id"):
            continue
        similarity = question_text_similarity(question, other)
        other_concept = normalize_duplicate_text(other.get("tested_concept"))
        shared_target = bool(targets & _answer_targets(other))
        same_concept = bool(concept and concept == other_concept)
        same_source = shares_source(question, other)

        relevant = (
            same_concept
            or (shared_target and similarity >= 0.08)
            or similarity >= 0.22
            or (same_source and similarity >= 0.12)
        )
        if not relevant:
            continue
        score = (
            similarity * 0.7
            + (0.2 if same_concept else 0)
            + (0.15 if shared_target else 0)
            + (0.05 if same_source else 0)
        )
        ranked.append((score, other))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [question for _, question in ranked[:limit]]


def is_high_confidence_near_duplicate(left, right):
    """Conservative non-LLM fallback used in offline generation mode."""
    left_goal = normalize_duplicate_text(left.get("question_goal"))
    right_goal = normalize_duplicate_text(right.get("question_goal"))
    if left_goal and right_goal and left_goal != right_goal:
        return False
    if not (_answer_targets(left) & _answer_targets(right)):
        return False
    similarity = question_text_similarity(left, right)
    return similarity >= 0.78 or (shares_source(left, right) and similarity >= 0.68)


def compact_question(question, *, max_body=800):
    """Keep only the fields needed for duplicate avoidance and judging."""
    return {
        "id": str(question.get("id") or ""),
        "body": str(question.get("body") or "")[:max_body],
        "question_type": str(question.get("question_type") or ""),
        "choices": [str(choice)[:240] for choice in (question.get("choices") or [])[:8]],
        "answer": str(question.get("answer") or "")[:400],
        "tested_concept": str(question.get("tested_concept") or "")[:200],
        "answer_target": str(question.get("answer_target") or "")[:200],
        "question_goal": str(question.get("question_goal") or "")[:40],
    }
