import type { Metadata } from "next";
import { SettingsProvider } from "@/components/settings/settings-provider";
import { AuthProvider } from "@/components/auth/auth-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Revia · 复习项目",
  description: "面向考试复习的知识阅读工具",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body><AuthProvider><SettingsProvider>{children}</SettingsProvider></AuthProvider></body></html>;
}
