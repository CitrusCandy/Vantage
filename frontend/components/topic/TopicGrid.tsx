"use client";

import React, { useState, useMemo } from "react";
import { Layers, Sparkles, SlidersHorizontal, Inbox } from "lucide-react";
import { Topic } from "@/lib/types";
import { TopicCard } from "./TopicCard";
import { TopicSearch } from "./TopicSearch";
import { Button } from "../common/Button";

interface TopicGridProps {
  topics: Topic[];
  onOpenCreateModal?: () => void;
}

export const TopicGrid: React.FC<TopicGridProps> = ({
  topics,
  onOpenCreateModal,
}) => {
  const [searchTerm, setSearchTerm] = useState("");
  const [activeTab, setActiveTab] = useState<"all" | "synthesized" | "active">("all");
  const [sortBy, setSortBy] = useState<"recent" | "trending" | "volume">("recent");

  const filteredTopics = useMemo(() => {
    let list = [...topics];

    // Search filter
    if (searchTerm.trim()) {
      const term = searchTerm.toLowerCase();
      list = list.filter(
        (t) =>
          t.title.toLowerCase().includes(term) ||
          t.slug.toLowerCase().includes(term)
      );
    }

    // Tab filter
    if (activeTab === "synthesized") {
      list = list.filter((t) => (t.perspectives?.length || 0) > 0);
    } else if (activeTab === "active") {
      list = list.filter((t) => (t.source_coverage?.total_combined || 0) > 0);
    }

    // Sorting
    list.sort((a, b) => {
      if (sortBy === "trending") {
        return b.trending_score - a.trending_score;
      }
      if (sortBy === "volume") {
        return (
          (b.source_coverage?.total_combined || 0) -
          (a.source_coverage?.total_combined || 0)
        );
      }
      return (
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      );
    });

    return list;
  }, [topics, searchTerm, activeTab, sortBy]);

  return (
    <div id="topics" className="space-y-6">
      {/* Control Bar: Search, Tabs, Sort */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        {/* Search */}
        <TopicSearch value={searchTerm} onChange={setSearchTerm} />

        {/* Filter Tabs & Sort Dropdown */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Tabs */}
          <div className="flex items-center p-1 rounded-xl bg-surface-light border border-surface-border">
            <button
              onClick={() => setActiveTab("all")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                activeTab === "all"
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              All ({topics.length})
            </button>
            <button
              onClick={() => setActiveTab("synthesized")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                activeTab === "synthesized"
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Synthesized
            </button>
            <button
              onClick={() => setActiveTab("active")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                activeTab === "active"
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Ingested
            </button>
          </div>

          {/* Sort Dropdown */}
          <select
            value={sortBy}
            onChange={(e: any) => setSortBy(e.target.value)}
            className="bg-surface-light border border-surface-border text-slate-200 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-indigo-500/50 cursor-pointer"
          >
            <option value="recent">Sort: Most Recent</option>
            <option value="trending">Sort: Highest Trending</option>
            <option value="volume">Sort: Most Discourse Posts</option>
          </select>
        </div>
      </div>

      {/* Grid */}
      {filteredTopics.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {filteredTopics.map((topic) => (
            <TopicCard key={topic.id} topic={topic} />
          ))}
        </div>
      ) : (
        <div className="glass-card rounded-2xl p-12 text-center space-y-4 max-w-md mx-auto">
          <div className="w-12 h-12 rounded-2xl bg-surface-light flex items-center justify-center mx-auto text-slate-400">
            <Inbox className="w-6 h-6" />
          </div>
          <div>
            <h3 className="text-base font-bold text-slate-200">
              No matching discourse topics found
            </h3>
            <p className="text-xs text-slate-400 mt-1">
              {searchTerm
                ? `No topics match "${searchTerm}". Try analyzing a new topic.`
                : "No topics available in this category yet."}
            </p>
          </div>
          {onOpenCreateModal && (
            <Button
              variant="primary"
              size="sm"
              onClick={onOpenCreateModal}
              icon={<Sparkles className="w-3.5 h-3.5" />}
            >
              Analyze New Topic
            </Button>
          )}
        </div>
      )}
    </div>
  );
};
