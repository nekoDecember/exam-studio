"""Extract source-grounded fact candidates and recognize document metadata."""

import re
import unicodedata


_EXPLANATORY_CUE = re.compile(
    r"とは.{1,}|を指す|を意味する|である|です|となる|になる|と定め|"
    r"が必要|を要する|しなければ|してはならない|を行う|を実施する|"
    r"を確認する|を含む|により.{2,}|によって.{2,}|"
    r"を.{1,12}(?:管理する|保存する|記録する|提出する|提供する|申請する|承認する|判断する|選択する|適用する)|"
    r"(?:は|が)(?:必要|不要|禁止|可能|不可|対象|義務|原則|認められる|含まれる)|"
    r"場合.{0,12}(?:は|に|を)|理由(?:は|が).{1,}|目的(?:は|が).{1,}|"
    r"条件(?:を|は|が).{1,}|例外(?:として|の場合|は).{1,}|"
    r"手順(?:は|として).{1,}"
)
_NUMERIC_FACT = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:%|％|円|万円|億円|ドル|年度|年|か月|ヶ月|月|週間|週|日|時間|分|秒|人|名|件|回|個|台|本|冊|倍|点|種類|km|kg|cm|mm|MB|GB|KB)",
    re.IGNORECASE,
)

_QUESTION_METADATA_PATTERNS = (
    re.compile(r"(?:何|どの|どれ)(?:ページ|頁|行|スライド|章番号|節番号)"),
    re.compile(
        r"(?:ページ|頁|行|スライド)(?:番号|数).{0,8}(?:は|が)?(?:何|どれ|いくつ)"
    ),
    re.compile(
        r"(?:見出し|小見出し|表題|タイトル|題名|資料名|ファイル名|章名|節名)"
        r".{0,10}(?:は|を|が)?(?:何|どれ|何と|なんと|呼ばれ|書かれ|記載)"
    ),
    re.compile(
        r"(?:何|どの|どれ).{0,8}(?:見出し|小見出し|表題|タイトル|題名|章名|節名)"
    ),
    re.compile(r"(?:どこ|何行|どの行).{0,8}(?:記載|書かれ|載って)"),
)
_ENGLISH_METADATA_PATTERNS = (
    re.compile(r"\b(?:what|which)\s+(?:page|line|slide|heading|title|section)\b"),
    re.compile(r"\b(?:number|count)\s+of\s+(?:pages|lines|slides)\b"),
    re.compile(r"\bwhat\s+is\s+(?:the\s+)?(?:page|line|slide)\s+(?:number|count)\b"),
    re.compile(r"\b(?:heading|title)\b.{0,24}\b(?:page|slide|document|file)\b"),
    re.compile(r"\b(?:page|line|slide)\b.{0,24}\b(?:heading|title)\b"),
    re.compile(r"\bwhat\s+is\s+(?:the\s+)?(?:heading|title|file\s+name)\b"),
)


def _compact(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", text)


def metadata_trivia_reason(question):
    """Return a reason when the prompt asks about document layout or location."""
    normalized = unicodedata.normalize("NFKC", str(question.get("body") or "")).casefold()
    body = _compact(normalized)
    if not body:
        return "問題文が空です"
    if any(pattern.search(body) for pattern in _QUESTION_METADATA_PATTERNS) or any(
        pattern.search(normalized) for pattern in _ENGLISH_METADATA_PATTERNS
    ):
        return "ページ・行・見出しなど資料の構造や場所だけを問う問題は出題しません"

    has_location = re.search(r"(?:ページ|頁|行番号|スライド番号)", body) or re.search(
        r"\b(?:page|line|slide)\b", normalized
    )
    has_document_label = re.search(
        r"(?:見出し|小見出し|表題|タイトル|題名|資料名|ファイル名|章名|節名)",
        body,
    ) or re.search(r"\b(?:heading|title|file name)\b", normalized)
    has_document_context = re.search(r"(?:資料|文書|このページ|このスライド)", body) or re.search(
        r"\b(?:document|file)\b", normalized
    )
    if has_document_label and (has_location or has_document_context):
        return "資料のページ・見出し・タイトルを特定するだけの問題は出題しません"
    return ""


def has_substantive_source_text(value):
    """Keep prose, factual table rows, and explicit numerical information."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return False

    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"^\s*(?:#{1,6}\s*|[-*・●◦▪▶■□※]+\s*)", "", raw_line).strip()
        line = re.sub(r"^(?:(?:第)?\d{1,4}[.)．、:：](?!\d)\s*)", "", line)
        line = line.strip(" \t　:：【】[]()（）")
        if line:
            lines.append(line)
    if not lines:
        return False

    if any(_EXPLANATORY_CUE.search(line) for line in lines):
        return True
    if any(len(line) >= 24 for line in lines):
        return True
    if any(len(line) >= 10 and re.search(r"[。！？!?]", line) for line in lines):
        return True
    if any(len(line) >= 8 and re.search(r"[:：].{2,}", line) for line in lines):
        return True
    return any(_NUMERIC_FACT.search(line) for line in lines)


def extract_testable_facts(value, limit=12):
    """Extract concise, position-local propositions to focus question writing."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    candidates = re.split(r"(?<=[。！？!?])|[\r\n]+", text)
    result = []
    seen = set()
    for raw in candidates:
        fact = re.sub(
            r"^\s*(?:[-*・●◦▪▶■□※]+\s*|(?:第)?\d{1,4}[.)．、:：](?!\d)\s*)",
            "",
            raw,
        )
        fact = fact.strip(" \t　:：【】[]()（）")
        if not fact or not has_substantive_source_text(fact):
            continue
        key = re.sub(r"\s+", "", fact).casefold()
        if key in seen:
            continue
        seen.add(key)
        if re.search(r"(?:ただし|例外|除く|の場合|条件|要件)", fact):
            kind = "condition_or_exception"
        elif re.search(r"(?:ため|により|によって|結果|原因|影響)", fact):
            kind = "cause_or_effect"
        elif re.search(r"(?:一方|対して|違い|比較|よりも)", fact):
            kind = "comparison"
        elif re.search(r"(?:まず|次に|その後|前に|後に|手順|順に)", fact):
            kind = "sequence_or_procedure"
        elif re.search(r"(?:とは|を指す|を意味する|と定義)", fact):
            kind = "definition"
        elif re.search(r"(?:必要|しなければ|してはならない|禁止|許可|義務|定め)", fact):
            kind = "rule"
        else:
            kind = "claim"
        result.append({"kind": kind, "text": fact[:1200]})
        if len(result) >= limit:
            break
    return result
