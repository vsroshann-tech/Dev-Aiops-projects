"""Local embedding providers with zero external data leakage."""

import hashlib
import math
import re
from typing import List

import httpx

from app.config import settings

_TOKEN_RE = re.compile(r"[a-z0-9_./:-]{2,}")
DEFAULT_DIM = 384

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "has", "have", "how", "in", "is", "it", "many", "me",
    "my", "of", "on", "or", "our", "run", "running", "that", "the", "there",
    "this", "to", "use", "using", "what", "when", "where", "which", "who",
    "why", "with", "you", "your",
}

_SYNONYMS = {
    "server": ["servers", "ec2", "eks", "cluster", "clusters", "instance", "instances", "node", "nodes", "asg"],
    "servers": ["server", "ec2", "eks", "cluster", "clusters", "instance", "instances", "node", "nodes", "asg"],
    "project": ["projects", "service", "services", "repo", "repository", "workload"],
    "projects": ["project", "service", "services", "repo", "repository", "workload"],
    "repo": ["repository", "github", "workflow"],
    "repository": ["repo", "github", "workflow"],
    "db": ["database", "databases", "rds", "postgres", "postgresql", "mysql"],
    "database": ["db", "databases", "rds", "postgres", "postgresql", "mysql"],
    "databases": ["db", "database", "rds", "postgres", "postgresql", "mysql"],
    "pipeline": ["workflow", "workflows", "ci", "cd", "github", "actions"],
}


def tokenize(text: str, remove_stopwords: bool = False) -> List[str]:
    """Tokenize technical DevOps text preserving identifiers like error codes and flags."""
    tokens = _TOKEN_RE.findall(text.lower())
    if remove_stopwords:
        return [t for t in tokens if t not in STOP_WORDS]
    return tokens


def expand_query_tokens(text: str) -> List[str]:
    """Return meaningful query tokens plus DevOps domain synonyms for accurate retrieval."""
    base = tokenize(text, remove_stopwords=True)
    expanded = list(base)
    for tok in base:
        for syn in _SYNONYMS.get(tok, []):
            if syn not in expanded:
                expanded.append(syn)
    return expanded


def local_hash_embed(text: str, dim: int = DEFAULT_DIM) -> List[float]:
    """Generate a deterministic L2-normalized local vector using word + char-trigram hashing."""
    vec = [0.0] * dim
    tokens = tokenize(text)
    if not tokens:
        return vec

    for tok in tokens:
        idx = int(hashlib.blake2b(tok.encode("utf-8"), digest_size=4).hexdigest(), 16) % dim
        vec[idx] += 1.5
        if len(tok) >= 4:
            for i in range(len(tok) - 2):
                gram = tok[i : i + 3]
                g_idx = int(hashlib.blake2b(gram.encode("utf-8"), digest_size=4).hexdigest(), 16) % dim
                vec[g_idx] += 0.35

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def embed_text(text: str) -> List[float]:
    """Embed text using local Ollama embeddings when configured, else local hash embedding."""
    if settings.llm_provider == "ollama":
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{settings.ollama_base_url.rstrip('/')}/api/embeddings",
                    json={"model": settings.ollama_embed_model, "prompt": text},
                )
                if resp.status_code == 200:
                    emb = resp.json().get("embedding")
                    if isinstance(emb, list) and emb:
                        return [float(x) for x in emb]
        except httpx.HTTPError:
            pass

    return local_hash_embed(text)
