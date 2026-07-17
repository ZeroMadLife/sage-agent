"""Bounded text-PDF parser coverage without OCR side effects."""

from io import BytesIO

import pytest
from pypdf import PdfWriter

from core.knowledge.parsing import DocumentRequiresOcrError, ParseRequest, TextPdfParser


def _text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(payload)


def _request(payload: bytes) -> ParseRequest:
    return ParseRequest(
        source_id="src_pdf",
        relative_path="manual.pdf",
        source_revision="sha256:pdf",
        media_type="application/pdf",
        payload=payload,
    )


def test_text_pdf_parser_preserves_page_provenance() -> None:
    document = TextPdfParser().parse(_request(_text_pdf("Durable PDF Harness")))

    assert document.provenance.parser_id == "sage.pdf.text"
    assert document.title == "manual"
    assert document.blocks[0].page == 1
    assert document.blocks[0].kind == "paragraph"
    assert "Durable PDF Harness" in document.rendered_markdown
    assert "## Page 1" in document.rendered_markdown


def test_blank_pdf_requires_ocr_instead_of_silent_empty_wiki() -> None:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(buffer)

    with pytest.raises(DocumentRequiresOcrError, match="requires OCR"):
        TextPdfParser().parse(_request(buffer.getvalue()))
