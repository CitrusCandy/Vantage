import React from "react";
import { SourceCoverage } from "@/lib/types";
import { SourceBadge } from "./SourceBadge";

interface SourceBreakdownProps {
  coverage: SourceCoverage;
  totalUsable?: number;
}

export const SourceBreakdown: React.FC<SourceBreakdownProps> = ({
  coverage,
  totalUsable,
}) => {
  const total = coverage.total_combined || 1;
  const gPercent = Math.round((coverage.google_news / total) * 100);
  const rPercent = Math.round((coverage.reddit / total) * 100);
  const xPercent = Math.round((coverage.x / total) * 100);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span className="font-semibold uppercase tracking-wider text-slate-300">
          Source Platform Coverage
        </span>
        <span>
          {coverage.total_combined.toLocaleString()} total ingested posts
        </span>
      </div>

      {/* Segmented Bar */}
      <div className="h-2.5 w-full bg-slate-800 rounded-full overflow-hidden flex gap-0.5 p-0.5">
        {coverage.google_news > 0 && (
          <div
            className="bg-blue-500 rounded-l-full transition-all duration-500"
            style={{ width: `${gPercent}%` }}
            title={`Google News: ${coverage.google_news} (${gPercent}%)`}
          />
        )}
        {coverage.reddit > 0 && (
          <div
            className="bg-orange-500 transition-all duration-500"
            style={{ width: `${rPercent}%` }}
            title={`Reddit: ${coverage.reddit} (${rPercent}%)`}
          />
        )}
        {coverage.x > 0 && (
          <div
            className="bg-zinc-400 rounded-r-full transition-all duration-500"
            style={{ width: `${xPercent}%` }}
            title={`X: ${coverage.x} (${xPercent}%)`}
          />
        )}
      </div>

      {/* Badges */}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <SourceBadge source="google_news" count={coverage.google_news} />
        <SourceBadge source="reddit" count={coverage.reddit} />
        <SourceBadge source="x" count={coverage.x} />
      </div>
    </div>
  );
};
