import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageHeader } from "../components/AppShell";
import {
  AlertIcon,
  CheckIcon,
  ChevronRightIcon,
  LibraryIcon,
  Spinner,
  SparkleIcon,
} from "../components/icons";
import {
  Badge,
  Banner,
  Button,
  Card,
  EmptyState,
  Modal,
  Select,
  Textarea,
  cn,
} from "../components/ui";
import { api, ApiError } from "../lib/api";
import type { DocumentRow, TaskRow } from "../types";

const TEMPLATES = [
  { id: "summary", label: "全书总结", prompt: "请对所选资料生成全书总结，按章节组织，标注解析或覆盖上的限制。" },
  { id: "outline", label: "教学大纲", prompt: "依据所选教材生成教学大纲，包含教学目标、内容顺序、重点难点、课时安排与考核建议。" },
  { id: "lesson", label: "教案", prompt: "为指定章节编写教案，包含教学目标、讲解过程、例题或活动、作业安排。" },
  { id: "review", label: "复习提纲", prompt: "生成复习提纲，梳理知识结构、重点、易混淆点及配套练习。" },
  { id: "exam", label: "智能出卷", prompt: "按指定范围、题量与总分生成试卷，并附答案、解析与评分要点。" },
  { id: "analysis", label: "考点分析", prompt: "提取试卷题目并关联知识点，统计各考点出现的年份、次数与实际分值。" },
  { id: "compare", label: "多文档比较", prompt: "比较所选资料对同一主题的表述，列出共同点、差异、冲突及来源。" },
  { id: "custom", label: "自定义任务", prompt: "" },
];

const MODE_OPTIONS = [
  { value: "auto", label: "自动判断（推荐）" },
  { value: "precise", label: "精准检索" },
  { value: "full", label: "全量阅读" },
  { value: "mixed", label: "混合研究" },
];

const TASK_STATUS: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const TASK_TONE: Record<string, "neutral" | "accent" | "ok" | "warn" | "danger"> = {
  queued: "neutral",
  running: "accent",
  paused: "warn",
  completed: "ok",
  failed: "danger",
  cancelled: "neutral",
  interrupted: "warn",
};

