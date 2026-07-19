"""Parser Registry and stable parsed-document contracts."""

from .errors import DocumentParseError, DocumentRequiresOcrError
from .external import (
    ExternalAdapterError,
    ExternalParseAdapter,
    ExternalParseCompleted,
    ExternalParseCoordinator,
    ExternalParseDispatch,
    ExternalParsePending,
    ExternalParsePolicy,
    ExternalParsePolicyError,
    ExternalParseProgress,
    ExternalParseTicket,
    ExternalParsingFailedError,
    ExternalParsingTransientError,
    ProgressCallback,
    ResumableExternalParseAdapter,
)
from .html import HtmlParser
from .markdown import MarkdownParser
from .pdf import TextPdfParser
from .registry import (
    DocumentParser,
    ParserConflictError,
    ParserNotFoundError,
    ParserRegistry,
)
from .serialization import deserialize_document, serialize_document
from .types import (
    BlockKind,
    ParseArtifact,
    ParsedBlock,
    ParsedDocument,
    ParseProvenance,
    ParseRequest,
)


def default_parser_registry() -> ParserRegistry:
    registry = ParserRegistry()
    registry.register(HtmlParser())
    registry.register(MarkdownParser())
    registry.register(TextPdfParser())
    return registry


__all__ = [
    "BlockKind",
    "DocumentParseError",
    "DocumentParser",
    "DocumentRequiresOcrError",
    "ExternalAdapterError",
    "ExternalParseAdapter",
    "ExternalParseCompleted",
    "ExternalParseCoordinator",
    "ExternalParseDispatch",
    "ExternalParsePending",
    "ExternalParsePolicy",
    "ExternalParsePolicyError",
    "ExternalParseProgress",
    "ExternalParseTicket",
    "ExternalParsingFailedError",
    "ExternalParsingTransientError",
    "HtmlParser",
    "MarkdownParser",
    "ParseArtifact",
    "ParseProvenance",
    "ParseRequest",
    "ParsedBlock",
    "ParsedDocument",
    "ParserConflictError",
    "ParserNotFoundError",
    "ParserRegistry",
    "ProgressCallback",
    "ResumableExternalParseAdapter",
    "TextPdfParser",
    "default_parser_registry",
    "deserialize_document",
    "serialize_document",
]
