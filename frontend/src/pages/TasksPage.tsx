import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageHeader } from "../components/AppShell";
import {
  AlertIcon,
  CheckIcon,
  FileIcon,
  RefreshIcon,
  TasksIcon,
  Spinner,
} from "../components/icons";
import {
  Badge,
  Banner,
  Button,
  Card,
  EmptyState,
  Modal,
  cn,
} from "../components/ui";
import { api, ApiError } from "../lib/api";
import type { TaskDetail, TaskEvent, TaskRow } from "../types";

const TERMINAL = new Set(["completed", "failed", "cancelled", "interrupted"]);

const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const STATUS_TONE: Record<string, "neutral" | "accent" | "ok" | "warn" | "danger"> = {
  queued: "neutral",
  running: "accent",
  paused: "warn",
  completed: "ok",
  failed: "danger",
  cancelled: "neutral",
  interrupted: "warn",
};

const STAGE_LABEL: Record<string, string> = {
  queued: "排队",
  preparing: "准备批次",
  reading: "全量阅读",
  synthesis: "综合成果",
  agent: "Agent 处理",
  paused: "已暂停",
  completed: "完成",
  budget: "预算停止",
  failed: "失败",
};

const MODE_LABEL: Record<string, string> = {
  precise: "精准检索",
  full: "全量阅读",
  mixed: "混合研究",
};

function eventText(event: TaskEvent | { type: string; payload: Record<string, unknown> }): string {
  const payload = event.payload || {};
  switch (event.type) {
    case "status":
      return (
        String(payload.message || "") ||
        `状态：${STATUS_LABEL[String(payload.status)] ?? String(payload.status ?? "")}`
      );
    case "stage":
      return `进入阶段：${STAGE_LABEL[String(payload.stage)] ?? String(payload.stage ?? "")}`;
    case "batch": {
      const index = Number(payload.batch_index ?? 0) + 1;
      const total = Number(payload.total ?? 0);
      const status = String(payload.status ?? "");
      const label = status === "running" ? "开始" : status === "failed" ? "失败" : "完成";
      return `批次 ${index}/${total} ${label} · ${String(payload.label ?? "")}${
        payload.error ? `（${String(payload.error)}）` : ""
      }`;
    }
    case "progress":
      return `进度：${payload.done_batches}/${payload.total_batches} 完成${
        Number(payload.failed_batches) ? `，${payload.failed_batches} 失败` : ""
      }`;
    case "artifact":
      return `成果已写入 ${String(payload.path ?? "")}`;
    case "error":
      return String(payload.message ?? "任务失败");
    case "log":
      return String(payload.message ?? "");
    default:
      return `${event.type}: ${JSON.stringify(payload).slice(0, 200)}`;
  }
}

