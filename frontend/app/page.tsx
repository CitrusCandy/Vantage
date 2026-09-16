"use client";

import React, { useState, useEffect } from "react";
import {
  Sparkles,
  TrendingUp,
  Compass,
  Layers,
  Activity,
  PlusCircle,
  RefreshCw,
  Zap,
  Globe,
  MessageSquare,
  ShieldCheck,
} from "lucide-react";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { TrendingSection } from "@/components/topic/TrendingSection";
import { TopicGrid } from "@/components/topic/TopicGrid";
import { CreateTopicModal } from "@/components/topic/CreateTopicModal";
import { Button } from "@/components/common/Button";
import { Spinner } from "@/components/common/Spinner";
import { getTopics, getTrendingTopics, triggerWorkerRefresh } from "@/lib/api";
import { Topic } from "@/lib/types";

// Fallback demo topics for instant rich showcase when API database is fresh
const DEMO_TOPICS: Topic[] = [
  {
    id: 1,
    title: "Global Commercialization of Autonomous AI Agents",
    slug: "global-commercialization-of-autonomous-ai-agents",
    search_count: 85,
    trending_score: 0.92,
    source_coverage: {
      google_news: 42,
      reddit: 128,
      x: 310,
      total_combined: 480,
    },
    updated_at: new Date(Date.now() - 1000 * 60 * 25).toISOString(),
    created_at: new Date(Date.now() - 1000 * 60 * 60 * 24).toISOString(),
    last_clustered_at: new Date().toISOString(),
    perspectives: [
      {
        id: 101,
        cluster_id: 1,
        perspective_type: "Economic Acceleration & Enterprise Productivity",
        summary:
          "AI agents represent an unprecedented leap in knowledge-work productivity, automating complex workflows across finance, healthcare, and engineering.",
        estimated_share: 0.45,
        key_arguments: [
          "Radical reduction in software and business operational costs",
          "Accelerates scientific breakthroughs via autonomous experimentation",
          "High enterprise adoption velocity driving global GDP growth",
        ],
        sample_quotes: [
          {
            quote:
              "Enterprise deployments of autonomous developer agents are showing a 40% reduction in cycle times across global engineering teams.",
            source: "google_news",
            author_handle: "TechChronicle",
            url: "https://news.google.com",
            engagement: { score: 140, likes: 85 },
          },
          {
            quote:
              "We replaced 6 manual triage pipelines with agent swarms and haven't had a major outage in 3 months.",
            source: "reddit",
            author_handle: "eng_lead_sf",
            url: "https://reddit.com",
            engagement: { score: 430, num_comments: 67 },
          },
        ],
        created_at: new Date().toISOString(),
      },
      {
        id: 102,
        cluster_id: 2,
        perspective_type: "Labor Market Displacement & Socioeconomic Friction",
        summary:
          "Widespread agentic deployment risks rapid white-collar workforce disruption without adequate transition safety nets.",
        estimated_share: 0.32,
        key_arguments: [
          "Knowledge workers facing compression in entry-level hiring",
          "Wealth concentration among frontier foundation model owners",
          "Lack of regulatory frameworks for rapid retraining and support",
        ],
        sample_quotes: [
          {
            quote:
              "Junior developer and legal analyst hiring rates are dropping as companies test autonomous coding and document analysis tooling.",
            source: "x",
            author_handle: "LaborEconReport",
            url: "https://x.com",
            engagement: { likes: 1200, retweets: 340 },
          },
        ],
        created_at: new Date().toISOString(),
      },
      {
        id: 103,
        cluster_id: 3,
        perspective_type: "Security, Alignment & Autonomous Governance Concerns",
        summary:
          "Delegating critical tool execution and financial permissions to non-deterministic agents creates critical systemic vulnerabilities.",
        estimated_share: 0.23,
        key_arguments: [
          "Prompt injection and recursive agent jailbreaks pose security risks",
          "Difficulty in auditing complex multi-agent decision chains",
          "Urgent requirement for cryptographic agent identity verification",
        ],
        sample_quotes: [
          {
            quote:
              "Until agent architectures have formal verification for tool execution, giving them API access to banking or infrastructure is irresponsible.",
            source: "reddit",
            author_handle: "sec_researcher",
            url: "https://reddit.com",
            engagement: { score: 280, num_comments: 54 },
          },
        ],
        created_at: new Date().toISOString(),
      },
    ],
  },
  {
    id: 2,
    title: "Next-Generation Solid-State Battery Commercialization",
    slug: "next-generation-solid-state-battery-commercialization",
    search_count: 54,
    trending_score: 0.81,
    source_coverage: {
      google_news: 38,
      reddit: 85,
      x: 175,
      total_combined: 298,
    },
    updated_at: new Date(Date.now() - 1000 * 60 * 90).toISOString(),
    created_at: new Date(Date.now() - 1000 * 60 * 60 * 48).toISOString(),
    last_clustered_at: new Date().toISOString(),
    perspectives: [
      {
        id: 201,
        cluster_id: 1,
        perspective_type: "Energy Density & EV Range Breakthrough",
        summary:
          "Solid-state chemistry provides 2x energy density, sub-10 minute fast charging, and completely eliminates thermal runaway fire hazards.",
        estimated_share: 0.62,
        key_arguments: [
          "1,000+ km vehicle range becomes standard across passenger EVs",
          "Non-flammable solid electrolyte radically improves safety",
        ],
        sample_quotes: [
          {
            quote:
              "Pilot line test cells achieved 450 Wh/kg with zero degradation over 1,500 fast-charge cycles.",
            source: "google_news",
            author_handle: "EVBatteryWeekly",
            url: "https://news.google.com",
            engagement: { score: 95 },
          },
        ],
        created_at: new Date().toISOString(),
      },
      {
        id: 202,
        cluster_id: 2,
        perspective_type: "Manufacturing Yield & Scaling Skepticism",
        summary:
          "High ceramic electrolyte brittleness and anode dendrite formation at scale remain unproven outside laboratory environments.",
        estimated_share: 0.38,
        key_arguments: [
          "Mass roll-to-roll production costs are currently 3x higher than LFP/NMC",
          "Commercial mass deployment timeline likely pushes beyond 2028",
        ],
        sample_quotes: [
          {
            quote:
              "Every few years we hear solid-state is 2 years away. Scaling clean-room pouch cell manufacturing without micro-fractures is brutally difficult.",
            source: "reddit",
            author_handle: "battery_engineer_99",
            url: "https://reddit.com",
            engagement: { score: 190, num_comments: 42 },
          },
        ],
        created_at: new Date().toISOString(),
      },
    ],
  },
  {
    id: 3,
    title: "Global Central Bank Digital Currencies (CBDC) Rollout",
    slug: "global-central-bank-digital-currencies-cbdc-rollout",
    search_count: 42,
    trending_score: 0.74,
    source_coverage: {
      google_news: 28,
      reddit: 95,
      x: 140,
      total_combined: 263,
    },
    updated_at: new Date(Date.now() - 1000 * 60 * 180).toISOString(),
    created_at: new Date(Date.now() - 1000 * 60 * 60 * 72).toISOString(),
    last_clustered_at: new Date().toISOString(),
  },
];

