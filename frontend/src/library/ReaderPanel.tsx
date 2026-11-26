import { useEffect, useMemo, useState } from "react";
import { ApiError, api, pageImageUrl } from "../lib/api";
import type { BlockRow, DocumentDetail } from "../types";
import {
  AlertIcon,
  ChevronRightIcon,
  FileIcon,
  RefreshIcon,
  SearchIcon,
  Spinner,
} from "../components/icons";
import { Badge, Banner, Button, EmptyState, Input, cn } from "../components/ui";
import { BLOCK_KIND_LABEL, DOC_STATUS, FORMAT_LABEL } from "./status";

export function ReaderPanel({
  detail,
  loading,
  onRefresh,
  onReindex,
  onRebuildVectors,
  onDelete,
}: {
  detail: DocumentDetail | null;
  loading: boolean;
  onRefresh: () => Promise<void>;
  onReindex: () => Promise<void>;
  onRebuildVectors: () => Promise<void>;
  onDelete: () => void;
}) {
  const [tab, setTab] = useState<"structure" | "page">("structure");
  const [sectionId, setSectionId] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<BlockRow[]>([]);
  const [blocksLoading, setBlocksLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [ocrBusy, setOcrBusy] = useState(false);
  const [ocrMessage, setOcrMessage] = useState<string | null>(null);
  const [reindexBusy, setReindexBusy] = useState(false);

  const documentId = detail?.document.id ?? null;

  useEffect(() => {
    setTab("structure");
    setError(null);
    setOcrMessage(null);
    const first = detail?.sections[0];
    setSectionId(first?.id ?? null);
    setPage(first?.page_start ?? 1);
  }, [documentId, detail?.sections]);

  useEffect(() => {
    if (!documentId || !sectionId) {
      setBlocks([]);
      return;
    }
    let cancelled = false;
    setBlocksLoading(true);
    api
      .getSection(documentId, sectionId)
      .then((data) => {
        if (!cancelled) setBlocks(data.blocks);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "读取章节失败");
      })
      .finally(() => {
        if (!cancelled) setBlocksLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, sectionId]);

  const status = detail ? DOC_STATUS[detail.document.parse_status] : null;
  const pageCount = detail?.document.page_count ?? detail?.version?.page_count ?? 1;
  const sortedSections = useMemo(
    () => [...(detail?.sections ?? [])].sort((a, b) => a.order_index - b.order_index),
    [detail?.sections],
  );

  if (loading) {
    return (
      <div className="flex h-full min-h-[320px] items-center justify-center gap-2 text-[13px] text-muted">
        <Spinner className="animate-spin" /> 正在读取文档…
      </div>
    );
  }

  if (!detail) {
    return (
      <EmptyState
        icon={<FileIcon className="size-5" />}
        title="选择一份文档查看原文"
        description="左侧列表来自你添加的真实本地文件。点击文档可查看目录结构、正文内容与 PDF 页面图像。"
      />
    );
  }

  const document = detail.document;
  const canShowPages = document.format === "pdf" || document.format === "image";

  const runOcr = async () => {
    setOcrBusy(true);
    setOcrMessage(null);
    setError(null);
    try {
      const result = await api.ocrPages(document.id, [page]);
      setOcrMessage(
        result.warnings.length
          ? result.warnings.join("；")
          : `第 ${page} 页已重新识别并重建索引。`,
      );
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "OCR 失败");
    } finally {
      setOcrBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-[320px] flex-col">
      <div className="border-b border-line px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-[15px] font-semibold text-ink">
                {document.title || document.rel_path || document.path}
              </h2>
              <Badge tone="neutral">{FORMAT_LABEL[document.format] ?? document.format}</Badge>
              {status ? <Badge tone={status.tone}>{status.label}</Badge> : null}
            </div>
            <div className="mt-1 break-all font-mono text-[11px] text-muted">{document.path}</div>
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-muted">
              <span>{pageCount} 页</span>
              <span>{detail.versions.length} 个版本</span>
              {detail.version ? (
                <>
                  <span>{detail.version.block_count} 个文本块</span>
                  <span>{detail.version.chunk_count} 个索引块</span>
                </>
              ) : null}
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <Button
              size="sm"
              variant="secondary"
              disabled={reindexBusy}
              onClick={async () => {
                setReindexBusy(true);
                try {
                  await onReindex();
                } finally {
                  setReindexBusy(false);
                }
              }}
            >
              <RefreshIcon className={cn("size-3.5", reindexBusy && "animate-spin")} />
              重新索引
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void onRebuildVectors()}
              title="仅重建该文档的向量索引，不重新解析原文"
            >
              重建向量
            </Button>
            <Button size="sm" variant="ghost" onClick={onDelete}>
              移除
            </Button>
          </div>
        </div>

        {document.parse_status === "needs_ocr" ? (
          <Banner tone="warn" className="mt-3">
            该文档的部分页面没有有效文本层。请先在系统设置中配置 OCR，再使用「重新索引」或下方「识别本页」。
          </Banner>
        ) : null}
        {document.parse_status === "failed" && document.parse_error ? (
          <Banner tone="danger" title="解析失败" className="mt-3">
            {document.parse_error}
          </Banner>
        ) : null}
        {error ? (
          <Banner tone="danger" className="mt-3">
            {error}
          </Banner>
        ) : null}
        {ocrMessage ? (
          <Banner tone="ok" className="mt-3">
            {ocrMessage}
          </Banner>
        ) : null}
      </div>

      <div className="flex border-b border-line px-5">
        {[
          { id: "structure" as const, label: "结构与正文" },
          { id: "page" as const, label: "页面图像" },
        ].map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            className={cn(
              "-mb-px px-3 py-2.5 text-[12.5px] font-medium transition-colors",
              tab === item.id
                ? "border-b-2 border-accent text-ink"
                : "border-b-2 border-transparent text-muted hover:text-ink",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "structure" ? (
        <div className="grid min-h-0 flex-1 grid-cols-[210px_minmax(0,1fr)]">
          <div className="max-h-[560px] overflow-y-auto border-r border-line p-2">
            {sortedSections.length === 0 ? (
              <div className="px-2 py-3 text-[12px] text-muted">没有可显示的章节结构。</div>
            ) : null}
            {sortedSections.map((section) => (
              <button
                key={section.id}
                type="button"
                onClick={() => setSectionId(section.id)}
                className={cn(
                  "mb-0.5 flex w-full items-start gap-1.5 rounded-md py-1.5 pr-2 text-left transition-colors",
                  sectionId === section.id
                    ? "bg-accent-soft text-accent-ink"
                    : "text-ink-soft hover:bg-surface-strong",
                )}
                style={{ paddingLeft: 6 + Math.max(0, section.level - 1) * 10 }}
              >
                <ChevronRightIcon className="mt-0.5 size-3.5 shrink-0 opacity-60" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] font-medium">
                    {section.title || "未命名章节"}
                  </span>
                  <span className="block text-[10.5px] text-muted">
                    {section.level > 1 ? `第 ${section.level} 级 · ` : ""}
                    {section.page_start && section.title !== `第 ${section.page_start} 页`
                      ? `第 ${section.page_start}${section.page_end && section.page_end !== section.page_start ? `–${section.page_end}` : ""} 页`
                      : `${section.block_count} 段`}
                  </span>
                </span>
              </button>
            ))}
          </div>

          <div className="max-h-[560px] overflow-y-auto px-5 py-4">
            {blocksLoading ? (
              <div className="flex items-center gap-2 text-[12.5px] text-muted">
                <Spinner className="animate-spin" /> 读取章节…
              </div>
            ) : blocks.length === 0 ? (
              <p className="text-[12.5px] text-muted">该章节没有正文内容。</p>
            ) : (
              <div className="space-y-3.5">
                {blocks.map((block, blockIndex) => {
                  const prevPage = blockIndex > 0 ? blocks[blockIndex - 1].page : null;
                  return (
                  <div key={block.id}>
                    {block.kind !== "paragraph" ? (
                      <Badge tone="neutral" className="mb-1">
                        {BLOCK_KIND_LABEL[block.kind] ?? block.kind}
                      </Badge>
                    ) : null}
                    <p className="whitespace-pre-wrap text-[13px] leading-[1.75] text-ink-soft">
                      {block.text}
                    </p>
                    {block.page && block.page !== prevPage ? (
                      <button
                        type="button"
                        onClick={() => {
                          setPage(block.page ?? 1);
                          if (canShowPages) setTab("page");
                        }}
                        className="mt-1 text-[11px] text-muted underline-offset-2 hover:text-accent hover:underline"
                      >
                        原文第 {block.page} 页
                      </button>
                    ) : null}
                  </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {!canShowPages ? (
            <EmptyState
              icon={<SearchIcon className="size-5" />}
              title="该格式没有页面图像"
              description="Markdown 与 OCR JSON 请使用「结构与正文」查看；PDF 保留原页渲染能力。"
            />
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                  上一页
                </Button>
                <Input
                  className="w-20 text-center"
                  type="number"
                  min={1}
                  max={pageCount}
                  value={page}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    if (Number.isFinite(value)) setPage(Math.min(pageCount, Math.max(1, value)));
                  }}
                />
                <span className="text-[12px] text-muted">/ {pageCount}</span>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={page >= pageCount}
                  onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                >
                  下一页
                </Button>
                <div className="flex-1" />
                <Button size="sm" variant="secondary" disabled={ocrBusy} onClick={() => void runOcr()}>
                  {ocrBusy ? <Spinner className="size-3.5 animate-spin" /> : <RefreshIcon className="size-3.5" />}
                  识别本页
                </Button>
              </div>
              <div className="overflow-hidden rounded-lg border border-line bg-surface-muted">
                <img
                  key={`${document.id}-${page}`}
                  src={pageImageUrl(document.id, page)}
                  alt={`第 ${page} 页`}
                  className="mx-auto block max-w-full"
                  loading="lazy"
                />
              </div>
              <p className="text-[11.5px] text-muted">
                <AlertIcon className="mr-1 inline size-3.5 align-[-2px]" />
                页面图像来自本地原件渲染；原始文件不会被修改。
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}