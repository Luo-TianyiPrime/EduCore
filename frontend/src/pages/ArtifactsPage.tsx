import { useEffect, useMemo, useState } from "react";
import MarkdownIt from "markdown-it";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/AppShell";
import {
  AlertIcon,
  ArtifactsIcon,
  CheckIcon,
  FileIcon,
  PencilIcon,
  RefreshIcon,
  Spinner,
  XIcon,
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
  Textarea,
  cn,
} from "../components/ui";
import { api, ApiError, artifactDownloadUrl } from "../lib/api";
import type {
  ArtifactDetail,
  ArtifactRow,
  ArtifactSectionNode,
  ArtifactValidation,
} from "../types";

const md = new MarkdownIt({ html: false, linkify: true, breaks: false });

const KIND_LABEL: Record<string, string> = {
  outline: "教学大纲",
  lesson: "教案讲义",
  exam: "试卷",
  summary: "总结",
  analysis: "分析",
  comparison: "比较",
  document: "文档",
};

const STATUS_META: Record<string, { label: string; tone: "ok" | "warn" | "danger" }> = {
  pass: { label: "通过", tone: "ok" },
  warn: { label: "提示", tone: "warn" },
  fail: { label: "未通过", tone: "danger" },
};

function flattenSections(nodes: ArtifactSectionNode[] | undefined, depth = 0): { title: string; level: number; depth: number }[] {
  if (!nodes) return [];
  const result: { title: string; level: number; depth: number }[] = [];
  for (const node of nodes) {
    result.push({ title: node.title, level: node.level, depth });
    result.push(...flattenSections(node.children, depth + 1));
  }
  return result;
}

function validationTone(validation: ArtifactValidation): "ok" | "warn" | "danger" {
  return validation.overall === "fail" ? "danger" : validation.overall === "warn" ? "warn" : "ok";
}

