"""Mem0 client factory tests."""

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
