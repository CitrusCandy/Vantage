import {
  AlertSummary,
  CandidateTopic,
  OpsOverview,
  OpsPipelineMetrics,
  OpsSourceHealth,
  OpsWorkerMetrics,
  Perspective,
  ResourceBudgetsResponse,
  ResourceUsageResponse,
  Topic,
  TopicCreate,
  WorkerStatus,
} from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window === "undefined" ? "http://127.0.0.1:8000/api" : "/api");

// Minimum polling interval (ms) — prevents hammering the backend
const MIN_POLL_INTERVAL_MS = parseInt(
  process.env.NEXT_PUBLIC_MIN_POLL_INTERVAL_MS || "15000",
  10,
);

// Request deduplication: track in-flight requests by key
const _inflightControllers: Map<string, AbortController> = new Map();

/**
 * Cancel any in-flight request for the same deduplication key before starting a new one.
 * Returns an AbortController signal to pass to the request.
 */
function _deduplicatedSignal(key?: string): AbortSignal | undefined {
  if (!key) return undefined;
  const existing = _inflightControllers.get(key);
  if (existing) {
    existing.abort(); // Cancel stale request
  }
  const controller = new AbortController();
  _inflightControllers.set(key, controller);
  return controller.signal;
}

function _clearInflight(key?: string) {
  if (key) _inflightControllers.delete(key);
}

/** Custom error class for rate-limited responses. */
export class RateLimitError extends Error {
  retryAfter: number;
  constructor(retryAfter: number, detail: string) {
    super(detail);
    this.name = "RateLimitError";
    this.retryAfter = retryAfter;
  }
}