export function WorkbenchPage() {
  const navigate = useNavigate();
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [instruction, setInstruction] = useState("");
  const [mode, setMode] = useState("auto");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [scopeOpen, setScopeOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [template, setTemplate] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [docs, taskList] = await Promise.all([
          api.getDocuments({ limit: 300 }),
          api.getTasks(6),
        ]);
        if (!cancelled) {
          setDocuments(docs.items.filter((item) => item.is_present && item.current_version_id));
          setTasks(taskList.items);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "读取资料库失败");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedCount = selected.size;
  const scopeLabel = useMemo(() => {
    if (selectedCount === 0) return `全部资料（${documents.length} 个文档）`;
    return `已选择 ${selectedCount} 个文档`;
  }, [selectedCount, documents.length]);

  const start = async () => {
    const text = instruction.trim();
    if (!text) {
      setError("请先描述任务要求");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.createTask({
        instruction: text,
        document_ids: selectedCount ? Array.from(selected) : undefined,
        mode: mode as "auto" | "precise" | "full" | "mixed",
      });
      navigate(`/tasks?task=${result.task.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "任务创建失败");
    } finally {
      setBusy(false);
    }
  };

  const toggleDocument = (documentId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  };

  return (
    <>
      <PageHeader
        eyebrow="AI 工作台"
        title="把教学资料变成可用的成果"
        description="导入教材、真题与教研资料后，直接描述任务。程序负责检索、阅读、分析与导出，你负责审核与使用。"
        actions={
          documents.length > 0 ? (
            <Badge tone="accent">{documents.length} 个文档已就绪</Badge>
          ) : (
            <Badge tone="neutral">资料库为空</Badge>
          )
        }
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_330px]">
        <div className="space-y-5">
          <Card className="p-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <div className="text-[15px] font-semibold text-ink">新建任务</div>
                <div className="mt-0.5 text-[12.5px] text-muted">
                  用自然语言说明目标、范围与输出要求。
                </div>
              </div>
              <div className="w-44">
                <Select value={mode} onChange={(event) => setMode(event.target.value)}>
                  {MODE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </div>
            </div>

            <Textarea
              rows={6}
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              placeholder="例如：为《中外历史纲要》第三单元生成教学大纲，安排 6 课时，标明重点难点与考核建议。"
            />

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button variant="secondary" size="sm" onClick={() => setScopeOpen(true)}>
                <LibraryIcon className="size-4" />
                {scopeLabel}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setInstruction("");
                  setTemplate(null);
                }}
                disabled={!instruction}
              >
                清空
              </Button>
              <div className="flex-1" />
              <Button
                variant="primary"
                disabled={busy || documents.length === 0 || !instruction.trim()}
                onClick={() => void start()}
              >
                {busy ? <Spinner className="size-4 animate-spin" /> : <SparkleIcon className="size-4" />}
                开始处理
              </Button>
            </div>

            {documents.length === 0 ? (
              <Banner tone="neutral" className="mt-3">
                当前资料库为空。先在「资料管理」中添加本地文件夹并完成索引，任务才会真正读取你的文件。
              </Banner>
            ) : null}
            {error ? (
              <Banner tone="danger" className="mt-3">
                {error}
              </Banner>
            ) : null}
            <p className="mt-2 text-[11.5px] text-muted">
              任务创建时会固定所选文档的当前版本；之后即使原文件更新，任务引用的证据仍保持原样。
            </p>
          </Card>

          <Card className="p-5">
            <div className="mb-3 text-[13px] font-medium text-ink-soft">常用任务</div>
            <div className="flex flex-wrap gap-2">
              {TEMPLATES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setInstruction(item.prompt || item.label);
                    setTemplate(item.id);
                  }}
                  className={cn(
                    "rounded-full border px-3 py-1.5 text-[12.5px] transition-colors",
                    template === item.id
                      ? "border-accent/40 bg-accent-soft text-accent-ink"
                      : "border-line bg-surface text-ink-soft hover:border-line-strong hover:text-ink",
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <p className="mt-3 text-[12px] leading-relaxed text-muted">
              模板只提供通用任务措辞，不包含学科重点或内置题库；具体范围与要求以你的资料和说明为准。
            </p>
          </Card>

          <Card className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-[13px] font-medium text-ink-soft">最近任务</div>
              <Link to="/tasks" className="text-[12px] text-accent hover:underline">
                全部任务
              </Link>
            </div>
            {tasks.length === 0 ? (
              <div className="rounded-lg border border-dashed border-line px-4 py-8 text-center text-[13px] text-muted">
                还没有任务记录。
              </div>
            ) : (
              <ul className="divide-y divide-line">
                {tasks.map((task) => (
                  <li key={task.id}>
                    <Link
                      to={`/tasks?task=${task.id}`}
                      className="flex items-center gap-3 py-2.5 text-left hover:text-ink"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[12.5px] text-ink">
                          {task.instruction.slice(0, 80)}
                        </div>
                        <div className="mt-0.5 text-[10.5px] text-muted">
                          {task.mode === "full" ? "全量阅读" : task.mode === "mixed" ? "混合研究" : "精准检索"}
                          {task.progress ? ` · ${Math.round(task.progress)}%` : ""}
                        </div>
                      </div>
                      <Badge tone={TASK_TONE[task.status] ?? "neutral"}>
                        {TASK_STATUS[task.status] ?? task.status}
                      </Badge>
                      <ChevronRightIcon className="size-3.5 text-muted" />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="space-y-5">
          <Card className="p-5">
            <div className="text-[13px] font-medium text-ink-soft">上手步骤</div>
            <ol className="mt-4 space-y-4">
              {[
                {
                  title: "添加资料文件夹",
                  text: "选择教材、真题或教研资料的本地目录。",
                  to: "/library",
                  done: documents.length > 0,
                },
                {
                  title: "配置模型服务",
                  text: "LLM、Embedding、Reranker、OCR 分别配置。",
                  to: "/settings",
                  done: false,
                },
                {
                  title: "提出任务并审核",
                  text: "Agent 完成分析与生成，你核对来源与结论。",
                  done: false,
                },
              ].map((step, index) => (
                <li key={step.title} className="flex gap-3">
                  <span
                    className={cn(
                      "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold",
                      step.done
                        ? "border-ok/30 bg-ok-soft text-ok"
                        : "border-line bg-surface-muted text-muted",
                    )}
                  >
                    {step.done ? <CheckIcon className="size-3" /> : index + 1}
                  </span>
                  <div>
                    <div className="text-[13px] font-medium text-ink">{step.title}</div>
                    <div className="mt-0.5 text-[12px] leading-relaxed text-muted">{step.text}</div>
                    {step.to ? (
                      <Link
                        to={step.to}
                        className="mt-1 inline-flex items-center gap-0.5 text-[12px] text-accent hover:underline"
                      >
                        前往 <ChevronRightIcon className="size-3.5" />
                      </Link>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          </Card>

          <Card className="p-5">
            <div className="mb-3 flex items-center gap-2 text-[13px] font-medium text-ink-soft">
              <AlertIcon className="size-4 text-warn" />
              资料与隐私
            </div>
            <p className="text-[12.5px] leading-relaxed text-muted">
              文件、索引、日志与成果都留在本机。仅在你配置模型服务后，相关文本或图像才会发送到对应服务商。
            </p>
          </Card>
        </div>
      </div>

      <Modal
        open={scopeOpen}
        title="选择资料范围"
        description="不选择表示使用全部已索引文档；任务创建后会固定这些文档的当前版本。"
        onClose={() => setScopeOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setSelected(new Set(documents.map((item) => item.id)))}>
              全选
            </Button>
            <Button variant="secondary" onClick={() => setSelected(new Set())}>
              使用全部资料
            </Button>
            <Button variant="primary" onClick={() => setScopeOpen(false)}>
              确定
            </Button>
          </>
        }
      >
        {documents.length === 0 ? (
          <EmptyState
            icon={<LibraryIcon className="size-5" />}
            title="资料库为空"
            description="请先在资料管理中导入文件夹。"
          />
        ) : (
          <div className="max-h-[55vh] space-y-1 overflow-y-auto pr-1">
            {documents.map((document) => (
              <label
                key={document.id}
                className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-line px-3 py-2 hover:bg-surface-muted"
              >
                <input
                  type="checkbox"
                  className="mt-1 accent-[var(--accent)]"
                  checked={selected.has(document.id)}
                  onChange={() => toggleDocument(document.id)}
                />
                <span className="min-w-0">
                  <span className="block truncate text-[12.5px] font-medium text-ink">
                    {document.title || document.rel_path || document.path}
                  </span>
                  <span className="block text-[10.5px] text-muted">
                    {document.format === "pdf" ? "PDF" : document.format === "markdown" ? "Markdown" : document.format}
                    {" · "}
                    {document.page_count} 页
                  </span>
                </span>
              </label>
            ))}
          </div>
        )}
      </Modal>
    </>
  );
}