import { useEffect, useState } from "react";
import { CheckIcon, MoonIcon, SunIcon, SpinnerIcon } from "./settings-icons";
import { Banner, cn } from "../components/ui";
import { api } from "../lib/api";
import { applyTheme, loadTheme, saveTheme, type ThemeMode } from "../lib/theme";

const OPTIONS: { id: ThemeMode; label: string; description: string; icon: typeof SunIcon }[] = [
  { id: "light", label: "浅色", description: "默认的桌面工作台外观", icon: SunIcon },
  { id: "dark", label: "深色", description: "低光环境使用", icon: MoonIcon },
  { id: "system", label: "跟随系统", description: "随操作系统外观自动切换", icon: CheckIcon },
];

export function AppearancePanel() {
  const [mode, setMode] = useState<ThemeMode>(loadTheme());
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const settings = await api.getSettings();
        if (settings.theme) {
          setMode(settings.theme);
          saveTheme(settings.theme);
        }
      } catch {
        /* Local theme still applies; backend may be offline. */
      }
    })();
  }, []);

  const choose = async (next: ThemeMode) => {
    setMode(next);
    saveTheme(next);
    applyTheme(next);
    setSaving(true);
    setMessage(null);
    try {
      await api.saveSettings({ theme: next });
      setMessage("外观已保存。");
    } catch {
      setMessage("已在本机生效，但未能同步到设置文件。");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      {message ? <Banner tone="ok">{message}</Banner> : null}

      <div className="grid gap-3 sm:grid-cols-3">
        {OPTIONS.map((option) => {
          const Icon = option.icon;
          const active = mode === option.id;
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => void choose(option.id)}
              className={cn(
                "rounded-xl border p-4 text-left transition-colors",
                active
                  ? "border-accent/50 bg-accent-soft/50 ring-1 ring-accent/20"
                  : "border-line bg-surface hover:border-line-strong",
              )}
            >
              <div className="flex items-center justify-between">
                <Icon className={cn("size-4", active ? "text-accent" : "text-muted")} />
                {active ? <CheckIcon className="size-4 text-accent" /> : null}
              </div>
              <div className="mt-3 text-[13.5px] font-medium text-ink">{option.label}</div>
              <div className="mt-0.5 text-[12px] text-muted">{option.description}</div>
            </button>
          );
        })}
      </div>

      <div className="flex items-center gap-2 text-[12px] text-muted">
        {saving ? <SpinnerIcon className="size-3.5 animate-spin" /> : null}
        主题立即生效，并写入本地设置。
      </div>
    </div>
  );
}
