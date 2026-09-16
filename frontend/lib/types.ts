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
