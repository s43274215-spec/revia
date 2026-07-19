"use client";

import { createContext, FormEvent, ReactNode, useContext, useEffect, useState } from "react";
import {
  AUTH_REQUIRED_EVENT,
  clearWorkspaceToken,
  PUBLIC_ACCESS_CLOSED_EVENT,
} from "@/lib/auth-token";
import {
  createAnonymousWorkspace,
  getAccessMode,
  logoutWorkspace,
  unlockWorkspace,
  validateWorkspaceSession,
  WorkspaceRole,
  WorkspaceSession,
} from "@/lib/auth-api";

type AuthState = "checking" | "locked" | "ready";
type AuthContextValue = { role: WorkspaceRole; logout: () => Promise<void> };

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>("checking");
  const [role, setRole] = useState<WorkspaceRole>("public");
  const [publicAccess, setPublicAccess] = useState(false);
  const [ownerLogin, setOwnerLogin] = useState(false);
  const [accessCode, setAccessCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    const updateState = (next: AuthState) => { if (active) setState(next); };
    const requireAuth = () => updateState("locked");
    const closePublicAccess = () => {
      if (!active) return;
      setPublicAccess(false);
      setOwnerLogin(true);
      setError("Revia 当前暂未开放，请稍后再试。");
      setState("locked");
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, requireAuth);
    window.addEventListener(PUBLIC_ACCESS_CLOSED_EVENT, closePublicAccess);

    const initialize = async () => {
      try {
        const mode = await getAccessMode();
        if (!active) return;
        setPublicAccess(mode.public_access_enabled);
        setOwnerLogin(!mode.public_access_enabled);
        clearWorkspaceToken();
        const session = await validateWorkspaceSession();
        if (!active) return;
        setRole(session.role);
        updateState("ready");
      } catch (reason) {
        if (!active) return;
        if (reason instanceof Error && reason.message.includes("暂未开放")) {
          setError(reason.message);
          setOwnerLogin(true);
          setPublicAccess(false);
        }
        updateState("locked");
      }
    };
    void initialize();
    return () => {
      active = false;
      window.removeEventListener(AUTH_REQUIRED_EVENT, requireAuth);
      window.removeEventListener(PUBLIC_ACCESS_CLOSED_EVENT, closePublicAccess);
    };
  }, []);

  const rememberSession = (session: WorkspaceSession) => {
    setRole(session.role);
    setState("ready");
  };

  const logout = async () => {
    try {
      await logoutWorkspace();
    } finally {
      clearWorkspaceToken();
      setRole("public");
      setAccessCode("");
      setError(null);
      setState("locked");
    }
  };

  const ownerUnlock = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = accessCode.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      const session = await unlockWorkspace(value);
      rememberSession(session);
      setAccessCode("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "访问码无效");
    } finally {
      setBusy(false);
    }
  };

  const startPublicWorkspace = async () => {
    setBusy(true);
    setError(null);
    try {
      rememberSession(await createAnonymousWorkspace());
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
    const showOwnerForm = ownerLogin || !publicAccess;
    return (
      <main className="access-page">
        <section className="access-card" aria-labelledby="access-title">
          <div className="access-brand"><span>R</span>Revia</div>
          <div className="access-rule"><i /></div>
          <p className="access-eyebrow">{showOwnerForm ? "固定工作区" : "匿名工作区"}</p>
          <h1 id="access-title">{showOwnerForm ? "输入访问码" : "进入你的学习空间"}</h1>
          {showOwnerForm ? (
            <form onSubmit={ownerUnlock}>
              <p className="access-copy">
                {publicAccess ? "输入站长或演示访问码，进入对应的固定 Workspace。" : "Revia 当前暂未开放；站长与演示访问仍可使用。"}
              </p>
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
                {busy ? "正在验证…" : "进入 Workspace"}
              </button>
            </form>
          ) : (
            <>
              <p className="access-copy">开始后将为此浏览器创建独立工作区。生成学习材料时，你需要填写自己的 DeepSeek API Key。</p>
              {error && <p className="access-error" role="alert">{error}</p>}
              <button type="button" disabled={busy} onClick={startPublicWorkspace}>
                {busy ? "正在创建…" : "开始使用 Revia"}
              </button>
            </>
          )}
          {publicAccess && (
            <button className="owner-entry-link" type="button" onClick={() => { setOwnerLogin((value) => !value); setError(null); }}>
              {showOwnerForm ? "返回公开入口" : "站长入口"}
            </button>
          )}
          <p className="access-note">会话由服务端安全验证，不依赖浏览器本地 Workspace 标识。</p>
        </section>
      </main>
    );
  }

  return (
    <AuthContext.Provider value={{ role, logout }}>
      {role === "demo" && (
        <div className="demo-session-banner" role="status">
          <span>演示模式 · 修改不会保存</span>
          <button type="button" onClick={() => void logout()}>退出</button>
        </div>
      )}
      {children}
    </AuthContext.Provider>
  );
}
