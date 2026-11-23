export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    const message = typeof detail === "string" ? detail : (detail as { message?: string })?.message || "请求失败";
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const token = localStorage.getItem("bm_token");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers });
  if (response.status === 204) return null as T;
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/auth")) {
      localStorage.removeItem("bm_token");
      if (!location.pathname.startsWith("/login")) location.assign("/login");
    }
    throw new ApiError(response.status, data?.detail ?? response.statusText);
  }
  return data as T;
}

export function saveToken(token: string) {
  localStorage.setItem("bm_token", token);
}

export async function waitJob(id: number): Promise<Job> {
  while (true) {
    const job = await api<Job>(`/api/jobs/${id}`);
    if (["completed", "failed", "cancelled", "interrupted"].includes(job.status)) return job;
    await new Promise((resolve) => setTimeout(resolve, 600));
  }
}

export interface Job {
  id: number;
  type: string;
  status: string;
  message: string;
  progress: Record<string, number | string>;
  updated_at: string;
}

export interface BookRow {
  id: number;
  title: string;
  authors_text: string;
  publisher: string;
  isbn: string;
  tags_text: string;
  series: string;
  series_index: string;
  primary_format: string;
  relative_path: string;
  size: number;
  name_conflict: number;
  suggested_category?: string;
  title_issue: string;
  title_suggestion: string;
  metadata_source: string;
  confidence: string;
  unclassified: boolean;
}

export interface FolderNode {
  id: number;
  relative_path: string;
  name: string;
  is_classified: number;
}
