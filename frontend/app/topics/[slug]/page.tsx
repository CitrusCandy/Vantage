"use client";

import React, { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Sparkles,
  TrendingUp,
  Clock,
  RefreshCw,
  Cpu,
  Shield,
  Layers,
  Activity,
  AlertCircle,
  CheckCircle,
  Database,
  ExternalLink,
} from "lucide-react";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { PerspectiveCard } from "@/components/perspective/PerspectiveCard";
import { PerspectiveFilter } from "@/components/perspective/PerspectiveFilter";
import { ShareBarChart } from "@/components/charts/ShareBarChart";
import { SourceBreakdown } from "@/components/source/SourceBreakdown";
import { Button } from "@/components/common/Button";
import { Spinner } from "@/components/common/Spinner";
import { Badge } from "@/components/common/Badge";
import {
  getTopicBySlug,
  triggerIngestion,
  triggerClustering,
  triggerSynthesis,
} from "@/lib/api";
import { Perspective, Topic } from "@/lib/types";
import { classifyStance, formatTimeAgo } from "@/lib/utils";

// Mock showcase perspectives for demo topics
const DEMO_FALLBACK_PERSPECTIVES: Record<string, Perspective[]> = {
  default: [
    {
      id: 1,
      cluster_id: 1,
      perspective_type: "Economic Acceleration & Market Growth",
      summary:
        "Advocates emphasize radical productivity gains, rapid reduction in enterprise operational friction, and expansion into high-margin automated services.",
      estimated_share: 0.52,
      key_arguments: [
        "Substantial operational cost reductions across core enterprise processes",
        "Democratizes advanced capabilities for small and medium businesses",
        "Drives private capital formation and technical innovation cycles",
      ],
      sample_quotes: [
        {
          quote:
            "Our multi-source benchmark demonstrated a 40% efficiency boost in production pipelines across 100 enterprise deployments.",
          source: "google_news",
          author_handle: "TechEnterpriseReview",
          url: "https://news.google.com",
          engagement: { score: 180, likes: 95 },
        },
        {
          quote:
            "The barrier to entry for building complex autonomous systems has dropped to near zero.",
          source: "reddit",
          author_handle: "sys_architect",
          url: "https://reddit.com",
          engagement: { score: 320, num_comments: 48 },
        },
      ],
      created_at: new Date().toISOString(),
    },
    {
      id: 2,
      cluster_id: 2,
      perspective_type: "Safety, Security & Alignment Risks",
      summary:
        "Critics and cybersecurity experts warn of prompt injection vulnerabilities, recursive execution risks, and lack of deterministic validation.",
      estimated_share: 0.31,
      key_arguments: [
        "Uncontrolled API execution without cryptographic identity verification",
        "Potential for weaponized automated disinformation campaigns",
        "Complex failure cascades that are difficult to trace in real time",
      ],
      sample_quotes: [
        {
          quote:
            "Without sandbox containment, automated agent loops can inadvertently trigger cascading financial or system errors.",
          source: "x",
          author_handle: "SecResearchLab",
          url: "https://x.com",
          engagement: { likes: 850, retweets: 210 },
        },
      ],
      created_at: new Date().toISOString(),
    },
    {
      id: 3,
      cluster_id: 3,
      perspective_type: "Regulatory Compliance & Ethical Standards",
      summary:
        "Policy researchers recommend structured governance frameworks, mandatory provenance metadata, and multi-stakeholder auditing standards.",
      estimated_share: 0.17,
      key_arguments: [
        "Requires harmonized cross-border compliance mechanisms",
        "Audit logs and provenance tracking must be immutable",
      ],
      sample_quotes: [
        {
          quote:
            "Regulatory clarity is essential so enterprise adopters know their liability boundaries before scaling deployments.",
          source: "google_news",
          author_handle: "PolicyBrief",
          url: "https://news.google.com",
          engagement: { score: 65 },
        },
      ],
      created_at: new Date().toISOString(),
    },
  ],
};

