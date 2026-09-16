import {
  CandidateTopic,
  OpsOverview,
  OpsPipelineMetrics,
  OpsSourceHealth,
  OpsWorkerMetrics,
  Perspective,
  Topic,
  TopicCreate,
  WorkerStatus,
} from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window === "undefined" ? "http://127.0.0.1:8000/api" : "/api");

async function fetchJson<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    cache: "no-store",
  });

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
  return fetchJson<Topic[]>(`/topics${query}`);
}

export async function getTrendingTopics(limit: number = 8, minScore: number = 0.0): Promise<Topic[]> {
  return fetchJson<Topic[]>(`/topics/trending?limit=${limit}&min_score=${minScore}`);
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
  return fetchJson<OpsOverview>("/ops/overview");
}

export async function getOpsPipelineMetrics(): Promise<OpsPipelineMetrics> {
  return fetchJson<OpsPipelineMetrics>("/ops/pipeline-metrics");
}

export async function getOpsSourceHealth(): Promise<OpsSourceHealth> {
  return fetchJson<OpsSourceHealth>("/ops/source-health");
}

export async function getOpsWorkerMetrics(): Promise<OpsWorkerMetrics> {
  return fetchJson<OpsWorkerMetrics>("/ops/worker-metrics");
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
