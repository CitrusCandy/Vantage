import React from "react";
import Link from "next/link";
import {
  TrendingUp,
  Clock,
  Layers,
  Sparkles,
  ArrowUpRight,
  ShieldAlert,
} from "lucide-react";
import { Topic } from "@/lib/types";
import { Card } from "../common/Card";
import { Badge } from "../common/Badge";
import { SourceBadge } from "../source/SourceBadge";
import { formatTimeAgo } from "@/lib/utils";

interface TopicCardProps {
  topic: Topic;
}

export const TopicCard: React.FC<TopicCardProps> = ({ topic }) => {
  const coverage = topic.source_coverage || {
    google_news: 0,
    reddit: 0,
    x: 0,
    total_combined: 0,
  };
  const perspectiveCount = topic.perspectives?.length || 0;
  const trendingPercent = Math.round(topic.trending_score * 100);

  return (
    <Link href={`/topics/${topic.slug}`} className="block h-full group">
      <Card
        hover
        className="h-full flex flex-col justify-between border-white/[0.07] hover:border-indigo-500/40"
      >
        <div className="space-y-3.5">
          {/* Top Bar: Trending Gauge & Time */}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-semibold">
              <TrendingUp className="w-3.5 h-3.5" />
              <span>Score: {trendingPercent}%</span>
            </div>

            <span className="text-[11px] text-slate-500 flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {formatTimeAgo(topic.updated_at)}
            </span>
          </div>

          {/* Topic Title */}
          <div>
            <h3 className="text-base sm:text-lg font-bold text-slate-100 group-hover:text-indigo-300 transition-colors line-clamp-2 leading-snug">
              {topic.title}
            </h3>
          </div>

          {/* Source Breakdown Badges */}
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            {coverage.google_news > 0 && (
              <SourceBadge source="google_news" count={coverage.google_news} />
            )}
            {coverage.reddit > 0 && (
              <SourceBadge source="reddit" count={coverage.reddit} />
            )}
            {coverage.x > 0 && (
              <SourceBadge source="x" count={coverage.x} />
            )}
            {coverage.total_combined === 0 && (
              <span className="text-xs text-slate-500 italic">
                Ready for ingestion
              </span>
            )}
          </div>
        </div>

        {/* Bottom Footer: Perspectives & Arrow */}
        <div className="mt-5 pt-3 border-t border-white/[0.05] flex items-center justify-between text-xs text-slate-400">
          <div className="flex items-center gap-2">
            {perspectiveCount > 0 ? (
              <span className="inline-flex items-center gap-1 text-emerald-400 font-medium">
                <Sparkles className="w-3.5 h-3.5" />
                {perspectiveCount} Perspectives Synthesized
              </span>
            ) : (
              <span className="text-slate-500">
                {coverage.total_combined > 0
                  ? "Awaiting Synthesis"
                  : "New Candidate"}
              </span>
            )}
          </div>

          <span className="p-1 rounded-lg text-slate-400 group-hover:text-white group-hover:bg-indigo-600/20 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all">
            <ArrowUpRight className="w-4 h-4" />
          </span>
        </div>
      </Card>
    </Link>
  );
};
