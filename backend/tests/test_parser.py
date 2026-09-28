import shutil
import zipfile

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.parser import MAX_CHUNK_CHARS, parse, split_chunks


def test_long_text_is_split_on_readable_boundaries():
    text = ("最初の段落です。" * 900) + "\n\n" + ("次の段落です。" * 900)
    chunks = split_chunks(
        [{"id": "original", "text": text, "categories": ["規程"]}]
    )
    assert len(chunks) > 1
    assert all(len(chunk["text"]) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert all(chunk["source_chunk_id"] == "original" for chunk in chunks)
    assert [chunk["segment_index"] for chunk in chunks] == list(
        range(1, len(chunks) + 1)
    )
    assert all(chunk["segment_count"] == len(chunks) for chunk in chunks)
    assert all(chunk["categories"] == ["規程"] for chunk in chunks)


def test_text_pdf_location(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(600, 800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 18 Tf 50 700 Td (Information security requires prompt reporting.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    path = tmp_path / "text.pdf"
    writer.write(path)
    chunks = parse(path)
    assert chunks[0]["page_number"] == 1
    assert "Information security" in chunks[0]["text"]


def test_powerpoint_and_html_extract_readable_sections(tmp_path):
    slides = tmp_path / "training.pptx"
    with zipfile.ZipFile(slides, "w") as package:
        package.writestr("ppt/slides/slide2.xml", '<p:sld xmlns:p="p" xmlns:a="a"><a:t>第二章</a:t><a:t>承認を得る</a:t></p:sld>')
        package.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="p" xmlns:a="a"><a:t>第一章</a:t><a:t>情報を守る</a:t></p:sld>')
    chunks = parse(slides)
    assert [chunk["slide_number"] for chunk in chunks] == [1, 2]
    assert "情報を守る" in chunks[0]["text"]
    page = tmp_path / "guide.html"
    page.write_text("<html><script>secret()</script><nav>メニュー</nav><article><h1>昇格試験</h1><p>情報管理を学ぶ。</p></article></html>", encoding="utf-8")
    text = parse(page)[0]["text"]
    assert "情報管理を学ぶ" in text and "メニュー" not in text and "secret" not in text


def test_pdf_over_old_page_limit_is_split_by_page(tmp_path):
    path = tmp_path / "large.pdf"
    with pymupdf.open() as pdf:
        for number in range(101):
            page = pdf.new_page()
            page.insert_text((40, 80), f"Policy page {number + 1}: report incidents immediately.")
        pdf.save(path)
    chunks = parse(path)
    assert len(chunks) == 101
    assert chunks[-1]["page_number"] == 101


@pytest.mark.skipif(
    not shutil.which("tesseract") or not shutil.which("pdftoppm"),
    reason="OCR system tools are installed in Docker",
)
@pytest.mark.parametrize("suffix", [".png", ".pdf"])
def test_image_and_scanned_pdf(tmp_path, suffix):
    image = Image.new("RGB", (1500, 320), "white")
    ImageDraw.Draw(image).text(
        (45, 100),
        "Security policy: report incidents promptly.",
        font=ImageFont.load_default(size=42),
        fill="black",
    )
    path = tmp_path / ("scan" + suffix)
    image.save(path)
    chunks = parse(path)
    assert "report" in chunks[0]["text"].lower()
    assert chunks[0]["page_number"] == 1 and chunks[0]["ocr_confidence"] > 0
