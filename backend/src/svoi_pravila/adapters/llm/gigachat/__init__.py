"""GigaChat TextGenerator adapter."""

from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.client import close_gigachat_client, create_gigachat_client

__all__ = [
    "GigaChatTextGenerator",
    "close_gigachat_client",
    "create_gigachat_client",
]
