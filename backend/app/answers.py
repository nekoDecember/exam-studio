"""Helpers for numeric answers and preventing answer leakage into prompts."""

import re
import unicodedata


_NUMBER = r"[+-]?\d[\d,]*(?:\.\d+)?(?:[〜～~–−][+-]?\d[\d,]*(?:\.\d+)?)?"
_UNIT = (
    r"(?:パーセント|億円|万円|千円|営業日|年間|週間|か月間|カ月間|ヶ月間|"
    r"種類|年度|か月|カ月|ヶ月|時間|分|秒|週|日間|年|月|日|"
    r"人|名|個|件|回|台|本|冊|倍|点|円|ドル|km|kg|cm|mm|MB|GB|KB|TB|"
    r"ml|mL|m|g|L|%|"
    r"以内|以上|以下|未満|程度|まで|強|弱)"
)
_UNIT_SUFFIX = rf"(?:(?:{_UNIT}))*"
_NUMERIC_ANSWER = re.compile(rf"^({_NUMBER})({_UNIT_SUFFIX})$", re.IGNORECASE)
_NUMERIC_IN_TEXT = re.compile(
    rf"(?<![\d.,])(?P<number>{_NUMBER})(?P<unit>{_UNIT_SUFFIX})(?![\d.,])",
    re.IGNORECASE,
)
_RANGE_SEPARATOR = re.compile(r"[〜～~–−]")
_ANSWER_SEPARATOR = re.compile(r"\s*/\s*")


def _canonical_number(value):
    value = value.replace(",", "")
    sign = ""
    if value[:1] in ("+", "-"):
        sign, value = value[0], value[1:]
    whole, _, fraction = value.partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    if whole == "0" and not fraction:
        sign = ""
    return f"{sign}{whole}{'.' + fraction if fraction else ''}"


def _canonical_numeric_expression(value):
    parts = _RANGE_SEPARATOR.split(value)
    return "~".join(_canonical_number(part) for part in parts)


def numeric_value_key(value):
    """Return a unit-insensitive key for a numeric answer, if applicable."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = re.sub(r"\s+", "", normalized).casefold()
    parts = _ANSWER_SEPARATOR.split(normalized)
    if len(parts) > 1:
        keys = [_single_numeric_value_key(part) for part in parts]
        return "/".join(keys) if all(key is not None for key in keys) else None
    return _single_numeric_value_key(normalized)


def _single_numeric_value_key(normalized):
    match = _NUMERIC_ANSWER.fullmatch(normalized)
    if not match:
        return None
    return _canonical_numeric_expression(match.group(1))


def strip_numeric_units(value):
    """Remove trailing measurement/count units from a numeric answer."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    parts = _ANSWER_SEPARATOR.split(normalized)
    if len(parts) > 1 and all(numeric_value_key(part) is not None for part in parts):
        return " / ".join(strip_numeric_units(part) for part in parts)
    match = _NUMERIC_ANSWER.fullmatch(re.sub(r"\s+", "", normalized))
    return match.group(1) if match else value


def answer_appears_in_body(body, answer):
    """Check whether a question body already gives its expected answer."""
    normalized_body = unicodedata.normalize("NFKC", str(body or "")).casefold()
    compact_body = re.sub(
        r"[\s、。！？!?.,:：;；()（）「」『』【】［］〈〉《》・…—\-]",
        "",
        normalized_body,
    )
    answer_parts = _ANSWER_SEPARATOR.split(
        unicodedata.normalize("NFKC", str(answer or "")).casefold()
    )
    for answer_part in answer_parts:
        numeric_key = numeric_value_key(answer_part)
        if numeric_key is not None:
            for match in _NUMERIC_IN_TEXT.finditer(normalized_body):
                if _canonical_numeric_expression(match.group("number")) == numeric_key:
                    return True
        compact_answer = re.sub(
            r"[\s、。！？!?.,:：;；()（）「」『』【】［］〈〉《》・…—\-]",
            "",
            answer_part,
        )
        if len(compact_answer) >= 2 and compact_answer in compact_body:
            return True
    return False