export default function HomePage() {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [trendingTopics, setTrendingTopics] = useState<Topic[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [allTopics, trending] = await Promise.all([
        getTopics(),
        getTrendingTopics(4, 0.0),
      ]);
      if (allTopics && allTopics.length > 0) {
        setTopics(allTopics);
        setTrendingTopics(trending && trending.length > 0 ? trending : allTopics.slice(0, 4));
      } else {
        // Use demo showcase data if fresh empty backend database
        setTopics(DEMO_TOPICS);
        setTrendingTopics(DEMO_TOPICS);
      }
    } catch (err) {
      console.warn("Using fallback showcase topics:", err);
      setTopics(DEMO_TOPICS);
      setTrendingTopics(DEMO_TOPICS);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleRefreshFeed = async () => {
    setIsRefreshing(true);
    try {
      await triggerWorkerRefresh({ auto_discover: true, force_refresh_all: false });
      await loadData();
    } catch {
      await loadData();
    } finally {
      setIsRefreshing(false);
    }
  };

  return (
    <div className="flex flex-col min-h-screen">
      <Navbar onOpenCreateModal={() => setIsCreateModalOpen(true)} />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 md:py-12 space-y-12 md:space-y-16 flex-1 w-full">
        {/* Hero Section */}
        <section className="relative text-center max-w-3xl mx-auto space-y-6 pt-4 pb-2">
          {/* Badge */}
          <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-xs font-semibold backdrop-blur-md shadow-inner">
            <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
            <span>Unbiased Public Discourse & Perspective Intelligence</span>
          </div>

          {/* Title */}
          <h1 className="text-3xl sm:text-5xl md:text-6xl font-extrabold tracking-tight text-white leading-[1.15]">
            See Every Angle of the News with{" "}
            <span className="gradient-accent-text">Vantage</span>
          </h1>

          {/* Description */}
          <p className="text-sm sm:text-base md:text-lg text-slate-300 font-normal leading-relaxed max-w-2xl mx-auto">
            We crawl <strong>Google News</strong>, <strong>Reddit</strong>, and{" "}
            <strong>X</strong>, eliminate bots and duplicates via MinHash/LSH,
            cluster arguments with HDBSCAN, and synthesize traceable viewpoints using AI.
          </p>

          {/* Action Buttons */}
          <div className="flex flex-wrap items-center justify-center gap-3 pt-2">
            <Button
              size="lg"
              variant="primary"
              icon={<PlusCircle className="w-5 h-5" />}
              onClick={() => setIsCreateModalOpen(true)}
            >
              Analyze Any Topic
            </Button>
            <Button
              size="lg"
              variant="glass"
              isLoading={isRefreshing}
              icon={<RefreshCw className="w-4 h-4" />}
              onClick={handleRefreshFeed}
            >
              Refresh Live Feed
            </Button>
          </div>

          {/* Key Metrics Banner */}
          <div className="pt-6 grid grid-cols-3 gap-3 max-w-xl mx-auto text-left">
            <div className="p-3 rounded-2xl bg-surface-light/40 border border-white/[0.05] text-center">
              <span className="block text-xl md:text-2xl font-extrabold text-white font-mono">
                3
              </span>
              <span className="text-[11px] text-slate-400 font-medium">
                Live Platforms Ingested
              </span>
            </div>
            <div className="p-3 rounded-2xl bg-surface-light/40 border border-white/[0.05] text-center">
              <span className="block text-xl md:text-2xl font-extrabold text-indigo-400 font-mono">
                100%
              </span>
              <span className="text-[11px] text-slate-400 font-medium">
                Traceable Evidence
              </span>
            </div>
            <div className="p-3 rounded-2xl bg-surface-light/40 border border-white/[0.05] text-center">
              <span className="block text-xl md:text-2xl font-extrabold text-emerald-400 font-mono">
                0
              </span>
              <span className="text-[11px] text-slate-400 font-medium">
                Editorial Bias
              </span>
            </div>
          </div>
        </section>

        {/* Live Trending Section */}
        {isLoading ? (
          <Spinner size="lg" label="Loading trending discourse signals..." />
        ) : (
          <TrendingSection trendingTopics={trendingTopics} />
        )}

        {/* Full Topics Directory */}
        <section className="space-y-6 pt-6 border-t border-white/[0.06]">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl sm:text-2xl font-extrabold text-white tracking-tight flex items-center gap-2">
                <Layers className="w-5 h-5 text-indigo-400" />
                <span>All Discourse Topics</span>
              </h2>
              <p className="text-xs sm:text-sm text-slate-400">
                Explore synthesized multi-perspective clusters or ingest new discourse streams
              </p>
            </div>
          </div>

          {isLoading ? (
            <Spinner size="md" label="Loading topics..." />
          ) : (
            <TopicGrid
              topics={topics}
              onOpenCreateModal={() => setIsCreateModalOpen(true)}
            />
          )}
        </section>
      </main>

      <Footer />

      {/* Create Topic Modal */}
      <CreateTopicModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onTopicCreated={() => loadData()}
      />
    </div>
  );
}
