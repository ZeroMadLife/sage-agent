"""GitHub identity integration for the V7 cloud control plane."""

from core.cloud.github.oauth import (
    GitHubOAuthConfig,
    GitHubOAuthService,
    InvalidOAuthTransaction,
    OAuthCompletion,
    OAuthProviderError,
    OAuthRegistrationDenied,
    OAuthStart,
)

__all__ = [
    "GitHubOAuthConfig",
    "GitHubOAuthService",
    "InvalidOAuthTransaction",
    "OAuthCompletion",
    "OAuthProviderError",
    "OAuthRegistrationDenied",
    "OAuthStart",
]
