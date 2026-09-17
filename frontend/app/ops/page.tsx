"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowLeft,
  Bell,
  BellRing,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Clock,
  Cpu,
  Database,
  Filter,
  Globe,
  History,
  Info,
  Key,
  Layers,
  Pause,
  Play,
  RefreshCw,
  Server,
  Shield,
  ShieldCheck,
  Sliders,
  Terminal,
  TrendingUp,
  XCircle,
  Zap,
} from "lucide-react";

import {
  getOpsAlerts,
  getOpsBackups,
  getOpsHistory,
  getOpsOverview,
  getOpsPipelineMetrics,
  getOpsSourceHealth,
  getOpsWorkerMetrics,
  getResourceUsage,
  getResourceBudgets,
  getSLOStatus,
  getPlatformMetrics,
  RateLimitError,
  triggerOpsAlertEvaluate,
  triggerOpsCreateBackup,
  triggerOpsRefreshTopic,
  triggerOpsReprocessTopic,
  triggerOpsTrending,
  triggerOpsVerifyBackup,
} from "@/lib/api";
import {
  AlertInstance,
  AlertSeverity,
  AlertSummary,
  BackupRecordItem,
  HealthState,
  OpsBackupsResponse,
  OpsHistoryItem,
  OpsHistoryResponse,
  OpsOverview,
  OpsPipelineMetrics,
  OpsSourceHealth,
  OpsWorkerMetrics,
  ResourceUsageResponse,
  ResourceBudgetsResponse,
  SLOSummaryResponse,
  PlatformMetricsResponse,
} from "@/lib/types";
import { formatDate, formatTimeAgo } from "@/lib/utils";

