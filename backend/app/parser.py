import re
import subprocess
import tempfile
from pathlib import Path
from PIL import Image
from pypdf import PdfReader
from openpyxl import load_workbook
import pytesseract
from .db import uid

ALLOWED = {".pdf", ".xlsx", ".png", ".jpg", ".jpeg", ".webp", ".txt"}


def ocr(image):
    result = pytesseract.image_to_data(
        image, lang="jpn+eng", output_type=pytesseract.Output.DICT, timeout=90
    )
    words = [
        (t, float(c))
        for t, c in zip(result["text"], result["conf"])
        if t.strip() and float(c) >= 0
    ]
    return " ".join(t for t, c in words), round(
        sum(c for t, c in words) / len(words), 1
    ) if words else 0


def parse(path):
    suffix = path.suffix.lower()
    chunks = []

    def add(text, **loc):
        if text.strip():
            chunks.append({"id": uid(), "text": text.strip(), "categories": [], **loc})

    if suffix == ".txt":
        add(path.read_text(encoding="utf-8-sig"))
    elif suffix == ".xlsx":
        wb = load_workbook(path, read_only=True, data_only=True)
        for sheet in wb:
            if sheet.max_row > 50000 or sheet.max_column > 1000:
                raise ValueError("シートが大きすぎます（上限50,000行・1,000列）")
            for start in range(1, sheet.max_row + 1, 40):
                end = min(start + 39, sheet.max_row)
                rows = [
                    "\t".join("" if v is None else str(v) for v in row)
                    for row in sheet.iter_rows(
                        min_row=start, max_row=end, values_only=True
                    )
                ]
                from openpyxl.utils import get_column_letter

                add(
                    "\n".join(rows),
                    sheet_name=sheet.title,
                    cell_range=f"A{start}:{get_column_letter(sheet.max_column)}{end}",
                    table=True,
                )
        wb.close()
    elif suffix == ".pdf":
        reader = PdfReader(path)
        if len(reader.pages) > 100:
            raise ValueError("PDFは100ページ以内で登録してください")
        for index, page in enumerate(reader.pages):
            text = page.extract_text(extraction_mode="layout") or ""
            if len(text.strip()) > 20:
                add(text, page_number=index + 1)
            else:
                with tempfile.TemporaryDirectory() as temp:
                    out = Path(temp) / "page"
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-f",
                            str(index + 1),
                            "-l",
                            str(index + 1),
                            "-scale-to",
                            "2400",
                            "-singlefile",
                            "-png",
                            str(path),
                            str(out),
                        ],
                        check=True,
                        capture_output=True,
                        timeout=90,
                    )
                    with Image.open(str(out) + ".png") as im:
                        text, confidence = ocr(im)
                    add(text, page_number=index + 1, ocr_confidence=confidence)
    else:
        with Image.open(path) as im:
            text, confidence = ocr(im)
        add(text, page_number=1, ocr_confidence=confidence)
    if not chunks:
        raise ValueError(
            "文字を抽出できませんでした。鮮明な資料を使用するかテキストで登録してください"
        )
    return chunks


def analyze(chunks):
    text = "\n".join(c["text"] for c in chunks)
    parts = re.split(
        r"(?m)(?=^(?:第[0-9０-９一二三四五六七八九十]+問|問\s*[0-9０-９]+))", text
    )
    questions = []
    for i, part in enumerate(p for p in parts if p.strip()):
        choices = re.findall(r"(?m)^\s*[①②③④⑤⑥A-Dア-エ1-4][.．、)）\s]\s*(.+)$", part)
        answer = re.search(r"(?:正解|解答|答え)\s*[:：]\s*(.+)", part)
        questions.append(
            {
                "id": uid(),
                "question_number": str(i + 1),
                "parent_question_id": "",
                "raw_text": part.strip(),
                "question_type": "choice"
                if choices
                else "blank"
                if re.search(r"[_＿]{2,}|[（(]\s*[)）]", part)
                else "short",
                "choices": choices,
                "answer": answer.group(1) if answer else "",
                "structure_json": {
                    "blank_count": len(re.findall(r"[_＿]{2,}|[（(]\s*[)）]", part)),
                    "word_bank": [],
                    "sub_questions": re.findall(
                        r"(?m)^\s*[（(][0-9０-９]+[)）].*", part
                    ),
                },
                "style_profile_json": {
                    "length": len(part),
                    "ending": part.strip()[-80:],
                },
            }
        )
    return questions
