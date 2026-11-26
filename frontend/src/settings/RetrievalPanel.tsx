import { useEffect, useState } from "react";
import { AlertIcon, CheckIcon, SpinnerIcon } from "./settings-icons";
import { Badge, Banner, Button, Card, Field, Input } from "../components/ui";
import { ApiError, api } from "../lib/api";
import type { AppSettings, EmbeddingProfile } from "../types";

function num(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function RetrievalPanel() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<EmbeddingProfile[]>([]);

  useEffect(() => {
    (async () => {
      try {
        setSettings(await api.getSettings());
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "加载设置失败");
      } finally {
        setLoading(false);
      }
    })();
    api
      .getEmbeddingProfiles()
      .then((data) => setProfiles(data.items))
      .catch(() => setProfiles([]));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-[13px] text-muted">
        <SpinnerIcon className="animate-spin" /> 正在读取设置…
      </div>
    );
  }
  if (!settings) {
    return <Banner tone="danger">{error ?? "设置不可用"}</Banner>;
  }

  const patch = (next: Partial<AppSettings>) => setSettings({ ...settings, ...next });

  const save = async () => {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await api.saveSettings({
        retrieval: settings.retrieval,
        chunking: settings.chunking,
        max_task_steps: settings.max_task_steps,
        task_budget_usd: settings.task_budget_usd,
      });
      setSettings(updated);
      setMessage("检索与切分参数已保存。新参数从下一次索引或查询开始生效。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const retrieval = settings.retrieval;
  const chunking = settings.chunking;

  return (
    <div className="space-y-5">
      {error ? <Banner tone="danger">{error}</Banner> : null}
      {message ? <Banner tone="ok">{message}</Banner> : null}

      <Card className="p-5">
        <h2 className="text-[14px] font-semibold text-ink">召回与融合</h2>
        <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
          向量与 BM25 分别召回，按名次做 RRF 融合，再由 Reranker 统一精排。以下数值是可调整的起点，不是固定承诺。
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          <Field label="向量候选数" hint="每路向量召回的候选条数">
            <Input
              type="number"
              min={1}
              value={retrieval.vector_candidates}
              onChange={(event) =>
                patch({
                  retrieval: { ...retrieval, vector_candidates: num(event.target.value, 100) },
                })
              }
            />
          </Field>
          <Field label="BM25 候选数">
            <Input
              type="number"
              min={1}
              value={retrieval.bm25_candidates}
              onChange={(event) =>
                patch({ retrieval: { ...retrieval, bm25_candidates: num(event.target.value, 100) } })
              }
            />
          </Field>
          <Field label="融合后精排条数">
            <Input
              type="number"
              min={1}
              value={retrieval.fusion_top}
              onChange={(event) => patch({ retrieval: { ...retrieval, fusion_top: num(event.target.value, 60) } })}
            />
          </Field>
          <Field label="最终返回条数">
            <Input
              type="number"
              min={1}
              value={retrieval.final_top}
              onChange={(event) => patch({ retrieval: { ...retrieval, final_top: num(event.target.value, 15) } })}
            />
          </Field>
          <Field label="RRF k 值" hint="默认 60，通常无需修改">
            <Input
              type="number"
              min={1}
              value={retrieval.rrf_k}
              onChange={(event) => patch({ retrieval: { ...retrieval, rrf_k: num(event.target.value, 60) } })}
            />
          </Field>
          <Field label="相邻上下文块数">
            <Input
              type="number"
              min={0}
              value={retrieval.expand_neighbors}
              onChange={(event) =>
                patch({ retrieval: { ...retrieval, expand_neighbors: num(event.target.value, 1) } })
              }
            />
          </Field>
        </div>
        <label className="mt-4 flex items-center gap-2 text-[12.5px] text-ink-soft">
          <input
            type="checkbox"
            className="accent-[var(--accent)]"
            checked={retrieval.expand_parent}
            onChange={(event) => patch({ retrieval: { ...retrieval, expand_parent: event.target.checked } })}
          />
          命中片段时同时返回父级章节（保留完整语境）
        </label>
      </Card>

      <Card className="p-5">
        <h2 className="text-[14px] font-semibold text-ink">结构感知切分</h2>
        <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
          尽量保持自然段、史料、题干与选项完整；超出上限的语义单元会继续拆分并保留父子关系。
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          <Field label="子块目标 Token">
            <Input
              type="number"
              min={32}
              value={chunking.target_tokens}
              onChange={(event) => patch({ chunking: { ...chunking, target_tokens: num(event.target.value, 384) } })}
            />
          </Field>
          <Field label="子块上限 Token">
            <Input
              type="number"
              min={32}
              value={chunking.max_tokens}
              onChange={(event) => patch({ chunking: { ...chunking, max_tokens: num(event.target.value, 512) } })}
            />
          </Field>
          <Field label="子块最小 Token">
            <Input
              type="number"
              min={1}
              value={chunking.min_tokens}
              onChange={(event) => patch({ chunking: { ...chunking, min_tokens: num(event.target.value, 64) } })}
            />
          </Field>
          <Field label="父级目标 Token">
            <Input
              type="number"
              min={64}
              value={chunking.parent_target_tokens}
              onChange={(event) =>
                patch({ chunking: { ...chunking, parent_target_tokens: num(event.target.value, 2048) } })
              }
            />
          </Field>
          <Field label="父级上限 Token">
            <Input
              type="number"
              min={64}
              value={chunking.parent_max_tokens}
              onChange={(event) =>
                patch({ chunking: { ...chunking, parent_max_tokens: num(event.target.value, 4096) } })
              }
            />
          </Field>
          <Field label="重叠 Token">
            <Input
              type="number"
              min={0}
              value={chunking.overlap_tokens}
              onChange={(event) => patch({ chunking: { ...chunking, overlap_tokens: num(event.target.value, 0) } })}
            />
          </Field>
        </div>
      </Card>

      <Card className="p-5">
        <h2 className="text-[14px] font-semibold text-ink">任务预算</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Field label="最大步骤数" hint="防止 Agent 无限循环的硬上限">
            <Input
              type="number"
              min={1}
              value={settings.max_task_steps}
              onChange={(event) => patch({ max_task_steps: num(event.target.value, 120) })}
            />
          </Field>
          <Field label="费用上限（美元，可选）" hint="留空表示不限制；未配置价格时无法估算费用。">
            <Input
              type="number"
              min={0}
              step="0.01"
              value={settings.task_budget_usd ?? ""}
              placeholder="不限制"
              onChange={(event) =>
                patch({ task_budget_usd: event.target.value.trim() === "" ? null : Number(event.target.value) })
              }
            />
          </Field>
        </div>
      </Card>

      <div className="flex items-center justify-between">
        <span className="text-[12px] text-muted">修改会在下一次索引或检索时使用。</span>
        <Button variant="primary" onClick={() => void save()} disabled={saving}>
          {saving ? <SpinnerIcon className="size-4 animate-spin" /> : <CheckIcon className="size-4" />}
          保存参数
        </Button>
      </div>

      <Card className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-[14px] font-semibold text-ink">向量索引状态</h2>
            <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
              每个 Embedding 模型对应独立的 LanceDB 表。同一资料库可共存多个模型；即使维度相同，也不会混用向量。
            </p>
          </div>
          <Badge tone="neutral">{profiles.length} 个向量空间</Badge>
        </div>
        {profiles.length === 0 ? (
          <p className="mt-3 rounded-lg border border-dashed border-line px-3 py-3 text-[12px] text-muted">
            尚未建立向量索引。配置 Embedding 并完成索引后，这里会显示各模型的空间、维度与已索引版本数。
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {profiles.map((profile) => (
              <li
                key={profile.id}
                className="rounded-lg border border-line bg-surface-muted px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[12.5px] font-medium text-ink">{profile.model}</span>
                  <Badge tone="neutral">{profile.dimensions} 维</Badge>
                  <Badge tone={profile.compatible ? "ok" : "warn"}>
                    {profile.compatible ? "可用" : "端点不匹配"}
                  </Badge>
                  <span className="text-[10.5px] text-muted">表 {profile.table_name}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-muted">
                  <span>{profile.version_count} 个文档版本</span>
                  <span>{profile.table_vectors} 条向量</span>
                  <span>端点：{profile.endpoint_name ?? "已移除"}</span>
                  {profile.last_used_at ? (
                    <span>最近使用 {new Date(profile.last_used_at).toLocaleDateString("zh-CN")}</span>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-[11px] leading-relaxed text-muted">
          「端点不匹配」表示原模型已更换或维度/查询指令发生变化，该空间不会参与向量检索；原文仍可通过关键词索引使用。
        </p>
      </Card>

      <Banner tone="neutral" title="提示">
        <span className="inline-flex items-start gap-1.5">
          <AlertIcon className="mt-0.5 size-3.5 shrink-0" />
          尚无真实语料基准数据前，这些参数只是合理的初始值；完成 S/M 档基准后会根据实测调整并记录。
        </span>
      </Banner>
    </div>
  );
}
