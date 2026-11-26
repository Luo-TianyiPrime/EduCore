import { type ReactNode, useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../lib/api";
import { applyTheme, loadTheme, watchSystemTheme } from "../lib/theme";
import type { HealthResponse } from "../types";
import {
  ArtifactsIcon,
  LibraryIcon,
  SearchIcon,
  SettingsIcon,
  StatusDotProxy,
  TasksIcon,
  WorkbenchIcon,
} from "./nav-icons";
import { cn } from "./ui";

interface NavItem {
  to: string;
  label: string;
  hint: string;
  icon: ReactNode;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "AI 工作台", hint: "提出任务", icon: <WorkbenchIcon />, end: true },
  { to: "/library", label: "资料管理", hint: "文件夹与文档", icon: <LibraryIcon /> },
  { to: "/search", label: "知识检索", hint: "混合检索", icon: <SearchIcon /> },
  { to: "/tasks", label: "任务中心", hint: "进度与恢复", icon: <TasksIcon /> },
  { to: "/artifacts", label: "成果管理", hint: "预览与导出", icon: <ArtifactsIcon /> },
  { to: "/settings", label: "系统设置", hint: "模型与存储", icon: <SettingsIcon /> },
];

export function AppShell() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const result = await api.health();
        if (!cancelled) {
          setHealth(result);
          setOnline(result.status === "ok");
        }
      } catch {
        if (!cancelled) setOnline(false);
      }
    };
    void check();
    const timer = window.setInterval(check, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    applyTheme(loadTheme());
    return watchSystemTheme(() => applyTheme(loadTheme()));
  }, []);

  return (
    <div className="flex h-full">
      <aside className="flex w-[236px] shrink-0 flex-col border-r border-line bg-surface-muted">
        <div className="flex items-center gap-2.5 px-5 pt-6 pb-5">
          <div className="flex size-8 items-center justify-center rounded-lg border border-accent/25 bg-accent-soft text-[13px] font-bold tracking-tight text-accent-ink">
            EC
          </div>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight text-ink">EduCore</div>
            <div className="text-[11px] text-muted">教育资料处理工作台</div>
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 px-3">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition-colors",
                  isActive
                    ? "bg-surface text-ink shadow-[0_1px_2px_rgb(0_0_0/0.04)] ring-1 ring-line"
                    : "text-ink-soft hover:bg-surface/70 hover:text-ink",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span className={cn(isActive ? "text-accent" : "text-muted")}>{item.icon}</span>
                  <span className="flex-1">
                    <span className="block text-[13.5px] font-medium leading-tight">{item.label}</span>
                    <span className="block text-[11px] text-muted">{item.hint}</span>
                  </span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-line px-5 py-3.5 text-[11px] text-muted">
          <div className="flex items-center gap-1.5">
            <StatusDotProxy tone={online === null ? "neutral" : online ? "ok" : "danger"} />
            {online === null ? "正在连接本地服务" : online ? "本地服务运行中" : "本地服务未连接"}
          </div>
          <div className="mt-1 flex items-center justify-between">
            <span>v{health?.version ?? "0.1.0"}</span>
            <span>数据仅存本机</span>
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[1180px] px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow ? (
          <div className="mb-1 text-[11px] font-medium uppercase tracking-[0.14em] text-accent">
            {eyebrow}
          </div>
        ) : null}
        <h1 className="text-[22px] font-semibold tracking-tight text-ink">{title}</h1>
        {description ? (
          <p className="mt-1.5 max-w-2xl text-[13.5px] leading-relaxed text-muted">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}
