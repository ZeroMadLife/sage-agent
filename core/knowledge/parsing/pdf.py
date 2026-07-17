"""Bounded text-layer PDF parser; OCR remains an explicit adapter boundary."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import PurePosixPath

from pypdf import PdfReader

from .common import stable_id
from .errors import DocumentParseError, DocumentRequiresOcrError
from .types import ParsedBlock, ParsedDocument, ParseProvenance, ParseRequest

_MAX_PDF_PAGES = 100
_MIN_TEXT_CHARACTERS = 16
_MAX_EXTRACTED_TEXT_CHARACTERS = 4 * 1024 * 1024


class TextPdfParser:
    parser_id = "sage.pdf.text"
    parser_version = "1.0.0"
    priority = 100
    media_types = frozenset({"application/pdf"})
    extensions = frozenset({".pdf"})

    def parse(self, request: ParseRequest) -> ParsedDocument:
        try:
            reader = PdfReader(BytesIO(request.payload), strict=False)
        except Exception as exc:
            raise DocumentParseError("PDF source could not be parsed") from exc
        if reader.is_encrypted:
            raise DocumentParseError("encrypted PDF is not supported")
        if len(reader.pages) > _MAX_PDF_PAGES:
            raise DocumentParseError(f"PDF exceeds {_MAX_PDF_PAGES} page limit")
        document_id = stable_id(
            "pdoc",
            request.source_id,
            request.relative_path,
            request.source_revision,
            self.parser_id,
            self.parser_version,
        )
        blocks: list[ParsedBlock] = []
        rendered_pages: list[str] = []
        extracted_characters = 0
        meaningful_characters = 0
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = _clean_pdf_text(page.extract_text() or "")
            except Exception as exc:
                raise DocumentParseError("PDF text extraction failed") from exc
            if not text:
                continue
            extracted_characters += len(text)
            meaningful_characters += len(re.sub(r"\s+", "", text))
            if extracted_characters > _MAX_EXTRACTED_TEXT_CHARACTERS:
                raise DocumentParseError("PDF extracted text exceeds 4 MiB limit")
            rendered = f"## Page {page_number}\n\n{text}"
            blocks.append(
                ParsedBlock(
                    block_id=stable_id("pblk", document_id, str(page_number), text),
                    ordinal=len(blocks),
                    kind="paragraph",
                    text=rendered,
                    heading_path=(f"Page {page_number}",),
                    page=page_number,
                    confidence=0.95,
                )
            )
            rendered_pages.append(rendered)
        rendered_markdown = "\n\n".join(rendered_pages).strip()
        if meaningful_characters < _MIN_TEXT_CHARACTERS:
            raise DocumentRequiresOcrError("PDF has no usable text layer and requires OCR")
        metadata_title = ""
        try:
            metadata_title = str(reader.metadata.title or "") if reader.metadata else ""
        except Exception:
            metadata_title = ""
        fallback = PurePosixPath(request.relative_path).stem.replace("-", " ").replace("_", " ")
        return ParsedDocument(
            document_id=document_id,
            source_id=request.source_id,
            relative_path=request.relative_path,
            source_revision=request.source_revision,
            title=re.sub(r"\s+", " ", metadata_title).strip() or fallback or "Untitled",
            language="und",
            rendered_markdown=rendered_markdown + "\n",
            blocks=tuple(blocks),
            provenance=ParseProvenance(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                input_revision=request.source_revision,
                media_type=request.media_type,
            ),
        )


def _clean_pdf_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)
