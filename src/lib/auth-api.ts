import { API_BASE_URL, apiRequest } from "./api-base";

export type WorkspaceSession = { token: string; workspace_id: string };

export async function unlockWorkspace(accessCode: string): Promise<WorkspaceSession> {
  const response = await fetch(`${API_BASE_URL}/auth/access`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_code: accessCode }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || `访问码验证失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<WorkspaceSession>;
}

export function validateWorkspaceSession(): Promise<{ workspace_id: string }> {
  return apiRequest<{ workspace_id: string }>("/auth/session");
}
