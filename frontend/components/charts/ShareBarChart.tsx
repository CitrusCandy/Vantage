import React from "react";
import { Perspective } from "@/lib/types";
import { getStanceBadgeStyle } from "@/lib/utils";

interface ShareBarChartProps {
  perspectives: Perspective[];
  onSelectPerspective?: (perspective: Perspective) => void;
  selectedId?: number | null;
}

export const ShareBarChart: React.FC<ShareBarChartProps> = ({
  perspectives,
  onSelectPerspective,
  selectedId,
}) => {
  if (!perspectives || perspectives.length === 0) return null;

  const totalShare = perspectives.reduce((acc, p) => acc + p.estimated_share, 0) || 1.0;

  return (
    <div className="glass-card rounded-2xl p-5 space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-bold text-slate-100 flex items-center gap-2">
            <span>Discourse Consensus Breakdown</span>
            <span className="text-xs font-normal text-slate-400">
              ({perspectives.length} distinct perspectives identified)
            </span>
          </h4>
          <p className="text-xs text-slate-400 mt-0.5">
            Estimated distribution of public sentiment across clustered sources
          </p>
        </div>
      </div>

      {/* Multi-segment distribution bar */}
      <div className="h-4 w-full bg-slate-900 rounded-xl overflow-hidden flex gap-0.5 p-0.5 border border-white/[0.06] shadow-inner">
        {perspectives.map((p) => {
          const percent = Math.round((p.estimated_share / totalShare) * 100);
          const style = getStanceBadgeStyle(p.perspective_type);
          const isSelected = selectedId === p.id;

          return (
            <button
              key={p.id}
              onClick={() => onSelectPerspective && onSelectPerspective(p)}
              className={`h-full transition-all duration-300 relative group first:rounded-l-lg last:rounded-r-lg ${
                isSelected ? "ring-2 ring-white z-10 brightness-110" : "hover:brightness-125"
              }`}
              style={{
                width: `${Math.max(percent, 4)}%`,
                backgroundColor: style.dot.replace("bg-", "").includes("emerald")
                  ? "#10b981"
                  : style.dot.replace("bg-", "").includes("rose")
                  ? "#f43f5e"
                  : style.dot.replace("bg-", "").includes("amber")
                  ? "#f59e0b"
                  : style.dot.replace("bg-", "").includes("violet")
                  ? "#8b5cf6"
                  : style.dot.replace("bg-", "").includes("cyan")
                  ? "#06b6d4"
                  : "#64748b",
              }}
              title={`${p.perspective_type}: ${percent}%`}
            />
          );
        })}
      </div>

      {/* Legend & Clickable Pills */}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        {perspectives.map((p) => {
          const percent = Math.round((p.estimated_share / totalShare) * 100);
          const style = getStanceBadgeStyle(p.perspective_type);
          const isSelected = selectedId === p.id;

          return (
            <button
              key={p.id}
              onClick={() => onSelectPerspective && onSelectPerspective(p)}
              className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-medium border transition-all ${
                isSelected
                  ? `${style.bg} ${style.text} ${style.border} ring-1 ring-white/30 scale-105`
                  : "bg-surface-light/60 text-slate-300 border-white/[0.05] hover:border-white/20 hover:text-white"
              }`}
            >
              <span className={`w-2 h-2 rounded-full ${style.dot}`} />
              <span className="font-semibold">{p.perspective_type}</span>
              <span className="text-slate-400 font-mono text-[11px]">
                {percent}%
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
};
