import React from "react";
import { ExternalLink, MessageSquare, ThumbsUp, Repeat, MessageCircle, User, Calendar } from "lucide-react";
import { SampleQuote } from "@/lib/types";
import { Modal } from "../common/Modal";
import { SourceBadge } from "../source/SourceBadge";
import { formatDate, isSafeExternalUrl } from "@/lib/utils";

interface QuoteDrawerProps {
  quote: SampleQuote | null;
  isOpen: boolean;
  onClose: () => void;
  perspectiveType?: string;
}

export const QuoteDrawer: React.FC<QuoteDrawerProps> = ({
  quote,
  isOpen,
  onClose,
  perspectiveType,
}) => {
  if (!quote) return null;

  const metrics = quote.engagement || {};

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Discourse Evidence Details"
      maxWidth="max-w-xl"
    >
      <div className="space-y-6">
        {/* Source Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 pb-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <SourceBadge source={quote.source} />
            {quote.author_handle && (
              <span className="text-xs text-slate-400 flex items-center gap-1 font-mono">
                <User className="w-3.5 h-3.5 text-slate-500" />
                @{quote.author_handle}
              </span>
            )}
          </div>

          {quote.created_at && (
            <span className="text-xs text-slate-500 flex items-center gap-1">
              <Calendar className="w-3.5 h-3.5" />
              {formatDate(quote.created_at)}
            </span>
          )}
        </div>

        {/* Full Quote Text */}
        <div className="p-4 rounded-xl bg-slate-900/80 border border-white/[0.06] relative">
          <span className="text-3xl font-serif text-indigo-400/40 absolute top-2 left-3 select-none">
            “
          </span>
          <p className="text-sm md:text-base text-slate-200 font-serif leading-relaxed italic pl-5 pr-2">
            {quote.quote}
          </p>
        </div>

        {/* Perspective Alignment */}
        {perspectiveType && (
          <div className="p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-xs text-indigo-300">
            <span className="font-semibold">Clustered Under Perspective:</span>{" "}
            {perspectiveType}
          </div>
        )}

        {/* Engagement Metrics */}
        {Object.keys(metrics).length > 0 && (
          <div className="grid grid-cols-3 gap-3">
            {"likes" in metrics && (
              <div className="p-3 rounded-xl bg-surface-light border border-surface-border text-center">
                <div className="flex items-center justify-center gap-1.5 text-xs text-slate-400 mb-1">
                  <ThumbsUp className="w-3.5 h-3.5 text-blue-400" />
                  <span>Likes / Score</span>
                </div>
                <span className="text-base font-bold text-white font-mono">
                  {(metrics.likes || metrics.score || 0).toLocaleString()}
                </span>
              </div>
            )}
            {"retweets" in metrics && (
              <div className="p-3 rounded-xl bg-surface-light border border-surface-border text-center">
                <div className="flex items-center justify-center gap-1.5 text-xs text-slate-400 mb-1">
                  <Repeat className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Retweets</span>
                </div>
                <span className="text-base font-bold text-white font-mono">
                  {(metrics.retweets || 0).toLocaleString()}
                </span>
              </div>
            )}
            {"num_comments" in metrics && (
              <div className="p-3 rounded-xl bg-surface-light border border-surface-border text-center">
                <div className="flex items-center justify-center gap-1.5 text-xs text-slate-400 mb-1">
                  <MessageCircle className="w-3.5 h-3.5 text-amber-400" />
                  <span>Comments</span>
                </div>
                <span className="text-base font-bold text-white font-mono">
                  {(metrics.num_comments || 0).toLocaleString()}
                </span>
              </div>
            )}
          </div>
        )}

        {/* Direct Link */}
        {quote.url && isSafeExternalUrl(quote.url) && (
          <div className="pt-2 flex justify-end">
            <a
              href={quote.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-gradient-to-r from-indigo-500 to-primary-600 text-white text-xs font-semibold hover:from-indigo-600 hover:to-primary-700 shadow-md shadow-indigo-500/20 transition-all"
            >
              <span>View Original Post on {quote.source.toUpperCase()}</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        )}
      </div>
    </Modal>
  );
};
