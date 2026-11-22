"""Pluggable private/local LLM client (Ollama, OpenAI-compatible VPC gateway, or deterministic POC fallback)."""

from typing import Dict, List, Optional

import httpx

from app.config import settings


SYSTEM_PROMPT = """You are an internal security-compliant DevOps AI Agent.
You troubleshoot GitHub Actions CI/CD, AWS infrastructure (EC2/EKS/ALB/RDS/CloudWatch),
PostgreSQL, MySQL, and internal security compliance.
Rules:
1. Ground your diagnosis ONLY in the provided Internal Docs (RAG), Active Skills, and MCP Tool Outputs.
2. Never invent credentials, resource IDs, or log lines not present in tool output or docs.
3. Never recommend disabling encryption, opening 0.0.0.0/0, or running destructive SQL without backups/change control.
4. Cite retrieved internal document filenames and headings when referencing company policy or runbooks."""


def generate_answer(
    user_query: str,
    context_bundle: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> tuple[str, str]:
    """Generate a response using configured LLM provider, returning (answer, provider_used)."""
    provider = settings.llm_provider.lower().strip()

    if provider == "ollama":
        reply = _call_ollama(user_query, context_bundle, history or [])
        if reply:
            return reply, f"ollama:{settings.ollama_model}"

    if provider == "openai_compatible" and settings.openai_base_url and settings.openai_api_key:
        reply = _call_openai_compatible(user_query, context_bundle, history or [])
        if reply:
            return reply, f"openai_compatible:{settings.openai_model}"

    return "", "local_fallback"


def _call_ollama(
    user_query: str, context_bundle: str, history: List[Dict[str, str]]
) -> Optional[str]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history[-6:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append(
        {
            "role": "user",
            "content": f"Context (Skills, RAG Docs, MCP Telemetry):\n{context_bundle}\n\nQuestion:\n{user_query}",
        }
    )
    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                json={"model": settings.ollama_model, "messages": messages, "stream": False},
            )
            if resp.status_code == 200:
                return resp.json().get("message", {}).get("content")
    except httpx.HTTPError:
        return None
    return None


def _call_openai_compatible(
    user_query: str, context_bundle: str, history: List[Dict[str, str]]
) -> Optional[str]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history[-6:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append(
        {
            "role": "user",
            "content": f"Context (Skills, RAG Docs, MCP Telemetry):\n{context_bundle}\n\nQuestion:\n{user_query}",
        }
    )
    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={"model": settings.openai_model, "messages": messages, "temperature": 0.1},
            )
            if resp.status_code == 200:
                choices = resp.json().get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content")
    except httpx.HTTPError:
        return None
    return None
