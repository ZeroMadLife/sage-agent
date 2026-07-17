"""Safe parser errors surfaced through durable ingestion jobs."""


class DocumentParseError(ValueError):
    """A supported document could not be parsed within the local contract."""


class DocumentRequiresOcrError(DocumentParseError):
    """A document has no usable text layer and requires an OCR adapter."""
