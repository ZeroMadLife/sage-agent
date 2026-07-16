"""Safe connector errors that can cross the Knowledge service boundary."""


class KnowledgeSourceError(ValueError):
    """Base class for deterministic source adapter failures."""


class KnowledgeSourceNotSupportedError(KnowledgeSourceError):
    """No trusted adapter is registered for the configured source kind."""


class KnowledgeSourceCheckpointConflictError(KnowledgeSourceError):
    """A paged source changed before its scan could complete."""


class KnowledgeScanError(KnowledgeSourceError):
    """The requested scan would leave its configured trust boundary."""
