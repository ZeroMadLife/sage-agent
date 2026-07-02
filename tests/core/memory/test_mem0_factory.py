"""Mem0 client factory tests."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from core.memory.mem0_factory import create_mem0_client


def test_create_mem0_client_returns_client() -> None:
    """Factory returns a Mem0 client when SDK initialization succeeds."""
    with (
        patch.dict(
            "os.environ",
            {
                "QDRANT_HOST": "localhost",
                "QDRANT_PORT": "6333",
                "DEEPSEEK_API_KEY": "test-deepseek",
                "DEEPSEEK_BASE_URL": "https://api.deepseek.test/v1",
            },
        ),
        patch("core.memory.mem0_factory.Mem0Client") as mock_class,
    ):
        mock_client = MagicMock()
        mock_class.from_config.return_value = mock_client

        client = create_mem0_client()

        assert client is mock_client
        mock_class.from_config.assert_called_once()
        config = mock_class.from_config.call_args.args[0]
        assert config["vector_store"]["provider"] == "qdrant"
        assert config["vector_store"]["config"]["host"] == "localhost"
        assert config["llm"]["config"]["api_key"] == "test-deepseek"
        assert config["llm"]["config"]["openai_base_url"] == "https://api.deepseek.test/v1"
        assert config["llm"]["config"]["top_p"] == 1.0
        assert "base_url" not in config["llm"]["config"]


def test_create_mem0_client_supports_openai_embedder_provider() -> None:
    """Factory can use an OpenAI-compatible embedder to avoid local model downloads."""
    with (
        patch.dict(
            "os.environ",
            {
                "MEM0_EMBEDDER_PROVIDER": "openai",
                "MEM0_EMBEDDER_MODEL": "text-embedding-3-small",
                "MEM0_EMBEDDER_API_KEY": "test-embedding-key",
                "MEM0_EMBEDDER_BASE_URL": "https://embedding.test/v1",
                "MEM0_EMBEDDER_DIMS": "1536",
            },
        ),
        patch("core.memory.mem0_factory.Mem0Client") as mock_class,
    ):
        mock_class.from_config.return_value = MagicMock()

        create_mem0_client()

        config = mock_class.from_config.call_args.args[0]
        assert config["embedder"]["provider"] == "openai"
        assert config["embedder"]["config"] == {
            "api_key": "test-embedding-key",
            "model": "text-embedding-3-small",
            "openai_base_url": "https://embedding.test/v1",
            "embedding_dims": 1536,
        }


def test_openai_embedder_defaults_to_openai_embedding_model() -> None:
    """OpenAI-compatible embedder has a provider-appropriate default model."""
    with (
        patch.dict("os.environ", {"MEM0_EMBEDDER_PROVIDER": "openai"}, clear=True),
        patch("core.memory.mem0_factory.Mem0Client") as mock_class,
    ):
        mock_class.from_config.return_value = MagicMock()

        create_mem0_client()

        config = mock_class.from_config.call_args.args[0]
        assert config["embedder"]["config"]["model"] == "text-embedding-3-small"


def test_create_mem0_client_returns_none_on_error() -> None:
    """Initialization failures degrade to no long-term memory."""
    with patch("core.memory.mem0_factory.Mem0Client") as mock_class:
        mock_class.from_config.side_effect = Exception("Qdrant not available")

        client = create_mem0_client()

        assert client is None


def test_create_mem0_client_accepts_invalid_qdrant_port() -> None:
    """Invalid port config also degrades cleanly."""
    with patch.dict("os.environ", {"QDRANT_PORT": "not-a-port"}):
        client = create_mem0_client()

        assert client is None


def test_huggingface_embedder_dependency_is_declared() -> None:
    """The default Mem0 embedder depends on sentence-transformers at runtime."""
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "sentence-transformers" in requirements
