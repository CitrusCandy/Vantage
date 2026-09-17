from abc import ABC, abstractmethod
import hashlib
import json
import logging
import math
import os
from typing import List, Optional
import urllib.error
import urllib.request

logger = logging.getLogger("app.processing.embeddings")


class BaseEmbeddingProvider(ABC):
    """Abstract base class for modular embedding providers."""

    model_name: str
    dimension: int

    @abstractmethod
    def embed_texts(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """Generate embedding vectors for a list of texts in batches."""
        pass

    def embed_single(self, text: str) -> List[float]:
        """Convenience method to embed a single text string."""
        results = self.embed_texts([text])
        if not results:
            raise RuntimeError("Failed to generate embedding for input text")
        return results[0]


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """Generates embeddings using OpenAI API (text-embedding-3-small)."""

    OPENAI_API_URL = "https://api.openai.com/v1/embeddings"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "text-embedding-3-small",
        dimension: int = 1536,
        timeout_seconds: float = 30.0,
    ):
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self.model_name = model_name
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds

    def embed_texts(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        if not texts:
            return []

        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Please set the OPENAI_API_KEY environment variable."
            )

        # Clamp batch size to budget limit
        try:
            from app.core import resource_governor
            batch_size = resource_governor.budget_manager.clamp("max_embedding_batch_size", batch_size)
        except ImportError:
            pass

        from app.core.resilience import BackoffStrategy, JitterMode, embeddings_breaker, retry_with_backoff

        if not embeddings_breaker.allow_request():
            logger.warning("[embeddings] Circuit breaker is OPEN. Fast-failing embedding request.")
            raise RuntimeError("Circuit breaker 'embeddings' is OPEN")

        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            # Replace empty strings with single whitespace to prevent API errors
            sanitized_batch = [t if t.strip() else " " for t in batch]

            payload = {
                "input": sanitized_batch,
                "model": self.model_name,
            }
            data = json.dumps(payload).encode("utf-8")

            req = urllib.request.Request(
                self.OPENAI_API_URL,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )

            def _fetch_batch():
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                        resp_data = json.loads(resp.read().decode("utf-8"))
                        # OpenAI returns data objects with index and embedding
                        items = sorted(resp_data.get("data", []), key=lambda x: x["index"])
                        return [item["embedding"] for item in items]
                except urllib.error.HTTPError as e:
                    err_body = e.read().decode("utf-8", errors="ignore")
                    logger.error(
                        "OpenAI API error (HTTP %d): %s - Body: %s",
                        e.code,
                        str(e),
                        err_body,
                    )
                    raise RuntimeError(f"OpenAI Embedding API error HTTP {e.code}: {err_body}") from e
                except urllib.error.URLError as e:
                    logger.error("OpenAI API network/timeout error: %s", str(e))
                    raise RuntimeError(f"OpenAI Embedding connection failed: {str(e)}") from e
                except Exception as e:
                    logger.error("Unexpected error during OpenAI embedding generation: %s", str(e))
                    raise RuntimeError(f"Unexpected embedding error: {str(e)}") from e

            backoff = BackoffStrategy(base_delay=0.5, max_delay=4.0, multiplier=2.0, jitter_mode=JitterMode.FULL)
            fetch_with_retries = retry_with_backoff(
                max_attempts=3,
                backoff=backoff,
                retryable_exceptions=(RuntimeError, TimeoutError, OSError),
                reraise_last=True,
            )(_fetch_batch)

            batch_embeddings = embeddings_breaker.execute(fetch_with_retries)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings


class MockEmbeddingProvider(BaseEmbeddingProvider):
    """Deterministic offline embedding provider for tests and local development without API keys."""

    def __init__(self, dimension: int = 64, model_name: str = "mock-embedding-v1"):
        self.dimension = dimension
        self.model_name = model_name

    def embed_texts(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        embeddings: List[List[float]] = []
        for text in texts:
            words = text.lower().split()
            vec = [0.0] * self.dimension
            if not words:
                embeddings.append(vec)
                continue

            for word in words:
                # Hash each word into vector buckets
                h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
                for dim_idx in range(self.dimension):
                    bit = (h >> (dim_idx % 64)) & 1
                    sign = 1.0 if bit == 1 else -1.0
                    weight = 1.0 / (1.0 + (dim_idx % 7))
                    vec[dim_idx] += sign * weight

            # L2 normalize
            norm = math.sqrt(sum(x * x for x in vec))
            if norm > 0:
                vec = [x / norm for x in vec]
            embeddings.append(vec)
        return embeddings


def get_embedding_provider(
    provider_type: Optional[str] = None,
) -> BaseEmbeddingProvider:
    """Factory for obtaining the active embedding provider."""
    ptype = (provider_type or os.getenv("EMBEDDING_PROVIDER", "")).lower()
    if ptype == "mock" or (not ptype and not os.getenv("OPENAI_API_KEY")):
        return MockEmbeddingProvider()
    return OpenAIEmbeddingProvider()
