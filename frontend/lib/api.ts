import type { AskResponse, DemoUser } from "./types";

const KEY = "qp.session";

export interface Session { token: string; user: DemoUser }

export function loadSession(): Session | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null) {
  try {
    if (s) window.localStorage.setItem(KEY, JSON.stringify(s));
    else window.localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: session lives in memory only */
  }
}

async function req<T>(path: string, token: string | null, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  demoUsers: () => req<DemoUser[]>("/api/auth/demo-users", null),
  login: (user_id: string) => req<{ token: string; user: DemoUser }>("/api/auth/demo-login", null, { method: "POST", body: JSON.stringify({ user_id }) }),
  ask: (token: string, question: string, conversation_id?: string | null) =>
    req<AskResponse>("/api/ask", token, { method: "POST", body: JSON.stringify({ question, conversation_id }) }),
  catalog: (token: string) => req<any>("/api/catalog", token),
  summary: (token: string) => req<any>("/api/ops/summary", token),
  audit: (token: string) => req<any[]>("/api/ops/audit?limit=100", token),
  drift: (token: string) => req<any>("/api/ops/drift", token),
  benchmark: (token: string) => req<any>("/api/ops/benchmark", token),
};
