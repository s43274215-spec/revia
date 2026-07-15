"use client";

import { FormEvent, ReactNode, useEffect, useState } from "react";
import { AUTH_REQUIRED_EVENT, getWorkspaceToken, saveWorkspaceToken } from "@/lib/auth-token";
import {
  createAnonymousWorkspace,
  getAccessMode,
  unlockWorkspace,
  validateWorkspaceSession,
} from "@/lib/auth-api";

type AuthState = "checking" | "locked" | "ready";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>("checking");
  const [publicAccess, setPublicAccess] = useState(false);
  const [accessCode, setAccessCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    const updateState = (next: AuthState) => { if (active) setState(next); };
    const requireAuth = () => updateState("locked");
    window.addEventListener(AUTH_REQUIRED_EVENT, requireAuth);

    const initialize = async () => {
      try {
        const mode = await getAccessMode();
        if (!active) return;
        setPublicAccess(mode.public_access_enabled);
        const token = getWorkspaceToken();
        if (!token) {
          updateState("locked");
          return;
        }
        await validateWorkspaceSession();
        updateState("ready");
      } catch {
        updateState("locked");
      }
    };
    void initialize();
    return () => {
      active = false;
      window.removeEventListener(AUTH_REQUIRED_EVENT, requireAuth);
    };
  }, []);

  const rememberSession = (token: string) => {
    saveWorkspaceToken(token);
    setState("ready");
  };

  const unlock = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = accessCode.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      const session = await unlockWorkspace(value);
      rememberSession(session.token);
      setAccessCode("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法验证访问码");
    } finally {
      setBusy(false);
    }
  };

  const startPublicWorkspace = async () => {
    setBusy(true);
    setError(null);
    try {
      const session = await createAnonymousWorkspace();
      rememberSession(session.token);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法创建匿名工作区");
    } finally {
      setBusy(false);
    }
  };

  if (state === "checking") {
    return <main className="access-page access-checking" aria-label="正在验证工作区"><i /></main>;
  }

  if (state === "locked") {
    return (
      <main className="access-page">
        <section className="access-card" aria-labelledby="access-title">
          <div className="access-brand"><span>R</span>Revia</div>
          <div className="access-rule"><i /></div>
          <p className="access-eyebrow">匿名工作区</p>
          <h1 id="access-title">进入你的学习空间</h1>
          {publicAccess ? (
            <>
              <p className="access-copy">开始后将为此浏览器创建独立工作区。生成学习材料时，你需要填写自己的 DeepSeek API Key。</p>
              {error && <p className="access-error" role="alert">{error}</p>}
              <button type="button" disabled={busy} onClick={startPublicWorkspace}>
                {busy ? "正在创建…" : "开始使用 Revia"}
              </button>
            </>
          ) : (
            <form onSubmit={unlock}>
              <p className="access-copy">输入访问码后，Revia 会为当前浏览器建立独立工作区。</p>
              <label htmlFor="app-access-code">访问码</label>
              <input
                id="app-access-code"
                type="password"
                value={accessCode}
                autoComplete="current-password"
                autoFocus
                maxLength={256}
                placeholder="请输入访问码"
                onChange={(event) => setAccessCode(event.target.value)}
              />
              {error && <p className="access-error" role="alert">{error}</p>}
              <button type="submit" disabled={!accessCode.trim() || busy}>
                {busy ? "正在验证…" : "进入 Revia"}
              </button>
            </form>
          )}
          <p className="access-note">工作区凭证仅保存在此浏览器中。</p>
        </section>
      </main>
    );
  }

  return children;
}
