export type EndpointKind = "llm" | "embedding" | "reranker" | "ocr" | "vlm";
export type Provider = "openai_compat" | "rerank_http" | "ocr_http" | "ocr_cli";

export interface Endpoint {
  id: string;
  name: string;
  kind: EndpointKind;
  provider: Provider;
  base_url: string;
  model: string;
  has_api_key: boolean;
  timeout_s: number;
  concurrency: number;
  rpm: number | null;
  tpm: number | null;
  context_limit: number | null;
  dimensions: number | null;
  query_instruction: string | null;
  document_instruction: string | null;
  distance: string;
  batch_size: number;
  price_input_per_mtok: number | null;
  price_output_per_mtok: number | null;
  price_per_call: number | null;
  enabled: boolean;
  extra: Record<string, unknown>;
}

export interface Defaults {
  llm: string | null;
  embedding: string | null;
  reranker: string | null;
  ocr: string | null;
  vlm: string | null;
}

export interface ModelsResponse {
  endpoints: Endpoint[];
  defaults: Defaults;
  secret_backend: "keyring" | "local_file";
}

export interface EndpointTestResult {
  ok: boolean;
  kind: EndpointKind;
  endpoint_id: string;
  endpoint_name: string;
  detail: string;
  latency_ms: number | null;
  dimensions: number | null;
  tool_calling: boolean | null;
  warnings: string[];
}

export interface HealthResponse {
  status: string;
  version: string;
  data_dir: string;
  database: boolean;
}

export interface SystemInfo {
  version: string;
  platform: string;
  system: string;
  machine: string;
  python: string;
  data_dir: string;
  database: string;
  secret_backend: string;
  disk_free_bytes: number;
  disk_total_bytes: number;
  endpoint_count: number;
}

export interface AppSettings {
  theme: "light" | "dark" | "system";
  language: string;
  retrieval: {
    vector_candidates: number;
    bm25_candidates: number;
    fusion_top: number;
    final_top: number;
    rrf_k: number;
    expand_parent: boolean;
    expand_neighbors: number;
  };
  chunking: {
    target_tokens: number;
    max_tokens: number;
    min_tokens: number;
    parent_target_tokens: number;
    parent_max_tokens: number;
    overlap_tokens: number;
  };
  max_task_steps: number;
  task_budget_usd: number | null;
  data_dir: string;
  secret_backend: string;
}

export interface UsageRow {
  endpoint_id: string;
  endpoint_kind: string;
  model: string | null;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_estimate: number | null;
}

export interface SourceFolder {
  id: string;
  path: string;
  recursive: number;
  created_at: string;
  last_scan_at: string | null;
  status: string;
  error: string | null;
  document_count: number;
  ready_count: number;
  needs_ocr_count: number;
  failed_count: number;
}

export interface DocumentRow {
  id: string;
  source_id: string | null;
  path: string;
  rel_path: string | null;
  format: string;
  title: string | null;
  size: number;
  mtime: number;
  page_count: number;
  current_version_id: string | null;
  parse_status: string;
  parse_error: string | null;
  is_present: number;
  created_at: string;
  updated_at: string;
  content_hash: string | null;
}

export interface SectionRow {
  id: string;
  version_id: string;
  parent_id: string | null;
  order_index: number;
  level: number;
  title: string;
  page_start: number | null;
  page_end: number | null;
  block_count: number;
}

export interface BlockRow {
  id: string;
  version_id: string;
  section_id: string;
  order_index: number;
  kind: string;
  text: string;
  page: number | null;
  bbox: number[] | null;
  meta: Record<string, unknown>;
}

export interface VersionRow {
  id: string;
  document_id: string;
  content_hash: string;
  parsed_path: string | null;
  page_count: number;
  block_count: number;
  chunk_count: number;
  created_at: string;
}

export interface DocumentDetail {
  document: DocumentRow;
  version: VersionRow | null;
  sections: SectionRow[];
  versions: VersionRow[];
}

export interface IndexJob {
  id: string;
  kind: string;
  source_id: string | null;
  document_id: string | null;
  status: string;
  total: number;
  processed: number;
  failed: number;
  message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_title: string | null;
  document_path: string;
  document_format: string | null;
  heading: string;
  text: string;
  page_start: number | null;
  page_end: number | null;
  section_id: string;
  section_path: string[];
  context: string;
  score: number;
  rrf_score: number;
  rerank_score: number | null;
  vector_rank: number | null;
  bm25_rank: number | null;
}

export interface SearchProfileDebug {
  profile_id: string;
  endpoint_id: string;
  model: string;
  dimensions: number;
  versions: number;
  candidates: number;
  note: string | null;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  debug: {
    scope_documents: number;
    scope_versions: number;
    profiles: SearchProfileDebug[];
    vector_candidates: number;
    bm25_candidates: number;
    trigram_used: boolean;
    fused_candidates: number;
    reranked: boolean;
    degraded: string[];
    timings_ms: Record<string, number>;
  };
}

export interface TaskRow {
  id: string;
  kind: string;
  mode: string;
  instruction: string;
  scope: {
    document_ids?: string[];
    source_ids?: string[];
    inputs?: { document_id: string; title: string | null; page_count: number; format: string }[];
    mode_reason?: string;
  };
  status: string;
  progress: number;
  stage: string | null;
  total_batches: number;
  done_batches: number;
  failed_batches: number;
  error: string | null;
  result_path: string | null;
  checkpoint_path: string | null;
  budget_usd: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface TaskBatch {
  id: string;
  task_id: string;
  batch_index: number;
  document_id: string | null;
  label: string | null;
  segments: { section_index: number; block_start: number; block_end: number }[];
  page_start: number | null;
  page_end: number | null;
  status: string;
  input_tokens: number;
  note_path: string | null;
  error: string | null;
}

export interface TaskEvent {
  id: number;
  task_id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface TaskDetail {
  task: TaskRow;
  batches: TaskBatch[];
  events: TaskEvent[];
  usage: UsageRow[];
}

export interface ArtifactRow {
  id: string;
  task_id: string | null;
  title: string;
  kind: string;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface ArtifactVersion {
  id: string;
  artifact_id: string;
  version: number;
  markdown: string;
  structure_json: string | null;
  instruction: string | null;
  created_at: string;
  chars?: number;
}

export interface ValidationCheck {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail";
  detail: string;
}

export interface ArtifactValidation {
  overall: "pass" | "warn" | "fail";
  checks: ValidationCheck[];
}

export interface ArtifactSectionNode {
  title: string;
  level: number;
  blocks?: { kind: string; text?: string; items?: string[]; rows?: string[][] }[];
  children?: ArtifactSectionNode[];
}

export interface ArtifactDetail {
  artifact: ArtifactRow;
  version: ArtifactVersion;
  structure: {
    title?: string;
    sections?: ArtifactSectionNode[];
  };
  versions: ArtifactVersion[];
  validation: ArtifactValidation;
}

export interface EmbeddingProfile {
  id: string;
  endpoint_id: string;
  model: string;
  dimensions: number;
  distance: string;
  query_instruction: string | null;
  document_instruction: string | null;
  table_name: string;
  status: string;
  created_at: string;
  last_used_at: string | null;
  version_count: number;
  table_vectors: number;
  endpoint_name: string | null;
  compatible: boolean;
}
