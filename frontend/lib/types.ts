export interface SourceCoverage {
  google_news: number;
  reddit: number;
  x: number;
  total_combined: number;
}

export interface SampleQuote {
  quote: string;
  source: "google_news" | "reddit" | "x" | string;
  author_handle?: string;
  url?: string;
  engagement?: Record<string, any>;
  created_at?: string;
}

export interface Perspective {
  id: number;
  cluster_id: number;
  perspective_type: string;
  summary: string;
  estimated_share: number;
  key_arguments: string[];
  sample_quotes: SampleQuote[];
  created_at: string;
}

export interface Topic {
  id: number;
  title: string;
  slug: string;
  search_count: number;
  trending_score: number;
  source_coverage: SourceCoverage;
  last_clustered_at?: string | null;
  updated_at: string;
  created_at: string;
  perspectives?: Perspective[];
}

export interface TopicCreate {
  title: string;
  slug?: string;
}

export interface WorkerStatus {
  is_running: boolean;
  total_runs: number;
  last_run_time: string | null;
  next_scheduled_run: string | null;
  worker_interval_hours: number;
  last_error: string | null;
  last_run_result: any | null;
}

export interface CandidateTopic {
  title: string;
  normalized_key: string;
  sources: string[];
  confidence: number;
  discovered_at: string;
}

export type StanceCategory =
  | "supportive"
  | "critical"
  | "skeptical"
  | "nuanced"
  | "neutral"
  | "optimistic"
  | "other";

export type HealthState = "healthy" | "degraded" | "unavailable" | "disabled";

export interface OpsPipelineRun {
  id: number;
  pipeline_name: string;
  topic_slug: string;
  total_duration_ms: number;
  stages_ms: Record<string, number>;
  status: "success" | "failed" | string;
  error?: string | null;
  timestamp: string;
}

export interface OpsOverview {
  status: HealthState;
  timestamp: string;
  api: {
    status: HealthState;
    version: string;
    environment: string;
  };
  database: {
    status: HealthState;
    latency_ms: number;
    total_topics: number;
    active_topics: number;
    recently_refreshed_24h: number;
  };
  worker: {
    is_running: boolean;
    total_runs: number;
    last_run_time: string | null;
    next_scheduled_run: string | null;
    interval_hours: number;
  };
  recent_pipeline_runs: OpsPipelineRun[];
  pipeline_summary: {
    total_runs: number;
    success_rate: number;
    avg_duration_ms: number;
  };
}

export interface OpsPipelineMetrics {
  total_runs: number;
  success_count: number;
  failure_count: number;
  success_rate: number;
  avg_duration_ms: number;
  median_duration_ms: number;
  per_stage_avg_ms: Record<string, number>;
  slowest_recent_stages: { stage: string; avg_duration_ms: number }[];
  recent_runs: OpsPipelineRun[];
}

export interface SourceHealthItem {
  source_name: string;
  enabled: boolean;
  status: HealthState;
  total_requests: number;
  success_count: number;
  failure_count: number;
  timeout_count: number;
  avg_latency_ms: number;
  last_success: string | null;
  last_failure: string | null;
  last_error_summary: string | null;
}

export type OpsSourceHealth = Record<string, SourceHealthItem>;

export interface OpsWorkerMetrics {
  scheduler_running: boolean;
  cycle_count: number;
  interval_hours: number;
  last_cycle_time: string | null;
  next_scheduled_cycle: string | null;
  last_error: string | null;
  last_cycle_summary: {
    topics_considered: number;
    topics_refreshed: number;
    topics_skipped: number;
    topics_failed: number;
    candidates_discovered: number;
  };
}