export default function TopicDetailPage() {
  const params = useParams();
  const slug = params?.slug as string;
  const router = useRouter();

  const [topic, setTopic] = useState<Topic | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Pipeline Action States
  const [isIngesting, setIsIngesting] = useState(false);
  const [isClustering, setIsClustering] = useState(false);
  const [isSynthesizing, setIsSynthesizing] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // Filter States
  const [selectedStance, setSelectedStance] = useState<string>("all");
  const [selectedSource, setSelectedSource] = useState<string>("all");
  const [selectedPerspectiveId, setSelectedPerspectiveId] = useState<number | null>(null);

  const fetchTopicData = async () => {
    if (!slug) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await getTopicBySlug(slug);
      setTopic(data);
    } catch (err: any) {
      console.warn("Could not fetch topic from live backend, falling back to demo:", err);
      // Fallback topic
      const titleFormatted = decodeURIComponent(slug)
        .replace(/-/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase());
      const fallback: Topic = {
        id: 999,
        title: titleFormatted,
        slug: slug,
        search_count: 15,
        trending_score: 0.78,
        source_coverage: {
          google_news: 35,
          reddit: 60,
          x: 120,
          total_combined: 215,
        },
        updated_at: new Date().toISOString(),
        created_at: new Date(Date.now() - 1000 * 60 * 60 * 12).toISOString(),
        last_clustered_at: new Date().toISOString(),
        perspectives: DEMO_FALLBACK_PERSPECTIVES.default,
      };
      setTopic(fallback);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchTopicData();
  }, [slug]);

  // Handle Pipeline Triggers
  const handleTriggerIngestion = async () => {
    if (!topic) return;
    setIsIngesting(true);
    setActionMessage("Scraping Google News, Reddit, and X into staging tables...");
    try {
      await triggerIngestion(topic.slug, 30);
      setActionMessage("Ingestion and merge complete! Refreshing dataset...");
      await fetchTopicData();
    } catch (err: any) {
      setActionMessage(`Ingestion note: ${err.message}`);
    } finally {
      setIsIngesting(false);
    }
  };

  const handleTriggerClustering = async () => {
    if (!topic) return;
    setIsClustering(true);
    setActionMessage("Generating embeddings and running HDBSCAN clustering...");
    try {
      await triggerClustering(topic.slug, 5);
      setActionMessage("Clustering complete! Refreshing topic...");
      await fetchTopicData();
    } catch (err: any) {
      setActionMessage(`Clustering note: ${err.message}`);
    } finally {
      setIsClustering(false);
    }
  };

  const handleTriggerSynthesis = async () => {
    if (!topic) return;
    setIsSynthesizing(true);
    setActionMessage("Extracting cluster samples & synthesizing viewpoints via LLM...");
    try {
      await triggerSynthesis(topic.slug, 5);
      setActionMessage("Perspective synthesis complete! Refreshing perspectives...");
      await fetchTopicData();
    } catch (err: any) {
      setActionMessage(`Synthesis note: ${err.message}`);
    } finally {
      setIsSynthesizing(false);
    }
  };

  // Extract and filter perspectives
  const perspectivesList = useMemo(() => {
    const rawList =
      topic?.perspectives && topic.perspectives.length > 0
        ? topic.perspectives
        : DEMO_FALLBACK_PERSPECTIVES.default;

    return rawList.filter((p) => {
      // Filter stance
      if (selectedStance !== "all") {
        const category = classifyStance(p.perspective_type);
        if (category !== selectedStance) return false;
      }
      // Filter source
      if (selectedSource !== "all") {
        const hasSource = p.sample_quotes?.some(
          (q) => q.source.toLowerCase() === selectedSource.toLowerCase()
        );
        if (!hasSource) return false;
      }
      return true;
    });
  }, [topic, selectedStance, selectedSource]);

  const totalShareSum = useMemo(() => {
    const rawList =
      topic?.perspectives && topic.perspectives.length > 0
        ? topic.perspectives
        : DEMO_FALLBACK_PERSPECTIVES.default;
    return rawList.reduce((acc, p) => acc + p.estimated_share, 0) || 1.0;
  }, [topic]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex flex-col justify-between">
        <Navbar />
        <main className="max-w-7xl mx-auto px-4 py-24 flex items-center justify-center">
          <Spinner size="lg" label="Loading topic perspectives and discourse clusters..." />
        </main>
        <Footer />
      </div>
    );
  }

  if (!topic) {
    return (
      <div className="min-h-screen flex flex-col justify-between">
        <Navbar />
        <main className="max-w-7xl mx-auto px-4 py-24 text-center space-y-4">
          <h2 className="text-2xl font-bold text-white">Topic Not Found</h2>
          <p className="text-sm text-slate-400">
            The discourse topic with slug &apos;{slug}&apos; could not be retrieved.
          </p>
          <Link href="/">
            <Button variant="primary">Return to Trending Feed</Button>
          </Link>
        </main>
        <Footer />
      </div>
    );
  }

  const coverage = topic.source_coverage || {
    google_news: 0,
    reddit: 0,
    x: 0,
    total_combined: 0,
  };
  const trendingPercent = Math.round(topic.trending_score * 100);

  return (
    <div className="flex flex-col min-h-screen">
      <Navbar />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8 flex-1 w-full">
        {/* Back Link */}
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-xs font-semibold text-slate-400 hover:text-white transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Trending Feed</span>
        </Link>

        {/* Topic Header Card */}
        <div className="glass-card rounded-3xl p-6 md:p-8 border border-white/[0.08] space-y-6 relative overflow-hidden">
          {/* Subtle Ambient Glow */}
          <div className="absolute top-0 right-0 w-96 h-96 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />

          {/* Top Row: Trending Score & Time */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-400 text-xs font-bold">
                <TrendingUp className="w-3.5 h-3.5" />
                Trending Score: {trendingPercent}%
              </span>
              <span className="text-xs text-slate-400 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                Updated {formatTimeAgo(topic.updated_at)}
              </span>
            </div>

            {/* Pipeline Action Buttons */}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                isLoading={isIngesting}
                icon={<Database className="w-3.5 h-3.5 text-blue-400" />}
                onClick={handleTriggerIngestion}
                title="Fetch new posts from Google News, Reddit, and X into staging tables"
              >
                Ingest Sources
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isLoading={isClustering}
                icon={<Cpu className="w-3.5 h-3.5 text-purple-400" />}
                onClick={handleTriggerClustering}
                title="Run MinHash deduplication, vector embeddings & HDBSCAN clustering"
              >
                Cluster Discourse
              </Button>
              <Button
                size="sm"
                variant="primary"
                isLoading={isSynthesizing}
                icon={<Sparkles className="w-3.5 h-3.5" />}
                onClick={handleTriggerSynthesis}
                title="Synthesize structured perspectives from clustered samples via LLM"
              >
                Synthesize Perspectives
              </Button>
            </div>
          </div>

          {/* Action Notification Banner */}
          {actionMessage && (
            <div className="p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-xs text-indigo-300 flex items-center gap-2 animate-fade-in">
              <Activity className="w-4 h-4 text-indigo-400 animate-spin" />
              <span>{actionMessage}</span>
            </div>
          )}

          {/* Title */}
          <div>
            <h1 className="text-2xl sm:text-4xl font-extrabold text-white tracking-tight leading-tight">
              {topic.title}
            </h1>
            <p className="text-xs sm:text-sm text-slate-400 mt-2 font-mono">
              Topic Slug: <span className="text-slate-300">{topic.slug}</span>
            </p>
          </div>

          {/* Source Breakdown Component */}
          <div className="pt-2 border-t border-white/[0.06]">
            <SourceBreakdown coverage={coverage} />
          </div>
        </div>

        {/* Share Distribution Chart */}
        <ShareBarChart
          perspectives={
            topic.perspectives && topic.perspectives.length > 0
              ? topic.perspectives
              : DEMO_FALLBACK_PERSPECTIVES.default
          }
          selectedId={selectedPerspectiveId}
          onSelectPerspective={(p) => setSelectedPerspectiveId(p.id)}
        />

        {/* Filter Controls */}
        <PerspectiveFilter
          selectedStance={selectedStance}
          onSelectStance={setSelectedStance}
          selectedSource={selectedSource}
          onSelectSource={setSelectedSource}
          totalPerspectives={perspectivesList.length}
        />

        {/* Perspective Cards Showcase Grid */}
        <section className="space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg sm:text-xl font-extrabold text-white tracking-tight flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-indigo-400" />
              <span>Synthesized Perspectives Showcase</span>
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 border border-slate-700">
                {perspectivesList.length} showing
              </span>
            </h2>
          </div>

          {perspectivesList.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {perspectivesList.map((perspective) => (
                <PerspectiveCard
                  key={perspective.id}
                  perspective={perspective}
                  totalShareSum={totalShareSum}
                  highlighted={selectedPerspectiveId === perspective.id}
                />
              ))}
            </div>
          ) : (
            <div className="glass-card rounded-2xl p-12 text-center space-y-3">
              <p className="text-sm font-semibold text-slate-300">
                No perspectives match the selected filters
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setSelectedStance("all");
                  setSelectedSource("all");
                }}
              >
                Reset Filters
              </Button>
            </div>
          )}
        </section>
      </main>

      <Footer />
    </div>
  );
}
