"""FastAPI application factory for Sage."""

import asyncio
import hashlib
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from sage_harness import (
    HarnessConfig,
    McpCatalogPort,
    McpManager,
    WebFetchPort,
    WebSearchPort,
)
from sage_harness.runtime.checkpoint import open_sqlite_checkpointer

from core.auth import AuthManager
from core.cloud.auth.repository import CloudRepository
from core.cloud.github import GitHubOAuthConfig, GitHubOAuthService
from core.cloud.model_providers import ModelProviderRepository, ProviderProbe
from core.coding.context import ModelCapabilityRegistry
from core.coding.provider_settings import SageProviderSettings, SageProviderSettingsStore
from core.coding.usage_store import UsageStore
from core.config.settings import get_settings
from core.harness.capability_health_store import CapabilityHealthStore
from core.harness.knowledge_source_proposal_adapter import (
    CodingKnowledgeSourceProposalService,
)
from core.harness.mcp_adapter import ConfiguredMcpCatalog
from core.harness.profile import normalize_runtime_profile
from core.harness.sandbox_factory import (
    normalize_sandbox_provider,
    reconcile_coding_sandboxes,
)
from core.harness.web_fetch import SafeWebFetchAdapter
from core.harness.web_search import SearxngWebSearchAdapter
from core.knowledge import (
    KnowledgeRecoveryPolicy,
    KnowledgeRelevancePolicy,
    KnowledgeRetrievalObservabilityConfig,
    KnowledgeSourceRoot,
    KnowledgeStore,
    load_relevance_policy,
)
from core.knowledge.embedding_factory import build_knowledge_embedding_provider
from core.knowledge.index_backend import KnowledgeIndexBackend
from core.knowledge.index_factory import build_knowledge_index
from core.knowledge.jobs import (
    KnowledgeJobRepository,
    KnowledgeJobService,
    RedisKnowledgeJobQueue,
)
from core.knowledge.parsing.adapters import build_external_parse_coordinator
from core.knowledge.retrieval import DenseEmbeddingProvider
from core.knowledge.source_proposals import KnowledgeSourceProposalRepository
from core.learning import MasteryLedger
from core.llm import create_llm
from core.publication import PublicationCandidateRepository, PublicationCandidateService
from db.database import AsyncSessionFactory
from db.migrations import init_db

logger = logging.getLogger(__name__)
DEFAULT_CODING_MODEL = "deepseek:deepseek-v4-flash"
DEFAULT_CODING_MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "deepseek:deepseek-v4-flash",
        "label": "DeepSeek V4 Flash",
        "provider": "deepseek",
    },
    {
        "id": "deepseek:deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "provider": "deepseek",
    },
]


