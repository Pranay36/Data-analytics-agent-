import type {
  Analysis,
  AnalysisSummary,
  ConnectionTest,
  DataSource,
  Schema,
} from "@/types/api";

/** Inlined at build time, so set it where the frontend is built, not where it runs. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const BASE = `${API_URL}/api/v1`;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI reports validation errors as a list and others as a string; flatten both. */
function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d?.msg === "string" ? d.msg : JSON.stringify(d))).join("; ");
  }
  return "Something went wrong.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, init);
  } catch {
    // The server is down or unreachable: say so, rather than surfacing "Failed to fetch".
    throw new ApiError(0, `Cannot reach the server at ${API_URL}. Is the backend running?`);
  }

  if (!response.ok) {
    let message = response.statusText;
    try {
      message = describe((await response.json()).detail);
    } catch {
      /* the body was not JSON; the status text will do */
    }
    throw new ApiError(response.status, message);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  listDatasources: () => request<DataSource[]>("/datasources"),
  getSchema: (id: string) => request<Schema>(`/datasources/${id}/schema`),
  getExamples: (id: string) => request<string[]>(`/datasources/${id}/examples`),
  syncDatasource: (id: string) => request<unknown>(`/datasources/${id}/sync`, { method: "POST" }),
  deleteDatasource: (id: string) => request<void>(`/datasources/${id}`, { method: "DELETE" }),

  testConnection: (body: unknown) => request<ConnectionTest>("/datasources/test", json(body)),
  createDatasource: (body: unknown) => request<DataSource>("/datasources", json(body)),
  uploadCsv: (name: string, files: File[]) => {
    const form = new FormData();
    form.append("name", name);
    files.forEach((file) => form.append("files", file));
    return request<DataSource>("/datasources/csv", { method: "POST", body: form });
  },

  startAnalysis: (datasource_id: string, question: string) =>
    request<{ id: string; status: string }>("/analyses", json({ datasource_id, question })),
  getAnalysis: (id: string) => request<Analysis>(`/analyses/${id}`),
  listAnalyses: (params: { limit?: number; offset?: number } = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString();
    return request<AnalysisSummary[]>(`/analyses${query ? `?${query}` : ""}`);
  },
};
