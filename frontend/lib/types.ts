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

export interface BackupRecordItem {
  backup_id: string;
  filename: string;
  created_at: string;
  completed_at?: string | null;
  status: "success" | "failed" | "in_progress" | string;
  is_verified: boolean;
  size_bytes: number;
  size_human?: string;
  checksum?: string | null;
  database_name?: string | null;
  schema_version?: string | null;
  error_type?: string | null;
  verified_at?: string | null;
  verification_error?: string | null;
}

export interface OpsBackupsResponse {
  status: string;
  total_count: number;
  config: {
    enabled: boolean;
    retention_count: number;
    retention_days: number;
    compression: boolean;
    verify_after_create: boolean;
    backup_interval_hours: number;
  };
  summary: {
    total_records: number;
    successful_records: number;
    failed_records: number;
    verified_records: number;
    latest_backup_time: string | null;
    latest_successful_backup: string | null;
    latest_failed_backup: string | null;
    latest_backup_age_seconds: number | null;
    total_size_bytes: number;
    total_size_human: string;
  };
  backups: BackupRecordItem[];
}

// ==========================================
// Resource Governance Types
// ==========================================

export interface RateLimitInfo {
  current: number;
  limit: number;
  window_seconds: number;
  utilization_pct: number;
}

export interface ConcurrencyInfo {
  current: number;
  limit: number;
  utilization_pct: number;
}

export interface BudgetUtilization {
  current: number;
  limit: number;
  utilization_pct: number;
  status: "normal" | "warning" | "critical";
}

export interface ExternalSourceUsage {
  active_requests: number;
  max_concurrent: number;
  hourly_used: number;
  hourly_budget: number;
  timeout_seconds: number;
  max_retries: number;
  utilization_pct: number;
}

export interface CostTracking {
  embedding_calls: number;
  embedding_items_total: number;
  synthesis_calls: number;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  items_processed: number;
  pipeline_invocations: number;
  external_requests: Record<string, number>;
}

export interface GovernanceBackendInfo {
  backend: "memory" | "redis" | string;
  configured_backend: string;
  is_healthy: boolean;
  fallback_active: boolean;
  fallback_allowed: boolean;
  redis_configured: boolean;
  last_error?: string | null;
}

export interface ResourceUsageResponse {
  governance_backend?: GovernanceBackendInfo;
  rate_limits: Record<string, RateLimitInfo>;
  concurrency: Record<string, ConcurrencyInfo>;
  cost_tracking: CostTracking;
  external_requests: Record<string, ExternalSourceUsage>;
  synthesis_rate: Record<string, { current_hour: number; limit: number }>;
  budget_utilization: Record<string, BudgetUtilization>;
  thresholds: {
    warning_threshold: number;
    critical_threshold: number;
  };
}

export interface ExternalSourceConfig {
  max_concurrent: number;
  timeout_seconds: number;
  max_retries: number;
  hourly_budget: number;
}

export interface ResourceBudgetsResponse {
  governance_backend?: GovernanceBackendInfo;
  budgets: Record<string, number>;
  thresholds: {
    warning_threshold: number;
    critical_threshold: number;
  };
  external_source_governance: Record<string, ExternalSourceConfig>;
}

// ==========================================
// Observability & SLO Types
// ==========================================

export interface SLIEvaluation {
  slo_name: string;
  display_name: string;
  target: number;
  current_value: number;
  unit: string;
  status: "COMPLIANT" | "WARNING" | "VIOLATED" | "INSUFFICIENT_DATA";
  error_budget_total: number;
  error_budget_consumed: number;
  error_budget_remaining_percent: number;
  burn_rate: number;
  window_samples: number;
  evaluated_at: string;
  details: Record<string, any>;
}

export interface SLOSummaryResponse {
  overall_status: "HEALTHY" | "DEGRADED" | "CRITICAL";
  total_slos: number;
  compliant: number;
  warning: number;
  violated: number;
  health_score_percent: number;
  evaluations: Record<string, SLIEvaluation>;
  evaluated_at: string;
}

export interface MetricPercentiles {
  count: number;
  window_samples?: number;
  sum: number;
  mean: number;
  min: number;
  max: number;
  p50: number;
  p90: number;
  p95: number;
  p99: number;
}

export interface MetricSample {
  labels: Record<string, string>;
  value?: number;
  percentiles?: MetricPercentiles;
  buckets?: Record<string, number>;
  last_updated?: number;
}

export interface MetricDefinition {
  name: string;
  type: "counter" | "gauge" | "histogram";
  description: string;
  unit: string;
  total?: number;
  samples: MetricSample[];
}

export interface PlatformMetricsResponse {
  timestamp: string;
  metrics: Record<string, MetricDefinition>;
}

export interface SecurityAuditLogEntry {
  id: number;
  timestamp: string;
  actor: string;
  role: string;
  action: string;
  resource?: string | null;
  ip_address?: string | null;
  user_agent?: string | null;
  status: "allowed" | "denied" | "failed" | "error" | string;
  details: Record<string, any>;
  error_message?: string | null;
}

export interface SecurityAuditLogsResponse {
  status: string;
  page: number;
  limit: number;
  total_count: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
  audit_logs: SecurityAuditLogEntry[];
}


