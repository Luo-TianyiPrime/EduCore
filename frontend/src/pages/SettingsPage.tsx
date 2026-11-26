import { useState } from "react";
import { PageHeader } from "../components/AppShell";
import { cn } from "../components/ui";
import { AppearancePanel } from "../settings/AppearancePanel";
import { ModelsPanel } from "../settings/ModelsPanel";
import { RetrievalPanel } from "../settings/RetrievalPanel";
import { StoragePanel } from "../settings/StoragePanel";

const TABS = [
  { id: "models", label: "模型服务" },
  { id: "retrieval", label: "检索与切分" },
  { id: "storage", label: "存储与凭据" },
  { id: "appearance", label: "外观" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function SettingsPage() {
  const [tab, setTab] = useState<TabId>("models");

  return (
    <>
      <PageHeader
        eyebrow="系统设置"
        title="设置"
        description="模型服务、索引参数与本地存储都只保存在这台电脑上。修改模型配置不会要求重新嵌入已有资料，除非你主动重建索引。"
      />

      <div className="mb-6 flex gap-1 border-b border-line">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            className={cn(
              "relative -mb-px px-3.5 py-2.5 text-[13px] font-medium transition-colors",
              tab === item.id
                ? "border-b-2 border-accent text-ink"
                : "border-b-2 border-transparent text-muted hover:text-ink",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "models" ? <ModelsPanel /> : null}
      {tab === "retrieval" ? <RetrievalPanel /> : null}
      {tab === "storage" ? <StoragePanel /> : null}
      {tab === "appearance" ? <AppearancePanel /> : null}
    </>
  );
}
