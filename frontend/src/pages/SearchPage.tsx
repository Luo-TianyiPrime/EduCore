import { useEffect, useState } from "react";
import { PageHeader } from "../components/AppShell";
import { AlertIcon, SearchIcon, Spinner } from "../components/icons";
import {
  Badge,
  Banner,
  Button,
  Card,
  EmptyState,
  Input,
  Modal,
  Select,
} from "../components/ui";
import { api, ApiError, pageImageUrl } from "../lib/api";
import type { SearchHit, SearchResponse, SourceFolder } from "../types";

function scoreLabel(hit: SearchHit): string {
  if (hit.rerank_score !== null) return `精排 ${hit.rerank_score.toFixed(3)}`;
  return `融合 ${hit.rrf_score.toFixed(4)}`;
}

export function SearchPage() {
  const [sources, setSources] = useState<SourceFolder[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showDebug, setShowDebug] = useState(false);
  const [pageView, setPageView] = useState<SearchHit | null>(null);
  const [pageError, setPageError] = useState(false);

  useEffect(() => {
    api
      .getSources()
      .then((data) => setSources(data.items))
      .catch(() => setSources([]));
  }, []);

  const runSearch = async () => {
    const text = query.trim();
    if (!text) {
      setError("请输入查询内容");
      return;
    }
    setLoading(true);
    setError(null);
    setExpanded(new Set());
    try {
      const response = await api.search({
        query: text,
        source_ids: sourceId ? [sourceId] : undefined,
      });
      setResult(response);
      setShowDebug(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "检索失败");
    } finally {
      setLoading(false);
    }
  };

  const toggle = (chunkId: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(chunkId)) next.delete(chunkId);
      else next.add(chunkId);
      return next;
    });
  };

  const canShowPage = pageView?.document_format === "pdf" || pageView?.document_format === "image";

  return (
    <>
      <PageHeader
        eyebrow="知识检索"
        title="检索你的资料库"
        description="向量与关键词分别召回，按名次做 RRF 融合后统一精排，并扩展父级与相邻上下文。结果可回跳原文。"
        actions={<Badge tone="accent">多模型索引</Badge>}
      />

      <Card className="mb-4 p-4">
        <div className="flex flex-wrap gap-2">
          <div className="relative min-w-[240px] flex-1">
            <SearchIcon className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted" />
            <Input
              className="pl-8"
              placeholder="输入概念、人物、事件或问题，例如：地球自转产生什么现象"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void runSearch();
              }}
            />
          </div>
          <div className="w-52">
            <Select value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
              <option value="">全部资料</option>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.path}
                </option>
              ))}
            </Select>
          </div>
          <Button variant="primary" disabled={loading} onClick={() => void runSearch()}>
            {loading ? <Spinner className="size-4 animate-spin" /> : <SearchIcon className="size-4" />}
            检索
          </Button>
        </div>
        <p className="mt-2 text-[11.5px] text-muted">
          资料范围以当前有效版本为准；已删除或已被新版本替代的文件不会返回旧内容。
        </p>
      </Card>

      {error ? (
        <Banner tone="danger" className="mb-4">
          {error}
        </Banner>
      ) : null}

      {loading ? (
        <Card className="p-10">
          <div className="flex items-center justify-center gap-2 text-[13px] text-muted">
            <Spinner className="animate-spin" /> 正在召回与精排…
          </div>
        </Card>
      ) : null}

      {!loading && !result ? (
        <Card className="p-5">
          <EmptyState
            icon={<SearchIcon className="size-5" />}
            title="输入查询开始检索"
            description="检索会同时使用已建立的向量索引和中文关键词索引；如果某套向量模型不可用，会明确提示并继续使用可用数据。"
          />
        </Card>
      ) : null}

      {!loading && result ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted">
            <span className="font-medium text-ink">命中 {result.hits.length} 条</span>
            <span>范围 {result.debug.scope_documents} 个文档</span>
            <span>向量候选 {result.debug.vector_candidates}</span>
            <span>关键词候选 {result.debug.bm25_candidates}</span>
            <span>{result.debug.reranked ? "已精排" : "未精排"}</span>
            <span>总耗时 {result.debug.timings_ms.total ?? 0} ms</span>
            <div className="flex-1" />
            <Button size="sm" variant="ghost" onClick={() => setShowDebug((value) => !value)}>
              {showDebug ? "收起检索详情" : "检索详情"}
            </Button>
          </div>

          {result.debug.degraded.length > 0 ? (
            <Banner tone="warn" title="部分能力降级">
              <ul className="list-inside list-disc space-y-0.5">
                {result.debug.degraded.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </Banner>
          ) : null}

          {showDebug ? (
            <Card className="p-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <div className="mb-2 text-[12px] font-medium text-ink-soft">向量空间</div>
                  {result.debug.profiles.length === 0 ? (
                    <p className="text-[12px] text-muted">没有可用的向量空间。</p>
                  ) : (
                    <ul className="space-y-2">
                      {result.debug.profiles.map((profile) => (
                        <li
                          key={profile.profile_id}
                          className="rounded-lg border border-line bg-surface-muted px-3 py-2 text-[11.5px]"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-ink">{profile.model}</span>
                            <Badge tone="neutral">{profile.dimensions} 维</Badge>
                            <span className="text-muted">{profile.versions} 个版本</span>
                            <span className="text-muted">召回 {profile.candidates}</span>
                          </div>
                          {profile.note ? <div className="mt-1 text-warn">{profile.note}</div> : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <div className="mb-2 text-[12px] font-medium text-ink-soft">耗时与融合</div>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11.5px]">
                    {Object.entries(result.debug.timings_ms).map(([key, value]) => (
                      <div key={key} className="flex justify-between gap-2 border-b border-line/60 pb-1">
                        <dt className="text-muted">
                          {{
                            scope: "范围解析",
                            vector: "向量召回",
                            bm25: "关键词召回",
                            fusion: "融合",
                            rerank: "精排",
                            expand: "上下文扩展",
                            total: "总计",
                          }[key] ?? key}
                        </dt>
                        <dd className="font-mono text-ink">{value} ms</dd>
                      </div>
                    ))}
                    <div className="col-span-2 text-muted">
                      融合候选 {result.debug.fused_candidates} · 三角索引
                      {result.debug.trigram_used ? "已启用" : "未启用"}
                    </div>
                  </dl>
                </div>
              </div>
            </Card>
          ) : null}

          {result.hits.length === 0 ? (
            <Card className="p-5">
              <EmptyState
                icon={<AlertIcon className="size-5" />}
                title="没有找到相关内容"
                description="可以尝试更短的关键词、检查资料范围，或先为资料建立向量索引。"
              />
            </Card>
          ) : (
            <div className="space-y-3">
              {result.hits.map((hit) => {
                const isExpanded = expanded.has(hit.chunk_id);
                return (
                  <Card key={hit.chunk_id} className="p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-[13.5px] font-medium text-ink">
                          {hit.document_title || hit.document_path}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-muted">
                          {hit.section_path.length > 0 ? <span>{hit.section_path.join(" / ")}</span> : null}
                          {hit.page_start ? (
                            <span>
                              第 {hit.page_start}
                              {hit.page_end && hit.page_end !== hit.page_start ? `–${hit.page_end}` : ""} 页
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge tone="accent">{scoreLabel(hit)}</Badge>
                        {hit.vector_rank ? <Badge tone="neutral">向量 #{hit.vector_rank}</Badge> : null}
                        {hit.bm25_rank ? <Badge tone="neutral">关键词 #{hit.bm25_rank}</Badge> : null}
                      </div>
                    </div>

                    <div className="mt-3 rounded-lg border border-line bg-surface-muted/70 px-3.5 py-3">
                      {hit.heading ? (
                        <div className="mb-1 text-[12px] font-medium text-accent-ink">{hit.heading}</div>
                      ) : null}
                      <p className="whitespace-pre-wrap text-[12.5px] leading-[1.75] text-ink-soft">
                        {isExpanded || hit.text.length <= 320
                          ? hit.text
                          : `${hit.text.slice(0, 320)}…`}
                      </p>
                      {isExpanded && hit.context && hit.context !== hit.text ? (
                        <div className="mt-2 border-t border-line pt-2 text-[11.5px] leading-relaxed text-muted">
                          <div className="mb-1 font-medium">扩展上下文（父级章节与相邻段落）</div>
                          <p className="whitespace-pre-wrap">{hit.context}</p>
                        </div>
                      ) : null}
                    </div>

                    <div className="mt-2.5 flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => toggle(hit.chunk_id)}
                        className="text-[11.5px] text-accent underline-offset-2 hover:underline"
                      >
                        {isExpanded ? "收起上下文" : "展开上下文"}
                      </button>
                      <span className="text-muted/60">·</span>
                      <button
                        type="button"
                        className="text-[11.5px] text-accent underline-offset-2 hover:underline"
                        onClick={() => {
                          setPageError(false);
                          setPageView(hit);
                        }}
                      >
                        查看原文
                      </button>
                      <span className="min-w-0 flex-1 truncate text-right font-mono text-[10.5px] text-muted">
                        {hit.document_path}
                      </span>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </div>
      ) : null}

      <Modal
        open={pageView !== null}
        title={pageView?.document_title || "原文"}
        description={
          pageView?.page_start
            ? `${pageView.document_path} · 第 ${pageView.page_start} 页`
            : pageView?.document_path
        }
        onClose={() => setPageView(null)}
        footer={
          <Button variant="secondary" onClick={() => setPageView(null)}>
            关闭
          </Button>
        }
      >
        {canShowPage && pageView?.page_start ? (
          pageError ? (
            <Banner tone="danger">页面图像无法加载，原始文件可能已被移动。</Banner>
          ) : (
            <div className="max-h-[65vh] overflow-auto rounded-lg border border-line bg-surface-muted">
              <img
                src={pageImageUrl(pageView.document_id, pageView.page_start)}
                alt={`第 ${pageView.page_start} 页`}
                className="mx-auto block max-w-full"
                onError={() => setPageError(true)}
              />
            </div>
          )
        ) : (
          <div className="max-h-[60vh] overflow-auto rounded-lg border border-line bg-surface-muted p-4">
            <p className="whitespace-pre-wrap text-[12.5px] leading-[1.8] text-ink-soft">
              {pageView?.context || pageView?.text}
            </p>
          </div>
        )}
      </Modal>
    </>
  );
}