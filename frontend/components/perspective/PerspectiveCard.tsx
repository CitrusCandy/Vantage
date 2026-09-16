"use client";

import React, { useState } from "react";
import {
  Sparkles,
  ExternalLink,
  Quote as QuoteIcon,
  ChevronRight,
  TrendingUp,
  Share2,
  CheckCircle2,
  Layers,
} from "lucide-react";
import { Perspective, SampleQuote } from "@/lib/types";
import { getStanceBadgeStyle, getSourcePlatformMeta } from "@/lib/utils";
import { Card } from "../common/Card";
import { SourceBadge } from "../source/SourceBadge";
import { QuoteDrawer } from "./QuoteDrawer";

interface PerspectiveCardProps {
  perspective: Perspective;
  totalShareSum?: number;
  highlighted?: boolean;
}

export const PerspectiveCard: React.FC<PerspectiveCardProps> = ({
  perspective,
  totalShareSum = 1.0,
  highlighted = false,
}) => {
  const [selectedQuote, setSelectedQuote] = useState<SampleQuote | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  const style = getStanceBadgeStyle(perspective.perspective_type);
  const percentShare = Math.round(
    (perspective.estimated_share / totalShareSum) * 100
  );

  const handleOpenQuote = (quote: SampleQuote) => {
    setSelectedQuote(quote);
    setIsDrawerOpen(true);
  };

  return (
    <>
      <Card
        className={`transition-all duration-300 border border-white/[0.08] hover:border-white/20 flex flex-col justify-between ${
          highlighted
            ? "ring-2 ring-indigo-500/50 shadow-[0_0_30px_-5px_rgba(99,102,241,0.2)]"
            : ""
        }`}
      >
        {/* Top Header: Stance Badge & Share Gauge */}
        <div className="space-y-4">
          <div className="flex items-start justify-between gap-3">
            {/* Stance Pill */}
            <div
              className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border ${style.bg} ${style.text} ${style.border}`}
            >
              <span className={`w-2 h-2 rounded-full ${style.dot}`} />
              <span>{style.label}</span>
            </div>

            {/* Estimated Share Badge */}
            <div className="flex items-center gap-2 px-3 py-1 rounded-xl bg-surface-light border border-surface-border">
              <span className="text-[11px] text-slate-400 font-medium uppercase tracking-wider">
                Est. Share:
              </span>
              <span className="text-sm font-extrabold text-white font-mono">
                {percentShare}%
              </span>
            </div>
          </div>

          {/* Perspective Title */}
          <div>
            <h3 className="text-lg md:text-xl font-extrabold text-slate-100 tracking-tight leading-snug">
              {perspective.perspective_type}
            </h3>

            {/* Progress Bar for Share */}
            <div className="mt-2.5 h-1.5 w-full bg-slate-800/80 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-700 bg-gradient-to-r from-indigo-500 to-purple-500"
                style={{ width: `${Math.max(percentShare, 5)}%` }}
              />
            </div>
          </div>

          {/* Summary / Narrative Stance */}
          <div className="p-4 rounded-xl bg-slate-900/60 border border-white/[0.04]">
            <p className="text-sm text-slate-300 font-serif leading-relaxed italic">
              “{perspective.summary}”
            </p>
          </div>

          {/* Key Arguments */}
          {perspective.key_arguments && perspective.key_arguments.length > 0 && (
            <div className="space-y-2.5 pt-1">
              <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-indigo-400" />
                <span>Key Supporting Arguments</span>
              </h4>
              <ul className="space-y-2">
                {perspective.key_arguments.map((arg, idx) => (
                  <li
                    key={idx}
                    className="text-xs sm:text-sm text-slate-300 flex items-start gap-2.5 leading-normal"
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-indigo-400/80 mt-1.5 shrink-0" />
                    <span>{arg}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Linked Sample Quotes / Evidence */}
        {perspective.sample_quotes && perspective.sample_quotes.length > 0 && (
          <div className="mt-6 pt-4 border-t border-white/[0.06] space-y-2.5">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span className="font-semibold uppercase tracking-wider flex items-center gap-1">
                <QuoteIcon className="w-3 h-3 text-indigo-400" />
                Verified Discourse Evidence ({perspective.sample_quotes.length})
              </span>
              <span className="text-[11px] text-slate-500">Click to expand</span>
            </div>

            <div className="space-y-2">
              {perspective.sample_quotes.slice(0, 3).map((quote, qIdx) => (
                <div
                  key={qIdx}
                  onClick={() => handleOpenQuote(quote)}
                  className="p-3 rounded-xl bg-surface-light/70 hover:bg-slate-800/90 border border-surface-border hover:border-indigo-500/40 cursor-pointer transition-all duration-200 group"
                >
                  <div className="flex items-center justify-between gap-2 mb-1.5">
                    <div className="flex items-center gap-2">
                      <SourceBadge source={quote.source} />
                      {quote.author_handle && (
                        <span className="text-[11px] text-slate-400 font-mono">
                          @{quote.author_handle}
                        </span>
                      )}
                    </div>
                    <ChevronRight className="w-3.5 h-3.5 text-slate-500 group-hover:text-white group-hover:translate-x-0.5 transition-all" />
                  </div>
                  <p className="text-xs text-slate-300 line-clamp-2 italic font-serif">
                    “{quote.quote}”
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>

      {/* Quote Detail Drawer / Modal */}
      <QuoteDrawer
        quote={selectedQuote}
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        perspectiveType={perspective.perspective_type}
      />
    </>
  );
};
