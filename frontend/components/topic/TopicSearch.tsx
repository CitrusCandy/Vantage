"use client";

import React from "react";
import { Search, X } from "lucide-react";

interface TopicSearchProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export const TopicSearch: React.FC<TopicSearchProps> = ({
  value,
  onChange,
  placeholder = "Search topics, breaking news, or discourse queries...",
}) => {
  return (
    <div className="relative w-full max-w-xl">
      <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none text-slate-400">
        <Search className="w-4 h-4" />
      </div>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full pl-11 pr-10 py-3 rounded-2xl bg-surface/90 border border-white/[0.08] text-sm text-slate-100 placeholder-slate-400 focus:outline-none focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/20 backdrop-blur-md transition-all shadow-lg shadow-black/20"
      />
      {value && (
        <button
          onClick={() => onChange("")}
          className="absolute inset-y-0 right-0 pr-3.5 flex items-center text-slate-400 hover:text-white transition-colors"
          aria-label="Clear search"
        >
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
};
