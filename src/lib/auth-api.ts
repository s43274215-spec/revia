import { API_BASE_URL, apiRequest } from "./api-base";

export type WorkspaceRole = "owner" | "demo" | "public";
export type WorkspaceSession = { workspace_id: string; role: WorkspaceRole };
export type AccessMode = { public_access_enabled: boolean; demo_access_enabled: boolean };

export async function getAccessMode(): Promise<AccessMode> {
  const response = await fetch(`${API_BASE_URL}/auth/mode`, { cache: "no-store", credentials: "include" });
  if (!response.ok) throw new Error(`无法读取访问模式（HTTP ${response.status}）`);
  return response.json() as Promise<AccessMode>;
}

export async function createAnonymousWorkspace(): Promise<WorkspaceSession> {
  const response = await fetch(`${API_BASE_URL}/auth/anonymous`, { method: "POST", credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || `无法创建工作区（HTTP ${response.status}）`);
  }
  return response.json() as Promise<WorkspaceSession>;
}

export async function unlockWorkspace(accessCode: string): Promise<WorkspaceSession> {
  const response = await fetch(`${API_BASE_URL}/auth/access`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ access_code: accessCode }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || `访问码验证失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<WorkspaceSession>;
}

export function validateWorkspaceSession(): Promise<{ workspace_id: string; role: WorkspaceRole }> {
  return apiRequest<{ workspace_id: string; role: WorkspaceRole }>("/auth/session");
}

export function logoutWorkspace(): Promise<void> {
  return apiRequest<void>("/auth/logout", { method: "POST" });
}
