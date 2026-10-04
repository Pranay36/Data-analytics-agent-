import type {
  Analysis,
  AnalysisSummary,
  AuthResponse,
  ConnectionTest,
  DataSource,
  Me,
  Schema,
} from "@/types/api";

/**
 * Where the browser sends API calls. Inlined at build time.
 *
 * Empty means "this same site": in production the Next.js server forwards /api/v1 to the real
 * backend (see `rewrites` in next.config.ts), so the browser never leaves its own origin. Set it
 * to a full URL to call a backend directly, as the Docker compose stack does.
 */
export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000")
).replace(/\/$/, "");
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

// ── Session ──────────────────────────────────────────────────────────────────
// The access token lives here, in memory only. Nothing script-readable survives a page
// close, so an XSS cannot lift a long-lived credential out of storage; the refresh token
// is an httpOnly cookie the page never sees at all.
let accessToken: string | null = null;
let onSessionLost: (() => void) | null = null;

export const session = {
  set: (token: string | null) => {
    accessToken = token;
  },
  /** Called when a request is refused and the session cannot be renewed. */
  onLost: (callback: (() => void) | null) => {
    onSessionLost = callback;
  },
};

let refreshing: Promise<AuthResponse | null> | null = null;

/**
 * Trade the refresh cookie for a new access token.
 *
 * One call at a time: a page polling three endpoints that all get a 401 together must not
 * start three refreshes, because the refresh token is single-use and only the first would
 * win. Everyone shares the one in flight.
 */
export function refreshSession(): Promise<AuthResponse | null> {
  refreshing ??= fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "include" })
    .then(async (response) => {
      if (!response.ok) return null;
      const body = (await response.json()) as AuthResponse;
      accessToken = body.access_token;
      return body;
    })
    .catch(() => null)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

// Calls that establish or end a session must not themselves trigger a refresh-and-retry.
const NO_REFRESH = ["/auth/login", "/auth/register", "/auth/refresh", "/auth/logout"];

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  const headers = new Headers(init?.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...init, headers, credentials: "include" });
  } catch {
    // The server is down or unreachable: say so, rather than surfacing "Failed to fetch".
    throw new ApiError(0, `Cannot reach the server${API_URL ? ` at ${API_URL}` : ""}. Is the backend running?`);
  }

  if (response.status === 401 && !retried && !NO_REFRESH.includes(path)) {
    if (await refreshSession()) return request<T>(path, init, true);
    accessToken = null;
    onSessionLost?.();
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
  register: (email: string, password: string, full_name?: string) =>
    request<AuthResponse>("/auth/register", json({ email, password, full_name })),
  login: (email: string, password: string) =>
    request<AuthResponse>("/auth/login", json({ email, password })),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<Me>("/auth/me"),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("/auth/change-password", json({ current_password, new_password })),

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
