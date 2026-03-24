"""
llm.py — Azure AI Foundry client for SQL generation

Calls gpt-4o-mini with the DDL system prompt to convert natural language
questions into executable T-SQL SELECT statements.

Auth: DefaultAzureCredential (no API keys).
"""

import logging
import os
import re

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from schema_prompt import SYSTEM_PROMPT

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (from .env)
# ---------------------------------------------------------------------------
AI_FOUNDRY_ENDPOINT = os.getenv("AI_FOUNDRY_ENDPOINT", "")
AI_FOUNDRY_MODEL = os.getenv("AI_FOUNDRY_MODEL", "gpt-4o-mini")

# ---------------------------------------------------------------------------
# Client (module-level, lazy-initialized)
# ---------------------------------------------------------------------------
_client: AzureOpenAI | None = None


def _get_client() -> AzureOpenAI:
    """Return the AzureOpenAI client, creating it once."""
    global _client
    if _client is None:
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        _client = AzureOpenAI(
            azure_endpoint=AI_FOUNDRY_ENDPOINT,
            azure_ad_token_provider=token_provider,
            api_version="2024-12-01-preview",
        )
        log.info("AzureOpenAI client initialised → %s", AI_FOUNDRY_ENDPOINT)
    return _client


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------
def _strip_markdown(text: str) -> str:
    """Remove markdown code fences if GPT wraps the SQL."""
    # ``` sql ... ``` or ``` ... ```
    text = re.sub(r"^```(?:sql)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()


def generate_sql_sync(question: str) -> str:
    """
    Call gpt-4o-mini to generate SQL for the given natural language question.
    Returns the raw SQL string.
    Raises on API or safety errors.
    """
    client = _get_client()
    log.info("Generating SQL for: %r", question[:120])

    response = client.chat.completions.create(
        model=AI_FOUNDRY_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": question},
        ],
        temperature=0,          # deterministic SQL
        max_tokens=800,
        timeout=30,
    )

    raw = response.choices[0].message.content or ""
    sql = _strip_markdown(raw)
    log.info("Generated SQL: %s", sql[:200])
    return sql


import asyncio


async def generate_sql(question: str) -> str:
    """Async wrapper — runs the synchronous LLM call in a thread."""
    return await asyncio.to_thread(generate_sql_sync, question)


# ---------------------------------------------------------------------------
# General-purpose chat completion (used by Sentiment Agent and future agents)
# ---------------------------------------------------------------------------

def chat_completion_sync(system_prompt: str, user_message: str, max_tokens: int = 1000) -> str:
    """
    Call gpt-4o-mini with a custom system prompt and user message.
    Returns the raw text response.
    Used by agents that need LLM reasoning beyond SQL generation.
    """
    client = _get_client()
    log.info("chat_completion: system=%d chars, user=%d chars", len(system_prompt), len(user_message))

    response = client.chat.completions.create(
        model=AI_FOUNDRY_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        temperature=0.2,        # slight creativity for theme extraction
        max_tokens=max_tokens,
        timeout=30,
    )

    result = response.choices[0].message.content or ""
    usage = response.usage
    log.info(
        "chat_completion: %d prompt + %d completion tokens",
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
    )
    return result


async def chat_completion(system_prompt: str, user_message: str, max_tokens: int = 1000) -> str:
    """Async wrapper — runs the synchronous chat completion in a thread."""
    return await asyncio.to_thread(chat_completion_sync, system_prompt, user_message, max_tokens)
