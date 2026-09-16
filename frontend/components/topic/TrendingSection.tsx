import React from "react";
import Link from "next/link";
import { Flame, TrendingUp, Sparkles, ArrowRight, Activity } from "lucide-react";
import { Topic } from "@/lib/types";

interface TrendingSectionProps {
  trendingTopics: Topic[];
}

export const TrendingSection: React.FC<TrendingSectionProps> = ({
  trendingTopics,
}) => {
  if (!trendingTopics || trendingTopics.length === 0) return null;

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-amber-500/20 to-orange-500/20 border border-amber-500/30 flex items-center justify-center text-amber-400">
            <Flame className="w-4 h-4 animate-bounce" />
          </div>
          <div>
            <h2 className="text-lg sm:text-xl font-extrabold text-slate-100 tracking-tight flex items-center gap-2">
              <span>Real-Time Trending Discourse</span>
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 uppercase tracking-wider">
                Live
              </span>
            </h2>
            <p className="text-xs text-slate-400">
              Ranked via multi-source velocity, unique platform reach, and engagement decay
            </p>
          </div>
        </div>
      </div>

      {/* Grid of trending cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {trendingTopics.slice(0, 4).map((topic, index) => {
          const scorePercent = Math.round(topic.trending_score * 100);
          return (
            <Link
              key={topic.id}
              href={`/topics/${topic.slug}`}
              className="group glass-card rounded-2xl p-4 border border-white/[0.08] hover:border-amber-500/40 hover:bg-surface-light/80 transition-all duration-300 relative overflow-hidden flex flex-col justify-between"
            >
              {/* Subtle top background glow */}
              <div className="absolute -right-6 -top-6 w-20 h-20 rounded-full bg-indigo-500/10 blur-xl group-hover:bg-amber-500/20 transition-all" />

              <div>
                <div className="flex items-center justify-between gap-2 mb-2.5">
                  <span className="text-xs font-mono font-bold text-amber-400/90 flex items-center gap-1">
                    #{index + 1} Trending
                  </span>
                  <span className="px-2 py-0.5 rounded-md bg-white/[0.06] text-[11px] font-mono text-slate-300 font-semibold">
                    {scorePercent}% Score
                  </span>
                </div>

                <h3 className="text-sm font-bold text-slate-100 group-hover:text-amber-200 transition-colors line-clamp-2 leading-snug">
                  {topic.title}
                </h3>
              </div>

              <div className="mt-4 pt-2.5 border-t border-white/[0.05] flex items-center justify-between text-xs text-slate-400">
                <span>
                  {topic.perspectives?.length
                    ? `${topic.perspectives.length} Perspectives`
                    : "Live Signals"}
                </span>
                <span className="text-indigo-400 group-hover:translate-x-1 transition-transform flex items-center gap-0.5 font-medium">
                  <span>Explore</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </span>
              </div>
            </Link>
          );
        })}
      </div>
    </section>
  );
};
