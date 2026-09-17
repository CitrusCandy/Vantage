from abc import ABC, abstractmethod
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

from app.core.security import sanitize_url
from app.core.telemetry import ops_metrics
from app.llm.schemas import (
    PerspectiveItem,
    PerspectiveSynthesisOutput,
    SampleQuote,
)

logger = logging.getLogger("app.llm.perspective")


class BasePerspectiveSynthesizer(ABC):
    """Abstract interface for multi-perspective LLM synthesizers."""

    model_name: str

    @abstractmethod
    def synthesize(
        self,
        topic_title: str,
        cluster_payloads: List[Dict[str, Any]],
        total_sample_size: int,
    ) -> PerspectiveSynthesisOutput:
        """Synthesize multi-perspective analysis from representative cluster discourse items."""
        pass


class OpenAIPerspectiveSynthesizer(BasePerspectiveSynthesizer):
    """OpenAI-backed multi-perspective synthesizer supporting structured JSON output."""

    OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

    SYSTEM_PROMPT = """You are an objective, balanced, and insightful intelligence analyst for Vantage News.
Your mission is to analyze clustered public discourse from diverse sources (Google News, Reddit, X) on a given topic, identify distinct viewpoints, and synthesize a structured multi-perspective brief.

Instructions:
1. Identify 2 to 6 distinct perspectives or stances from the provided discourse clusters.
2. For each perspective:
   - Provide a clear, neutral 'type' name (e.g. 'Economic Opportunity Proponents', 'Privacy & Surveillance Concerns', 'Open Source Advocates').
   - Estimate the discourse share (0.0 to 1.0 or percentage) based on cluster proportions.
   - Write a concise narrative summary.
   - List 2 to 4 concrete key arguments.
   - Provide 1 to 3 representative sample quotes verbatim or near-verbatim, strictly attributing the correct source and URL from the input data.
3. Provide an overall confidence note describing source diversity, potential bias, and data quality.
4. Output MUST be valid JSON adhering strictly to the required schema:
{
  "core_topic": "string",
  "perspectives": [
    {
      "type": "string",
      "estimated_share": 0.0,
      "summary": "string",
      "key_arguments": ["string"],
      "sample_quotes": [
        {"text": "string", "source": "string", "url": "string"}
      ]
    }
  ],
  "confidence_note": "string"
}
"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ):
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self.model_name = (
            model_name
            or os.getenv("OPENAI_PERSPECTIVE_MODEL", "gpt-4o-mini")
        ).strip()
        self.timeout_seconds = timeout_seconds

    def synthesize(
        self,
        topic_title: str,
        cluster_payloads: List[Dict[str, Any]],
        total_sample_size: int,
    ) -> PerspectiveSynthesisOutput:
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Please configure the OPENAI_API_KEY environment variable."
            )

        user_content = self._format_prompt_input(topic_title, cluster_payloads, total_sample_size)

        payload = {
            "model": self.model_name,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.OPENAI_CHAT_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        from app.core.resilience import BackoffStrategy, JitterMode, openai_breaker, retry_with_backoff

        def _do_openai_call():
            t_start = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    choice = resp_data["choices"][0]["message"]["content"]
                    parsed_json = json.loads(choice)
                    raw_output = PerspectiveSynthesisOutput.model_validate(parsed_json)
                    # Sanitize all quote URLs for security
                    for p in raw_output.perspectives:
                        for q in p.sample_quotes:
                            q.url = sanitize_url(q.url)
                    latency_ms = (time.perf_counter() - t_start) * 1000.0
                    ops_metrics.record_source_execution("openai", success=True, latency_ms=latency_ms)
                    return raw_output
            except urllib.error.HTTPError as e:
                latency_ms = (time.perf_counter() - t_start) * 1000.0
                err_body = e.read().decode("utf-8", errors="ignore")
                ops_metrics.record_source_execution("openai", success=False, latency_ms=latency_ms, error_summary=f"HTTP {e.code}")
                logger.error("OpenAI Chat Completion error HTTP %d: %s", e.code, err_body)
                raise RuntimeError(f"OpenAI Perspective API error HTTP {e.code}: {err_body}") from e
            except urllib.error.URLError as e:
                latency_ms = (time.perf_counter() - t_start) * 1000.0
                is_to = "timed out" in str(e).lower()
                ops_metrics.record_source_execution("openai", success=False, latency_ms=latency_ms, is_timeout=is_to, error_summary=str(e)[:100])
                logger.error("OpenAI connection failed: %s", str(e))
                raise RuntimeError(f"OpenAI connection error: {str(e)}") from e
            except json.JSONDecodeError as e:
                latency_ms = (time.perf_counter() - t_start) * 1000.0
                ops_metrics.record_source_execution("openai", success=False, latency_ms=latency_ms, error_summary="Malformed JSON")
                logger.error("Failed to parse JSON response from LLM: %s", str(e))
                raise RuntimeError(f"Malformed JSON from LLM: {str(e)}") from e

        backoff = BackoffStrategy(base_delay=1.0, max_delay=8.0, multiplier=2.0, jitter_mode=JitterMode.FULL)
        call_with_retries = retry_with_backoff(
            max_attempts=3,
            backoff=backoff,
            retryable_exceptions=(RuntimeError, TimeoutError, OSError),
            reraise_last=True,
        )(_do_openai_call)

        return openai_breaker.execute(call_with_retries)

    @staticmethod
    def _format_prompt_input(
        topic_title: str,
        cluster_payloads: List[Dict[str, Any]],
        total_sample_size: int,
    ) -> str:
        lines = [
            f"Topic: {topic_title}",
            f"Total Analyzed Samples: {total_sample_size}",
            f"Discourse Clusters ({len(cluster_payloads)} total):",
            "",
        ]

        for c_idx, cluster in enumerate(cluster_payloads, start=1):
            c_id = cluster.get("cluster_id", c_idx)
            size = cluster.get("size", 0)
            share = cluster.get("share", round(size / max(total_sample_size, 1), 4))
            samples = cluster.get("representative_samples", [])

            lines.append(f"--- Cluster #{c_id} (Size: {size}, Estimated Share: {share * 100:.1f}%) ---")
            for s_idx, sample in enumerate(samples, start=1):
                src = sample.get("source", "unknown")
                author = sample.get("author_handle", "anonymous")
                url = sample.get("url", "N/A")
                text = sample.get("text_content", "").strip()
                lines.append(f"  [{s_idx}] Source: {src} | Author: {author} | URL: {url}")
                lines.append(f"      \"{text}\"")
            lines.append("")

        return "\n".join(lines)


class MockPerspectiveSynthesizer(BasePerspectiveSynthesizer):
    """Deterministic offline mock synthesizer for testing without API keys."""

    model_name = "mock-perspective-synthesizer-v1"

    def synthesize(
        self,
        topic_title: str,
        cluster_payloads: List[Dict[str, Any]],
        total_sample_size: int,
    ) -> PerspectiveSynthesisOutput:
        perspectives: List[PerspectiveItem] = []

        if not cluster_payloads:
            perspectives.append(
                PerspectiveItem(
                    type="General Discussion",
                    estimated_share=1.0,
                    summary=f"Broad public commentary and observations regarding {topic_title}.",
                    key_arguments=["Diverse perspectives observed across reporting channels."],
                    sample_quotes=[],
                )
            )
        else:
            for idx, cluster in enumerate(cluster_payloads):
                cid = cluster.get("cluster_id", idx)
                size = cluster.get("size", 1)
                share = cluster.get("share", round(size / max(total_sample_size, 1), 4))
                samples = cluster.get("representative_samples", [])

                quotes = [
                    SampleQuote(
                        text=s.get("text_content", "")[:120] + "...",
                        source=s.get("source", "google_news"),
                        url=sanitize_url(s.get("url")),
                    )
                    for s in samples[:2]
                ]

                perspective_labels = [
                    "Regulatory & Policy Concerns",
                    "Market Growth & Commercial Opportunities",
                    "Technological & Scientific Optimism",
                    "Consumer Rights & Public Safety",
                    "International Competitiveness",
                ]
                label = perspective_labels[idx % len(perspective_labels)]

                perspectives.append(
                    PerspectiveItem(
                        type=label,
                        estimated_share=share,
                        summary=f"Key discourse angle #{cid} emphasizing {label.lower()} for {topic_title}.",
                        key_arguments=[
                            f"Focus on long-term implications for {topic_title}.",
                            f"Attributed to prominent discussions in cluster {cid}.",
                        ],
                        sample_quotes=quotes,
                    )
                )

        return PerspectiveSynthesisOutput(
            core_topic=topic_title,
            perspectives=perspectives,
            confidence_note=(
                f"Synthesized from {len(cluster_payloads)} distinct clusters "
                f"encompassing {total_sample_size} discourse records across Google News, Reddit, and X."
            ),
        )


def get_perspective_synthesizer(
    provider_type: Optional[str] = None,
) -> BasePerspectiveSynthesizer:
    """Factory for active LLM perspective synthesizer."""
    ptype = (provider_type or os.getenv("LLM_PROVIDER", "")).lower()
    if ptype == "mock" or (not ptype and not os.getenv("OPENAI_API_KEY")):
        return MockPerspectiveSynthesizer()
    return OpenAIPerspectiveSynthesizer()