export function ArtifactsPage() {
  const [artifacts, setArtifacts] = useState<ArtifactRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [lastExport, setLastExport] = useState<{ format: string; filename: string } | null>(null);
  const [includeCitations, setIncludeCitations] = useState(true);
  const [outputDir, setOutputDir] = useState("");
  const [regenOpen, setRegenOpen] = useState(false);
  const [regenHeading, setRegenHeading] = useState("");
  const [regenInstruction, setRegenInstruction] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getArtifacts();
        if (cancelled) return;
        setArtifacts(data.items);
        if (data.items.length > 0) setSelectedId((current) => current ?? data.items[0].id);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "读取成果列表失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getArtifact(selectedId);
        if (cancelled) return;
        setDetail(data);
        setDraft(data.version.markdown);
        setEditing(false);
        setLastExport(null);
        const headings = flattenSections(data.structure.sections);
        setRegenHeading(headings[0]?.title ?? "");
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "读取成果失败");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const preview = useMemo(
    () => (detail ? md.render(detail.version.markdown) : ""),
    [detail],
  );
  const headings = useMemo(
    () => flattenSections(detail?.structure.sections),
    [detail],
  );

  const refreshList = async () => {
    try {
      const data = await api.getArtifacts();
      setArtifacts(data.items);
    } catch {
      /* keep current list */
    }
  };

  const save = async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await api.updateArtifact(detail.artifact.id, draft);
      setDetail(updated);
      setEditing(false);
      setMessage("已保存为新版本。原有版本仍保留在版本历史中。");
      await refreshList();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const exportAs = async (format: "md" | "docx" | "json" | "pdf") => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.exportArtifact(detail.artifact.id, {
        format,
        include_citations: includeCitations,
        output_dir: outputDir.trim() || undefined,
      });
      setMessage(`已导出 ${result.filename}`);
      setLastExport({ format: result.format, filename: result.filename });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "导出失败");
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await api.regenerateArtifact(
        detail.artifact.id,
        regenHeading,
        regenInstruction,
      );
      setDetail(updated);
      setDraft(updated.version.markdown);
      setRegenOpen(false);
      setRegenInstruction("");
      setMessage(`已局部重生成「${regenHeading}」，其他章节保持不变。`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "重新生成失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="成果管理"
        title="生成的成果"
        description="预览、编辑与导出 Agent 生成的文档，支持按章节局部重新生成。编辑会创建新版本，不会覆盖历史。"
        actions={
          <Button variant="secondary" onClick={() => void refreshList()}>
            <RefreshIcon className="size-4" />
            刷新
          </Button>
        }
      />

      {error ? (
        <Banner tone="danger" className="mb-4">
          {error}
        </Banner>
      ) : null}
      {message ? (
        <Banner tone="ok" className="mb-4">
          <span className="flex flex-wrap items-center gap-2">
            {message}
            {lastExport ? (
              <a
                className="font-medium underline underline-offset-2"
                href={artifactDownloadUrl(detail?.artifact.id ?? "", lastExport.format)}
              >
                下载 {lastExport.filename}
              </a>
            ) : null}
          </span>
        </Banner>
      ) : null}

      {loading ? (
        <div className="flex items-center gap-2 text-[13px] text-muted">
          <Spinner className="animate-spin" /> 正在读取成果…
        </div>
      ) : artifacts.length === 0 ? (
        <Card className="p-5">
          <EmptyState
            icon={<ArtifactsIcon className="size-5" />}
            title="还没有成果"
            description="任务完成后会自动生成成果记录，可在这里预览、编辑与导出。"
            action={
              <Link to="/">
                <Button variant="primary">前往工作台</Button>
              </Link>
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
          <Card className="max-h-[760px] self-start overflow-y-auto">
            <ul>
              {artifacts.map((artifact) => (
                <li key={artifact.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(artifact.id)}
                    className={cn(
                      "flex w-full flex-col gap-1 border-b border-line px-4 py-3 text-left last:border-b-0",
                      selectedId === artifact.id ? "bg-accent-soft/60" : "hover:bg-surface-muted",
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="line-clamp-2 text-[12.5px] font-medium text-ink">
                        {artifact.title}
                      </span>
                      <Badge tone="neutral">{KIND_LABEL[artifact.kind] ?? artifact.kind}</Badge>
                    </div>
                    <span className="text-[10.5px] text-muted">
                      v{artifact.version} · {new Date(artifact.updated_at).toLocaleString("zh-CN")}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>

          <div className="space-y-4">
            {detail ? (
              <>
                <Card className="overflow-hidden">
                  <div className="border-b border-line px-5 py-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h2 className="text-[15px] font-semibold text-ink">{detail.artifact.title}</h2>
                          <Badge tone="neutral">{KIND_LABEL[detail.artifact.kind] ?? detail.artifact.kind}</Badge>
                          <Badge tone="accent">v{detail.version.version}</Badge>
                          {detail.artifact.task_id ? (
                            <Link
                              to={`/tasks?task=${detail.artifact.task_id}`}
                              className="text-[11px] text-accent hover:underline"
                            >
                              查看任务
                            </Link>
                          ) : null}
                        </div>
                        <div className="mt-1 text-[10.5px] text-muted">
                          更新于 {new Date(detail.artifact.updated_at).toLocaleString("zh-CN")} ·{" "}
                          {detail.version.markdown.length} 字符
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {editing ? (
                          <>
                            <Button size="sm" variant="secondary" disabled={busy} onClick={() => { setEditing(false); setDraft(detail.version.markdown); }}>
                              取消
                            </Button>
                            <Button size="sm" variant="primary" disabled={busy} onClick={() => void save()}>
                              {busy ? <Spinner className="size-3.5 animate-spin" /> : <CheckIcon className="size-3.5" />}
                              保存
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
                              <PencilIcon className="size-3.5" />
                              编辑
                            </Button>
                            <Button size="sm" variant="secondary" onClick={() => setRegenOpen(true)}>
                              <RefreshIcon className="size-3.5" />
                              局部重生成
                            </Button>
                          </>
                        )}
                      </div>
                    </div>
                  </div>

                  {editing ? (
                    <div className="p-4">
                      <Textarea
                        rows={24}
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        className="font-mono text-[12px] leading-relaxed"
                      />
                    </div>
                  ) : (
                    <div className="max-h-[560px] overflow-y-auto px-6 py-5">
                      <article className="md-preview" dangerouslySetInnerHTML={{ __html: preview }} />
                    </div>
                  )}

                  <div className="flex flex-wrap items-center gap-2 border-t border-line bg-surface-muted px-5 py-3">
                    <span className="text-[11.5px] text-muted">导出：</span>
                    {(["md", "docx", "json", "pdf"] as const).map((format) => (
                      <Button
                        key={format}
                        size="sm"
                        variant="secondary"
                        disabled={busy}
                        onClick={() => void exportAs(format)}
                      >
                        <FileIcon className="size-3.5" />
                        {format.toUpperCase()}
                      </Button>
                    ))}
                    <label className="ml-1 flex items-center gap-1.5 text-[11px] text-muted">
                      <input
                        type="checkbox"
                        className="accent-[var(--accent)]"
                        checked={includeCitations}
                        onChange={(event) => setIncludeCitations(event.target.checked)}
                      />
                      打印稿保留显式引用
                    </label>
                    <div className="ml-auto w-64">
                      <Input
                        className="h-8 text-[11px]"
                        value={outputDir}
                        placeholder="导出目录（可选）"
                        onChange={(event) => setOutputDir(event.target.value)}
                      />
                    </div>
                  </div>
                </Card>

                <Card className="p-5">
                  <div className="mb-3 flex items-center gap-2">
                    <span className="text-[13px] font-medium text-ink-soft">基础校验</span>
                    <Badge tone={validationTone(detail.validation)}>
                      {STATUS_META[detail.validation.overall]?.label ?? detail.validation.overall}
                    </Badge>
                    <span className="text-[11px] text-muted">
                      自动检查结构与可核对项；内容正确性仍需人工审核。
                    </span>
                  </div>
                  <ul className="space-y-2">
                    {(detail.validation.checks ?? []).map((check) => (
                      <li
                        key={check.id}
                        className="flex items-start gap-2 rounded-lg border border-line px-3 py-2"
                      >
                        {check.status === "pass" ? (
                          <CheckIcon className="mt-0.5 size-3.5 text-ok" />
                        ) : check.status === "warn" ? (
                          <AlertIcon className="mt-0.5 size-3.5 text-warn" />
                        ) : (
                          <XIcon className="mt-0.5 size-3.5 text-danger" />
                        )}
                        <div>
                          <div className="text-[12px] font-medium text-ink">{check.label}</div>
                          <div className="text-[11px] text-muted">{check.detail}</div>
                        </div>
                      </li>
                    ))}
                  </ul>
                </Card>

                {detail.versions.length > 1 ? (
                  <Card className="p-5">
                    <div className="mb-2 text-[13px] font-medium text-ink-soft">版本历史</div>
                    <ul className="space-y-1.5">
                      {detail.versions.map((version) => (
                        <li
                          key={version.id}
                          className="flex items-center justify-between gap-2 rounded-lg border border-line px-3 py-1.5 text-[11.5px]"
                        >
                          <span className="text-ink">v{version.version}</span>
                          <span className="min-w-0 flex-1 truncate text-muted">
                            {version.instruction || "初始生成"}
                          </span>
                          <span className="shrink-0 text-muted">
                            {new Date(version.created_at).toLocaleString("zh-CN")}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </Card>
                ) : null}
              </>
            ) : (
              <Card className="p-5">
                <EmptyState
                  icon={<ArtifactsIcon className="size-5" />}
                  title="选择一份成果"
                  description="左侧列出任务生成的成果，点击查看预览与校验结果。"
                />
              </Card>
            )}
          </div>
        </div>
      )}

      <Modal
        open={regenOpen}
        title="局部重生成"
        description="只重写所选章节，其他章节保持不变；结果会创建一个新版本。"
        onClose={() => {
          if (!busy) setRegenOpen(false);
        }}
        footer={
          <>
            <Button variant="secondary" disabled={busy} onClick={() => setRegenOpen(false)}>
              取消
            </Button>
            <Button
              variant="primary"
              disabled={busy || !regenHeading || !regenInstruction.trim()}
              onClick={() => void regenerate()}
            >
              {busy ? <Spinner className="size-4 animate-spin" /> : <RefreshIcon className="size-4" />}
              重新生成该章节
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="选择章节">
            <div className="w-full">
              <Select value={regenHeading} onChange={(event) => setRegenHeading(event.target.value)}>
                {headings.map((heading) => (
                  <option key={`${heading.depth}-${heading.title}`} value={heading.title}>
                    {"　".repeat(heading.depth)}
                    {heading.title}
                  </option>
                ))}
              </Select>
            </div>
          </Field>
          <Field label="修改要求" hint="例如：压缩为三段，补充真题中的对应考点，保留原有引用。">
            <Textarea
              rows={4}
              value={regenInstruction}
              onChange={(event) => setRegenInstruction(event.target.value)}
            />
          </Field>
        </div>
      </Modal>
    </>
  );
}