"""Extract source-grounded fact candidates and recognize document metadata."""

import re
import unicodedata

from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator

QUALITY_VERSION = "2026-10-01.v1"
ReasonCode = Literal[
    "document_metadata",
    "visual_trivia",
    "low_learning_value",
    "unsupported_answer",
    "ambiguous_target",
    "arbitrary_cloze",
    "answer_leak",
]


class QualityReason(BaseModel):
    code: ReasonCode
    message: str


class QualityChecks(BaseModel):
    model_config = ConfigDict(strict=True)
    learning_value: bool
    grounded: bool
    clear: bool


class QualityReview(BaseModel):
    model_config = ConfigDict(strict=True)
    acceptable: bool
    checks: QualityChecks
    reasons: list[QualityReason]
    warnings: list[str]

    @model_validator(mode="after")
    def consistent_decision(self):
        passed = all(self.checks.model_dump().values())
        if self.acceptable != passed or (self.acceptable and self.reasons):
            raise ValueError("品質審査の判定が整合していません")
        if not self.acceptable and not self.reasons:
            raise ValueError("品質棄却には理由が必要です")
        if any(not reason.message.strip() for reason in self.reasons):
            raise ValueError("品質棄却の説明が空です")
        return self


def quality_review(reasons=(), warnings=()):
    """Build a deterministic result for local rules and the demo provider."""
    codes = {reason["code"] for reason in reasons}
    return QualityReview(
        acceptable=not reasons,
        checks=QualityChecks(
            learning_value=not codes.intersection(
                {
                    "document_metadata",
                    "visual_trivia",
                    "low_learning_value",
                    "arbitrary_cloze",
                }
            ),
            grounded="unsupported_answer" not in codes,
            clear=not codes.intersection({"ambiguous_target", "answer_leak"}),
        ),
        reasons=[QualityReason(**reason) for reason in reasons],
        warnings=list(warnings),
    ).model_dump()


def obvious_trivia_reason(question, evidence=()):
    """Reject explicit presentation trivia, leaving contextual cases to review."""
    body = _compact(question.get("body"))
    normalized = unicodedata.normalize(
        "NFKC", str(question.get("body") or "")
    ).casefold()
    if reason := metadata_trivia_reason(question):
        return {"code": "document_metadata", "message": reason}
    evidence_text = " ".join(str(item.get("text") or "") for item in evidence)
    # In a graph-reading lesson, labels and scales can themselves be the topic.
    graph_lesson = re.search(
        r"グラフ(?:の)?(?:読み方|読解|作成方法)|軸の意味|目盛りの(?:意味|読み方)",
        evidence_text,
    )
    visual_count = re.search(
        r"(?:目盛り?|メモリ|メモリー|格子線|グリッド線).{0,16}(?:何本|何個|いくつ|本数|個数|数は)|"
        r"(?:何本|何個|いくつ).{0,16}(?:目盛り?|メモリ|格子線)",
        body,
    ) or re.search(
        r"\b(?:how many|number of)\b.{0,30}\b(?:ticks|tick marks|gridlines)\b",
        normalized,
    )
    if visual_count and not graph_lesson:
        return {
            "code": "visual_trivia",
            "message": "図表の目盛りや表示要素を数えるだけの問題です",
        }
    return None


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

def _compact(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", text)


def metadata_trivia_reason(question):
    """Return a reason when the prompt asks about document layout or location."""
    normalized = unicodedata.normalize(
        "NFKC", str(question.get("body") or "")
    ).casefold()
    body = _compact(normalized)
    if not body:
        return "問題文が空です"
    # Only explicit identification/location questions are fast-rejected.
    # A required form name, or a title mentioned as context, is meaningful.
    operational = re.search(r"提出|申請|添付|必要な書類|必要書類|保存する書類", body)
    asks_label = re.search(
        r"(?:資料名|ファイル名|書類名|文書名|タイトル|題名|表題|見出し|章名|節名)"
        r"(?:は|を|が|として|に).{0,12}(?:何|どれ|答え|選ん|記載|書かれ)",
        body,
    )
    document_identity = re.search(
        r"(?:この|本|入力した|登録した|提示された|アップロードした)(?:資料|文書|書類|報告書)",
        body,
    )
    location_only = re.search(
        r"(?:何ページ|何頁|何行|どのページ|どの行|どのスライド)|"
        r"(?:ページ数|ページ番号|行番号|スライド番号).{0,8}(?:何|いくつ|答え|選ん)|"
        r"(?:どこ|何行|どの行).{0,8}(?:記載|書かれ|載って)",
        body,
    )
    english_identity = re.search(
        r"\b(?:what|which)\b.{0,25}\b(?:title|file name|document name)\b",
        normalized,
    )
    if location_only or (
        not operational
        and (
            asks_label
            or english_identity
            or (
                document_identity
                and re.search(r"(?:名前|名称).{0,10}(?:何|答え|選ん)", body)
            )
        )
    ):
        return "入力資料の名前・見出し・記載場所だけを問う問題です"
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
