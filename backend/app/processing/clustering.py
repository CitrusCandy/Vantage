from collections import defaultdict
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sklearn.cluster import HDBSCAN
from sklearn.preprocessing import normalize

logger = logging.getLogger("app.processing.clustering")


@dataclass
class ClusterResult:
    """Output summary of an HDBSCAN clustering execution."""

    labels: List[int]
    cluster_count: int
    noise_count: int
    cluster_sizes: Dict[int, int]
    probabilities: List[float]
    representative_indices: Dict[int, List[int]] = field(default_factory=dict)


class HDBSCANClusterer:
    """Density-based clustering using HDBSCAN over L2-normalized embedding vectors."""

    def __init__(
        self,
        min_cluster_size: int = 3,
        min_samples: Optional[int] = None,
        cluster_selection_epsilon: float = 0.0,
        cluster_selection_method: str = "eom",
        n_representative_samples: int = 3,
    ):
        self.min_cluster_size = min_cluster_size
        self.min_samples = min_samples
        self.cluster_selection_epsilon = cluster_selection_epsilon
        self.cluster_selection_method = cluster_selection_method
        self.n_representative_samples = n_representative_samples

    def fit_predict(
        self,
        embeddings: List[List[float]],
        min_cluster_size_override: Optional[int] = None,
    ) -> ClusterResult:
        """Run HDBSCAN on embedding vectors and extract cluster metrics and exemplars."""
        if not embeddings:
            return ClusterResult(
                labels=[],
                cluster_count=0,
                noise_count=0,
                cluster_sizes={},
                probabilities=[],
                representative_indices={},
            )

        X = np.array(embeddings, dtype=np.float64)

        # Normalize vectors so Euclidean distance is strictly monotonic with Cosine distance
        X_norm = normalize(X, norm="l2", axis=1)

        min_c_size = (
            min_cluster_size_override
            if min_cluster_size_override is not None
            else self.min_cluster_size
        )

        # Ensure min_cluster_size does not exceed sample size
        min_c_size = max(2, min(min_c_size, len(embeddings)))

        clusterer = HDBSCAN(
            min_cluster_size=min_c_size,
            min_samples=self.min_samples,
            cluster_selection_epsilon=self.cluster_selection_epsilon,
            cluster_selection_method=self.cluster_selection_method,
            metric="euclidean",
            copy=True,
        )

        clusterer.fit(X_norm)

        labels: List[int] = [int(lbl) for lbl in clusterer.labels_]
        probs: List[float] = (
            [float(p) for p in clusterer.probabilities_]
            if hasattr(clusterer, "probabilities_") and clusterer.probabilities_ is not None
            else [1.0] * len(labels)
        )

        # Count cluster sizes and noise
        cluster_sizes: Dict[int, int] = defaultdict(int)
        noise_count = 0
        for lbl in labels:
            if lbl == -1:
                noise_count += 1
            else:
                cluster_sizes[lbl] += 1

        cluster_count = len(cluster_sizes)

        # Compute representative samples for each cluster (closest to centroid in embedding space)
        representative_indices: Dict[int, List[int]] = {}
        for cluster_id in cluster_sizes.keys():
            member_indices = [idx for idx, lbl in enumerate(labels) if lbl == cluster_id]
            if not member_indices:
                continue

            cluster_vectors = X_norm[member_indices]
            centroid = np.mean(cluster_vectors, axis=0, keepdims=True)
            centroid = normalize(centroid, norm="l2", axis=1)

            # Cosine similarity to centroid: dot product of normalized vectors
            similarities = np.dot(cluster_vectors, centroid.T).flatten()
            ranked_order = np.argsort(-similarities)

            top_k_indices = [
                member_indices[i]
                for i in ranked_order[: self.n_representative_samples]
            ]
            representative_indices[cluster_id] = top_k_indices

        logger.info(
            "HDBSCAN clustering finished: %d samples -> %d clusters, %d noise points. Cluster sizes: %s",
            len(embeddings),
            cluster_count,
            noise_count,
            dict(cluster_sizes),
        )

        return ClusterResult(
            labels=labels,
            cluster_count=cluster_count,
            noise_count=noise_count,
            cluster_sizes=dict(cluster_sizes),
            probabilities=probs,
            representative_indices=representative_indices,
        )
