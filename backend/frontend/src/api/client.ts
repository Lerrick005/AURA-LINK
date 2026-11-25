const BASE = import.meta.env.VITE_API_BASE_URL;

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

function authHeaders(token: string | null): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

async function handle(resp: Response) {   
  if (!resp.ok) {
    let code = `http_${resp.status}`;     
    let message = resp.statusText || "request failed";
    try {
      const body = await resp.json();     
      if (body?.error?.code) code = body.error.code;
      if (body?.error?.message) message = body.error.message;
      if (body?.detail) message = body.detail;
    } catch {
      // body was not JSON
    }
    throw new ApiError(resp.status, code, message);
  }
  if (resp.status === 204) return null;   
  return resp.json();
}

export async function apiGet<T>(path: string, token: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "GET",
    headers: authHeaders(token),
    credentials: "include",
  });
  return handle(r) as Promise<T>;
}

export async function apiPost<T>(
  path: string,
  token: string | null,
  body: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { ...authHeaders(token), ...extraHeaders },
    credentials: "include",
    body: JSON.stringify(body),
  });
  return handle(r) as Promise<T>;
}