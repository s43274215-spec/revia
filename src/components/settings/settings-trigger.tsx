"use client";

import { Icon } from "@/components/learning/icons";
import { useSettings } from "./settings-provider";

export function SettingsTrigger({ variant = "sidebar" }: { variant?: "sidebar" | "header" }) {
  const { openSettings } = useSettings();
  return <button className={`settings-trigger ${variant}`} onClick={openSettings}><Icon name="settings" size={16} /><span>设置</span></button>;
}
