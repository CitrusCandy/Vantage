import React from "react";
import { Filter, Sparkles, Check } from "lucide-react";
import { StanceCategory } from "@/lib/types";

interface PerspectiveFilterProps {
  selectedStance: string;
  onSelectStance: (stance: string) => void;
  selectedSource: string;
  onSelectSource: (source: string) => void;
  totalPerspectives: number;
}

export const PerspectiveFilter: React.FC<PerspectiveFilterProps> = ({
  selectedStance,
  onSelectStance,
  selectedSource,
  onSelectSource,
  totalPerspectives,
}) => {
  const stances = [
    { id: "all", label: "All Stances" },
    { id: "supportive", label: "Supportive", color: "text-emerald-400 border-emerald-500/30" },
    { id: "critical", label: "Critical", color: "text-rose-400 border-rose-500/30" },
    { id: "nuanced", label: "Nuanced", color: "text-violet-400 border-violet-500/30" },
    { id: "skeptical", label: "Skeptical", color: "text-amber-400 border-amber-500/30" },
    { id: "neutral", label: "Neutral", color: "text-slate-400 border-slate-500/30" },
  ];

  const sources = [
    { id: "all", label: "All Sources" },
    { id: "google_news", label: "Google News" },
    { id: "reddit", label: "Reddit" },
    { id: "x", label: "X" },
  ];

  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 rounded-2xl bg-surface/80 border border-white/[0.06] backdrop-blur-md">
      {/* Stance Filters */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 sm:pb-0 scrollbar-none">
        <div className="flex items-center gap-1.5 text-xs text-slate-400 font-semibold uppercase tracking-wider mr-2 shrink-0">
          <Filter className="w-3.5 h-3.5 text-indigo-400" />
          <span>Stance:</span>
        </div>
        {stances.map((s) => {
          const isActive = selectedStance === s.id;
          return (
            <button
              key={s.id}
              onClick={() => onSelectStance(s.id)}
              className={`px-3 py-1.5 rounded-xl text-xs font-semibold whitespace-nowrap transition-all ${
                isActive
                  ? "bg-indigo-600 text-white shadow-md shadow-indigo-500/25 border border-indigo-400/40"
                  : "bg-surface-light text-slate-400 hover:text-slate-200 hover:bg-slate-800/80 border border-transparent"
              }`}
            >
              {s.label}
            </button>
          );
        })}
      </div>

      {/* Source Filters */}
      <div className="flex items-center gap-2 shrink-0">
        <span className="text-xs text-slate-400 font-medium hidden md:inline">
          Filter Evidence Source:
        </span>
        <select
          value={selectedSource}
          onChange={(e) => onSelectSource(e.target.value)}
          className="bg-surface-light border border-surface-border text-slate-200 text-xs rounded-xl px-3 py-1.5 focus:outline-none focus:border-indigo-500/50 cursor-pointer"
        >
          {sources.map((src) => (
            <option key={src.id} value={src.id} className="bg-surface text-slate-200">
              {src.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
};
