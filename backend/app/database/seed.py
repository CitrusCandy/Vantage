"""Seed script to populate Vantage database with rich multi-source topics and synthesized perspectives."""

from datetime import datetime, timedelta
import logging

from app.database.database import Base, SessionLocal, engine
from app.database.models import (
    ClusterRun,
    CombinedRawData,
    Perspective,
    RawGoogleNews,
    RawReddit,
    RawX,
    Topic,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed")


def seed_database():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # Check if already seeded
        existing_count = db.query(Topic).count()
        if existing_count > 0:
            logger.info("Database already contains %d topics. Skipping seed.", existing_count)
            return

        logger.info("Seeding Vantage database with demonstration topics and perspectives...")

        # Topic 1: Autonomous AI Agents
        topic1 = Topic(
            title="Global Commercialization of Autonomous AI Agents",
            slug="global-commercialization-of-autonomous-ai-agents",
            search_count=145,
            trending_score=0.92,
            source_coverage={
                "google_news": 45,
                "reddit": 120,
                "x": 280,
                "total_combined": 445,
            },
            last_clustered_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(topic1)
        db.commit()
        db.refresh(topic1)

        # Seed sample Raw and Combined data
        now = datetime.utcnow()
        for i in range(12):
            raw_g = RawGoogleNews(
                slug_id=topic1.id,
                title=f"Tech analysts project enterprise autonomous agent spend will top $50B by 2028 (Sample {i})",
                snippet=f"Enterprise adoption velocity accelerates as foundation models add autonomous execution capability {i}",
                link=f"https://news.google.com/articles/ai-agent-{i}",
                source_name="TechAnalyst",
                published_at=now - timedelta(hours=i * 2),
            )
            raw_r = RawReddit(
                slug_id=topic1.id,
                post_id=f"t3_ai_agent_{i}",
                body=f"We integrated multi-agent workflows into our backend infrastructure and reduced ticket resolution by 60% (Sample {i})",
                score=250 + i * 10,
                num_comments=40 + i,
                subreddit="technology",
                author=f"dev_ops_{i}",
                created_utc=now - timedelta(hours=i * 2),
            )
            raw_x = RawX(
                slug_id=topic1.id,
                tweet_id=f"1928374{i}",
                text=f"Frontier foundation models are transitioning from chatbots to active autonomous execution runtimes (Sample {i})",
                likes=400 + i * 20,
                retweets=90 + i * 5,
                replies=15,
                handle="tech_lead",
                posted_at=now - timedelta(hours=i * 2),
            )
            comb = CombinedRawData(
                slug_id=topic1.id,
                source="google_news" if i % 3 == 0 else ("reddit" if i % 3 == 1 else "x"),
                text_content=f"Public discourse item {i} regarding economic and technical implications of AI agents",
                url=f"https://discourse.example.com/{i}",
                author_handle=f"user_{i}",
                engagement_metrics={"likes": 150, "retweets": 30, "score": 100},
                is_flagged_bot=False,
                created_at=now - timedelta(hours=i * 2),
            )
            db.add_all([raw_g, raw_r, raw_x, comb])

        # Add Perspectives
        p1 = Perspective(
            topic_id=topic1.id,
            perspective_type="Economic Acceleration & Enterprise Productivity",
            estimated_share=0.48,
            summary_points=[
                "AI agents represent a quantum leap in enterprise operational speed, automating repetitive knowledge-work.",
                "Reduces software development and business operations friction by 40%.",
                "Enables single-person startups to manage enterprise-scale operations.",
                "Accelerates scientific discovery via autonomous robotic laboratories.",
            ],
            sample_quotes=[
                {
                    "quote": "Enterprise deployments of autonomous developer agents are showing a 40% reduction in cycle times across global engineering teams.",
                    "source": "google_news",
                    "author_handle": "TechChronicle",
                    "url": "https://news.google.com",
                    "engagement": {"score": 140, "likes": 85},
                },
                {
                    "quote": "We replaced 6 manual triage pipelines with agent swarms and haven't had a major outage in 3 months.",
                    "source": "reddit",
                    "author_handle": "eng_lead_sf",
                    "url": "https://reddit.com",
                    "engagement": {"score": 430, "num_comments": 67},
                },
            ],
            confidence_note="Strong cross-source evidence from news and developer forums.",
            generated_at=datetime.utcnow(),
        )

        p2 = Perspective(
            topic_id=topic1.id,
            perspective_type="Labor Market Displacement & Socioeconomic Disruption",
            estimated_share=0.32,
            summary_points=[
                "Rapid autonomous agent deployment threatens junior knowledge-worker pipelines and risks steep inequality.",
                "Entry-level hiring compression across software, legal, and finance sectors.",
                "Monopolization of surplus capital by frontier model owners.",
                "Insufficient regulatory and social safety net adaptation velocity.",
            ],
            sample_quotes=[
                {
                    "quote": "Junior developer and legal analyst hiring rates are dropping as companies test autonomous coding and document analysis tooling.",
                    "source": "x",
                    "author_handle": "LaborEconReport",
                    "url": "https://x.com",
                    "engagement": {"likes": 1200, "retweets": 340},
                }
            ],
            confidence_note="High engagement on social platforms discussing entry-level employment trends.",
            generated_at=datetime.utcnow(),
        )

        p3 = Perspective(
            topic_id=topic1.id,
            perspective_type="Security, Alignment & Autonomous Governance Concerns",
            estimated_share=0.20,
            summary_points=[
                "Granting non-deterministic AI agents execution authority over financial and cloud tools presents systemic failure vectors.",
                "Prompt injection and recursive tool execution vulnerabilities.",
                "Lack of cryptographic provenance and non-repudiation for agent decisions.",
                "Emergent multi-agent coordination risks in high-frequency trading and grid ops.",
            ],
            sample_quotes=[
                {
                    "quote": "Until agent architectures have formal verification for tool execution, giving them API access to banking or infrastructure is irresponsible.",
                    "source": "reddit",
                    "author_handle": "sec_researcher",
                    "url": "https://reddit.com",
                    "engagement": {"score": 280, "num_comments": 54},
                }
            ],
            confidence_note="Consensus among cybersecurity and safety researchers.",
            generated_at=datetime.utcnow(),
        )

        # Topic 2: Solid-State Battery Commercialization
        topic2 = Topic(
            title="Next-Generation Solid-State Battery Commercialization",
            slug="next-generation-solid-state-battery-commercialization",
            search_count=78,
            trending_score=0.81,
            source_coverage={
                "google_news": 38,
                "reddit": 85,
                "x": 175,
                "total_combined": 298,
            },
            last_clustered_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(topic2)
        db.commit()
        db.refresh(topic2)

        p4 = Perspective(
            topic_id=topic2.id,
            perspective_type="Energy Density & EV Range Breakthrough",
            estimated_share=0.64,
            summary_points=[
                "Solid-state electrolytes offer 2x volumetric energy density and sub-10 minute charging.",
                "1,000+ km vehicle range standardizes across passenger electric vehicles.",
                "Non-flammable solid electrolyte completely eliminates thermal runaway risk.",
            ],
            sample_quotes=[
                {
                    "quote": "Pilot line test cells achieved 450 Wh/kg with zero degradation over 1,500 fast-charge cycles.",
                    "source": "google_news",
                    "author_handle": "EVBatteryWeekly",
                    "url": "https://news.google.com",
                    "engagement": {"score": 95},
                }
            ],
            confidence_note="Verified via laboratory press releases and industry reports.",
            generated_at=datetime.utcnow(),
        )

        p5 = Perspective(
            topic_id=topic2.id,
            perspective_type="Manufacturing Yield & Scaling Skepticism",
            estimated_share=0.36,
            summary_points=[
                "High ceramic electrolyte brittleness and anode interface degradation remain unproven at automotive scale.",
                "Roll-to-roll clean-room pouch cell production yields remain cost-prohibitive.",
                "Commercial automotive rollout likely delayed past 2028.",
            ],
            sample_quotes=[
                {
                    "quote": "Scaling clean-room pouch cell manufacturing without micro-fractures in the ceramic separator is brutally difficult.",
                    "source": "reddit",
                    "author_handle": "battery_engineer_99",
                    "url": "https://reddit.com",
                    "engagement": {"score": 190, "num_comments": 42},
                }
            ],
            confidence_note="Prominent sentiment in engineering discussion forums.",
            generated_at=datetime.utcnow(),
        )

        db.add_all([p1, p2, p3, p4, p5])
        db.commit()
        logger.info("Successfully seeded database with 2 topics and 5 perspectives!")

    except Exception as e:
        db.rollback()
        logger.error("Error during seed: %s", str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()
