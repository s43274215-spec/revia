const TOKEN_KEY = "revia-workspace-token-v1";
export const AUTH_REQUIRED_EVENT = "revia-auth-required";
export const PUBLIC_ACCESS_CLOSED_EVENT = "revia-public-access-closed";

export function getWorkspaceToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function saveWorkspaceToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearWorkspaceToken(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
}

export function requireAuthentication(): void {
  clearWorkspaceToken();
  window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
}

export function notifyPublicAccessClosed(): void {
  window.dispatchEvent(new Event(PUBLIC_ACCESS_CLOSED_EVENT));
}
