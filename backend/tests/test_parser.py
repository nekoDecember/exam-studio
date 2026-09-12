import shutil
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import pytest
from app.parser import parse


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