export default function OperationsPage() {
  const [overview, setOverview] = useState<OpsOverview | null>(null);
  const [pipelineMetrics, setPipelineMetrics] = useState<OpsPipelineMetrics | null>(null);
  const [sourceHealth, setSourceHealth] = useState<OpsSourceHealth | null>(null);
  const [workerMetrics, setWorkerMetrics] = useState<OpsWorkerMetrics | null>(null);
  const [alertsSummary, setAlertsSummary] = useState<AlertSummary | null>(null);
  const [sloSummary, setSloSummary] = useState<SLOSummaryResponse | null>(null);
  const [platformMetrics, setPlatformMetrics] = useState<PlatformMetricsResponse | null>(null);

  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [opsApiKey, setOpsApiKey] = useState<string>("");
  const [showKeyInput, setShowKeyInput] = useState<boolean>(false);
  const [showAlertHistory, setShowAlertHistory] = useState<boolean>(false);

  // Operational History / Audit State
  const [historyResponse, setHistoryResponse] = useState<OpsHistoryResponse | null>(null);
  const [historyType, setHistoryType] = useState<string>("all");
  const [historyStatus, setHistoryStatus] = useState<string>("");
  const [historyPage, setHistoryPage] = useState<number>(1);
  const [isHistoryLoading, setIsHistoryLoading] = useState<boolean>(false);

  // Control action state
  const [reprocessSlug, setReprocessSlug] = useState<string>("");
  const [actionMessage, setActionMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [isActionRunning, setIsActionRunning] = useState<boolean>(false);

  // Backup & Disaster Recovery State
  const [backupsResponse, setBackupsResponse] = useState<OpsBackupsResponse | null>(null);
  const [isBackupsLoading, setIsBackupsLoading] = useState<boolean>(false);
  const [isCreatingBackup, setIsCreatingBackup] = useState<boolean>(false);
  const [verifyingBackupId, setVerifyingBackupId] = useState<string | null>(null);

  // Resource Governance State
  const [resourceUsage, setResourceUsage] = useState<ResourceUsageResponse | null>(null);
  const [resourceBudgets, setResourceBudgets] = useState<ResourceBudgetsResponse | null>(null);

  const loadBackups = async () => {
    try {
      setIsBackupsLoading(true);
      const res = await getOpsBackups(15);
      setBackupsResponse(res);
    } catch (err: any) {
      console.error("Failed to load backup records:", err);
    } finally {
      setIsBackupsLoading(false);
    }
  };

  const handleCreateBackup = async (dryRun: boolean = false) => {
    setIsCreatingBackup(true);
    setActionMessage(null);
    try {
      const res = await triggerOpsCreateBackup(opsApiKey, dryRun);
      setActionMessage({
        type: "success",
        text: dryRun
          ? "Dry-run backup validation succeeded! Ready for logical dump."
          : `Backup created successfully! ID: ${res.backup_id} (${res.size_human || res.filename})`,
      });
      loadBackups();
    } catch (err: any) {
      setActionMessage({ type: "error", text: err.message || "Failed to create database backup" });
    } finally {
      setIsCreatingBackup(false);
    }
  };

  const handleVerifyBackup = async (backupId: string) => {
    setVerifyingBackupId(backupId);
    setActionMessage(null);
    try {
      const res = await triggerOpsVerifyBackup(backupId, opsApiKey);
      setActionMessage({
        type: "success",
        text: `Backup ${backupId} verified successfully! SHA-256 integrity check passed.`,
      });
      loadBackups();
    } catch (err: any) {
      setActionMessage({ type: "error", text: err.message || `Failed to verify backup ${backupId}` });
    } finally {
      setVerifyingBackupId(null);
    }
  };

  const loadAllTelemetry = async () => {
    try {
      setIsRefreshing(true);
      const [ov, pm, sh, wm, al, slos, pMetrics] = await Promise.all([
        getOpsOverview(),
        getOpsPipelineMetrics(),
        getOpsSourceHealth(),
        getOpsWorkerMetrics(),
        getOpsAlerts(),
        getSLOStatus().catch(() => null),
        getPlatformMetrics().catch(() => null),
      ]);
      setOverview(ov);
      setPipelineMetrics(pm);
      setSourceHealth(sh);
      setWorkerMetrics(wm);
      setAlertsSummary(al);
      if (slos) setSloSummary(slos);
      if (pMetrics) setPlatformMetrics(pMetrics);
      loadBackups();
      // Load resource governance data
      try {
        const [ru, rb] = await Promise.all([
          getResourceUsage(opsApiKey || undefined),
          getResourceBudgets(opsApiKey || undefined),
        ]);
        setResourceUsage(ru);
        setResourceBudgets(rb);
      } catch (err: any) {
        console.error("Failed to load resource governance data:", err);
      }
      setLastUpdated(new Date());
    } catch (err: any) {
      console.error("Failed to load operations telemetry:", err);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  const handleEvaluateAlerts = async () => {
    setIsActionRunning(true);
    setActionMessage(null);
    try {
      const res = await triggerOpsAlertEvaluate(opsApiKey);
      setAlertsSummary(res);
      setActionMessage({
        type: "success",
        text: `Alert evaluation cycle completed. ${res.active_count} active alerts, ${res.resolved_count} resolved.`,
      });
      loadAllTelemetry();
    } catch (err: any) {
      if (err instanceof RateLimitError) {
        setActionMessage({ type: "error", text: `Rate limited. Retry in ${err.retryAfter}s.` });
      } else {
        setActionMessage({ type: "error", text: err.message || "Failed to trigger alert evaluation" });
      }
    } finally {
      setIsActionRunning(false);
    }
  };

  const loadHistory = async () => {
    try {
      setIsHistoryLoading(true);
      const res = await getOpsHistory({
        type: historyType,
        status: historyStatus || undefined,
        page: historyPage,
        limit: 15,
      });
      setHistoryResponse(res);
    } catch (err: any) {
      console.error("Failed to load operations history:", err);
    } finally {
      setIsHistoryLoading(false);
    }
  };

  useEffect(() => {
    loadAllTelemetry();
  }, []);

  useEffect(() => {
    loadHistory();
  }, [historyType, historyStatus, historyPage]);

  // Auto-refresh interval (every 10 seconds)
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      loadAllTelemetry();
      loadHistory();
    }, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh, historyType, historyStatus, historyPage]);

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
      if (err instanceof RateLimitError) {
        setActionMessage({ type: "error", text: `Rate limited. Retry in ${err.retryAfter}s.` });
      } else {
        setActionMessage({ type: "error", text: err.message || "Failed to trigger trending discovery" });
      }
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

  const getSeverityBadge = (severity: AlertSeverity | string) => {
    switch (severity) {
      case "critical":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider bg-rose-500/20 text-rose-300 border border-rose-500/30 shadow-sm shadow-rose-500/20">
            <AlertOctagon className="w-3 h-3 text-rose-400" />
            Critical
          </span>
        );
      case "warning":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-300 border border-amber-500/30 shadow-sm shadow-amber-500/20">
            <AlertTriangle className="w-3 h-3 text-amber-400" />
            Warning
          </span>
        );
      case "info":
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider bg-blue-500/20 text-blue-300 border border-blue-500/30">
            <Info className="w-3 h-3 text-blue-400" />
            Info
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
              Internal system telemetry, alerts & incident readiness, source health status, and operational controls.
            </p>
          </div>

          {/* Action Bar */}
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={handleEvaluateAlerts}
              disabled={isActionRunning}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border bg-surface-light text-slate-300 border-surface-border hover:text-white hover:border-indigo-500/40 disabled:opacity-50 transition-all"
            >
              <BellRing className="w-3.5 h-3.5 text-indigo-400" />
              Evaluate Alerts
            </button>

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

        {/* 2. Production Alerting & Incident Readiness */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Active Alerts & Resolved History (2 cols) */}
          <div className="lg:col-span-2 p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Bell className="w-5 h-5 text-indigo-400" />
                <h2 className="text-base font-semibold text-white">Production Alerts Engine</h2>
                {alertsSummary && alertsSummary.active_count > 0 && (
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30">
                    {alertsSummary.active_count} Active
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowAlertHistory(!showAlertHistory)}
                  className="text-xs text-indigo-400 hover:text-indigo-300 underline font-medium"
                >
                  {showAlertHistory ? "Show Active Alerts" : `View History (${alertsSummary?.resolved_count || 0})`}
                </button>
              </div>
            </div>

            {!showAlertHistory ? (
              <div className="space-y-3">
                {alertsSummary && alertsSummary.active_alerts.length > 0 ? (
                  alertsSummary.active_alerts.map((alert) => (
                    <div
                      key={alert.id}
                      className="p-4 rounded-xl bg-slate-900/80 border border-rose-500/20 shadow-sm shadow-rose-500/5 space-y-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          {getSeverityBadge(alert.severity)}
                          <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-slate-800 text-slate-300 border border-white/5">
                            {alert.component}
                          </span>
                        </div>
                        <span className="text-[11px] font-mono text-slate-400">
                          {alert.occurrence_count > 1 ? `${alert.occurrence_count} occurrences` : "1 occurrence"}
                        </span>
                      </div>
                      <p className="text-xs text-slate-200">{alert.message}</p>
                      <div className="flex items-center justify-between text-[10px] text-slate-500 pt-1 border-t border-white/[0.04]">
                        <span>First seen: {formatTimeAgo(alert.first_seen)}</span>
                        <span>Last trigger: {formatTimeAgo(alert.last_seen)}</span>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="p-6 rounded-xl bg-slate-900/40 border border-white/[0.04] text-center space-y-2">
                    <ShieldCheck className="w-8 h-8 text-emerald-400 mx-auto opacity-80" />
                    <p className="text-xs font-semibold text-slate-200">No Active Alerts</p>
                    <p className="text-[11px] text-slate-400 max-w-md mx-auto">
                      All evaluated operational invariants (DB connectivity, worker cycle, source error rates, pipeline latency, LLM endpoints) are nominal.
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <div className="space-y-3">
                {alertsSummary && alertsSummary.resolved_alerts.length > 0 ? (
                  alertsSummary.resolved_alerts.map((alert) => (
                    <div
                      key={alert.id + (alert.resolved_at || "")}
                      className="p-3.5 rounded-xl bg-slate-900/40 border border-white/[0.04] space-y-1.5 opacity-80 hover:opacity-100 transition-opacity"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="px-2 py-0.5 rounded text-[10px] font-semibold uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            Resolved
                          </span>
                          <span className="text-xs font-mono text-slate-300">{alert.component}</span>
                        </div>
                        <span className="text-[10px] font-mono text-slate-500">
                          Resolved {alert.resolved_at ? formatTimeAgo(alert.resolved_at) : ""}
                        </span>
                      </div>
                      <p className="text-xs text-slate-400">{alert.message}</p>
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-slate-500 py-6 text-center">
                    No resolved alert history recorded in current session.
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Incident Readiness Context (1 col) */}
          <div className="p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-5 h-5 text-emerald-400" />
                <h2 className="text-base font-semibold text-white">Incident Readiness</h2>
              </div>
              <span className="text-[11px] font-mono text-slate-400">
                {overview?.incident_readiness?.readiness_state === "ready" ? "READY" : "DEGRADED"}
              </span>
            </div>

            <div className="space-y-2.5 text-xs">
              <div className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] flex items-center justify-between">
                <span className="text-slate-400">Readiness Probe:</span>
                <span className="font-semibold text-emerald-400 flex items-center gap-1">
                  <CheckCircle className="w-3.5 h-3.5" />
                  {overview?.incident_readiness?.readiness_state?.toUpperCase() || "READY"}
                </span>
              </div>

              <div className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] flex items-center justify-between">
                <span className="text-slate-400">Current Worker Cycle:</span>
                <span className="font-mono text-slate-200">
                  #{overview?.incident_readiness?.current_worker_cycle || overview?.worker?.total_runs || 0}
                </span>
              </div>

              <div className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] flex items-center justify-between">
                <span className="text-slate-400">Last Database Ping:</span>
                <span className="font-mono text-emerald-400">
                  {overview?.incident_readiness?.last_database_check?.latency_ms || overview?.database?.latency_ms || 0} ms
                </span>
              </div>

              <div className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-1.5">
                <span className="text-slate-400 block">Last Source Successes:</span>
                <div className="space-y-1 text-[11px]">
                  <div className="flex justify-between font-mono">
                    <span className="text-slate-500">Google News:</span>
                    <span className="text-slate-300">
                      {overview?.incident_readiness?.last_successful_ingestion?.google_news
                        ? formatTimeAgo(overview.incident_readiness.last_successful_ingestion.google_news)
                        : "Nominal"}
                    </span>
                  </div>
                  <div className="flex justify-between font-mono">
                    <span className="text-slate-500">Reddit:</span>
                    <span className="text-slate-300">
                      {overview?.incident_readiness?.last_successful_ingestion?.reddit
                        ? formatTimeAgo(overview.incident_readiness.last_successful_ingestion.reddit)
                        : "Nominal"}
                    </span>
                  </div>
                  <div className="flex justify-between font-mono">
                    <span className="text-slate-500">X (Scraper):</span>
                    <span className="text-slate-300">
                      {overview?.incident_readiness?.last_successful_ingestion?.x
                        ? formatTimeAgo(overview.incident_readiness.last_successful_ingestion.x)
                        : "Nominal"}
                    </span>
                  </div>
                </div>
              </div>

              <div className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] flex items-center justify-between">
                <span className="text-slate-400">Last Pipeline Run:</span>
                <span className="font-mono text-slate-300">
                  {overview?.incident_readiness?.last_successful_pipeline
                    ? formatTimeAgo(overview.incident_readiness.last_successful_pipeline)
                    : "No runs"}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* 3. Middle Section: Ingestion Source Health & Pipeline Metrics */}
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

        {/* 4. Disaster Recovery & Backup Readiness */}
        <div className="p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-5">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-5 h-5 text-emerald-400" />
              <div>
                <h2 className="text-base font-semibold text-white flex items-center gap-2">
                  Disaster Recovery & Backup Readiness
                  {backupsResponse && (
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-medium ${
                        backupsResponse.summary.failed_records === 0 && backupsResponse.summary.successful_records > 0
                          ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                          : backupsResponse.summary.successful_records === 0
                          ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                          : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                      }`}
                    >
                      {backupsResponse.summary.failed_records === 0 && backupsResponse.summary.successful_records > 0
                        ? "HEALTHY"
                        : backupsResponse.summary.successful_records === 0
                        ? "INITIALIZING"
                        : "WARNINGS"}
                    </span>
                  )}
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Automated PostgreSQL logical snapshots, SHA-256 integrity validation, and retention lifecycle.
                </p>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center gap-2">
              <button
                onClick={() => handleCreateBackup(true)}
                disabled={isCreatingBackup || isActionRunning}
                className="px-3 py-1.5 rounded-xl bg-slate-900 border border-white/10 hover:bg-white/[0.04] text-slate-300 text-xs font-semibold disabled:opacity-40 transition-all"
                title="Test backup parameters without writing database dump"
              >
                Dry-Run Check
              </button>
              <button
                onClick={() => handleCreateBackup(false)}
                disabled={isCreatingBackup || isActionRunning}
                className="px-3.5 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold disabled:opacity-40 transition-all shadow-md shadow-emerald-600/20 flex items-center gap-1.5"
              >
                <Database className="w-3.5 h-3.5" />
                {isCreatingBackup ? "Creating Dump..." : "Create Backup Now"}
              </button>
              <button
                onClick={loadBackups}
                disabled={isBackupsLoading}
                className="p-1.5 rounded-xl bg-slate-900 border border-white/10 hover:bg-white/[0.04] text-slate-400 transition-all"
                title="Refresh Backups List"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isBackupsLoading ? "animate-spin text-indigo-400" : ""}`} />
              </button>
            </div>
          </div>

          {/* Backup KPI Summary Grid */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="p-3.5 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-1">
              <span className="text-[11px] text-slate-400 flex items-center gap-1">
                <Clock className="w-3.5 h-3.5 text-indigo-400" /> Latest Successful Backup
              </span>
              <span className="text-sm font-bold text-white font-mono block">
                {backupsResponse?.summary.latest_successful_backup
                  ? formatTimeAgo(backupsResponse.summary.latest_successful_backup)
                  : "No backups recorded"}
              </span>
              <span className="text-[10px] text-slate-500 block truncate">
                {backupsResponse?.summary.latest_successful_backup
                  ? formatDate(backupsResponse.summary.latest_successful_backup)
                  : "Scheduler awaiting trigger"}
              </span>
            </div>

            <div className="p-3.5 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-1">
              <span className="text-[11px] text-slate-400 flex items-center gap-1">
                <CheckCircle className="w-3.5 h-3.5 text-emerald-400" /> Verification Integrity
              </span>
              <span className="text-sm font-bold text-emerald-400 font-mono block">
                {backupsResponse?.summary.verified_records || 0} / {backupsResponse?.summary.total_records || 0} Verified
              </span>
              <span className="text-[10px] text-slate-500 block">
                {backupsResponse?.summary.failed_records || 0} failed dump records
              </span>
            </div>

            <div className="p-3.5 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-1">
              <span className="text-[11px] text-slate-400 flex items-center gap-1">
                <Database className="w-3.5 h-3.5 text-cyan-400" /> Total Storage Footprint
              </span>
              <span className="text-sm font-bold text-white font-mono block">
                {backupsResponse?.summary.total_size_human || "0 B"}
              </span>
              <span className="text-[10px] text-slate-500 block">
                {backupsResponse?.total_count || 0} snapshots cataloged
              </span>
            </div>

            <div className="p-3.5 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-1">
              <span className="text-[11px] text-slate-400 flex items-center gap-1">
                <Sliders className="w-3.5 h-3.5 text-amber-400" /> Retention & Schedule
              </span>
              <span className="text-sm font-bold text-white font-mono block">
                Keep {backupsResponse?.config.retention_count || 7} / {backupsResponse?.config.retention_days || 30}d
              </span>
              <span className="text-[10px] text-slate-500 block">
                Cadence: {backupsResponse?.config.backup_interval_hours || 24}h • {backupsResponse?.config.compression ? "gzip" : "raw"}
              </span>
            </div>
          </div>

          {/* Backups Catalog Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[11px] text-slate-400 border-b border-white/[0.06] uppercase tracking-wider">
                <tr>
                  <th className="pb-3 font-medium">Backup ID / Timestamp</th>
                  <th className="pb-3 font-medium">Filename</th>
                  <th className="pb-3 font-medium">Size</th>
                  <th className="pb-3 font-medium">SHA-256 Checksum</th>
                  <th className="pb-3 font-medium">Integrity State</th>
                  <th className="pb-3 font-medium text-right">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {backupsResponse && backupsResponse.backups.length > 0 ? (
                  backupsResponse.backups.map((b) => (
                    <tr key={b.backup_id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="py-3">
                        <div className="space-y-0.5">
                          <span className="text-white font-mono font-semibold block text-[11px]">
                            {b.backup_id}
                          </span>
                          <span className="text-slate-500 text-[10px]">
                            {formatTimeAgo(b.created_at)} ({formatDate(b.created_at)})
                          </span>
                        </div>
                      </td>
                      <td className="py-3 font-mono text-[11px] text-slate-300">
                        {b.filename}
                      </td>
                      <td className="py-3 font-mono text-cyan-300 font-semibold">
                        {b.size_human || `${b.size_bytes} B`}
                      </td>
                      <td className="py-3">
                        {b.checksum ? (
                          <span
                            className="font-mono text-[10px] text-slate-400 bg-slate-900 px-2 py-0.5 rounded border border-white/[0.06] truncate max-w-[120px] inline-block"
                            title={`SHA-256: ${b.checksum}`}
                          >
                            {b.checksum.slice(0, 12)}...
                          </span>
                        ) : (
                          <span className="text-slate-600 text-[10px]">N/A</span>
                        )}
                      </td>
                      <td className="py-3">
                        {b.is_verified ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            <CheckCircle className="w-3 h-3" /> VERIFIED
                          </span>
                        ) : (
                          <button
                            onClick={() => handleVerifyBackup(b.backup_id)}
                            disabled={verifyingBackupId === b.backup_id || isActionRunning}
                            className="px-2 py-0.5 rounded bg-slate-900 border border-white/10 text-slate-300 hover:text-white hover:bg-white/[0.06] text-[10px] font-semibold transition-all flex items-center gap-1"
                          >
                            {verifyingBackupId === b.backup_id ? (
                              <>
                                <RefreshCw className="w-2.5 h-2.5 animate-spin" /> Verifying...
                              </>
                            ) : (
                              <>
                                <Shield className="w-2.5 h-2.5" /> Verify Integrity
                              </>
                            )}
                          </button>
                        )}
                      </td>
                      <td className="py-3 text-right">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                            b.status === "success"
                              ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                              : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                          }`}
                        >
                          {b.status.toUpperCase()}
                        </span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-slate-500 text-xs">
                      {isBackupsLoading
                        ? "Loading database backup records..."
                        : "No database backups recorded yet. Click 'Create Backup Now' to take an initial snapshot."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Disaster Recovery / Restore Runbook Banner */}
          <div className="p-4 rounded-xl bg-slate-900/90 border border-indigo-500/20 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-indigo-400 text-xs font-semibold">
                <Terminal className="w-4 h-4" /> Disaster Recovery Restoration Runbook
              </div>
              <p className="text-[11px] text-slate-400">
                To prevent catastrophic data loss, database restoration is intentionally locked from HTTP APIs and strictly requires authenticated CLI execution.
              </p>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-black/60 border border-white/10 font-mono text-[11px] text-emerald-400 select-all whitespace-nowrap">
              python -m app.database.restore --backup &lt;file&gt; --confirm
            </div>
          </div>
        </div>

        {/* 5. Recent Pipeline Runs Table */}
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

        {/* 5. Persistent Operational History & Auditability Log */}
        <div className="p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-5">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <History className="w-5 h-5 text-indigo-400" />
              <div>
                <h2 className="text-base font-semibold text-white flex items-center gap-2">
                  Persistent Operational History & Audit Log
                  {historyResponse && (
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-medium bg-white/[0.06] text-slate-300 border border-white/10">
                      {historyResponse.total_count} records
                    </span>
                  )}
                </h2>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  PostgreSQL audit history preserved across server restarts with configurable retention.
                </p>
              </div>
            </div>

            {/* Filter Controls */}
            <div className="flex items-center gap-2 flex-wrap">
              <div className="flex bg-slate-900/80 p-1 rounded-xl border border-white/[0.06] text-xs">
                {[
                  { id: "all", label: "All" },
                  { id: "pipeline_runs", label: "Pipelines" },
                  { id: "source_executions", label: "Sources" },
                  { id: "worker_cycles", label: "Workers" },
                  { id: "alerts", label: "Alerts" },
                ].map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => {
                      setHistoryType(tab.id);
                      setHistoryPage(1);
                    }}
                    className={`px-3 py-1 rounded-lg text-[11px] font-medium transition-all ${
                      historyType === tab.id
                        ? "bg-indigo-600 text-white shadow-sm"
                        : "text-slate-400 hover:text-white"
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {/* Status filter */}
              <select
                value={historyStatus}
                onChange={(e) => {
                  setHistoryStatus(e.target.value);
                  setHistoryPage(1);
                }}
                className="px-2.5 py-1.5 rounded-xl bg-slate-900 border border-white/10 text-slate-300 text-xs focus:outline-none focus:border-indigo-500"
              >
                <option value="">All Statuses</option>
                <option value="success">Success</option>
                <option value="failed">Failed</option>
                <option value="timeout">Timeout</option>
                <option value="active">Active</option>
                <option value="resolved">Resolved</option>
              </select>

              <button
                onClick={() => loadHistory()}
                disabled={isHistoryLoading}
                className="p-1.5 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-slate-300 border border-white/10 transition-colors"
                title="Refresh audit history"
              >
                <RefreshCw className={`w-4 h-4 ${isHistoryLoading ? "animate-spin text-indigo-400" : ""}`} />
              </button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[11px] text-slate-400 border-b border-white/[0.06] uppercase tracking-wider">
                <tr>
                  <th className="pb-3 font-medium">Type</th>
                  <th className="pb-3 font-medium">Recorded At</th>
                  <th className="pb-3 font-medium">Entity / Operation</th>
                  <th className="pb-3 font-medium">Audit Metadata & Metrics</th>
                  <th className="pb-3 font-medium text-right">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {historyResponse && historyResponse.items.length > 0 ? (
                  historyResponse.items.map((item, idx) => {
                    const timeVal = item.started_at || item.last_seen || item.timestamp;
                    return (
                      <tr key={idx} className="hover:bg-white/[0.02] transition-colors">
                        <td className="py-3">
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-mono font-semibold ${
                              item.record_type === "pipeline_run"
                                ? "bg-indigo-500/10 text-indigo-400 border border-indigo-500/20"
                                : item.record_type === "source_execution"
                                ? "bg-cyan-500/10 text-cyan-400 border border-cyan-500/20"
                                : item.record_type === "worker_cycle"
                                ? "bg-purple-500/10 text-purple-400 border border-purple-500/20"
                                : "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                            }`}
                          >
                            {item.record_type === "pipeline_run"
                              ? "PIPELINE"
                              : item.record_type === "source_execution"
                              ? "SOURCE"
                              : item.record_type === "worker_cycle"
                              ? "WORKER"
                              : "ALERT"}
                          </span>
                        </td>
                        <td className="py-3 text-slate-400 font-mono text-[11px]">
                          {timeVal ? formatTimeAgo(timeVal) : "—"}
                        </td>
                        <td className="py-3 text-white font-medium">
                          {item.record_type === "pipeline_run" && (
                            <div>
                              <span className="font-mono text-slate-300">{item.pipeline_type}</span>
                              {item.topic_slug && (
                                <span className="block text-[11px] text-indigo-400 font-normal">
                                  topic: {item.topic_slug}
                                </span>
                              )}
                            </div>
                          )}
                          {item.record_type === "source_execution" && (
                            <div>
                              <span className="font-semibold text-cyan-300">{item.source}</span>
                              <span className="block text-[11px] text-slate-400 font-mono">
                                op: {item.operation}
                              </span>
                            </div>
                          )}
                          {item.record_type === "worker_cycle" && (
                            <div className="font-mono text-slate-300">
                              Cycle #{item.cycle_id}
                            </div>
                          )}
                          {item.record_type === "alert" && (
                            <div>
                              <span className="font-semibold text-amber-300">{item.rule_name}</span>
                              <span className="block text-[11px] text-slate-400 font-mono">
                                {item.component}
                              </span>
                            </div>
                          )}
                        </td>
                        <td className="py-3 text-slate-300">
                          {item.record_type === "pipeline_run" && (
                            <div className="flex flex-wrap gap-2 items-center text-[11px]">
                              <span className="text-indigo-300 font-mono font-semibold">
                                {item.duration_ms} ms
                              </span>
                              {item.sample_size !== undefined && item.sample_size > 0 && (
                                <span className="text-slate-400">items: {item.sample_size}</span>
                              )}
                              {item.perspective_count !== undefined && item.perspective_count > 0 && (
                                <span className="text-slate-400">perspectives: {item.perspective_count}</span>
                              )}
                              {item.error_type && (
                                <span className="text-rose-400 truncate max-w-xs">{item.error_type}</span>
                              )}
                            </div>
                          )}
                          {item.record_type === "source_execution" && (
                            <div className="flex flex-wrap gap-2 items-center text-[11px]">
                              <span className="text-cyan-300 font-mono font-semibold">
                                {item.duration_ms} ms
                              </span>
                              {item.item_count !== undefined && item.item_count > 0 && (
                                <span className="text-slate-400">items: {item.item_count}</span>
                              )}
                              {item.error_type && (
                                <span className="text-rose-400 truncate max-w-xs">{item.error_type}</span>
                              )}
                            </div>
                          )}
                          {item.record_type === "worker_cycle" && (
                            <div className="flex flex-wrap gap-2 items-center text-[11px]">
                              <span className="text-purple-300 font-mono font-semibold">
                                {item.duration_ms} ms
                              </span>
                              <span className="text-slate-400">
                                refreshed: {item.topics_refreshed}/{item.topics_considered}
                              </span>
                              {item.topics_failed !== undefined && item.topics_failed > 0 && (
                                <span className="text-rose-400">failed: {item.topics_failed}</span>
                              )}
                            </div>
                          )}
                          {item.record_type === "alert" && (
                            <div className="text-[11px] space-y-0.5">
                              <p className="text-slate-300">{item.message}</p>
                              <div className="flex gap-2 text-[10px] text-slate-500">
                                <span>occurrences: {item.occurrence_count}</span>
                                {item.resolved_at && <span>resolved: {formatTimeAgo(item.resolved_at)}</span>}
                              </div>
                            </div>
                          )}
                        </td>
                        <td className="py-3 text-right">
                          <span
                            className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                              item.status === "success" || item.status === "resolved"
                                ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                                : item.status === "active"
                                ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                                : item.status === "timeout"
                                ? "bg-orange-500/10 text-orange-400 border border-orange-500/20"
                                : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                            }`}
                          >
                            {(item.status || "UNKNOWN").toUpperCase()}
                          </span>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-slate-500 text-xs">
                      {isHistoryLoading
                        ? "Loading operational history from PostgreSQL..."
                        : "No persistent operational history records found for the selected filter."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination Footer */}
          {historyResponse && historyResponse.total_pages > 1 && (
            <div className="flex items-center justify-between pt-4 border-t border-white/[0.06] text-xs text-slate-400">
              <div>
                Page {historyResponse.page} of {historyResponse.total_pages} ({historyResponse.total_count} total events)
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setHistoryPage((p) => Math.max(1, p - 1))}
                  disabled={!historyResponse.has_prev || isHistoryLoading}
                  className="px-3 py-1.5 rounded-xl bg-slate-900 border border-white/10 hover:bg-white/[0.04] disabled:opacity-40 transition-all flex items-center gap-1"
                >
                  <ChevronLeft className="w-3.5 h-3.5" /> Previous
                </button>
                <button
                  onClick={() => setHistoryPage((p) => Math.min(historyResponse.total_pages, p + 1))}
                  disabled={!historyResponse.has_next || isHistoryLoading}
                  className="px-3 py-1.5 rounded-xl bg-slate-900 border border-white/10 hover:bg-white/[0.04] disabled:opacity-40 transition-all flex items-center gap-1"
                >
                  Next <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>

        {/* 7. Service Level Objectives (SLOs) & Reliability Engineering */}
        <div className="p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
                <Activity className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-base font-semibold text-white flex items-center gap-2">
                  Service Level Objectives (SLOs) & Error Budgets
                  {sloSummary && (
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                        sloSummary.overall_status === "HEALTHY"
                          ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                          : sloSummary.overall_status === "DEGRADED"
                          ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                          : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                      }`}
                    >
                      {sloSummary.overall_status}
                    </span>
                  )}
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Explicit reliability targets, real-time SLI tracking, error budget consumption, and burn rates.
                </p>
              </div>
            </div>

            {/* Health Score Pill */}
            {sloSummary && (
              <div className="flex items-center gap-3 bg-slate-900/80 px-4 py-2 rounded-xl border border-white/[0.06]">
                <div className="text-right">
                  <span className="text-[10px] text-slate-400 block">Health Score</span>
                  <span className="text-sm font-mono font-bold text-emerald-400">
                    {sloSummary.health_score_percent}%
                  </span>
                </div>
                <div className="w-px h-6 bg-white/10" />
                <div className="text-left text-[11px] text-slate-400 space-y-0.5">
                  <span className="text-emerald-400 font-mono block">{sloSummary.compliant} Compliant</span>
                  <span className="text-slate-500 font-mono block">{sloSummary.total_slos} Total Targets</span>
                </div>
              </div>
            )}
          </div>

          {/* 9 SLO Cards Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {sloSummary &&
              Object.entries(sloSummary.evaluations).map(([key, evalItem]) => {
                const isCompliant = evalItem.status === "COMPLIANT";
                const isWarning = evalItem.status === "WARNING";
                const statusBadge = isCompliant
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : isWarning
                  ? "bg-amber-500/10 text-amber-400 border-amber-500/20"
                  : "bg-rose-500/10 text-rose-400 border-rose-500/20";

                const budgetColor =
                  evalItem.error_budget_remaining_percent > 50
                    ? "from-emerald-500 to-teal-500"
                    : evalItem.error_budget_remaining_percent > 20
                    ? "from-amber-500 to-yellow-500"
                    : "from-rose-500 to-red-500";

                return (
                  <div
                    key={key}
                    className="p-4 rounded-xl bg-slate-900/60 border border-white/[0.04] hover:border-white/[0.08] transition-all space-y-3"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <span className="text-xs font-semibold text-white block">
                          {evalItem.display_name}
                        </span>
                        <span className="text-[10px] text-slate-400">
                          Target: {evalItem.target} {evalItem.unit}
                        </span>
                      </div>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase border ${statusBadge}`}>
                        {evalItem.status}
                      </span>
                    </div>

                    {/* Current vs Target metric */}
                    <div className="flex items-baseline justify-between pt-1">
                      <span className="text-xl font-bold font-mono text-white">
                        {evalItem.current_value}
                        <span className="text-xs font-normal text-slate-400 ml-1">{evalItem.unit}</span>
                      </span>
                      <span className="text-[11px] font-mono text-slate-400">
                        Burn Rate: <strong className={evalItem.burn_rate > 1.0 ? "text-amber-400" : "text-slate-300"}>{evalItem.burn_rate}x</strong>
                      </span>
                    </div>

                    {/* Error Budget Bar */}
                    <div className="space-y-1">
                      <div className="flex justify-between text-[10px] text-slate-400">
                        <span>Error Budget Remaining</span>
                        <span className="font-mono font-semibold text-slate-200">
                          {evalItem.error_budget_remaining_percent}%
                        </span>
                      </div>
                      <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                        <div
                          className={`h-full rounded-full bg-gradient-to-r ${budgetColor} transition-all duration-500`}
                          style={{ width: `${Math.min(100, Math.max(0, evalItem.error_budget_remaining_percent))}%` }}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
          </div>

          {/* Real-time Metric Latency Percentiles (P50, P90, P95, P99) */}
          {platformMetrics && (
            <div className="pt-4 border-t border-white/[0.06] space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                  <Cpu className="w-3.5 h-3.5 text-purple-400" />
                  Rolling Latency Percentiles & Histograms
                </span>
                <span className="text-[10px] font-mono text-slate-500">
                  Prometheus exposition endpoint: <code className="text-slate-400 font-mono">/metrics</code>
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {[
                  { key: "http_request_duration_ms", label: "HTTP API Gateway Latency", unit: "ms" },
                  { key: "pipeline_duration_ms", label: "Pipeline Execution Latency", unit: "ms" },
                  { key: "database_query_duration_ms", label: "Database Query Latency", unit: "ms" },
                ].map((item) => {
                  const m = platformMetrics.metrics[item.key];
                  const pcts = m?.samples?.[0]?.percentiles;
                  return (
                    <div key={item.key} className="p-3.5 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-2.5">
                      <div className="flex justify-between items-center">
                        <span className="text-xs font-semibold text-slate-200">{item.label}</span>
                        <span className="text-[10px] font-mono text-slate-400">{pcts?.count || 0} samples</span>
                      </div>
                      <div className="grid grid-cols-4 gap-1 text-center font-mono">
                        <div className="p-1.5 rounded bg-slate-950/60">
                          <span className="text-[9px] text-slate-500 block">P50</span>
                          <span className="text-xs font-bold text-slate-200">{pcts?.p50 || 0}</span>
                        </div>
                        <div className="p-1.5 rounded bg-slate-950/60">
                          <span className="text-[9px] text-slate-500 block">P90</span>
                          <span className="text-xs font-bold text-slate-200">{pcts?.p90 || 0}</span>
                        </div>
                        <div className="p-1.5 rounded bg-slate-950/60">
                          <span className="text-[9px] text-slate-500 block">P95</span>
                          <span className="text-xs font-bold text-indigo-400">{pcts?.p95 || 0}</span>
                        </div>
                        <div className="p-1.5 rounded bg-slate-950/60">
                          <span className="text-[9px] text-slate-500 block">P99</span>
                          <span className="text-xs font-bold text-purple-400">{pcts?.p99 || 0}</span>
                        </div>
                      </div>
                      <div className="flex justify-between text-[10px] text-slate-500 font-mono pt-1">
                        <span>Mean: {pcts?.mean || 0}ms</span>
                        <span>Min: {pcts?.min || 0}ms</span>
                        <span>Max: {pcts?.max || 0}ms</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* 8. Resource Governance Section */}
        <div className="p-6 rounded-2xl bg-surface-light border border-surface-border backdrop-blur-md space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Sliders className="w-5 h-5 text-cyan-400" />
              <h2 className="text-base font-semibold text-white">Resource Governance & Cost Controls</h2>
            </div>
            <div className="flex items-center gap-2 text-[11px]">
              {resourceUsage?.governance_backend && (
                <span
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full font-mono text-[10px] font-semibold border ${
                    resourceUsage.governance_backend.backend === "redis"
                      ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
                      : "bg-cyan-500/10 text-cyan-400 border-cyan-500/20"
                  }`}
                >
                  <Server className="w-3 h-3" />
                  Backend: {resourceUsage.governance_backend.backend.toUpperCase()}
                  {resourceUsage.governance_backend.fallback_active && (
                    <span className="text-amber-400 font-bold">(FALLBACK ACTIVE)</span>
                  )}
                </span>
              )}
              <span className="text-slate-400">Rate Limits • Budgets • Concurrency</span>
            </div>
          </div>

          {/* Rate Limits Grid */}
          {resourceUsage && (
            <div className="space-y-4">
              <span className="text-xs font-semibold text-slate-300 block">API Rate Limits</span>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {Object.entries(resourceUsage.rate_limits).map(([key, info]) => {
                  const statusColor =
                    info.utilization_pct >= 90
                      ? "from-rose-500 to-red-500"
                      : info.utilization_pct >= 70
                        ? "from-amber-500 to-yellow-500"
                        : "from-cyan-500 to-blue-500";
                  const statusBadge =
                    info.utilization_pct >= 90
                      ? "bg-rose-500/15 text-rose-300 border-rose-500/25"
                      : info.utilization_pct >= 70
                        ? "bg-amber-500/15 text-amber-300 border-amber-500/25"
                        : "bg-slate-800/40 text-slate-400 border-white/5";
                  return (
                    <div
                      key={key}
                      className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-2"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-mono text-slate-300 capitalize">
                          {key.replace(/[:_]/g, " ")}
                        </span>
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${statusBadge}`}
                        >
                          {info.current}/{info.limit}
                        </span>
                      </div>
                      <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                        <div
                          className={`h-full rounded-full bg-gradient-to-r ${statusColor} transition-all duration-500`}
                          style={{ width: `${Math.min(100, info.utilization_pct)}%` }}
                        />
                      </div>
                      <span className="text-[10px] text-slate-500">
                        {info.window_seconds}s window • {info.utilization_pct}% used
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Concurrency & Budget Status */}
          {resourceUsage && (
            <div className="space-y-4">
              <span className="text-xs font-semibold text-slate-300 block">Concurrency Slots & Budgets</span>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {Object.entries(resourceUsage.concurrency).map(([resource, info]) => {
                  const statusColor =
                    info.utilization_pct >= 90
                      ? "text-rose-400"
                      : info.utilization_pct >= 70
                        ? "text-amber-400"
                        : "text-emerald-400";
                  return (
                    <div
                      key={resource}
                      className="p-3.5 rounded-xl bg-slate-900/60 border border-white/[0.04] flex items-center justify-between"
                    >
                      <div>
                        <span className="text-[11px] text-slate-400 block capitalize">
                          {resource.replace(/_/g, " ")}
                        </span>
                        <span className={`text-sm font-bold font-mono ${statusColor}`}>
                          {info.current}/{info.limit}
                        </span>
                      </div>
                      <span className="text-[10px] text-slate-500">{info.utilization_pct}%</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Cost Tracking */}
          {resourceUsage && (
            <div className="space-y-4">
              <span className="text-xs font-semibold text-slate-300 block">Cost Tracking (Session)</span>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                {[
                  { label: "Embedding Calls", value: resourceUsage.cost_tracking.embedding_calls },
                  { label: "Embedding Items", value: resourceUsage.cost_tracking.embedding_items_total },
                  { label: "Synthesis Calls", value: resourceUsage.cost_tracking.synthesis_calls },
                  { label: "Est. Input Tokens", value: resourceUsage.cost_tracking.estimated_input_tokens.toLocaleString() },
                  { label: "Est. Output Tokens", value: resourceUsage.cost_tracking.estimated_output_tokens.toLocaleString() },
                  { label: "Pipelines Run", value: resourceUsage.cost_tracking.pipeline_invocations },
                ].map((stat) => (
                  <div
                    key={stat.label}
                    className="p-3 rounded-xl bg-slate-900/60 border border-white/[0.04] text-center"
                  >
                    <span className="text-[10px] text-slate-400 block mb-0.5">{stat.label}</span>
                    <span className="text-sm font-bold text-white font-mono">{stat.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Budget Utilization Warnings */}
          {resourceUsage && (
            <div className="space-y-4">
              <span className="text-xs font-semibold text-slate-300 block">Budget Utilization</span>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {Object.entries(resourceUsage.budget_utilization)
                  .filter(([_, info]) => info.limit > 0)
                  .map(([resource, info]) => {
                    const borderColor =
                      info.status === "critical"
                        ? "border-rose-500/30"
                        : info.status === "warning"
                          ? "border-amber-500/30"
                          : "border-white/[0.04]";
                    const statusIcon =
                      info.status === "critical" ? (
                        <AlertOctagon className="w-3.5 h-3.5 text-rose-400" />
                      ) : info.status === "warning" ? (
                        <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                      ) : (
                        <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />
                      );
                    return (
                      <div
                        key={resource}
                        className={`p-3 rounded-xl bg-slate-900/60 border ${borderColor} flex items-center justify-between`}
                      >
                        <div className="flex items-center gap-2">
                          {statusIcon}
                          <span className="text-[11px] text-slate-300 capitalize">
                            {resource.replace(/max_|_/g, (m) => (m === "_" ? " " : ""))}
                          </span>
                        </div>
                        <span className="text-[11px] font-mono text-slate-400">
                          {info.current}/{info.limit}
                        </span>
                      </div>
                    );
                  })}
              </div>
            </div>
          )}

          {/* External Source Governance */}
          {resourceUsage && (
            <div className="space-y-4">
              <span className="text-xs font-semibold text-slate-300 block">External API Request Governance</span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {Object.entries(resourceUsage.external_requests).map(([source, usage]) => {
                  const barColor =
                    usage.utilization_pct >= 90
                      ? "from-rose-500 to-red-500"
                      : usage.utilization_pct >= 70
                        ? "from-amber-500 to-yellow-500"
                        : "from-emerald-500 to-cyan-500";
                  return (
                    <div
                      key={source}
                      className="p-3.5 rounded-xl bg-slate-900/60 border border-white/[0.04] space-y-2"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-semibold text-white capitalize">
                          {source.replace(/_/g, " ")}
                        </span>
                        <div className="flex items-center gap-2 text-[10px] text-slate-400">
                          <span>Active: {usage.active_requests}/{usage.max_concurrent}</span>
                          <span>•</span>
                          <span>Hourly: {usage.hourly_used}/{usage.hourly_budget}</span>
                        </div>
                      </div>
                      <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                        <div
                          className={`h-full rounded-full bg-gradient-to-r ${barColor} transition-all duration-500`}
                          style={{ width: `${Math.min(100, usage.utilization_pct)}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between text-[10px] text-slate-500">
                        <span>Timeout: {usage.timeout_seconds}s</span>
                        <span>Max retries: {usage.max_retries}</span>
                        <span>{usage.utilization_pct}% hourly budget</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

