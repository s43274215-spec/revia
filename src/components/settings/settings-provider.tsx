"use client";

import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { Icon } from "@/components/learning/icons";
import { useAuth } from "@/components/auth/auth-provider";
import {
  getDeepSeekStatus,
  saveDeepSeekAPIKey,
  testDeepSeekConnection,
} from "@/lib/deepseek-settings-api";
import { getSiteSettings, updateSiteSettings } from "@/lib/site-settings-api";

type Feedback = { kind: "success" | "error"; text: string } | null;
type SettingsContextValue = { openSettings: () => void };

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function useSettings() {
  const context = useContext(SettingsContext);
  if (!context) throw new Error("useSettings must be used inside SettingsProvider");
  return context;
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const { role, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [apiKey, setAPIKey] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [publicAccess, setPublicAccess] = useState(false);
  const [siteLoading, setSiteLoading] = useState(false);
  const [siteBusy, setSiteBusy] = useState(false);

  const close = useCallback(() => {
    setOpen(false);
    setAPIKey("");
    setVisible(false);
    setFeedback(null);
  }, []);

  const openSettings = useCallback(() => {
    setOpen(true);
    if (role === "demo") {
      setFeedback(null);
      return;
    }
    setLoadingStatus(true);
    setFeedback(null);
    getDeepSeekStatus()
      .then((result) => setConfigured(result.configured))
      .catch((error: Error) => setFeedback({ kind: "error", text: `无法读取配置状态：${error.message}` }))
      .finally(() => setLoadingStatus(false));
    if (role === "owner") {
      setSiteLoading(true);
      getSiteSettings()
        .then((result) => setPublicAccess(result.public_access_enabled))
        .catch((error: Error) => setFeedback({ kind: "error", text: `无法读取公开状态：${error.message}` }))
        .finally(() => setSiteLoading(false));
    }
  }, [role]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close, open]);

  const save = async () => {
    const value = apiKey.trim();
    if (!value) return;
    setBusy("save"); setFeedback(null);
    try {
      const result = await saveDeepSeekAPIKey(value);
      setConfigured(result.configured);
      setAPIKey(""); setVisible(false);
      setFeedback({ kind: "success", text: result.message });
    } catch (error) {
      setFeedback({ kind: "error", text: error instanceof Error ? error.message : "保存失败，请重试" });
    } finally { setBusy(null); }
  };

  const test = async () => {
    setBusy("test"); setFeedback(null);
    try {
      const result = await testDeepSeekConnection(apiKey.trim() || undefined);
      setFeedback({ kind: result.success ? "success" : "error", text: result.message });
    } catch (error) {
      setFeedback({ kind: "error", text: error instanceof Error ? error.message : "测试连接失败" });
    } finally { setBusy(null); }
  };

  const togglePublicAccess = async () => {
    if (siteBusy || siteLoading) return;
    const previous = publicAccess;
    const next = !previous;
    setPublicAccess(next);
    setSiteBusy(true);
    setFeedback(null);
    try {
      const result = await updateSiteSettings(next);
      setPublicAccess(result.public_access_enabled);
      setFeedback({ kind: "success", text: result.public_access_enabled ? "Revia 公开访问已开启" : "Revia 公开访问已关闭" });
    } catch (error) {
      setPublicAccess(previous);
      setFeedback({ kind: "error", text: error instanceof Error ? error.message : "公开状态更新失败" });
    } finally {
      setSiteBusy(false);
    }
  };

  return (
    <SettingsContext.Provider value={{ openSettings }}>
      {children}
      <div className={`settings-layer ${open ? "is-open" : ""}`} aria-hidden={!open}>
        <button className="settings-scrim" aria-label="关闭设置" tabIndex={open ? 0 : -1} onClick={close} />
        <aside className="settings-drawer" role="dialog" aria-modal="true" aria-labelledby="settings-title">
          <header className="settings-header">
            <div><span>设置</span><h2 id="settings-title">DeepSeek API</h2></div>
            <button aria-label="关闭设置" onClick={close}><Icon name="close" /></button>
          </header>
          <div className="settings-body">
            {role === "demo" ? <div className="demo-settings-note"><strong>演示模式</strong><p>演示 Workspace 与站长数据隔离。可以浏览、搜索和导出 Word，但不会保存修改或发起解析、OCR 与重新生成。</p></div> : <>
            <div className="settings-status-row">
              <div><strong>连接配置</strong><p>用于生成 Revia 学习材料。</p></div>
              <span className={`settings-status ${configured ? "configured" : ""}`}>
                <i />{loadingStatus ? "检查中" : configured ? "已配置" : "未配置"}
              </span>
            </div>
            <div className="settings-divider" />
            <label htmlFor="deepseek-api-key">API Key</label>
            <div className="secret-input">
              <input
                id="deepseek-api-key"
                type={visible ? "text" : "password"}
                value={apiKey}
                maxLength={180}
                autoComplete="off"
                placeholder={configured ? "输入新的 Key 可更新配置" : "输入 DeepSeek API Key"}
                onChange={(event) => setAPIKey(event.target.value)}
              />
              <button type="button" aria-label={visible ? "隐藏 API Key" : "显示 API Key"} onClick={() => setVisible((value) => !value)}>
                <Icon name={visible ? "eyeOff" : "eye"} size={17} />
              </button>
            </div>
            <p className="settings-security-note">API Key 经加密后发送，并隔离保存在当前匿名工作区，不会写入浏览器存储。</p>
            {feedback && <p className={`settings-feedback ${feedback.kind}`} role="status" aria-live="polite"><i />{feedback.text}</p>}
            <div className="settings-actions">
              <button className="settings-test" disabled={busy !== null || loadingStatus} onClick={test}>
                {busy === "test" ? "正在测试…" : "测试连接"}
              </button>
              <button className="settings-save" disabled={!apiKey.trim() || busy !== null} onClick={save}>
                {busy === "save" ? "正在保存…" : "保存"}
              </button>
            </div>
            {role === "owner" && (
              <section className="site-access-settings" aria-labelledby="site-access-title">
                <div className="settings-divider" />
                <div className="site-access-heading">
                  <div>
                    <strong id="site-access-title">Revia 公开访问</strong>
                    <p>{publicAccess
                      ? "任何人都可以创建独立工作区并使用自己的 DeepSeek API Key。"
                      : "目前仅站长可以使用，普通用户的数据不会被删除。"}</p>
                  </div>
                  <button
                    className={`site-access-switch ${publicAccess ? "is-on" : ""}`}
                    type="button"
                    role="switch"
                    aria-checked={publicAccess}
                    aria-label="切换 Revia 公开访问"
                    disabled={siteLoading || siteBusy}
                    onClick={togglePublicAccess}
                  >
                    <i />
                  </button>
                </div>
                <span className={`site-access-state ${publicAccess ? "is-open" : ""}`}>
                  {siteLoading ? "读取中" : siteBusy ? "更新中" : publicAccess ? "已公开" : "已关闭"}
                </span>
              </section>
            )}
            </>}
            <button className="workspace-logout" type="button" onClick={() => void logout()}>退出当前 Workspace</button>
          </div>
        </aside>
      </div>
    </SettingsContext.Provider>
  );
}
