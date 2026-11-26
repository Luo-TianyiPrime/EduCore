import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "../components/AppShell";
import {
  AlertIcon,
  CheckIcon,
  FileIcon,
  LibraryIcon,
  PlusIcon,
  RefreshIcon,
  SearchIcon,
  Spinner,
  TrashIcon,
} from "../components/icons";
import {
  Badge,
  Banner,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Modal,
  Select,
  cn,
} from "../components/ui";
import { api, ApiError } from "../lib/api";
import type { DocumentDetail, DocumentRow, IndexJob, SourceFolder } from "../types";
import { DOC_STATUS, FORMAT_LABEL, isActiveJob } from "../library/status";
import { ReaderPanel } from "../library/ReaderPanel";

function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function formatDate(value: string): string {
  try {
    return new Date(value).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return value;
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function LibraryPage() {
  const [sources, setSources] = useState<SourceFolder[]>([]);
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [jobs, setJobs] = useState<IndexJob[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [sourceFilter, setSourceFilter] = useState<string | "all">("all");
  const [statusFilter, setStatusFilter] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [dialog, setDialog] = useState<null | "folder" | "file" | "delete-source" | "delete-document">(null);
  const [pathValue, setPathValue] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; label: string } | null>(null);

  const [reloadToken, setReloadToken] = useState(0);
  const reload = useCallback(() => setReloadToken((value) => value + 1), []);

  const loadSources = useCallback(async () => {
    try {
      const data = await api.getSources();
      setSources(data.items);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "读取资料文件夹失败");
    }
  }, []);

  const loadJobs = useCallback(async () => {
    try {
      const data = await api.getJobs(20);
      setJobs(data.items);
    } catch {
      /* keep last known jobs */
    }
  }, []);

  useEffect(() => {
    void loadSources();
    void loadJobs();
  }, [reloadToken, loadSources, loadJobs]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getDocuments({
          sourceId: sourceFilter === "all" ? undefined : sourceFilter,
          status: statusFilter || undefined,
          query: appliedQuery || undefined,
        });
        if (cancelled) return;
        setDocuments(data.items);
        setCounts(data.counts);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "读取文档列表失败");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceFilter, statusFilter, appliedQuery, reloadToken]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    api
      .getDocument(selectedId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "读取文档详情失败");
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, reloadToken]);

  // Poll job progress while the library is open.
  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadJobs();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [loadJobs]);

  const activeCount = jobs.filter((job) => isActiveJob(job.status)).length;
  const previousActive = useRef(0);
  useEffect(() => {
    if (previousActive.current > 0 && activeCount === 0) {
      reload();
    }
    previousActive.current = activeCount;
  }, [activeCount, reload]);

  const activeJobs = jobs.filter((job) => isActiveJob(job.status));

  const submitAdd = async () => {
    const path = pathValue.trim();
    if (!path) {
      setError("请填写完整路径，或使用「浏览…」选择本地位置。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (dialog === "folder") {
        const result = await api.addSource({ path, recursive, scan: true });
        setSourceFilter(result.source.id);
      } else {
        const result = await api.addDocument(path);
        setSelectedId(result.document.id);
      }
      setDialog(null);
      setPathValue("");
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "添加失败");
    } finally {
      setBusy(false);
    }
  };

  const browse = async () => {
    setPicking(true);
    setError(null);
    try {
      const mode = dialog === "file" ? "file" : "directory";
      const result = await api.pickPath(mode);
      if (result.path) setPathValue(result.path);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `${err.message} 你仍可直接在下方输入绝对路径。`
          : "系统选择器不可用，请手动输入绝对路径。",
      );
    } finally {
      setPicking(false);
    }
  };

  const scanSource = async (sourceId: string) => {
    setError(null);
    try {
      await api.scanSource(sourceId);
      await loadJobs();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "启动扫描失败");
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setBusy(true);
    try {
      if (dialog === "delete-source") {
        await api.deleteSource(deleteTarget.id);
        if (sourceFilter === deleteTarget.id) setSourceFilter("all");
      } else {
        await api.deleteDocument(deleteTarget.id);
        if (selectedId === deleteTarget.id) setSelectedId(null);
      }
      setDialog(null);
      setDeleteTarget(null);
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "删除失败");
    } finally {
      setBusy(false);
    }
  };

  const reindexSelected = async () => {
    if (!detail) return;
    try {
      await api.reindexDocument(detail.document.id);
      await loadJobs();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "重新索引失败");
    }
  };

  const rebuildVectors = async (payload: { source_id?: string; document_id?: string }) => {
    setError(null);
    try {
      await api.rebuildVectors(payload);
      await loadJobs();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "向量重建失败");
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="资料管理"
        title="资料库"
        description="添加本地文件夹或单个文件，程序递归扫描并建立索引。原始文件只读引用，不会被复制或修改。"
        actions={
          <>
            <Button variant="secondary" onClick={() => setDialog("file")}>
              <FileIcon className="size-4" />
              添加文件
            </Button>
            <Button
              variant="secondary"
              disabled={sources.length === 0}
              onClick={() => {
                void (async () => {
                  const targets = sources.map((source) => source.id);
                  for (const id of targets) {
                    await scanSource(id);
                  }
                })();
              }}
            >
              <RefreshIcon className="size-4" />
              全部重新扫描
            </Button>
            <Button variant="primary" onClick={() => setDialog("folder")}>
              <PlusIcon className="size-4" />
              添加文件夹
            </Button>
          </>
        }
      />

      {error ? (
        <Banner tone="danger" className="mb-4">
          {error}
        </Banner>
      ) : null}

      {activeJobs.length > 0 ? (
        <Card className="mb-4 p-4">
          <div className="flex items-center gap-2 text-[13px] font-medium text-ink">
            <Spinner className="animate-spin" />
            正在建立索引
          </div>
          <div className="mt-3 space-y-3">
            {activeJobs.map((job) => {
              const total = job.total || 0;
              const percent = total > 0 ? Math.min(100, Math.round((job.processed / total) * 100)) : 0;
              return (
                <div key={job.id}>
                  <div className="mb-1 flex items-center justify-between text-[11.5px] text-muted">
                    <span>
                      {job.kind === "document" ? "重新索引文档" : "扫描资料文件夹"}
                      {job.message ? ` · ${job.message}` : ""}
                    </span>
                    <span>
                      {job.processed}/{total || "?"} {job.failed > 0 ? `· 失败 ${job.failed}` : ""}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-surface-strong">
                    <div
                      className={cn(
                        "h-full rounded-full bg-accent transition-[width]",
                        total === 0 && "w-1/3 animate-pulse",
                      )}
                      style={total > 0 ? { width: `${percent}%` } : undefined}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      ) : null}

      {sources.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<LibraryIcon className="size-5" />}
            title="还没有添加任何资料"
            description="选择包含教材、真题、试卷或教研资料的本地文件夹。程序会递归扫描受支持的 PDF、Markdown、OCR JSON 与图片文件。"
            action={
              <Button variant="primary" onClick={() => setDialog("folder")}>
                <PlusIcon className="size-4" />
                添加资料文件夹
              </Button>
            }
          />
        </Card>
      ) : (
        <>
          <div className="mb-5 grid gap-3 md:grid-cols-2">
            {sources.map((source) => {
              const active = sourceFilter === source.id;
              return (
                <Card
                  key={source.id}
                  className={cn(
                    "cursor-pointer p-4 transition-colors",
                    active ? "ring-1 ring-accent/40" : "hover:border-line-strong",
                  )}
                >
                  <div
                    onClick={() => setSourceFilter(active ? "all" : source.id)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") setSourceFilter(active ? "all" : source.id);
                    }}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <LibraryIcon className="size-4 shrink-0 text-accent" />
                          <span className="truncate text-[13.5px] font-medium text-ink">
                            {basename(source.path)}
                          </span>
                          {source.status === "scanning" ? (
                            <Badge tone="accent">扫描中</Badge>
                          ) : source.error ? (
                            <Badge tone="danger">异常</Badge>
                          ) : null}
                        </div>
                        <div className="mt-1 break-all font-mono text-[11px] text-muted">
                          {source.path}
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={(event) => {
                            event.stopPropagation();
                            void scanSource(source.id);
                          }}
                        >
                          <RefreshIcon className="size-3.5" />
                          扫描
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label="移除"
                          onClick={(event) => {
                            event.stopPropagation();
                            setDeleteTarget({ id: source.id, label: basename(source.path) });
                            setDialog("delete-source");
                          }}
                        >
                          <TrashIcon className="size-3.5 text-danger" />
                        </Button>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-muted">
                      <span>{source.document_count} 个文档</span>
                      <span className="text-ok">{source.ready_count} 已就绪</span>
                      {source.needs_ocr_count > 0 ? (
                        <span className="text-warn">{source.needs_ocr_count} 需 OCR</span>
                      ) : null}
                      {source.failed_count > 0 ? (
                        <span className="text-danger">{source.failed_count} 失败</span>
                      ) : null}
                      {source.last_scan_at ? (
                        <span>上次扫描 {formatDate(source.last_scan_at)}</span>
                      ) : null}
                    </div>
                    {source.error ? (
                      <div className="mt-2 text-[11.5px] text-danger">{source.error}</div>
                    ) : null}
                  </div>
                </Card>
              );
            })}
          </div>

          <div className="mb-4 flex flex-wrap items-center gap-2">
            <div className="relative w-64">
              <SearchIcon className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted" />
              <Input
                className="pl-8"
                placeholder="搜索标题或路径"
                value={queryInput}
                onChange={(event) => setQueryInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") setAppliedQuery(queryInput.trim());
                }}
              />
            </div>
            <Button variant="secondary" onClick={() => setAppliedQuery(queryInput.trim())}>
              搜索
            </Button>
            <div className="w-36">
              <Select
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
              >
                <option value="">全部状态</option>
                <option value="ready">已就绪</option>
                <option value="needs_ocr">需要 OCR</option>
                <option value="failed">失败</option>
              </Select>
            </div>
            <div className="flex-1" />
            <span className="text-[11.5px] text-muted">
              {counts.present ?? 0} 个有效文档 · {counts.missing ?? 0} 个已移除
            </span>
            {sourceFilter !== "all" ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void rebuildVectors({ source_id: sourceFilter })}
                title="为所选文件夹内全部文档重建向量"
              >
                <RefreshIcon className="size-3.5" />
                重建该文件夹向量
              </Button>
            ) : null}
            <Button size="sm" variant="ghost" onClick={() => reload()}>
              <RefreshIcon className="size-3.5" />
              刷新
            </Button>
          </div>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,430px)_minmax(0,1fr)]">
            <Card className="max-h-[660px] self-start overflow-y-auto">
              {documents.length === 0 ? (
                <div className="px-4 py-10 text-center text-[12.5px] text-muted">
                  当前筛选条件下没有文档。
                </div>
              ) : (
                <ul>
                  {documents.map((document) => {
                    const status = DOC_STATUS[document.parse_status] ?? {
                      label: document.parse_status,
                      tone: "neutral" as const,
                    };
                    return (
                      <li key={document.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedId(document.id)}
                          className={cn(
                            "flex w-full items-start gap-3 border-b border-line px-4 py-3 text-left transition-colors last:border-b-0",
                            selectedId === document.id
                              ? "bg-accent-soft/60"
                              : "hover:bg-surface-muted",
                          )}
                        >
                          <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md border border-line bg-surface text-[9px] font-semibold text-muted">
                            {document.format === "pdf"
                              ? "PDF"
                              : document.format === "markdown"
                                ? "MD"
                                : document.format === "ocr_json"
                                  ? "OCR"
                                  : "IMG"}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5">
                              <span className="truncate text-[13px] font-medium text-ink">
                                {document.title || document.rel_path || basename(document.path)}
                              </span>
                              {!document.is_present ? <Badge tone="neutral">已移除</Badge> : null}
                            </span>
                            <span className="mt-0.5 block truncate font-mono text-[10.5px] text-muted">
                              {document.rel_path || document.path}
                            </span>
                            <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10.5px] text-muted">
                              <Badge tone={status.tone}>{status.label}</Badge>
                              <span>{FORMAT_LABEL[document.format] ?? document.format}</span>
                              <span>{document.page_count} 页</span>
                              <span>{formatSize(document.size)}</span>
                              <span>{formatDate(document.updated_at)}</span>
                            </span>
                          </span>
                          {document.parse_error ? (
                            <AlertIcon className="mt-1 size-3.5 shrink-0 text-danger" />
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Card>

            <Card className="overflow-hidden">
              <ReaderPanel
                detail={detail}
                loading={detailLoading}
                onRefresh={async () => {
                  if (selectedId) {
                    const fresh = await api.getDocument(selectedId);
                    setDetail(fresh);
                  }
                  reload();
                }}
                onReindex={async () => {
                  await reindexSelected();
                }}
                onRebuildVectors={async () => {
                  if (detail) await rebuildVectors({ document_id: detail.document.id });
                }}
                onDelete={() => {
                  if (!detail) return;
                  setDeleteTarget({
                    id: detail.document.id,
                    label: detail.document.title || basename(detail.document.path),
                  });
                  setDialog("delete-document");
                }}
              />
            </Card>
          </div>
        </>
      )}

      <Modal
        open={dialog === "folder" || dialog === "file"}
        title={dialog === "folder" ? "添加资料文件夹" : "添加资料文件"}
        description={
          dialog === "folder"
            ? "选择一个本地文件夹。程序会递归扫描支持的格式，文件保持原位。"
            : "添加单个 PDF、Markdown、OCR JSON 或图片文件。"
        }
        onClose={() => {
          if (busy) return;
          setDialog(null);
        }}
        footer={
          <>
            <Button variant="secondary" disabled={busy} onClick={() => setDialog(null)}>
              取消
            </Button>
            <Button variant="primary" disabled={busy} onClick={() => void submitAdd()}>
              {busy ? <Spinner className="size-4 animate-spin" /> : <CheckIcon className="size-4" />}
              添加并扫描
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field
            label={dialog === "folder" ? "文件夹绝对路径" : "文件绝对路径"}
            hint="浏览器无法直接读取本地路径，请使用「浏览…」调用系统选择器，或手动粘贴完整路径。"
          >
            <div className="flex gap-2">
              <Input
                value={pathValue}
                placeholder={dialog === "folder" ? "D:\\教学资料" : "D:\\教学资料\\真题.pdf"}
                onChange={(event) => setPathValue(event.target.value)}
              />
              <Button variant="secondary" disabled={picking} onClick={() => void browse()}>
                {picking ? <Spinner className="size-4 animate-spin" /> : null}
                浏览…
              </Button>
            </div>
          </Field>
          {dialog === "folder" ? (
            <label className="flex items-center gap-2 text-[12.5px] text-ink-soft">
              <input
                type="checkbox"
                className="accent-[var(--accent)]"
                checked={recursive}
                onChange={(event) => setRecursive(event.target.checked)}
              />
              包含子文件夹（递归扫描）
            </label>
          ) : null}
        </div>
      </Modal>

      <Modal
        open={dialog === "delete-source" || dialog === "delete-document"}
        title={dialog === "delete-source" ? "移除资料文件夹" : "移除文档索引"}
        description={
          dialog === "delete-source"
            ? `确定移除「${deleteTarget?.label ?? ""}」及其全部索引记录？`
            : `确定移除「${deleteTarget?.label ?? ""}」的索引记录？`
        }
        onClose={() => {
          if (busy) return;
          setDialog(null);
          setDeleteTarget(null);
        }}
        footer={
          <>
            <Button variant="secondary" disabled={busy} onClick={() => setDialog(null)}>
              取消
            </Button>
            <Button variant="danger" disabled={busy} onClick={() => void confirmDelete()}>
              确认移除
            </Button>
          </>
        }
      >
        <Banner tone="warn">
          只会删除 EduCore 的索引与解析缓存，不会删除或移动你的原始文件。之后仍可重新添加该目录。
        </Banner>
      </Modal>
    </>
  );
}