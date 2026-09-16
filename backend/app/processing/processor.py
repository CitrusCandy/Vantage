from dataclasses import asdict, dataclass, field
import hashlib
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.database.models import CombinedRawData, Topic
from app.processing.bot_detector import BotDetector
from app.processing.minhash_lsh import MinHash, MinHashLSH
from app.processing.text_cleaner import (
    clean_text_for_display,
    extract_shingles,
    normalize_text_for_matching,
)

logger = logging.getLogger("app.processing.processor")


@dataclass
class ProcessingStatistics:
    """Detailed statistics captured during discourse preprocessing and deduplication."""

    total_raw_items: int = 0
    bot_flagged_count: int = 0
    exact_duplicates_removed: int = 0
    near_duplicates_removed: int = 0
    usable_items_count: int = 0
    volume_gate_threshold: int = 30
    volume_gate_passed: bool = False
    source_breakdown: Dict[str, int] = field(default_factory=dict)


@dataclass
class ProcessingResult:
    """Output payload of discourse processing."""

    topic_id: int
    statistics: ProcessingStatistics
    usable_items: List[CombinedRawData]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "statistics": asdict(self.statistics),
            "usable_item_ids": [item.raw_id for item in self.usable_items],
        }


class DiscourseProcessor:
    """Orchestrates cross-source discourse normalization, bot heuristics, MinHash/LSH deduplication, and volume gating."""

    def __init__(
        self,
        min_volume_threshold: int = 30,
        jaccard_threshold: float = 0.8,
        num_perm: int = 128,
        shingle_k: int = 3,
        bot_detector: Optional[BotDetector] = None,
    ):
        self.min_volume_threshold = min_volume_threshold
        self.jaccard_threshold = jaccard_threshold
        self.num_perm = num_perm
        self.shingle_k = shingle_k
        self.bot_detector = bot_detector or BotDetector()

    def process_topic_items(
        self,
        topic: Topic,
        db: Session,
        min_volume_override: Optional[int] = None,
    ) -> ProcessingResult:
        """Process, filter, and deduplicate all raw discourse items for a given topic from combined_raw_data."""
        threshold = (
            min_volume_override
            if min_volume_override is not None
            else self.min_volume_threshold
        )

        raw_items: List[CombinedRawData] = (
            db.query(CombinedRawData)
            .filter(CombinedRawData.slug_id == topic.id)
            .order_by(CombinedRawData.created_at.asc())
            .all()
        )

        logger.info(
            "Processing started: Topic ID %d ('%s') with %d combined items",
            topic.id,
            topic.title,
            len(raw_items),
        )

        stats = ProcessingStatistics(
            total_raw_items=len(raw_items),
            volume_gate_threshold=threshold,
        )

        # Step 1: Apply bot/spam heuristics & flag in DB without deletion
        non_bot_items: List[CombinedRawData] = []
        for item in raw_items:
            bot_result = self.bot_detector.evaluate(item)
            if bot_result.is_bot:
                item.is_flagged_bot = True
                stats.bot_flagged_count += 1
                logger.debug(
                    "CombinedRawData #%d flagged as bot/spam: %s",
                    item.raw_id,
                    ", ".join(bot_result.reasons),
                )
            else:
                item.is_flagged_bot = False
                non_bot_items.append(item)

        # Step 2: Cross-Source Exact and Near-Duplicate Deduplication (MinHash + LSH)
        seen_exact_hashes: Set[str] = set()
        lsh_index = MinHashLSH(
            threshold=self.jaccard_threshold,
            num_perm=self.num_perm,
        )

        usable_items: List[CombinedRawData] = []
        source_counts: Dict[str, int] = {}

        for item in non_bot_items:
            norm_text = normalize_text_for_matching(item.text_content)
            if not norm_text:
                continue

            # Check exact duplicate via SHA-256 across all sources
            exact_hash = hashlib.sha256(norm_text.encode("utf-8")).hexdigest()
            if exact_hash in seen_exact_hashes:
                stats.exact_duplicates_removed += 1
                continue
            seen_exact_hashes.add(exact_hash)

            # Check near-duplicate via MinHash + LSH across sources
            shingles = extract_shingles(norm_text, k=self.shingle_k)
            minhash = MinHash(num_perm=self.num_perm)
            minhash.update(shingles)

            near_matches = lsh_index.query(minhash)
            if near_matches:
                stats.near_duplicates_removed += 1
                logger.debug(
                    "CombinedRawData #%d detected as near-duplicate of %s",
                    item.raw_id,
                    near_matches,
                )
                continue

            # Insert unique representative item into LSH index
            lsh_index.insert(item.raw_id or len(usable_items), minhash)
            usable_items.append(item)
            source_counts[item.source] = source_counts.get(item.source, 0) + 1

        # Step 3: Evaluate Volume Gate
        stats.usable_items_count = len(usable_items)
        stats.volume_gate_passed = stats.usable_items_count >= threshold
        stats.source_breakdown = source_counts

        # Commit flagged bot statuses
        db.commit()

        logger.info(
            "Processing completed for topic '%s': Total=%d, Bots=%d, ExactDupes=%d, NearDupes=%d, Usable=%d (Gate passed: %s)",
            topic.title,
            stats.total_raw_items,
            stats.bot_flagged_count,
            stats.exact_duplicates_removed,
            stats.near_duplicates_removed,
            stats.usable_items_count,
            stats.volume_gate_passed,
        )

        return ProcessingResult(
            topic_id=topic.id,
            statistics=stats,
            usable_items=usable_items,
        )
