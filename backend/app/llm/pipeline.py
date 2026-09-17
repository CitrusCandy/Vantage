from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import resource_governor
from app.core.telemetry import PipelineTimingTracker
from app.database.models import Perspective, Topic
from app.llm.perspective import (
    BasePerspectiveSynthesizer,
    get_perspective_synthesizer,
)
from app.llm.schemas import PerspectiveSynthesisOutput
from app.processing.cluster_pipeline import ClusterPipeline

logger = logging.getLogger("app.llm.pipeline")


class PerspectivePipeline:
    """End-to-end pipeline selecting representative cluster samples, prompting LLM, and persisting perspectives."""

    def __init__(
        self,
        synthesizer: Optional[BasePerspectiveSynthesizer] = None,
        cluster_pipeline: Optional[ClusterPipeline] = None,
        max_top_clusters: Optional[int] = None,
        max_samples_per_cluster: Optional[int] = None,
    ):
        self.synthesizer = synthesizer or get_perspective_synthesizer()
        self.cluster_pipeline = cluster_pipeline or ClusterPipeline()
        self.max_top_clusters = max_top_clusters or resource_governor.budget_manager.get_limit("max_clusters_to_llm")
        self.max_samples_per_cluster = max_samples_per_cluster or resource_governor.budget_manager.get_limit("max_samples_per_cluster")

    def run_synthesis_for_topic(
        self,
        topic: Topic,
        db: Session,
        min_volume_threshold: int = 30,
        cluster_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute perspective synthesis and persist Perspective records in database."""
        logger.info(
            "Perspective synthesis initiated for Topic ID %d ('%s')",
            topic.id,
            topic.title,
        )

        # Check synthesis rate budget for this topic
        allowed, current, limit = resource_governor.synthesis_tracker.check_and_record(topic.slug)
        if not allowed:
            logger.warning(
                "Synthesis rate limit exceeded for topic '%s': %d/%d per hour",
                topic.slug, current, limit,
            )
            return {
                "status": "rate_limited",
                "message": f"Synthesis rate limit exceeded ({current}/{limit} per hour for topic '{topic.slug}')",
                "topic_id": topic.id,
                "topic_slug": topic.slug,
            }

        tracker = PipelineTimingTracker("perspective_pipeline")

        # 1. Ensure Clustering Exists
        if not cluster_data:
            with tracker.track("upstream_clustering"):
                cluster_data = self.cluster_pipeline.run_for_topic(
                    topic=topic,
                    db=db,
                    min_volume_threshold=min_volume_threshold,
                )

        if cluster_data.get("status") == "insufficient_volume":
            logger.warning(
                "Cannot synthesize perspectives for topic '%s': Insufficient volume",
                topic.title,
            )
            return {
                "status": "insufficient_volume",
                "message": cluster_data.get("message"),
                "topic_id": topic.id,
                "topic_slug": topic.slug,
                "sample_size": cluster_data.get("sample_size", 0),
                "perspectives": [],
                "confidence_note": "Synthesis halted due to insufficient sample volume.",
                "timings": tracker.get_summary(),
            }

        raw_clusters = cluster_data.get("clusters", [])
        total_sample_size = cluster_data.get("sample_size", 0)

        # 2. Filter & Trim Representative Samples for Token Control
        with tracker.track("representative_sample_preparation"):
            sorted_clusters = sorted(raw_clusters, key=lambda c: c.get("size", 0), reverse=True)
            top_clusters = sorted_clusters[: self.max_top_clusters]
            remaining_clusters = sorted_clusters[self.max_top_clusters :]

            prepared_clusters: List[Dict[str, Any]] = []
            for cluster in top_clusters:
                samples = cluster.get("representative_samples", [])
                trimmed_samples = samples[: self.max_samples_per_cluster]
                prepared_clusters.append({
                    "cluster_id": cluster.get("cluster_id"),
                    "size": cluster.get("size"),
                    "share": cluster.get("share"),
                    "representative_samples": trimmed_samples,
                })

            # Merge tiny/residual clusters into other/misc if needed
            if remaining_clusters:
                other_size = sum(c.get("size", 0) for c in remaining_clusters)
                other_samples: List[Dict[str, Any]] = []
                for c in remaining_clusters:
                    other_samples.extend(c.get("representative_samples", []))
                prepared_clusters.append({
                    "cluster_id": "other_misc",
                    "size": other_size,
                    "share": round(other_size / max(total_sample_size, 1), 4),
                    "representative_samples": other_samples[: self.max_samples_per_cluster],
                })

        # 3. Call LLM Synthesizer (with graceful degradation fallback)
        with tracker.track("llm_structured_inference", cluster_count=len(prepared_clusters)):
            logger.info(
                "Invoking LLM synthesizer for topic '%s' with %d clusters",
                topic.title,
                len(prepared_clusters),
            )
            try:
                synthesis_output: PerspectiveSynthesisOutput = self.synthesizer.synthesize(
                    topic_title=topic.title,
                    cluster_payloads=prepared_clusters,
                    total_sample_size=total_sample_size,
                )
            except Exception as synth_err:
                logger.warning(
                    "LLM synthesis failed for topic '%s' (%s). Falling back to extractive fallback synthesis.",
                    topic.title,
                    str(synth_err),
                )
                from app.llm.perspective import MockPerspectiveSynthesizer
                fallback_synth = MockPerspectiveSynthesizer()
                synthesis_output = fallback_synth.synthesize(
                    topic_title=topic.title,
                    cluster_payloads=prepared_clusters,
                    total_sample_size=total_sample_size,
                )
                synthesis_output.confidence_note = (
                    f"Generated via fail-soft extractive fallback due to upstream provider degradation: {str(synth_err)[:100]}"
                )

            # Track synthesis cost (estimate tokens from cluster payload size)
            total_samples = sum(len(c.get("representative_samples", [])) for c in prepared_clusters)
            estimated_input = total_samples * 50  # ~50 tokens per sample estimate
            estimated_output = len(synthesis_output.perspectives) * 200  # ~200 tokens per perspective
            resource_governor.cost_tracker.record_synthesis_call(
                estimated_input_tokens=estimated_input,
                estimated_output_tokens=estimated_output,
            )

        # 4. Idempotently Replace Stale Perspectives for Topic
        with tracker.track("perspective_persistence"):
            db.query(Perspective).filter(Perspective.topic_id == topic.id).delete()

            persisted_perspectives: List[Perspective] = []
            for p in synthesis_output.perspectives:
                summary_points_payload = {
                    "summary": p.summary,
                    "key_arguments": p.key_arguments,
                }
                sample_quotes_payload = [q.model_dump() for q in p.sample_quotes]

                record = Perspective(
                    topic_id=topic.id,
                    perspective_type=p.type,
                    estimated_share=p.estimated_share,
                    summary_points=summary_points_payload,
                    sample_quotes=sample_quotes_payload,
                    confidence_note=synthesis_output.confidence_note,
                    generated_at=datetime.utcnow(),
                )
                db.add(record)
                persisted_perspectives.append(record)

            topic.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(topic)

        logger.info(
            "Persisted %d perspectives for Topic ID %d ('%s')",
            len(persisted_perspectives),
            topic.id,
            topic.title,
        )

        return {
            "status": "success",
            "topic_id": topic.id,
            "topic_slug": topic.slug,
            "core_topic": synthesis_output.core_topic,
            "confidence_note": synthesis_output.confidence_note,
            "sample_size": total_sample_size,
            "perspectives_count": len(persisted_perspectives),
            "perspectives": [
                {
                    "per_id": rec.per_id,
                    "type": rec.perspective_type,
                    "estimated_share": rec.estimated_share,
                    "summary_points": rec.summary_points,
                    "sample_quotes": rec.sample_quotes,
                    "generated_at": rec.generated_at.isoformat() if rec.generated_at else None,
                }
                for rec in persisted_perspectives
            ],
            "timings": tracker.get_summary(),
        }
