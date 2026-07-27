"""Safe PNG parser exposing metadata and one normalized whole-image region."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import PurePosixPath

from PIL import Image

from .common import stable_id
from .errors import DocumentParseError
from .types import ParsedBlock, ParsedDocument, ParseProvenance, ParseRequest

_MAX_PIXELS = 40_000_000
_MAX_PNG_BYTES = 20 * 1024 * 1024
_MAX_METADATA_CHARACTERS = 4_096


class PngImageParser:
    parser_id = "sage.png"
    parser_version = "1.0.0"
    priority = 100
    media_types = frozenset({"image/png"})
    extensions = frozenset({".png"})

    def parse(self, request: ParseRequest) -> ParsedDocument:
        if not request.payload or len(request.payload) > _MAX_PNG_BYTES:
            raise DocumentParseError("PNG source exceeds input limits")
        try:
            with Image.open(BytesIO(request.payload)) as image:
                detected_format = image.format
                width, height = image.size
                metadata = dict(image.info)
                image.verify()
            if detected_format != "PNG" or width < 1 or height < 1 or width * height > _MAX_PIXELS:
                raise ValueError("PNG source exceeds image limits")
            with Image.open(BytesIO(request.payload)) as image:
                image.load()
                if image.format != "PNG" or image.size != (width, height):
                    raise ValueError("PNG validation changed after decode")
        except Exception as exc:
            raise DocumentParseError("PNG source could not be parsed") from exc
        fallback = PurePosixPath(request.relative_path).stem.replace("-", " ").replace("_", " ")
        title = _metadata_text(metadata.get("Title")) or fallback or "Untitled image"
        description = _metadata_text(metadata.get("Description"))
        lines = [f"Image dimensions: {width} x {height} px."]
        if description:
            lines.append(description)
        else:
            lines.append("No embedded semantic description; OCR/VLM is required for image content.")
        text = "\n".join(lines)
        document_id = stable_id(
            "pdoc",
            request.source_id,
            request.relative_path,
            request.source_revision,
            self.parser_id,
            self.parser_version,
        )
        block = ParsedBlock(
            block_id=stable_id("pblk", document_id, "0", "media", text),
            ordinal=0,
            kind="media",
            text=text,
            heading_path=(title,),
            page=1,
            bbox=(0.0, 0.0, 1.0, 1.0),
            media_ref=request.relative_path,
            confidence=1.0,
        )
        return ParsedDocument(
            document_id=document_id,
            source_id=request.source_id,
            relative_path=request.relative_path,
            source_revision=request.source_revision,
            title=title[:500],
            language="und",
            rendered_markdown=f"# {title}\n\n{text}\n",
            blocks=(block,),
            provenance=ParseProvenance(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                input_revision=request.source_revision,
                media_type=request.media_type,
            ),
        )


def _metadata_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:_MAX_METADATA_CHARACTERS]
