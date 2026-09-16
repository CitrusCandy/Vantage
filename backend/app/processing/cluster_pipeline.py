from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import resource_governor
from app.core.telemetry import PipelineTimingTracker
from app.database.models import ClusterRun, RawData, Topic
from app.processing.clustering import ClusterResult, HDBSCANClusterer
from app.processing.embeddings import BaseEmbeddingProvider, get_embedding_provider
from app.processing.processor import DiscourseProcessor, ProcessingResult

logger = logging.getLogger("app.processing.cluster_pipeline")


class ClusterPipeline:
    """End-to-end pipeline connecting Step 2 preprocessing, embeddings generation, and HDBSCAN clustering."""

    def __init__(
        self,
        processor: Optional[DiscourseProcessor] = None,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
        clusterer: Optional[HDBSCANClusterer] = None,
    ):
        self.processor = processor or DiscourseProcessor()
        self.embedding_provider = embedding_provider or get_embedding_provider()
        self.clusterer = clusterer or HDBSCANClusterer()

    def run_for_topic(
        self,
        topic: Topic,
        db: Session,
        min_volume_threshold: int = 30,
        min_cluster_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute discourse deduplication, embedding generation, HDBSCAN clustering, and ClusterRun persistence."""
        logger.info(
            "Cluster pipeline initiated for Topic ID %d ('%s') with min_volume_threshold=%d",
            topic.id,
            topic.title,
            min_volume_threshold,
        )
        tracker = PipelineTimingTracker("cluster_pipeline")

        # 1. Run Step 2 Discourse Preprocessing and Deduplication
        with tracker.track("deduplication_and_filtering"):
            proc_result: ProcessingResult = self.processor.process_topic_items(
                topic=topic,
                db=db,
                min_volume_override=min_volume_threshold,
            )

        usable_items = proc_result.usable_items

        # 2. Minimum-Volume Gating
        if len(usable_items) < min_volume_threshold:
            logger.warning(
                "Topic ID %d has insufficient usable items (%d < %d required)",
                topic.id,
                len(usable_items),
                min_volume_threshold,
            )
            return {
                "status": "insufficient_volume",
                "message": (
                    f"Insufficient usable discourse items ({len(usable_items)} available, "
                    f"{min_volume_threshold} required). Please ingest more data before clustering."
                ),
                "topic_id": topic.id,
                "topic_slug": topic.slug,
                "sample_size": len(usable_items),
                "min_volume_threshold": min_volume_threshold,
                "statistics": proc_result.to_dict()["statistics"],
                "cluster_count": 0,
                "noise_count": 0,
                "cluster_sizes": {},
                "clusters": [],
                "timings": tracker.get_summary(),
            }

        # 3. Extract text content, clamping to budget
        max_processing = resource_governor.budget_manager.get_limit("max_processing_items")
        if len(usable_items) > max_processing:
            logger.warning(
                "Clamping usable items from %d to budget limit %d for topic ID %d",
                len(usable_items), max_processing, topic.id,
            )
            usable_items = usable_items[:max_processing]

        max_embeddings = resource_governor.budget_manager.get_limit("max_embeddings_per_run")
        if len(usable_items) > max_embeddings:
            logger.warning(
                "Clamping items to embedding budget limit %d for topic ID %d",
                max_embeddings, topic.id,
            )
            usable_items = usable_items[:max_embeddings]

        texts = [item.text_content for item in usable_items]

        # 4. Generate Embeddings
        with tracker.track("embedding_generation", text_count=len(texts), provider=self.embedding_provider.model_name):
            embeddings = self.embedding_provider.embed_texts(texts)
            resource_governor.cost_tracker.record_embedding_call(item_count=len(texts))
            resource_governor.cost_tracker.record_items_processed(len(texts))

        # 5. Execute HDBSCAN Clustering
        with tracker.track("hdbscan_clustering", sample_count=len(usable_items)):
            cluster_result: ClusterResult = self.clusterer.fit_predict(
                embeddings=embeddings,
                min_cluster_size_override=min_cluster_size,
            )

        # 6. Persist ClusterRun record
        with tracker.track("persistence"):
            cluster_run = ClusterRun(
                topic_id=topic.id,
                cluster_algorithm="hdbscan",
                cluster_count=cluster_result.cluster_count,
                sample_size=len(usable_items),
                run_at=datetime.utcnow(),
            )
            db.add(cluster_run)

            # Update Topic metadata
            topic.last_clustered_at = datetime.utcnow()
            topic.updated_at = datetime.utcnow()

            db.commit()
            db.refresh(cluster_run)
            db.refresh(topic)

        # 7. Assemble Structured Cluster Payload with Representative Samples
        clusters_payload: List[Dict[str, Any]] = []
        for cluster_id, size in sorted(cluster_result.cluster_sizes.items()):
            rep_indices = cluster_result.representative_indices.get(cluster_id, [])
            rep_samples = [
                {
                    "raw_id": usable_items[idx].raw_id,
                    "source": usable_items[idx].source,
                    "text_content": usable_items[idx].text_content,
                    "author_handle": usable_items[idx].author_handle,
                    "url": usable_items[idx].url,
                    "engagement_metrics": usable_items[idx].engagement_metrics,
                    "created_at": usable_items[idx].created_at.isoformat()
                    if usable_items[idx].created_at
                    else None,
                }
                for idx in rep_indices
            ]

            clusters_payload.append({
                "cluster_id": cluster_id,
                "size": size,
                "share": round(size / len(usable_items), 4),
                "representative_samples": rep_samples,
            })

        logger.info(
            "ClusterRun #%d successfully persisted for topic '%s': %d clusters, %d noise points from %d samples",
            cluster_run.run_id,
            topic.title,
            cluster_run.cluster_count,
            cluster_result.noise_count,
            cluster_run.sample_size,
        )

        return {
            "status": "success",
            "cluster_run_id": cluster_run.run_id,
            "topic_id": topic.id,
            "topic_slug": topic.slug,
            "sample_size": len(usable_items),
            "cluster_count": cluster_result.cluster_count,
            "noise_count": cluster_result.noise_count,
            "cluster_sizes": cluster_result.cluster_sizes,
            "clusters": clusters_payload,
            "statistics": proc_result.to_dict()["statistics"],
            "run_at": cluster_run.run_at.isoformat(),
            "timings": tracker.get_summary(),
        }
