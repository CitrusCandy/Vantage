# Vantage News - Revised System Architecture

## Overview

Vantage News ingests real-time public discourse across multiple independent platforms (Google News RSS, Reddit, and X), stages the records into isolated platform-specific tables, merges and normalizes them into a unified `CombinedRawData` dataset, applies MinHash/LSH deduplication and bot/spam filtering, clusters topics using HDBSCAN over dense vector embeddings, synthesizes multi-perspective summaries using LLMs, and presents them in a modern web showcase.

---

## Architecture Flow

```text
Google News ──┐
Reddit ───────┼──> Staging Tables
X ────────────┘
                    ↓
              Merge / ETL
                    ↓
           CombinedRawData
                    ↓
        Normalize + Filtering
                    ↓
              HDBSCAN
                    ↓
           LLM Perspectives
                    ↓
             Frontend
```

---

## Key Pipeline Stages

1. **Independent Scrapers & Staging Layer**:
   - **Google News RSS**: Parsed via `feedparser` into `raw_google_news` (`title`, `link`, `source_name`, `published_at`, `snippet`, `slug_id`).
   - **Reddit**: Ingested via official PRAW / REST endpoints into `raw_reddit` (`post_id`, `body`, `score`, `num_comments`, `subreddit`, `author`, `created_utc`, `slug_id`).
   - **X (Twitter)**: Scraped with fail-soft isolation into `raw_x` (`tweet_id`, `text`, `likes`, `retweets`, `replies`, `handle`, `posted_at`, `slug_id`).
   - **Failure Isolation**: Individual scraper rate-limits or network failures never impact other sources.

2. **Merge & Normalization ETL**:
   - Reads `raw_google_news`, `raw_reddit`, and `raw_x` for a given topic.
   - Unions available records into `combined_raw_data`.
   - Preserves source attribution, original URLs, engagement metrics, and timestamps.
   - Idempotent: repeated merge runs do not create duplicate records.
   - Compound index on `(slug_id, source, created_at)`.

3. **Discourse Preprocessing & Deduplication**:
   - Text normalization, HTML entity unescaping, and URL stripping for content matching.
   - **MinHash + LSH**: Locality-Sensitive Hashing detects exact and near-duplicate stories across different platforms (cross-source deduplication).
   - **Bot/Spam Heuristics**: Link density, spam/promo patterns, bot usernames, and extreme repetition flag `is_flagged_bot = True` without deleting raw records.
   - **Minimum-Volume Gate**: Enforces minimum usable item volume (default: 30 items) before downstream clustering.

4. **Embedding & HDBSCAN Clustering Layer**:
   - Generates dense vector embeddings using OpenAI `text-embedding-3-small` (or local embedding models).
   - Cosine-equivalent $L_2$ vector normalization.
   - Density-based HDBSCAN clustering rejecting outliers/noise (`label = -1`).
   - Extracts top representative samples and persists `ClusterRun` execution metadata.

5. **LLM Perspective Synthesis**:
   - Extracts core perspective stances, arguments, and representative citations from top clusters.

6. **Frontend Showcase**:
   - Responsive multi-perspective comparison interface.
