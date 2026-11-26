import type {
  AppSettings,
  BlockRow,
  DocumentDetail,
  DocumentRow,
  EndpointTestResult,
  HealthResponse,
  IndexJob,
  ModelsResponse,
  SourceFolder,
  SystemInfo,
  UsageRow,
  SearchResponse,
  TaskDetail,
  TaskRow,
  ArtifactDetail,
  ArtifactRow,
  EmbeddingProfile,
} from "../types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  status: number;
  code?: string;
  extra?: string | null;

  constructor(message: string, status: number, code?: string, extra?: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("无法连接本地服务，请确认 EduCore 服务已启动。", 0);
  }

  if (!response.ok) {
    let detail = response.statusText;
    let code: string | undefined;
    let extra: string | null | undefined;
    try {
      const body = (await response.json()) as {
        detail?: string | { msg?: string }[];
        code?: string;
        extra?: string;
      };
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) {
        detail = body.detail.map((item) => item.msg ?? "参数错误").join("；");
      }
      code = body.code;
      extra = body.extra;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(detail, response.status, code, extra);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),

  getModels: () => request<ModelsResponse>("/api/models"),

  saveModels: (payload: unknown) =>
    request<ModelsResponse>("/api/models", { method: "PUT", body: JSON.stringify(payload) }),

  testModel: (payload: { endpoint_id?: string; endpoint?: unknown }) =>
    request<EndpointTestResult>("/api/models/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getSettings: () => request<AppSettings>("/api/settings"),

  saveSettings: (payload: unknown) =>
    request<AppSettings>("/api/settings", { method: "PUT", body: JSON.stringify(payload) }),

  systemInfo: () => request<SystemInfo>("/api/system/info"),

  quitApp: () => request<{ status: string }>("/api/system/quit", { method: "POST" }),

  usage: (taskId?: string) =>
    request<{ items: UsageRow[] }>(
      `/api/system/usage${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
    ),

  pickPath: (mode: "directory" | "file") =>
    request<{ path: string | null }>("/api/system/pick-path", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  // -- library ---------------------------------------------------------
  getSources: () =>
    request<{ items: SourceFolder[]; stats: Record<string, number>; data_dir: string }>(
      "/api/sources",
    ),

  addSource: (payload: { path: string; recursive: boolean; scan: boolean }) =>
    request<{ source: SourceFolder; jobs: IndexJob[] }>("/api/sources", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  scanSource: (sourceId: string) =>
    request<{ jobs: IndexJob[] }>(`/api/sources/${sourceId}/scan`, { method: "POST" }),

  deleteSource: (sourceId: string) =>
    request<void>(`/api/sources/${sourceId}`, { method: "DELETE" }),

  getDocuments: (params?: {
    sourceId?: string;
    status?: string;
    query?: string;
    includeMissing?: boolean;
    limit?: number;
  }) => {
    const search = new URLSearchParams();
    if (params?.sourceId) search.set("source_id", params.sourceId);
    if (params?.status) search.set("status", params.status);
    if (params?.query) search.set("query", params.query);
    if (params?.includeMissing) search.set("include_missing", "true");
    if (params?.limit) search.set("limit", String(params.limit));
    const suffix = search.toString() ? `?${search.toString()}` : "";
    return request<{ items: DocumentRow[]; counts: Record<string, number> }>(
      `/api/documents${suffix}`,
    );
  },

  addDocument: (path: string) =>
    request<{ document: DocumentRow; jobs: IndexJob[] }>("/api/documents", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),

  getDocument: (documentId: string) =>
    request<DocumentDetail>(`/api/documents/${documentId}`),

  getSection: (documentId: string, sectionId: string) =>
    request<{ section_id: string; blocks: BlockRow[] }>(
      `/api/documents/${documentId}/sections/${sectionId}`,
    ),

  reindexDocument: (documentId: string) =>
    request<{ jobs: IndexJob[] }>(`/api/documents/${documentId}/reindex`, {
      method: "POST",
    }),

  ocrPages: (documentId: string, pages: number[]) =>
    request<{ pages: number[]; text: Record<string, string>; warnings: string[] }>(
      `/api/documents/${documentId}/ocr`,
      { method: "POST", body: JSON.stringify({ pages }) },
    ),

  deleteDocument: (documentId: string) =>
    request<void>(`/api/documents/${documentId}`, { method: "DELETE" }),

  getJobs: (limit = 30) =>
    request<{ items: IndexJob[] }>(`/api/index/jobs?limit=${limit}`),

  getJob: (jobId: string) => request<{ job: IndexJob }>(`/api/index/jobs/${jobId}`),

  getEmbeddingProfiles: () =>
    request<{ items: EmbeddingProfile[] }>("/api/index/profiles"),

  rebuildVectors: (payload: { source_id?: string; document_id?: string }) =>
    request<{ jobs: IndexJob[] }>("/api/index/vectors", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  search: (payload: {
    query: string;
    document_ids?: string[];
    source_ids?: string[];
    top_k?: number;
    use_reranker?: boolean;
  }) =>
    request<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // -- tasks -----------------------------------------------------------
  createTask: (payload: {
    instruction: string;
    document_ids?: string[];
    source_ids?: string[];
    mode?: "auto" | "precise" | "full" | "mixed";
  }) =>
    request<{ task: TaskRow }>("/api/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getTasks: (limit = 50) => request<{ items: TaskRow[] }>(`/api/tasks?limit=${limit}`),

  getTask: (taskId: string) => request<TaskDetail>(`/api/tasks/${taskId}`),

  taskAction: (taskId: string, action: "pause" | "resume" | "cancel" | "retry") =>
    request<{ task: TaskRow }>(`/api/tasks/${taskId}/action`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),

  taskOutput: (taskId: string) =>
    request<{ path: string; content: string; task_id: string }>(
      `/api/tasks/${taskId}/output`,
    ),

  // -- artifacts -------------------------------------------------------
  getArtifacts: (taskId?: string) =>
    request<{ items: ArtifactRow[] }>(
      `/api/artifacts${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
    ),

  getArtifact: (artifactId: string) =>
    request<ArtifactDetail>(`/api/artifacts/${artifactId}`),

  updateArtifact: (artifactId: string, markdown: string) =>
    request<ArtifactDetail>(`/api/artifacts/${artifactId}`, {
      method: "PUT",
      body: JSON.stringify({ markdown }),
    }),

  regenerateArtifact: (artifactId: string, heading: string, instruction: string) =>
    request<ArtifactDetail>(`/api/artifacts/${artifactId}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ heading, instruction }),
    }),

  exportArtifact: (
    artifactId: string,
    payload: { format: "md" | "docx" | "json" | "pdf"; output_dir?: string; include_citations?: boolean },
  ) =>
    request<{ path: string; format: string; filename: string }>(
      `/api/artifacts/${artifactId}/export`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
};

export function artifactDownloadUrl(artifactId: string, format: string): string {
  return `${BASE}/api/artifacts/${artifactId}/download/${format}`;
}

export function pageImageUrl(documentId: string, page: number, scale = 1.8): string {
  return `${BASE}/api/documents/${documentId}/pages/${page}?scale=${scale}`;
}
