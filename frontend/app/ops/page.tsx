"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  CheckCircle,
  Clock,
  Cpu,
  Database,
  Globe,
  Key,
  Layers,
  Pause,
  Play,
  RefreshCw,
  Server,
  Shield,
  Sliders,
  TrendingUp,
  XCircle,
  Zap,
} from "lucide-react";

import {
  getOpsOverview,
  getOpsPipelineMetrics,
  getOpsSourceHealth,
  getOpsWorkerMetrics,
  triggerOpsRefreshTopic,
  triggerOpsReprocessTopic,
  triggerOpsTrending,
} from "@/lib/api";
import {
  HealthState,
  OpsOverview,
  OpsPipelineMetrics,
  OpsSourceHealth,
  OpsWorkerMetrics,
} from "@/lib/types";
import { formatDate, formatTimeAgo } from "@/lib/utils";

export default function OperationsPage() {
  const [overview, setOverview] = useState<OpsOverview | null>(null);
  const [pipelineMetrics, setPipelineMetrics] = useState<OpsPipelineMetrics | null>(null);
  const [sourceHealth, setSourceHealth] = useState<OpsSourceHealth | null>(null);
  const [workerMetrics, setWorkerMetrics] = useState<OpsWorkerMetrics | null>(null);

  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [opsApiKey, setOpsApiKey] = useState<string>("");
  const [showKeyInput, setShowKeyInput] = useState<boolean>(false);

  // Control action state
  const [reprocessSlug, setReprocessSlug] = useState<string>("");
  const [actionMessage, setActionMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [isActionRunning, setIsActionRunning] = useState<boolean>(false);

  const loadAllTelemetry = async () => {
    try {
      setIsRefreshing(true);
      const [ov, pm, sh, wm] = await Promise.all([
        getOpsOverview(),
        getOpsPipelineMetrics(),
        getOpsSourceHealth(),
        getOpsWorkerMetrics(),
      ]);
      setOverview(ov);
      setPipelineMetrics(pm);
      setSourceHealth(sh);
      setWorkerMetrics(wm);
      setLastUpdated(new Date());
    } catch (err: any) {
      console.error("Failed to load operations telemetry:", err);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    loadAllTelemetry();
  }, []);

  // Auto-refresh interval (every 10 seconds)
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      loadAllTelemetry();
    }, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh]);

  const handleRunTrending = async () => {
    setIsActionRunning(true);
    setActionMessage(null);
    try {
      const res = await triggerOpsTrending(opsApiKey);
      setActionMessage({
        type: "success",
        text: `Trending discovery completed. Discovered ${res.candidate_count || 0} candidate topics.`,
      });
      loadAllTelemetry();
    } catch (err: any) {
      setActionMessage({ type: "error", text: err.message || "Failed to trigger trending discovery" });
    } finally {
      setIsActionRunning(false);
    }
  };

  const handleReprocessTopic = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!reprocessSlug.trim()) return;

    setIsActionRunning(true);
    setActionMessage(null);
    try {
      const res = await triggerOpsReprocessTopic(reprocessSlug.trim(), opsApiKey);
      setActionMessage({
        type: "success",
        text: `Reprocessed topic '${reprocessSlug}' in ${res.timings?.total_duration_ms || 0}ms.`,
      });
      setReprocessSlug("");
      loadAllTelemetry();
    } catch (err: any) {
      setActionMessage({ type: "error", text: err.message || "Failed to reprocess topic" });
    } finally {
      setIsActionRunning(false);
    }
  };

  const getStatusBadge = (status: HealthState | string) => {
    switch (status) {
      case "healthy":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 shadow-sm shadow-emerald-500/10">
            <CheckCircle className="w-3.5 h-3.5" />
            Healthy
          </span>
        );
      case "degraded":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20 shadow-sm shadow-amber-500/10">
            <AlertTriangle className="w-3.5 h-3.5" />
            Degraded
          </span>
        );
      case "unavailable":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20 shadow-sm shadow-rose-500/10">
            <XCircle className="w-3.5 h-3.5" />
            Unavailable
          </span>
        );
      case "disabled":
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-700/30 text-slate-400 border border-slate-600/30">
            Disabled
          </span>
        );
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 py-8 px-4 sm:px-6 lg:px-8">
      <div className="max-w-7xl mx-auto space-y-8">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 pb-6 border-b border-white/[0.08]">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Link
                href="/"
                className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition-colors"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
                Back to News
              </Link>
              <span className="text-slate-600">•</span>
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                <Activity className="w-3 h-3 animate-pulse" />
                Live Telemetry
              </span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-slate-200 to-slate-400">
              Operations & Observability Control
            </h1>
            <p className="text-xs sm:text-sm text-slate-400 mt-1">
              Internal system telemetry, pipeline latency breakdown, source health status, and operational controls.
            </p>
          </div>

          {/* Action Bar */}
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => setShowKeyInput(!showKeyInput)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                opsApiKey
                  ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
                  : "bg-surface-light text-slate-400 border-surface-border hover:text-white"
              }`}
            >
              <Key className="w-3.5 h-3.5" />
              {opsApiKey ? "Ops Key Set" : "Configure Ops Key"}
            </button>

            <button
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                autoRefresh
                  ? "bg-indigo-500/10 text-indigo-400 border-indigo-500/30"
                  : "bg-surface-light text-slate-400 border-surface-border"
              }`}
            >
              {autoRefresh ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
              {autoRefresh ? "Auto-Refresh On" : "Auto-Refresh Paused"}
            </button>

            <button
              onClick={loadAllTelemetry}
              disabled={isRefreshing}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-900 hover:bg-slate-200 disabled:opacity-50 transition-all shadow-sm"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* API Key Modal / Popover */}
        {showKeyInput && (
          <div className="p-4 rounded-xl bg-surface-light border border-surface-border space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-white flex items-center gap-1.5">
                <Shield className="w-4 h-4 text-indigo-400" />
                Operational API Key Guard (X-Ops-Key)
              </span>
              <button
                onClick={() => setShowKeyInput(false)}
                className="text-xs text-slate-400 hover:text-white"
              >
                Close
              </button>
            </div>
            <p className="text-xs text-slate-400">
              If <code className="text-slate-300 font-mono">OPS_API_KEY</code> is configured on the backend, enter it below to authorize triggering manual worker cycles and reprocessing jobs.
            </p>
            <div className="flex gap-2">
              <input
                type="password"
                placeholder="Enter X-Ops-Key..."
                value={opsApiKey}
                onChange={(e) => setOpsApiKey(e.target.value)}
                className="flex-1 px-3 py-1.5 rounded-lg bg-slate-950 border border-white/10 text-xs text-white focus:outline-none focus:border-indigo-500 font-mono"
              />
              <button
                onClick={() => setShowKeyInput(false)}
                className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-500"
              >
                Save
              </button>
            </div>
          </div>
        )}

        {/* Action feedback toast */}
        {actionMessage && (
          <div
            className={`p-4 rounded-xl text-xs flex items-center justify-between border ${
              actionMessage.type === "success"
                ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/20"
                : "bg-rose-500/10 text-rose-300 border-rose-500/20"
            }`}
          >
            <span>{actionMessage.text}</span>
            <button onClick={() => setActionMessage(null)} className="text-slate-400 hover:text-white">
              Dismiss
            </button>
          </div>
        )}

        {/* 1. System Health Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* API Service */}
          <div className="p-5 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-3">
            <div className="flex items-center justify-between">
              <div className="w-9 h-9 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400">
                <Server className="w-5 h-5" />
              </div>
              {getStatusBadge(overview?.api?.status || "healthy")}
            </div>
            <div>
              <span className="text-xs text-slate-400 block">API Service</span>
              <span className="text-lg font-bold text-white font-mono">v{overview?.api?.version || "2.0.0"}</span>
            </div>
            <div className="pt-2 border-t border-white/[0.04] text-[11px] text-slate-400 flex justify-between">
              <span>Environment:</span>
              <span className="font-mono text-slate-300 uppercase">{overview?.api?.environment || "prod"}</span>
            </div>
          </div>

          {/* Database */}
          <div className="p-5 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-3">
            <div className="flex items-center justify-between">
              <div className="w-9 h-9 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
                <Database className="w-5 h-5" />
              </div>
              {getStatusBadge(overview?.database?.status || "healthy")}
            </div>
            <div>
              <span className="text-xs text-slate-400 block">Database Storage</span>
              <span className="text-lg font-bold text-white font-mono">
                {overview?.database?.total_topics || 0} Topics
              </span>
            </div>
            <div className="pt-2 border-t border-white/[0.04] text-[11px] text-slate-400 flex justify-between">
              <span>Ping Latency:</span>
              <span className="font-mono text-emerald-400">{overview?.database?.latency_ms || 0} ms</span>
            </div>
          </div>

          {/* Background Worker */}
          <div className="p-5 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-3">
            <div className="flex items-center justify-between">
              <div className="w-9 h-9 rounded-xl bg-purple-500/10 border border-purple-500/20 flex items-center justify-center text-purple-400">
                <Cpu className="w-5 h-5" />
              </div>
              {getStatusBadge(overview?.worker?.is_running ? "healthy" : "degraded")}
            </div>
            <div>
              <span className="text-xs text-slate-400 block">Background Scheduler</span>
              <span className="text-lg font-bold text-white font-mono">
                {overview?.worker?.total_runs || 0} Cycles
              </span>
            </div>
            <div className="pt-2 border-t border-white/[0.04] text-[11px] text-slate-400 flex justify-between">
              <span>Interval:</span>
              <span className="font-mono text-slate-300">{overview?.worker?.interval_hours || 2}h</span>
            </div>
          </div>

          {/* OpenAI Service */}
          <div className="p-5 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-3">
            <div className="flex items-center justify-between">
              <div className="w-9 h-9 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400">
                <Zap className="w-5 h-5" />
              </div>
              {getStatusBadge(sourceHealth?.openai?.status || "healthy")}
            </div>
            <div>
              <span className="text-xs text-slate-400 block">OpenAI Synthesis</span>
              <span className="text-lg font-bold text-white font-mono">
                {sourceHealth?.openai?.avg_latency_ms || 0} ms avg
              </span>
            </div>
            <div className="pt-2 border-t border-white/[0.04] text-[11px] text-slate-400 flex justify-between">
              <span>Total Requests:</span>
              <span className="font-mono text-slate-300">{sourceHealth?.openai?.total_requests || 0}</span>
            </div>
          </div>
        </div>

        {/* 2. Middle Section: Ingestion Source Health & Pipeline Metrics */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Source Health Matrix */}
          <div className="p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Globe className="w-5 h-5 text-indigo-400" />
                <h2 className="text-base font-semibold text-white">Ingestion Source Health</h2>
              </div>
              <span className="text-[11px] text-slate-400">Isolated Scraper Fault-Tolerance</span>
            </div>

            <div className="space-y-3">
              {sourceHealth &&
                Object.entries(sourceHealth).map(([key, src]) => (
                  <div
                    key={key}
                    className="p-4 rounded-xl bg-slate-900/60 border border-white/[0.04] flex flex-col sm:flex-row sm:items-center justify-between gap-3"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-white capitalize">
                          {key.replace("_", " ")}
                        </span>
                        {getStatusBadge(src.status)}
                      </div>
                      <div className="text-[11px] text-slate-400 flex items-center gap-3">
                        <span>Requests: <strong className="text-slate-200">{src.total_requests}</strong></span>
                        <span>Success: <strong className="text-emerald-400">{src.success_count}</strong></span>
                        <span>Failures: <strong className="text-rose-400">{src.failure_count + src.timeout_count}</strong></span>
                      </div>
                    </div>

                    <div className="text-right sm:text-right">
                      <span className="text-xs font-mono font-bold text-white block">
                        {src.avg_latency_ms} ms
                      </span>
                      <span className="text-[10px] text-slate-500 block">
                        {src.last_success ? `Active ${formatTimeAgo(src.last_success)}` : "No activity"}
                      </span>
                    </div>
                  </div>
                ))}
            </div>
          </div>

          {/* Pipeline Performance Metrics */}
          <div className="p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="w-5 h-5 text-purple-400" />
                <h2 className="text-base font-semibold text-white">Pipeline Latency Breakdown</h2>
              </div>
              <span className="text-[11px] text-slate-400">
                {pipelineMetrics?.total_runs || 0} Total Runs
              </span>
            </div>

            {/* Aggregated Stats */}
            <div className="grid grid-cols-3 gap-3">
              <div className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] text-center">
                <span className="text-[11px] text-slate-400 block mb-0.5">Average</span>
                <span className="text-sm font-bold text-white font-mono">
                  {pipelineMetrics?.avg_duration_ms || 0} ms
                </span>
              </div>
              <div className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] text-center">
                <span className="text-[11px] text-slate-400 block mb-0.5">Median</span>
                <span className="text-sm font-bold text-white font-mono">
                  {pipelineMetrics?.median_duration_ms || 0} ms
                </span>
              </div>
              <div className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] text-center">
                <span className="text-[11px] text-slate-400 block mb-0.5">Success Rate</span>
                <span className="text-sm font-bold text-emerald-400 font-mono">
                  {((pipelineMetrics?.success_rate || 1) * 100).toFixed(1)}%
                </span>
              </div>
            </div>

            {/* Per-Stage Horizontal Latency Bars */}
            <div className="space-y-3 pt-2">
              <span className="text-xs font-semibold text-slate-300 block">Stage Average Latencies</span>
              {pipelineMetrics && pipelineMetrics.slowest_recent_stages.length > 0 ? (
                pipelineMetrics.slowest_recent_stages.map((st, idx) => {
                  const maxLatency = Math.max(...pipelineMetrics.slowest_recent_stages.map((s) => s.avg_duration_ms), 1);
                  const pct = Math.min(100, Math.max(10, (st.avg_duration_ms / maxLatency) * 100));
                  return (
                    <div key={st.stage} className="space-y-1">
                      <div className="flex justify-between text-xs">
                        <span className="text-slate-300 font-mono text-[11px] capitalize">
                          {st.stage.replace(/_/g, " ")}
                        </span>
                        <span className="text-slate-400 font-mono text-[11px]">
                          {st.avg_duration_ms} ms
                        </span>
                      </div>
                      <div className="h-2 rounded-full bg-slate-900 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all duration-500"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="text-xs text-slate-500 py-4 text-center">
                  No stage timing records yet. Execute a pipeline to populate telemetry.
                </div>
              )}
            </div>
          </div>
        </div>

        {/* 3. Operational Controls */}
        <div className="p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sliders className="w-5 h-5 text-amber-400" />
              <h2 className="text-base font-semibold text-white">Operational Controls</h2>
            </div>
            <span className="text-[11px] text-slate-400">Manual administrative triggers</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Run Trending Discovery */}
            <div className="p-4 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-3">
              <div>
                <h3 className="text-sm font-semibold text-white">Trigger Trending Discovery</h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Polls trend providers (Google Trends, Reddit, X) and discovers emerging topics.
                </p>
              </div>
              <button
                onClick={handleRunTrending}
                disabled={isActionRunning}
                className="w-full py-2.5 px-4 rounded-xl bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-500 disabled:opacity-50 transition-all shadow-md shadow-indigo-600/20"
              >
                {isActionRunning ? "Executing Discovery..." : "Run Trend Discovery Now"}
              </button>
            </div>

            {/* Reprocess Topic Pipeline */}
            <form onSubmit={handleReprocessTopic} className="p-4 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-3">
              <div>
                <h3 className="text-sm font-semibold text-white">Reprocess Specific Topic</h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Re-runs full multi-source ingestion → merge → clustering → LLM perspective brief.
                </p>
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Enter topic slug (e.g. quantum-computing)..."
                  value={reprocessSlug}
                  onChange={(e) => setReprocessSlug(e.target.value)}
                  className="flex-1 px-3 py-2 rounded-xl bg-slate-950 border border-white/10 text-xs text-white placeholder:text-slate-600 focus:outline-none focus:border-indigo-500 font-mono"
                />
                <button
                  type="submit"
                  disabled={isActionRunning || !reprocessSlug.trim()}
                  className="py-2 px-4 rounded-xl bg-white text-slate-950 text-xs font-semibold hover:bg-slate-200 disabled:opacity-50 transition-all"
                >
                  Reprocess
                </button>
              </div>
            </form>
          </div>
        </div>

        {/* 4. Recent Pipeline Runs Table */}
        <div className="p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Clock className="w-5 h-5 text-emerald-400" />
              <h2 className="text-base font-semibold text-white">Recent Pipeline Executions</h2>
            </div>
            <span className="text-[11px] text-slate-400">Telemetry history (last 20 runs)</span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[11px] text-slate-400 border-b border-white/[0.06] uppercase tracking-wider">
                <tr>
                  <th className="pb-3 font-medium">Timestamp</th>
                  <th className="pb-3 font-medium">Pipeline</th>
                  <th className="pb-3 font-medium">Topic Slug</th>
                  <th className="pb-3 font-medium">Total Duration</th>
                  <th className="pb-3 font-medium">Stage Breakdown</th>
                  <th className="pb-3 font-medium text-right">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {pipelineMetrics && pipelineMetrics.recent_runs.length > 0 ? (
                  pipelineMetrics.recent_runs.map((run) => (
                    <tr key={run.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="py-3 text-slate-400 font-mono text-[11px]">
                        {formatTimeAgo(run.timestamp)}
                      </td>
                      <td className="py-3 text-slate-300 font-medium font-mono text-[11px]">
                        {run.pipeline_name}
                      </td>
                      <td className="py-3 text-white font-medium">
                        <Link
                          href={`/topics/${run.topic_slug}`}
                          className="hover:text-indigo-400 transition-colors"
                        >
                          {run.topic_slug}
                        </Link>
                      </td>
                      <td className="py-3 text-indigo-300 font-mono font-bold">
                        {run.total_duration_ms} ms
                      </td>
                      <td className="py-3">
                        <div className="flex flex-wrap gap-1">
                          {Object.entries(run.stages_ms || {}).map(([stage, ms]) => (
                            <span
                              key={stage}
                              className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-900 border border-white/[0.06] text-slate-400"
                            >
                              {stage.split("_")[0]}: {ms}ms
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="py-3 text-right">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                            run.status === "success"
                              ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                              : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                          }`}
                        >
                          {run.status.toUpperCase()}
                        </span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-slate-500 text-xs">
                      No recent pipeline runs recorded yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