export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("task");
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [liveEvents, setLiveEvents] = useState<TaskEvent[]>([]);
  const [output, setOutput] = useState<{ content: string; path: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  const loadList = useCallback(async () => {
    try {
      const data = await api.getTasks(60);
      setTasks(data.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "读取任务列表失败");
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setLiveEvents([]);
      return;
    }
    let stopped = false;
    const tick = async () => {
      try {
        const data = await api.getTask(selectedId);
        if (stopped) return;
        setDetail(data);
        setError(null);
        if (!TERMINAL.has(data.task.status)) {
          timerRef.current = window.setTimeout(tick, 1500);
        } else {
          void loadList();
        }
      } catch (err) {
        if (!stopped) setError(err instanceof ApiError ? err.message : "读取任务失败");
      }
    };
    void tick();
    return () => {
      stopped = true;
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [selectedId, loadList]);

  // Live SSE stream. The server replays persisted events first, so this also
  // reconstructs history after a reload.
  useEffect(() => {
    if (!selectedId) return;
    setLiveEvents([]);
    const source = new EventSource(`/api/tasks/${selectedId}/events`);
    const handler = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data) as Record<string, unknown>;
        setLiveEvents((current) => [
          ...current.slice(-299),
          {
            id: Number(event.lastEventId || 0),
            task_id: selectedId,
            type: event.type,
            payload,
            created_at: new Date().toISOString(),
          },
        ]);
      } catch {
        /* ignore malformed event */
      }
    };
    for (const type of ["status", "stage", "batch", "progress", "artifact", "error", "log"]) {
      source.addEventListener(type, handler as EventListener);
    }
    source.addEventListener("done", () => source.close());
    source.onerror = () => source.close();
    return () => source.close();
  }, [selectedId]);

  const act = async (action: "pause" | "resume" | "cancel" | "retry") => {
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    try {
      await api.taskAction(selectedId, action);
      const data = await api.getTask(selectedId);
      setDetail(data);
      await loadList();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const openOutput = async () => {
    if (!selectedId) return;
    setError(null);
    try {
      setOutput(await api.taskOutput(selectedId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "成果尚不可用");
    }
  };

  const task = detail?.task ?? null;
  const events = liveEvents.length > 0 ? liveEvents : detail?.events ?? [];

  return (
    <>
      <PageHeader
        eyebrow="任务中心"
        title="任务与进度"
        description="查看任务的阶段、批次覆盖、模型消耗，并可暂停、继续、取消或重试。中断后从最近检查点继续，不重做已完成批次。"
        actions={
          <Button variant="secondary" onClick={() => void loadList()}>
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

      {tasks.length === 0 && !selectedId ? (
        <Card className="p-5">
          <EmptyState
            icon={<TasksIcon className="size-5" />}
            title="暂无任务"
            description="在 AI 工作台描述任务并开始处理，进度与批次会显示在这里。"
            action={
              <Link to="/">
                <Button variant="primary">前往工作台</Button>
              </Link>
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
          <Card className="max-h-[720px] overflow-y-auto">
            <ul>
              {tasks.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setParams({ task: item.id })}
                    className={cn(
                      "flex w-full flex-col gap-1.5 border-b border-line px-4 py-3 text-left transition-colors last:border-b-0",
                      selectedId === item.id ? "bg-accent-soft/60" : "hover:bg-surface-muted",
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="line-clamp-2 text-[12.5px] text-ink">{item.instruction}</span>
                      <Badge tone={STATUS_TONE[item.status] ?? "neutral"}>
                        {STATUS_LABEL[item.status] ?? item.status}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-2 text-[10.5px] text-muted">
                      <span>{MODE_LABEL[item.mode] ?? item.mode}</span>
                      {item.total_batches > 0 ? (
                        <span>
                          批次 {item.done_batches}/{item.total_batches}
                          {item.failed_batches ? ` · 失败 ${item.failed_batches}` : ""}
                        </span>
                      ) : null}
                      <span className="ml-auto">{item.progress ? `${Math.round(item.progress)}%` : ""}</span>
                    </div>
                    <div className="h-1 overflow-hidden rounded-full bg-surface-strong">
                      <div
                        className="h-full rounded-full bg-accent"
                        style={{ width: `${Math.min(100, Math.max(0, item.progress))}%` }}
                      />
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </Card>

          <Card className="overflow-hidden">
            {!task ? (
              <EmptyState
                icon={<TasksIcon className="size-5" />}
                title="选择一个任务查看详情"
                description="左侧列出当前与历史任务。任务详情包含批次覆盖、事件日志、模型消耗与成果预览。"
              />
            ) : (
              <div>
                <div className="border-b border-line px-5 py-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={STATUS_TONE[task.status] ?? "neutral"}>
                          {STATUS_LABEL[task.status] ?? task.status}
                        </Badge>
                        <Badge tone="neutral">{MODE_LABEL[task.mode] ?? task.mode}</Badge>
                        <span className="text-[11px] text-muted">
                          {STAGE_LABEL[task.stage ?? ""] ?? task.stage ?? ""}
                        </span>
                      </div>
                      <p className="mt-2 text-[13px] leading-relaxed text-ink">{task.instruction}</p>
                      <div className="mt-1 text-[10.5px] text-muted">
                        {new Date(task.created_at).toLocaleString("zh-CN")} · 任务 {task.id}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {task.status === "running" || task.status === "queued" ? (
                        <Button size="sm" variant="secondary" disabled={busy} onClick={() => void act("pause")}>
                          暂停
                        </Button>
                      ) : null}
                      {task.status === "paused" || task.status === "interrupted" ? (
                        <Button size="sm" variant="primary" disabled={busy} onClick={() => void act("resume")}>
                          继续
                        </Button>
                      ) : null}
                      {!TERMINAL.has(task.status) ? (
                        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act("cancel")}>
                          取消
                        </Button>
                      ) : null}
                      {task.status === "failed" ||
                      task.status === "cancelled" ||
                      task.status === "interrupted" ||
                      (task.status === "completed" && task.failed_batches > 0) ? (
                        <Button size="sm" variant="secondary" disabled={busy} onClick={() => void act("retry")}>
                          重试
                        </Button>
                      ) : null}
                      {task.result_path ? (
                        <Button size="sm" variant="primary" onClick={() => void openOutput()}>
                          <FileIcon className="size-3.5" />
                          查看成果
                        </Button>
                      ) : null}
                    </div>
                  </div>

                  <div className="mt-4">
                    <div className="mb-1 flex justify-between text-[11px] text-muted">
                      <span>
                        {task.total_batches > 0
                          ? `批次 ${task.done_batches}/${task.total_batches}${
                              task.failed_batches ? ` · 失败 ${task.failed_batches}` : ""
                            }`
                          : "准备中"}
                      </span>
                      <span>{Math.round(task.progress)}%</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-surface-strong">
                      <div
                        className={cn(
                          "h-full rounded-full transition-[width]",
                          task.status === "failed" ? "bg-danger" : "bg-accent",
                        )}
                        style={{ width: `${Math.min(100, Math.max(0, task.progress))}%` }}
                      />
                    </div>
                  </div>

                  {task.error ? (
                    <Banner tone={task.status === "completed" ? "warn" : "danger"} className="mt-3">
                      {task.error}
                    </Banner>
                  ) : null}
                  {task.result_path ? (
                    <div className="mt-2 break-all font-mono text-[10.5px] text-muted">
                      成果文件：{task.result_path}
                    </div>
                  ) : null}
                </div>

                <div className="grid max-h-[520px] gap-0 overflow-y-auto lg:grid-cols-2">
                  <div className="border-r border-line px-5 py-4">
                    <div className="mb-2 text-[12px] font-medium text-ink-soft">
                      批次覆盖（{detail?.batches.length ?? 0}）
                    </div>
                    {(detail?.batches.length ?? 0) === 0 ? (
                      <p className="text-[12px] text-muted">
                        精准检索任务不建立全量批次，直接由 Agent 使用检索与阅读工具。
                      </p>
                    ) : (
                      <ul className="space-y-1.5">
                        {detail?.batches.map((batch) => (
                          <li
                            key={batch.id}
                            className="flex items-start gap-2 rounded-lg border border-line px-2.5 py-1.5"
                          >
                            {batch.status === "completed" ? (
                              <CheckIcon className="mt-0.5 size-3.5 shrink-0 text-ok" />
                            ) : batch.status === "failed" ? (
                              <AlertIcon className="mt-0.5 size-3.5 shrink-0 text-danger" />
                            ) : batch.status === "running" ? (
                              <Spinner className="mt-0.5 size-3.5 shrink-0 animate-spin text-accent" />
                            ) : (
                              <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-muted/50" />
                            )}
                            <div className="min-w-0">
                              <div className="truncate text-[11.5px] text-ink">
                                {batch.batch_index + 1}. {batch.label || batch.document_id}
                              </div>
                              <div className="text-[10px] text-muted">
                                {batch.status}
                                {batch.page_start
                                  ? ` · 第 ${batch.page_start}${
                                      batch.page_end && batch.page_end !== batch.page_start
                                        ? `–${batch.page_end}`
                                        : ""
                                    } 页`
                                  : ""}
                                {batch.error ? ` · ${batch.error}` : ""}
                              </div>
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div className="px-5 py-4">
                    <div className="mb-2 text-[12px] font-medium text-ink-soft">事件日志</div>
                    <ul className="space-y-1.5">
                      {events.slice(-120).map((event, index) => (
                        <li
                          key={`${event.id}-${index}`}
                          className={cn(
                            "rounded-md px-2 py-1 text-[11px] leading-relaxed",
                            event.type === "error"
                              ? "bg-danger-soft text-danger"
                              : event.type === "artifact"
                                ? "bg-ok-soft text-ok"
                                : "bg-surface-muted text-ink-soft",
                          )}
                        >
                          {eventText(event)}
                        </li>
                      ))}
                    </ul>

                    {detail && detail.usage.length > 0 ? (
                      <>
                        <div className="mt-4 mb-2 text-[12px] font-medium text-ink-soft">模型消耗</div>
                        <ul className="space-y-1 text-[11px] text-ink-soft">
                          {detail.usage.map((row) => (
                            <li key={`${row.endpoint_id}-${row.model}`} className="flex justify-between gap-2">
                              <span className="truncate">
                                {row.model ?? row.endpoint_kind} · {row.calls} 次
                              </span>
                              <span className="shrink-0 font-mono text-muted">
                                {row.input_tokens}/{row.output_tokens} tok
                                {row.cost_estimate !== null ? ` · $${row.cost_estimate.toFixed(4)}` : ""}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                  </div>
                </div>
              </div>
            )}
          </Card>
        </div>
      )}

      <Modal
        open={output !== null}
        title="成果预览"
        description={output?.path}
        onClose={() => setOutput(null)}
        footer={
          <Button variant="secondary" onClick={() => setOutput(null)}>
            关闭
          </Button>
        }
      >
        <div className="max-h-[65vh] overflow-auto rounded-lg border border-line bg-surface-muted p-4">
          <pre className="whitespace-pre-wrap font-sans text-[12.5px] leading-[1.8] text-ink-soft">
            {output?.content}
          </pre>
        </div>
      </Modal>
    </>
  );
}