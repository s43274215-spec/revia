import { notifyPublicAccessClosed, requireAuthentication } from "./auth-token";

export const API_BASE_URL = "/api/backend";

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers, credentials: "include" });
  const body = response.ok ? null : await response.json().catch(() => null) as { detail?: string } | null;
  if (response.status === 401 && typeof window !== "undefined") requireAuthentication();
  if (
    response.status === 403
    && body?.detail === "Revia 当前暂未开放，请稍后再试。"
    && typeof window !== "undefined"
  ) notifyPublicAccessClosed();
  if (!response.ok) {
    throw new Error(body?.detail || `请求失败（HTTP ${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