async function fetchJson<T>(
  endpoint: string,
  options: RequestInit = {},
  dedupKey?: string,
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;
  const signal = _deduplicatedSignal(dedupKey);
  const response = await fetch(url, {
    ...options,
    signal,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    cache: "no-store",
  });

  _clearInflight(dedupKey);

  if (response.status === 429) {
    const retryAfter = parseInt(response.headers.get("Retry-After") || "30", 10);
    let detail = `Rate limited. Retry after ${retryAfter}s.`;
    try {
      const err = await response.json();
      detail = err.detail || detail;
    } catch {
      // Ignore JSON parse errors for rate-limit responses
    }
    throw new RateLimitError(retryAfter, detail);
  }

  if (!response.ok) {
    let errorDetail = "API Request failed";
    try {
      const err = await response.json();
      errorDetail = err.detail || err.message || errorDetail;
    } catch {
      errorDetail = `HTTP ${response.status}: ${response.statusText}`;
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export async function getTopics(search?: string): Promise<Topic[]> {
  const query = search ? `?search=${encodeURIComponent(search)}` : "";
  return fetchJson<Topic[]>(`/topics${query}`, {}, "getTopics");
}

export async function getTrendingTopics(limit: number = 8, minScore: number = 0.0): Promise<Topic[]> {
  return fetchJson<Topic[]>(`/topics/trending?limit=${limit}&min_score=${minScore}`, {}, "getTrending");
}

export async function getTopicBySlug(slug: string): Promise<Topic> {
  return fetchJson<Topic>(`/topics/${encodeURIComponent(slug)}`);
}

export async function createTopic(data: TopicCreate): Promise<Topic> {
  return fetchJson<Topic>("/topics", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function triggerIngestion(slug: string, limitPerSource: number = 50): Promise<any> {
  return fetchJson(`/topics/${encodeURIComponent(slug)}/ingest?limit_per_source=${limitPerSource}`, {
    method: "POST",
  });
}

export async function triggerClustering(slug: string, minVolume: number = 10): Promise<any> {
  return fetchJson(`/topics/${encodeURIComponent(slug)}/cluster?min_volume_threshold=${minVolume}`, {
    method: "POST",
  });
}

export async function triggerSynthesis(slug: string, minVolume: number = 10): Promise<any> {
  return fetchJson(`/topics/${encodeURIComponent(slug)}/synthesize?min_volume_threshold=${minVolume}`, {
    method: "POST",
  });
}

export async function getWorkerStatus(): Promise<WorkerStatus> {
  return fetchJson<WorkerStatus>("/workers/status");
}

export async function triggerWorkerRefresh(options?: {
  auto_discover?: boolean;
  force_refresh_all?: boolean;
  min_volume_threshold?: number;
}): Promise<any> {
  const params = new URLSearchParams();
  if (options?.auto_discover) params.set("auto_discover", "true");
  if (options?.force_refresh_all) params.set("force_refresh_all", "true");
  if (options?.min_volume_threshold) params.set("min_volume_threshold", String(options.min_volume_threshold));

  const query = params.toString() ? `?${params.toString()}` : "";
  return fetchJson(`/workers/refresh-trending${query}`, {
    method: "POST",
  });
}

export async function discoverCandidateTrends(limitPerProvider: number = 10): Promise<{
  status: string;
  candidate_count: number;
  candidates: CandidateTopic[];
}> {
  return fetchJson(`/workers/discover-trends?limit_per_provider=${limitPerProvider}`, {
    method: "POST",
  });
}

// ==========================================
// Operational & Observability APIs
// ==========================================

export async function getOpsOverview(): Promise<OpsOverview> {
  return fetchJson<OpsOverview>("/ops/overview", {}, "getOpsOverview");
}

export async function getOpsPipelineMetrics(): Promise<OpsPipelineMetrics> {
  return fetchJson<OpsPipelineMetrics>("/ops/pipeline-metrics", {}, "getOpsPipelineMetrics");
}

export async function getOpsSourceHealth(): Promise<OpsSourceHealth> {
  return fetchJson<OpsSourceHealth>("/ops/source-health", {}, "getOpsSourceHealth");
}

export async function getOpsWorkerMetrics(): Promise<OpsWorkerMetrics> {
  return fetchJson<OpsWorkerMetrics>("/ops/worker-metrics", {}, "getOpsWorkerMetrics");
}

export async function triggerOpsTrending(apiKey?: string): Promise<any> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-Ops-Key"] = apiKey;
  return fetchJson("/ops/run-trending", {
    method: "POST",
    headers,
  });
}

export async function triggerOpsRefreshTopic(slug: string, apiKey?: string): Promise<any> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-Ops-Key"] = apiKey;
  return fetchJson(`/ops/refresh-topic/${encodeURIComponent(slug)}`, {
    method: "POST",
    headers,
  });
}

export async function triggerOpsReprocessTopic(slug: string, apiKey?: string): Promise<any> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-Ops-Key"] = apiKey;
  return fetchJson(`/ops/reprocess-topic/${encodeURIComponent(slug)}`, {
    method: "POST",
    headers,
  });
}

export async function getOpsAlerts(autoEvaluate?: boolean): Promise<AlertSummary> {
  const query = autoEvaluate ? "?auto_evaluate=true" : "";
  return fetchJson<AlertSummary>(`/ops/alerts${query}`, {}, "getOpsAlerts");
}

export async function triggerOpsAlertEvaluate(apiKey?: string): Promise<AlertSummary> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-Ops-Key"] = apiKey;
  return fetchJson<AlertSummary>("/ops/alerts/evaluate", {
    method: "POST",
    headers,
  });
}

export async function getOpsHistory(params?: {
  type?: string;
  component?: string;
  status?: string;
  topic_slug?: string;
  start_time?: string;
  end_time?: string;
  page?: number;
  limit?: number;
}): Promise<import("./types").OpsHistoryResponse> {
  const queryParts: string[] = [];
  if (params?.type) queryParts.push(`type=${encodeURIComponent(params.type)}`);
  if (params?.component) queryParts.push(`component=${encodeURIComponent(params.component)}`);
  if (params?.status) queryParts.push(`status=${encodeURIComponent(params.status)}`);
  if (params?.topic_slug) queryParts.push(`topic_slug=${encodeURIComponent(params.topic_slug)}`);
  if (params?.start_time) queryParts.push(`start_time=${encodeURIComponent(params.start_time)}`);
  if (params?.end_time) queryParts.push(`end_time=${encodeURIComponent(params.end_time)}`);
  if (params?.page) queryParts.push(`page=${params.page}`);
  if (params?.limit) queryParts.push(`limit=${params.limit}`);

  const qs = queryParts.length > 0 ? `?${queryParts.join("&")}` : "";
  return fetchJson<import("./types").OpsHistoryResponse>(`/ops/history${qs}`, {}, "getOpsHistory");
}

export async function getOpsBackups(limit: number = 20): Promise<import("./types").OpsBackupsResponse> {
  return fetchJson<import("./types").OpsBackupsResponse>(`/ops/backups?limit=${limit}`, {}, "getOpsBackups");
}

export async function triggerOpsCreateBackup(apiKey?: string, dryRun?: boolean): Promise<any> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-Ops-Key"] = apiKey;
  const qs = dryRun ? "?dry_run=true" : "";
  return fetchJson(`/ops/backups/create${qs}`, {
    method: "POST",
    headers,
  });
}

