"""GigaChat SDK client factory — credentials only from Settings."""

from __future__ import annotations

import ssl

from gigachat import GigaChat

from svoi_pravila.config import GigaChatRuntimeSettings


def _ssl_context(settings: GigaChatRuntimeSettings) -> ssl.SSLContext:
    """Build a verifying SSL context rooted at the configured НУЦ CA bundle."""
    return ssl.create_default_context(cafile=str(settings.gigachat_ca_bundle_file.resolve()))


def create_gigachat_client(settings: GigaChatRuntimeSettings) -> GigaChat:
    """Create one async-capable GigaChat client from application Settings.

    All SDK parameters are passed explicitly. TLS verification is always on.
    """
    return GigaChat(
        credentials=settings.gigachat_credentials.get_secret_value(),
        scope=settings.gigachat_scope.api_scope(),
        ssl_context=_ssl_context(settings),
        verify_ssl_certs=True,
        timeout=settings.gigachat_timeout_seconds,
        max_retries=settings.gigachat_max_retries,
    )


async def close_gigachat_client(client: GigaChat) -> None:
    """Dispose HTTP clients held by the SDK."""
    await client.aclose()
