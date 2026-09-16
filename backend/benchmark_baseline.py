"""Benchmark script to measure baseline latency and stage-by-stage pipeline timings."""

from datetime import datetime, timedelta
import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base, get_db
from app.database.models import CombinedRawData, RawGoogleNews, RawReddit, RawX, Topic
from app.ingestion.merge_pipeline import MergePipeline
from app.llm.perspective import MockPerspectiveSynthesizer
from app.llm.pipeline import PerspectivePipeline
from app.main import app
from app.processing.cluster_pipeline import ClusterPipeline
from app.processing.embeddings import MockEmbeddingProvider
from app.processing.processor import DiscourseProcessor
from app.workers.trending import TrendingScorer


def run_benchmark():
    print("=== Vantage Baseline Latency & Timing Benchmark ===")

    # 1. Startup & Setup
    t0 = time.perf_counter()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    startup_time_ms = (time.perf_counter() - t0) * 1000.0
    print(f"[*] Engine & TestClient Startup Time: {startup_time_ms:.2f} ms")

    # 2. Representative API Endpoint Latency
    endpoints = [
        ("GET", "/health", None),
        ("POST", "/api/topics", {"title": "Autonomous AI Swarms"}),
        ("GET", "/api/topics", None),
        ("GET", "/api/topics/trending", None),
        ("GET", "/api/workers/status", None),
    ]

    print("\n--- API Endpoint Response Latencies ---")
    for method, path, payload in endpoints:
        start_req = time.perf_counter()
        if method == "GET":
            resp = client.get(path)
        else:
            resp = client.post(path, json=payload)
        latency_ms = (time.perf_counter() - start_req) * 1000.0
        print(f"[{method}] {path} -> Status {resp.status_code} ({latency_ms:.2f} ms)")

    # 3. Stage-by-Stage Pipeline Timings
    print("\n--- Stage-by-Stage Pipeline Timings ---")
    topic = db.query(Topic).first()
    now = datetime.utcnow()

    # Stage A: Ingestion Staging Persistence
    t_stage = time.perf_counter()
    for i in range(20):
        g = RawGoogleNews(
            slug_id=topic.id,
            title=f"Sample News Item {i} on Autonomous AI Systems",
            snippet=f"Detailed market report #{i} evaluating enterprise deployment velocity.",
            link=f"https://news.google.com/{i}",
            source_name="TechDaily",
            published_at=now - timedelta(minutes=i),
        )
        r = RawReddit(
            slug_id=topic.id,
            post_id=f"t3_{i}",
            body=f"Reddit discussion thread #{i} analyzing scalability hurdles and developer adoption.",
            score=150 + i,
            num_comments=25,
            subreddit="artificial",
            author=f"ai_researcher_{i}",
            created_utc=now - timedelta(minutes=i),
        )
        x = RawX(
            slug_id=topic.id,
            tweet_id=f"1900{i}",
            text=f"Live discourse post #{i} highlighting autonomous capability frontiers. #AISwarms",
            likes=300 + i,
            retweets=50,
            replies=8,
            handle=f"lead_{i}",
            posted_at=now - timedelta(minutes=i),
        )
        db.add_all([g, r, x])
    db.commit()
    t_staging_ms = (time.perf_counter() - t_stage) * 1000.0
    print(f"1. Ingestion Staging Persistence (60 records): {t_staging_ms:.2f} ms")

    # Stage B: Merge & Normalization
    t_merge_start = time.perf_counter()
    merge_pipeline = MergePipeline()
    merge_res = merge_pipeline.merge_topic_staging_data(topic=topic, db=db)
    t_merge_ms = (time.perf_counter() - t_merge_start) * 1000.0
    print(f"2. Merge & Normalization ({merge_res['new_records_added']} records): {t_merge_ms:.2f} ms")

    # Stage C: Preprocessing, MinHash/LSH & Bot Filtering
    t_filter_start = time.perf_counter()
    processor = DiscourseProcessor()
    proc_res = processor.process_topic_items(topic=topic, db=db, min_volume_override=5)
    t_filter_ms = (time.perf_counter() - t_filter_start) * 1000.0
    print(f"3. MinHash/LSH & Bot Filtering ({len(proc_res.usable_items)} usable items): {t_filter_ms:.2f} ms")

    # Stage D: Embedding Generation
    t_emb_start = time.perf_counter()
    emb_provider = MockEmbeddingProvider(dimension=64)
    texts = [item.text_content for item in proc_res.usable_items]
    embeddings = emb_provider.embed_texts(texts)
    t_emb_ms = (time.perf_counter() - t_emb_start) * 1000.0
    print(f"4. Embedding Generation ({len(texts)} texts): {t_emb_ms:.2f} ms")

    # Stage E: HDBSCAN Clustering & ClusterRun Persistence
    t_clust_start = time.perf_counter()
    cluster_pipeline = ClusterPipeline(
        processor=processor,
        embedding_provider=emb_provider,
    )
    cluster_res = cluster_pipeline.run_for_topic(topic=topic, db=db, min_volume_threshold=5)
    t_clust_ms = (time.perf_counter() - t_clust_start) * 1000.0
    print(f"5. HDBSCAN Clustering ({cluster_res.get('cluster_count', 0)} clusters): {t_clust_ms:.2f} ms")

    # Stage F: LLM Perspective Synthesis & Persistence
    t_synth_start = time.perf_counter()
    perspective_pipeline = PerspectivePipeline(synthesizer=MockPerspectiveSynthesizer())
    synthesis_res = perspective_pipeline.run_synthesis_for_topic(
        topic=topic,
        db=db,
        min_volume_threshold=5,
        cluster_data=cluster_res,
    )
    t_synth_ms = (time.perf_counter() - t_synth_start) * 1000.0
    print(f"6. LLM Perspective Synthesis ({synthesis_res.get('perspectives_count', 0)} perspectives): {t_synth_ms:.2f} ms")

    # Stage G: Trending Score Recalculation
    t_trend_start = time.perf_counter()
    scorer = TrendingScorer()
    score_breakdown = scorer.calculate_topic_score(topic=topic, db=db)
    topic.trending_score = score_breakdown.final_score
    db.commit()
    t_trend_ms = (time.perf_counter() - t_trend_start) * 1000.0
    print(f"7. Trending Score Calculation (Score: {topic.trending_score}): {t_trend_ms:.2f} ms")

    total_pipeline_ms = (
        t_staging_ms
        + t_merge_ms
        + t_filter_ms
        + t_emb_ms
        + t_clust_ms
        + t_synth_ms
        + t_trend_ms
    )
    print(f"\n[*] Total End-to-End Pipeline Duration: {total_pipeline_ms:.2f} ms")
    print("====================================================")


if __name__ == "__main__":
    run_benchmark()
