"""Connection to the Azure OpenAI inference endpoint."""

from __future__ import annotations

import openai


MISSING_MESSAGE = "Enter the Azure base URL and API key in the admin settings."

# Azure keeps nothing, so a reasoning model's sealed thoughts must travel with each turn.
STATELESS = {"store": False, "include": ["reasoning.encrypted_content"]}


def azure_base_url(endpoint: str) -> str:
    base = endpoint.strip().rstrip("/")
    return f"{base}/" if base.endswith("/openai/v1") else f"{base}/openai/v1/"


def azure_client(base_url: str, api_key: str, *, timeout: float = 120.0,
                 max_retries: int = 4) -> openai.AsyncOpenAI | None:
    """Build the client, or None so that the admin page can show what is missing."""
    if not base_url.strip() or not api_key.strip():
        return None
    return openai.AsyncOpenAI(
        base_url=azure_base_url(base_url), api_key=api_key.strip(),
        max_retries=max_retries, timeout=timeout,
    )


def is_content_filter(exc: Exception) -> bool:
    """Azure rejects flagged requests with a 400 whose code is content_filter."""
    return getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc)


def check_finished(response) -> None:
    """Azure reports a failed or cut-off reply inside a normal 200 response."""
    if response.status == "failed":
        raise RuntimeError(f"The model failed: {response.error.code}: {response.error.message}")
    if response.status == "incomplete":
        raise RuntimeError(f"The model stopped early: {response.incomplete_details.reason}")


def function_calls(response) -> list:
    return [item for item in response.output if item.type == "function_call"]


def replay_items(response) -> list[dict]:
    """A reply's output as input for the next turn; tool calls need their reasoning with them."""
    return [item.model_dump(exclude_none=True, by_alias=True) for item in response.output]
