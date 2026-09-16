from dataclasses import dataclass
import os


@dataclass
class WorkerConfig:
    """Configurable scoring weights and execution parameters for background workers."""

    # Trending Scoring Weights: trending_score = w1*velocity + w2*sources + w3*engagement - w4*decay
    w1_mention_velocity: float = float(os.getenv("TRENDING_W1_VELOCITY", "0.35"))
    w2_unique_sources: float = float(os.getenv("TRENDING_W2_SOURCES", "0.25"))
    w3_engagement_rate: float = float(os.getenv("TRENDING_W3_ENGAGEMENT", "0.25"))
    w4_time_decay: float = float(os.getenv("TRENDING_W4_DECAY", "0.15"))

    # Worker Cadence & Thresholds
    worker_interval_hours: float = float(os.getenv("WORKER_INTERVAL_HOURS", "2.0"))
    min_trending_score_for_refresh: float = float(os.getenv("MIN_TRENDING_SCORE_REFRESH", "0.20"))
    stagnant_hours_threshold: float = float(os.getenv("STAGNANT_HOURS_THRESHOLD", "48.0"))
    decay_half_life_hours: float = float(os.getenv("DECAY_HALF_LIFE_HOURS", "24.0"))


def get_worker_config() -> WorkerConfig:
    """Load worker configuration from environment variables."""
    return WorkerConfig()
