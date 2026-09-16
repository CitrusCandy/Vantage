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

export type AlertSeverity = "info" | "warning" | "critical";

export interface AlertInstance {
  id: string;
  rule_name: string;
  severity: AlertSeverity;
  component: string;
  message: string;
  status: "active" | "resolved";
  first_seen: string;
  last_seen: string;
  resolved_at?: string | null;
  occurrence_count: number;
  metadata?: Record<string, any>;
}

export interface AlertSummary {
  last_evaluation_time: string | null;
  active_count: number;
  resolved_count: number;
  active_alerts: AlertInstance[];
  resolved_alerts: AlertInstance[];
}

export interface IncidentReadinessInfo {
  readiness_state: "ready" | "unready" | "degraded";
  current_worker_cycle: number;
  last_database_check: {
    status: HealthState;
    latency_ms: number;
    timestamp: string;
  };
  last_successful_ingestion: Record<string, string | null>;
  last_successful_pipeline: string | null;
  last_successful_synthesis: string | null;
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
  incident_readiness?: IncidentReadinessInfo;
  alerts_summary?: {
    active_count: number;
    resolved_count: number;
    last_evaluation_time: string | null;
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

export interface OpsHistoryItem {
  record_type: "pipeline_run" | "source_execution" | "worker_cycle" | "alert";
  timestamp?: string | null;
  status?: string;
  duration_ms?: number;
  // Pipeline Run specific
  run_id?: number;
  topic_id?: number | null;
  topic_slug?: string | null;
  pipeline_type?: string;
  started_at?: string | null;
  completed_at?: string | null;
  sample_size?: number;
  cluster_count?: number;
  perspective_count?: number;
  failure_stage?: string | null;
  error_type?: string | null;
  stages_ms?: Record<string, number>;
  // Source Execution specific
  execution_id?: number;
  source?: string;
  operation?: string;
  item_count?: number;
  // Worker Cycle specific
  cycle_id?: number;
  topics_considered?: number;
  topics_refreshed?: number;
  topics_skipped?: number;
  topics_failed?: number;
  error_summary?: string | null;
  // Alert specific
  id?: string;
  rule_name?: string;
  severity?: "info" | "warning" | "critical";
  component?: string;
  message?: string;
  occurrence_count?: number;
  first_seen?: string | null;
  last_seen?: string | null;
  resolved_at?: string | null;
  metadata?: Record<string, any>;
}

export interface OpsHistoryResponse {
  status: string;
  type: string;
  page: number;
  limit: number;
  total_count: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
  items: OpsHistoryItem[];
}