export async function triggerOpsVerifyBackup(backupId: string, apiKey?: string): Promise<any> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-Ops-Key"] = apiKey;
  return fetchJson(`/ops/backups/verify/${encodeURIComponent(backupId)}`, {
    method: "POST",
    headers,
  });
}

// ==========================================
// Resource Governance APIs
// ==========================================

export async function getResourceUsage(apiKey?: string): Promise<ResourceUsageResponse> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-Ops-Key"] = apiKey;
  return fetchJson<ResourceUsageResponse>("/ops/resource-usage", { headers }, "getResourceUsage");
}

export async function getResourceBudgets(apiKey?: string): Promise<ResourceBudgetsResponse> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-Ops-Key"] = apiKey;
  return fetchJson<ResourceBudgetsResponse>("/ops/resource-budgets", { headers }, "getResourceBudgets");
}

// ==========================================
// SLO & Platform Metrics APIs
// ==========================================

export async function getSLOStatus(): Promise<import("./types").SLOSummaryResponse> {
  return fetchJson<import("./types").SLOSummaryResponse>("/ops/slos", {}, "getSLOStatus");
}

export async function getPlatformMetrics(): Promise<import("./types").PlatformMetricsResponse> {
  return fetchJson<import("./types").PlatformMetricsResponse>("/ops/metrics", {}, "getPlatformMetrics");
}

export async function getSecurityAuditLogs(params?: {
  action?: string;
  actor?: string;
  status?: string;
  start_time?: string;
  end_time?: string;
  page?: number;
  limit?: number;
  apiKey?: string;
}): Promise<import("./types").SecurityAuditLogsResponse> {
  const queryParts: string[] = [];
  if (params?.action) queryParts.push(`action=${encodeURIComponent(params.action)}`);
  if (params?.actor) queryParts.push(`actor=${encodeURIComponent(params.actor)}`);
  if (params?.status) queryParts.push(`status=${encodeURIComponent(params.status)}`);
  if (params?.start_time) queryParts.push(`start_time=${encodeURIComponent(params.start_time)}`);
  if (params?.end_time) queryParts.push(`end_time=${encodeURIComponent(params.end_time)}`);
  if (params?.page) queryParts.push(`page=${params.page}`);
  if (params?.limit) queryParts.push(`limit=${params.limit}`);

  const qs = queryParts.length > 0 ? `?${queryParts.join("&")}` : "";
  const headers: Record<string, string> = {};
  if (params?.apiKey) headers["X-Ops-Key"] = params.apiKey;

  return fetchJson<import("./types").SecurityAuditLogsResponse>(
    `/ops/audit-logs${qs}`,
    { headers },
    "getSecurityAuditLogs",
  );
}

/** Utility: get minimum safe polling interval in ms. */
export function getMinPollIntervalMs(): number {
  return MIN_POLL_INTERVAL_MS;
}