def create_app(
    auth: AuthManager | None = None,
    coding_model_factory: Any | None = None,
    coding_goal_evaluator_factory: Any | None = None,
    coding_workspace_root: str | Path | None = None,
    coding_storage_root: str | Path | None = None,
    coding_model_catalog: list[dict[str, Any]] | None = None,
    coding_model_capabilities: dict[str, object] | ModelCapabilityRegistry | None = None,
    coding_default_model: str | None = None,
    coding_checkpoint_anchor_key: bytes | None = None,
    coding_deerflow_v2_enabled: bool | None = None,
    coding_default_runtime_profile: str | None = None,
    coding_harness_config: HarnessConfig | None = None,
    coding_sandbox_provider: str | None = None,
    coding_sandbox_image: str | None = None,
    coding_mcp_catalog: McpCatalogPort | None = None,
    coding_web_fetch_port: WebFetchPort | None = None,
    coding_web_search_port: WebSearchPort | None = None,
    database_auto_migrate: bool | None = None,
    cloud_repository: CloudRepository | None = None,
    cloud_dev_login_enabled: bool | None = None,
    cloud_canary_invite_login_enabled: bool | None = None,
    cloud_secure_cookies: bool | None = None,
    cloud_app_env: str | None = None,
    cloud_github_oauth_service: GitHubOAuthService | None = None,
    cloud_frontend_url: str | None = None,
    cloud_model_provider_repository: ModelProviderRepository | None = None,
    cloud_model_provider_probe: ProviderProbe | None = None,
    knowledge_workspace_root: str | Path | None = None,
    knowledge_database_path: str | Path | None = None,
    knowledge_source_roots: Mapping[str, KnowledgeSourceRoot] | None = None,
    knowledge_embedding_provider: DenseEmbeddingProvider | None = None,
    knowledge_relevance_policy: KnowledgeRelevancePolicy | None = None,
    knowledge_index: KnowledgeIndexBackend | None = None,
    knowledge_job_service: KnowledgeJobService | None = None,
    knowledge_source_proposal_service: CodingKnowledgeSourceProposalService | None = None,
    knowledge_jobs_enabled: bool | None = None,
    publication_candidate_service: PublicationCandidateService | None = None,
) -> FastAPI:
    """Create the Sage API app."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        app.state.coding_goal_followup_shutdown = False
        checkpoint_stack = AsyncExitStack()
        service = getattr(app.state, "knowledge_job_service", None)
        proposal_service = getattr(app.state, "knowledge_source_proposal_service", None)
        try:
            if bool(getattr(app.state, "database_auto_migrate", False)):
                try:
                    await init_db()
                except Exception as exc:
                    logger.error(
                        "Local database migration failed; run `python -m db.migrations` "
                        "after confirming PostgreSQL is ready: %s",
                        type(exc).__name__,
                    )
                    raise RuntimeError(
                        "local database migration failed; run `python -m db.migrations` "
                        "after confirming PostgreSQL is ready"
                    ) from exc

            sandbox_provider = (
                str(getattr(app.state, "coding_sandbox_provider", "local_workspace"))
                .strip()
                .lower()
            )
            if sandbox_provider == "container":
                try:
                    app.state.coding_sandbox_reconciled = await asyncio.to_thread(
                        reconcile_coding_sandboxes,
                        sandbox_provider,
                    )
                except Exception as exc:
                    logger.error("Container sandbox reconciliation failed: %s", type(exc).__name__)
                    raise
            if bool(getattr(app.state, "coding_deerflow_v2_enabled", False)):
                app.state.sage_harness_checkpointer = await checkpoint_stack.enter_async_context(
                    open_sqlite_checkpointer(
                        Path(app.state.coding_storage_root) / "harness-checkpoints.sqlite3"
                    )
                )
            if isinstance(proposal_service, CodingKnowledgeSourceProposalService):
                await proposal_service.prepare()
            if isinstance(service, KnowledgeJobService):
                await service.start()
            yield
        finally:
            app.state.coding_goal_followup_shutdown = True
            if isinstance(service, KnowledgeJobService):
                await service.stop()
            configured_store = getattr(app.state, "knowledge_store", None)
            configured_index = getattr(configured_store, "knowledge_index", None)
            close_index = getattr(configured_index, "close", None)
            if callable(close_index):
                close_index()
            owned_redis = getattr(app.state, "knowledge_job_redis_client", None)
            if owned_redis is not None:
                await owned_redis.aclose()
            await app.state.coding_run_registry.shutdown()
            goal_tasks = tuple(getattr(app.state, "coding_goal_followup_tasks", ()))
            for task in goal_tasks:
                task.cancel()
            if goal_tasks:
                await asyncio.gather(*goal_tasks, return_exceptions=True)
            mcp_manager = getattr(app.state, "coding_mcp_manager", None)
            if isinstance(mcp_manager, McpManager):
                await mcp_manager.aclose()
            await checkpoint_stack.aclose()

    app = FastAPI(title="Sage API", lifespan=lifespan)
    app.state.auth = auth
    app.state.coding_goal_evaluator_factory = coding_goal_evaluator_factory
    app.state.coding_goal_followup_tasks = set()
    app.state.coding_goal_followup_shutdown = False
    settings = get_settings()
    app_env = cloud_app_env or settings.app_env
    if app_env == "production" and cloud_repository is None:
        settings.validate_cloud_production_secrets()
    app.state.cloud_repository = cloud_repository or CloudRepository(AsyncSessionFactory)
    app.state.cloud_app_env = app_env
    app.state.cloud_dev_login_enabled = (
        settings.cloud_dev_login_enabled
        if cloud_dev_login_enabled is None
        else cloud_dev_login_enabled
    )
    app.state.cloud_canary_invite_login_enabled = (
        settings.cloud_canary_invite_login_enabled
        if cloud_canary_invite_login_enabled is None
        else cloud_canary_invite_login_enabled
    )
    app.state.cloud_secure_cookies = (
        True
        if app_env != "development"
        else settings.cloud_secure_cookies
        if cloud_secure_cookies is None
        else cloud_secure_cookies
    )
    app.state.cloud_frontend_url = cloud_frontend_url or settings.cloud_frontend_url
    app.state.database_auto_migrate = (
        False
        if app_env == "production" or settings.app_env == "production"
        else database_auto_migrate
        if database_auto_migrate is not None
        else settings.sage_auto_migrate and settings.app_env == "development"
    )
    provider_secret = settings.model_provider_encryption_secret
    if not provider_secret:
        provider_secret = hashlib.sha256(
            f"{settings.app_secret_key}:development-model-providers".encode()
        ).hexdigest()
    app.state.cloud_model_provider_repository = (
        cloud_model_provider_repository
        or ModelProviderRepository(
            AsyncSessionFactory,
            encryption_secret=provider_secret,
        )
    )
    app.state.cloud_model_provider_probe = cloud_model_provider_probe or ProviderProbe(
        app_env=app_env
    )
    app.state.cloud_github_oauth_service = cloud_github_oauth_service
    if app.state.cloud_github_oauth_service is None and all(
        (
            settings.github_oauth_client_id,
            settings.github_oauth_client_secret,
            settings.github_oauth_transaction_secret,
            settings.github_token_encryption_secret,
        )
    ):
        app.state.cloud_github_oauth_service = GitHubOAuthService(
            app.state.cloud_repository,
            GitHubOAuthConfig(
                client_id=settings.github_oauth_client_id,
                client_secret=settings.github_oauth_client_secret,
                redirect_uri=settings.github_oauth_redirect_uri,
                scope=settings.github_oauth_scope,
                transaction_secret=settings.github_oauth_transaction_secret,
                token_encryption_secret=settings.github_token_encryption_secret,
            ),
        )
    repo_root = Path(__file__).resolve().parent.parent
    resolved_workspace_root = Path(
        coding_workspace_root or settings.sage_coding_workspace_root or repo_root
    ).resolve()
    legacy_manifest_path = os.getenv(
        "SAGE_CODING_MODELS_FILE", str(repo_root / "config" / "coding_models.toml")
    )
    managed_settings_path = os.getenv("SAGE_CODING_SETTINGS_FILE") or None
    provider_settings_store = SageProviderSettingsStore(
        resolved_workspace_root,
        external_path=managed_settings_path,
        legacy_manifest_path=legacy_manifest_path,
    )
    resolved_settings: SageProviderSettings | None = None
    if coding_model_catalog is None and coding_model_capabilities is None:
        resolved_settings = provider_settings_store.load()
        resolved_catalog = resolved_settings.catalog
        resolved_capabilities = resolved_settings.registry
        resolved_default_model = coding_default_model or resolved_settings.default_model
    else:
        resolved_catalog = (
            DEFAULT_CODING_MODEL_CATALOG if coding_model_catalog is None else coding_model_catalog
        )
        resolved_capabilities = (
            coding_model_capabilities
            if isinstance(coding_model_capabilities, ModelCapabilityRegistry)
            else ModelCapabilityRegistry(coding_model_capabilities)
            if coding_model_capabilities is not None
            else ModelCapabilityRegistry.from_env()
        )
        resolved_default_model = coding_default_model or DEFAULT_CODING_MODEL
    app.state.coding_provider_settings_store = provider_settings_store
    app.state.coding_provider_settings = resolved_settings
    app.state.coding_provider_settings_available = resolved_settings is not None
    app.state.coding_model_catalog = resolved_catalog
    app.state.coding_default_model = resolved_default_model
    app.state.coding_model_capabilities = resolved_capabilities
    app.state.coding_model_reasoning_modes = (
        resolved_settings.reasoning_modes
        if resolved_settings is not None
        else {
            str(item["id"]): tuple(str(mode) for mode in item.get("reasoning_modes", []))
            for item in resolved_catalog
        }
    )
    if coding_model_factory is None:

        def default_coding_model_factory(
            model_id: str = resolved_default_model,
            *,
            reasoning_mode: str = "off",
        ) -> Any:
            settings = app.state.coding_provider_settings
            if settings is None:
                return create_llm(model_id, reasoning_mode=reasoning_mode)
            return create_llm(
                model_id,
                provider_settings=settings,
                reasoning_mode=reasoning_mode,
            )

        app.state.coding_model_factory = default_coding_model_factory
    else:
        app.state.coding_model_factory = coding_model_factory
    app.state.coding_checkpoint_anchor_key = coding_checkpoint_anchor_key
    app.state.coding_deerflow_v2_enabled = (
        settings.sage_deerflow_v2_enabled
        if coding_deerflow_v2_enabled is None
        else coding_deerflow_v2_enabled
    )
    configured_default_runtime_profile = (
        settings.sage_coding_default_runtime_profile
        if coding_default_runtime_profile is None
        else coding_default_runtime_profile
    )
    if not str(configured_default_runtime_profile).strip():
        raise ValueError("coding default runtime profile must not be empty")
    app.state.coding_default_runtime_profile = normalize_runtime_profile(
        configured_default_runtime_profile
    )
    app.state.coding_harness_config = coding_harness_config or HarnessConfig(
        max_model_calls=settings.sage_harness_max_model_calls,
        max_tool_calls=settings.sage_harness_max_tool_calls,
        max_run_tokens=settings.sage_harness_max_run_tokens,
        max_run_seconds=settings.sage_harness_max_run_seconds,
    )
    app.state.coding_sandbox_provider = normalize_sandbox_provider(
        settings.sage_coding_sandbox_provider
        if coding_sandbox_provider is None
        else coding_sandbox_provider
    )
    app.state.coding_sandbox_image = str(
        settings.sage_coding_sandbox_image if coding_sandbox_image is None else coding_sandbox_image
    ).strip()
    if not app.state.coding_sandbox_image:
        raise ValueError("coding sandbox image must not be empty")
    app.state.sage_harness_checkpointer = None
    resolved_mcp_catalog = coding_mcp_catalog or ConfiguredMcpCatalog({})
    app.state.coding_mcp_catalog = resolved_mcp_catalog
    app.state.coding_mcp_manager = (
        resolved_mcp_catalog if isinstance(resolved_mcp_catalog, McpManager) else None
    )
    if coding_web_search_port is not None:
        app.state.coding_web_search_port = coding_web_search_port
    elif settings.sage_web_search_enabled:
        if not settings.sage_web_search_endpoint.strip():
            raise ValueError("SAGE_WEB_SEARCH_ENDPOINT is required when web search is enabled")
        app.state.coding_web_search_port = SearxngWebSearchAdapter(
            settings.sage_web_search_endpoint,
            allow_http=app_env == "development",
            timeout_seconds=settings.sage_web_search_timeout_seconds,
        )
    else:
        app.state.coding_web_search_port = None
    if coding_web_fetch_port is not None:
        app.state.coding_web_fetch_port = coding_web_fetch_port
    elif settings.sage_web_fetch_enabled:
        app.state.coding_web_fetch_port = SafeWebFetchAdapter(
            connect_timeout_seconds=settings.sage_web_fetch_connect_timeout_seconds,
            read_timeout_seconds=settings.sage_web_fetch_read_timeout_seconds,
            total_timeout_seconds=settings.sage_web_fetch_total_timeout_seconds,
        )
    else:
        app.state.coding_web_fetch_port = None
    app.state.coding_workspace_root = resolved_workspace_root
    app.state.coding_storage_root = Path(
        coding_storage_root or settings.sage_coding_storage_root or (repo_root / ".coding")
    ).resolve()
    configured_knowledge_root = (
        Path(knowledge_workspace_root).expanduser()
        if knowledge_workspace_root is not None
        else Path(settings.knowledge_workspace_root).expanduser()
        if settings.knowledge_workspace_root.strip()
        else None
    )
    configured_source_roots = knowledge_source_roots
    if configured_source_roots is None and settings.knowledge_source_root.strip():
        source_id = settings.knowledge_source_id.strip() or "sage-learning"
        configured_source_roots = {
            source_id: KnowledgeSourceRoot(
                root_id=source_id,
                kind=settings.knowledge_source_kind.strip() or "obsidian",
                label=settings.knowledge_source_label.strip() or source_id,
                path=Path(settings.knowledge_source_root).expanduser(),
            )
        }
    app.state.knowledge_store = None
    app.state.knowledge_job_service = knowledge_job_service
    app.state.knowledge_job_redis_client = None
    app.state.knowledge_source_proposal_service = knowledge_source_proposal_service
    owns_knowledge_job_service = False
    if configured_knowledge_root is not None:
        configured_knowledge_database = (
            Path(knowledge_database_path).expanduser()
            if knowledge_database_path is not None
            else Path(settings.knowledge_database_path).expanduser()
            if settings.knowledge_database_path.strip()
            else configured_knowledge_root / ".sage" / "knowledge.sqlite3"
        )
        configured_embedding = knowledge_embedding_provider
        if configured_embedding is None:
            configured_embedding = build_knowledge_embedding_provider(settings)
        configured_relevance_policy = knowledge_relevance_policy
        if configured_relevance_policy is None and settings.knowledge_relevance_policy_path.strip():
            configured_relevance_policy = load_relevance_policy(
                Path(settings.knowledge_relevance_policy_path).expanduser().resolve()
            )
        configured_index = knowledge_index or build_knowledge_index(
            backend=settings.knowledge_index_backend,
            workspace_id=settings.knowledge_workspace_id,
            postgres_dsn=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
            postgres_connect_timeout_seconds=settings.knowledge_postgres_connect_timeout_seconds,
            postgres_pool_max_connections=settings.knowledge_postgres_pool_max_connections,
            embedding_provider=configured_embedding,
            relevance_policy=configured_relevance_policy,
            observability=KnowledgeRetrievalObservabilityConfig(
                enabled=settings.knowledge_retrieval_observability_enabled,
                hmac_key=settings.knowledge_retrieval_observability_hmac_key,
                max_candidates=settings.knowledge_retrieval_observability_candidate_limit,
            ),
        )
        app.state.knowledge_store = KnowledgeStore(
            configured_knowledge_root,
            configured_knowledge_database,
            configured_source_roots or {},
            knowledge_index=configured_index,
            recovery_policy=KnowledgeRecoveryPolicy(
                enabled=settings.knowledge_recovery_enabled,
                min_results=settings.knowledge_recovery_min_results,
                top_k_multiplier=settings.knowledge_recovery_top_k_multiplier,
                max_top_k=settings.knowledge_recovery_max_top_k,
            ),
        )
        app.state.knowledge_store.initialize()
        enable_jobs = (
            settings.knowledge_jobs_enabled
            if knowledge_jobs_enabled is None
            else knowledge_jobs_enabled
        )
        if app.state.knowledge_job_service is None and enable_jobs:
            redis_module: Any = import_module("redis.asyncio")
            redis_client: Any = redis_module.from_url(settings.redis_url)
            app.state.knowledge_job_redis_client = redis_client
            app.state.knowledge_job_service = KnowledgeJobService(
                app.state.knowledge_store,
                KnowledgeJobRepository(AsyncSessionFactory),
                RedisKnowledgeJobQueue(redis_client),
                external_parser=build_external_parse_coordinator(settings),
            )
            owns_knowledge_job_service = True
    if (
        app.state.knowledge_source_proposal_service is None
        and app.state.knowledge_job_service is not None
        and owns_knowledge_job_service
        and app_env != "production"
    ):
        app.state.knowledge_source_proposal_service = CodingKnowledgeSourceProposalService(
            KnowledgeSourceProposalRepository(AsyncSessionFactory),
            app.state.knowledge_job_service,
            coding_storage_root=app.state.coding_storage_root,
        )
    app.state.coding_usage_store = UsageStore(resolved_workspace_root / ".sage" / "usage.sqlite3")
    app.state.harness_capability_health_store = CapabilityHealthStore(
        app.state.coding_storage_root / "capability-health.sqlite3"
    )
    app.state.mastery_ledger = MasteryLedger(
        app.state.coding_storage_root / "mastery-ledger.sqlite3"
    )
    app.state.publication_candidate_service = (
        publication_candidate_service
        or PublicationCandidateService(PublicationCandidateRepository(AsyncSessionFactory))
    )
    app.state.coding_sessions = {}
    from api.coding_runs import CodingRunRegistry

    app.state.coding_run_registry = CodingRunRegistry(
        app.state.coding_storage_root,
        capability_health_store=app.state.harness_capability_health_store,
    )

    from api import (
        assistant,
        cloud_auth,
        cloud_model_providers,
        cloud_workspaces,
        coding,
        knowledge,
        publication,
        routes,
    )

    app.include_router(assistant.router)
    app.include_router(routes.health_router)
    app.include_router(routes.router)
    app.include_router(coding.router)
    app.include_router(knowledge.router)
    app.include_router(publication.router)
    app.include_router(cloud_auth.router)
    app.include_router(cloud_model_providers.router)
    app.include_router(cloud_workspaces.router)
    return app


app = create_app(auth=AuthManager(get_settings().app_access_codes))
