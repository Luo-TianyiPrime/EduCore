import { useEffect, useMemo, useState } from "react";
import { AlertIcon, CheckIcon, PencilIcon, PlusIcon, PlayIcon, SpinnerIcon, TrashIcon, XIcon } from "./settings-icons";
import {
  Badge,
  Banner,
  Button,
  Card,
  Field,
  Input,
  Modal,
  Select,
  Switch,
} from "../components/ui";
import { ApiError, api } from "../lib/api";
import type { Defaults, Endpoint, EndpointKind, EndpointTestResult, Provider } from "../types";

interface Editable extends Endpoint {
  api_key: string;
  clear_api_key: boolean;
  command: string;
  extra_text: string;
}

type TestState = { loading: true } | { loading: false; result: EndpointTestResult } | { loading: false; error: string };

const KIND_ORDER: EndpointKind[] = ["llm", "embedding", "reranker", "ocr", "vlm"];

const KIND_META: Record<
  EndpointKind,
  {
    title: string;
    description: string;
    provider: Provider;
    modelLabel: string;
    basePlaceholder: string;
    modelPlaceholder: string;
  }
> = {
  llm: {
    title: "大语言模型（LLM）",
    description: "负责理解任务、调用工具、分析与生成内容。需要支持工具调用才能使用完整 Agent 能力。",
    provider: "openai_compat",
    modelLabel: "模型名",
    basePlaceholder: "https://api.example.com/v1",
    modelPlaceholder: "例如 qwen3-max / deepseek-chat",
  },
  embedding: {
    title: "Embedding",
    description: "将文本块编码为向量。每个模型是一个独立索引空间，同一资料库可共存多个模型。",
    provider: "openai_compat",
    modelLabel: "模型名",
    basePlaceholder: "https://api.example.com/v1",
    modelPlaceholder: "例如 Qwen3-Embedding-0.6B",
  },
  reranker: {
    title: "Reranker",
    description: "对融合后的候选统一精排，接收查询与候选原文，返回相关性分数。",
    provider: "rerank_http",
    modelLabel: "模型名",
    basePlaceholder: "https://api.example.com/v1",
    modelPlaceholder: "例如 Qwen3-Reranker-0.6B",
  },
  ocr: {
    title: "OCR",
    description: "识别扫描页与图片文字。支持云端 HTTP 服务，或调用本机已安装的 OCR 命令行程序。",
    provider: "ocr_http",
    modelLabel: "模型名（可选）",
    basePlaceholder: "https://ocr.example.com/ocr",
    modelPlaceholder: "可留空",
  },
  vlm: {
    title: "视觉模型（VLM，可选）",
    description: "理解图表、图片内容。未配置时，依赖图像的分析会明确标注未完成。",
    provider: "openai_compat",
    modelLabel: "模型名",
    basePlaceholder: "https://api.example.com/v1",
    modelPlaceholder: "例如 qwen-vl-max",
  },
};

function newId(): string {
  return crypto.randomUUID().replace(/-/g, "").slice(0, 12);
}

function toEditable(endpoint: Endpoint): Editable {
  return {
    ...endpoint,
    api_key: "",
    clear_api_key: false,
    command: String((endpoint.extra ?? {}).command ?? ""),
    extra_text: JSON.stringify(endpoint.extra ?? {}, null, 2),
  };
}

function buildEndpointPayload(endpoint: Editable) {
  let extra: Record<string, unknown> = {};
  if (endpoint.extra_text.trim()) {
    try {
      const parsed = JSON.parse(endpoint.extra_text) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("必须是 JSON 对象");
      }
      extra = parsed as Record<string, unknown>;
    } catch (error) {
      throw new Error(`${endpoint.name || "端点"} 的高级参数不是合法 JSON：${(error as Error).message}`);
    }
  }
  if (endpoint.provider === "ocr_cli") {
    extra = { ...extra, command: endpoint.command };
  }
  return {
    id: endpoint.id,
    name: endpoint.name,
    kind: endpoint.kind,
    provider: endpoint.provider,
    base_url: endpoint.base_url,
    model: endpoint.model,
    timeout_s: endpoint.timeout_s,
    concurrency: endpoint.concurrency,
    rpm: endpoint.rpm,
    tpm: endpoint.tpm,
    context_limit: endpoint.context_limit,
    dimensions: endpoint.dimensions,
    query_instruction: endpoint.query_instruction,
    document_instruction: endpoint.document_instruction,
    distance: endpoint.distance,
    batch_size: endpoint.batch_size,
    price_input_per_mtok: endpoint.price_input_per_mtok,
    price_output_per_mtok: endpoint.price_output_per_mtok,
    price_per_call: endpoint.price_per_call,
    enabled: endpoint.enabled,
    extra,
    ...(endpoint.api_key ? { api_key: endpoint.api_key } : {}),
    ...(endpoint.clear_api_key ? { clear_api_key: true } : {}),
  };
}

function numberOrNull(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function ModelsPanel() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [endpoints, setEndpoints] = useState<Editable[]>([]);
  const [defaults, setDefaults] = useState<Defaults>({
    llm: null,
    embedding: null,
    reranker: null,
    ocr: null,
    vlm: null,
  });
  const [secretBackend, setSecretBackend] = useState<"keyring" | "local_file">("keyring");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, TestState>>({});
  const [deleteTarget, setDeleteTarget] = useState<Editable | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getModels();
        if (cancelled) return;
        setEndpoints(data.endpoints.map(toEditable));
        setDefaults(data.defaults);
        setSecretBackend(data.secret_backend);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "加载模型配置失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const grouped = useMemo(() => {
    const map = new Map<EndpointKind, Editable[]>();
    for (const kind of KIND_ORDER) map.set(kind, []);
    for (const endpoint of endpoints) map.get(endpoint.kind)?.push(endpoint);
    return map;
  }, [endpoints]);

  const update = (id: string, patch: Partial<Editable>) => {
    setEndpoints((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  };

  const addEndpoint = (kind: EndpointKind) => {
    const meta = KIND_META[kind];
    const endpoint: Editable = {
      id: newId(),
      name: "",
      kind,
      provider: meta.provider,
      base_url: "",
      model: "",
      has_api_key: false,
      timeout_s: 60,
      concurrency: 4,
      rpm: null,
      tpm: null,
      context_limit: null,
      dimensions: kind === "embedding" ? null : null,
      query_instruction: null,
      document_instruction: null,
      distance: "cosine",
      batch_size: 32,
      price_input_per_mtok: null,
      price_output_per_mtok: null,
      price_per_call: null,
      enabled: true,
      extra: {},
      api_key: "",
      clear_api_key: false,
      command: "",
      extra_text: "{}",
    };
    setEndpoints((current) => [...current, endpoint]);
    setEditingId(endpoint.id);
    setNotice(null);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const payload = {
        defaults,
        endpoints: endpoints.map(buildEndpointPayload),
      };
      const data = await api.saveModels(payload);
      setEndpoints(data.endpoints.map(toEditable));
      setDefaults(data.defaults);
      setSecretBackend(data.secret_backend);
      setEditingId(null);
      setNotice("模型配置已保存。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (endpoint: Editable) => {
    setTests((current) => ({ ...current, [endpoint.id]: { loading: true } }));
    try {
      const result = await api.testModel({ endpoint: buildEndpointPayload(endpoint) });
      setTests((current) => ({ ...current, [endpoint.id]: { loading: false, result } }));
    } catch (err) {
      setTests((current) => ({
        ...current,
        [endpoint.id]: { loading: false, error: err instanceof Error ? err.message : "测试失败" },
      }));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-[13px] text-muted">
        <SpinnerIcon className="animate-spin" /> 正在读取模型配置…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {secretBackend === "local_file" ? (
        <Banner tone="warn" title="系统凭据存储不可用">
          当前 API Key 以受限权限文件保存在配置目录中，安全性低于操作系统凭据存储。建议在支持 keyring 的桌面环境中重新填写密钥。
        </Banner>
      ) : (
        <Banner tone="ok" title="API Key 存入操作系统凭据存储">
          设置文件只保存端点配置与密钥引用，不会把明文 Key 写入项目或配置。
        </Banner>
      )}

      {error ? <Banner tone="danger">{error}</Banner> : null}
      {notice ? <Banner tone="ok">{notice}</Banner> : null}

      {KIND_ORDER.map((kind) => {
        const meta = KIND_META[kind];
        const items = grouped.get(kind) ?? [];
        return (
          <section key={kind}>
            <div className="mb-2 flex items-end justify-between gap-3">
              <div>
                <h2 className="text-[14px] font-semibold text-ink">{meta.title}</h2>
                <p className="mt-0.5 max-w-2xl text-[12px] leading-relaxed text-muted">{meta.description}</p>
              </div>
              <Button size="sm" variant="secondary" onClick={() => addEndpoint(kind)}>
                <PlusIcon className="size-3.5" />
                添加
              </Button>
            </div>

            <div className="space-y-3">
              {items.length === 0 ? (
                <div className="rounded-lg border border-dashed border-line px-4 py-4 text-[12.5px] text-muted">
                  尚未配置。{kind === "llm" ? "没有 LLM 时无法启动 Agent 任务。" : ""}
                </div>
              ) : null}

              {items.map((endpoint) => {
                const editing = editingId === endpoint.id;
                const test = tests[endpoint.id];
                const isDefault = defaults[kind] === endpoint.id;
                return (
                  <Card key={endpoint.id} className="overflow-hidden">
                    <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-[13.5px] font-medium text-ink">
                            {endpoint.name || "未命名端点"}
                          </span>
                          <Badge tone="neutral">{endpoint.provider}</Badge>
                          {endpoint.has_api_key ? <Badge tone="ok">已存密钥</Badge> : null}
                          {isDefault ? <Badge tone="accent">默认</Badge> : null}
                          {!endpoint.enabled ? <Badge tone="warn">已停用</Badge> : null}
                        </div>
                        <div className="mt-0.5 truncate font-mono text-[11.5px] text-muted">
                          {endpoint.provider === "ocr_cli" ? endpoint.command || "未填写命令" : endpoint.base_url}
                          {endpoint.model ? ` · ${endpoint.model}` : ""}
                        </div>
                      </div>

                      <div className="flex items-center gap-1.5">
                        <label className="mr-1 flex cursor-pointer items-center gap-1.5 text-[11.5px] text-muted">
                          <input
                            type="radio"
                            name={`default-${kind}`}
                            className="accent-[var(--accent)]"
                            checked={isDefault}
                            onChange={() => setDefaults((current) => ({ ...current, [kind]: endpoint.id }))}
                          />
                          默认
                        </label>
                        <Switch
                          checked={endpoint.enabled}
                          onChange={(value) => update(endpoint.id, { enabled: value })}
                          label="启用"
                        />
                        <Button size="sm" variant="ghost" onClick={() => setEditingId(editing ? null : endpoint.id)}>
                          <PencilIcon className="size-3.5" />
                          {editing ? "收起" : "编辑"}
                        </Button>
                        <Button size="sm" variant="secondary" onClick={() => void runTest(endpoint)}>
                          {test?.loading ? <SpinnerIcon className="size-3.5 animate-spin" /> : <PlayIcon className="size-3.5" />}
                          测试
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setDeleteTarget(endpoint)} aria-label="删除">
                          <TrashIcon className="size-3.5 text-danger" />
                        </Button>
                      </div>
                    </div>

                    {test && !test.loading ? (
                      <div className="border-t border-line px-4 py-2.5">
                        {"result" in test ? (
                          <div className="flex flex-wrap items-center gap-2 text-[12px]">
                            {test.result.ok ? (
                              <span className="inline-flex items-center gap-1 text-ok">
                                <CheckIcon className="size-3.5" /> 连接正常
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-danger">
                                <XIcon className="size-3.5" /> 连接失败
                              </span>
                            )}
                            <span className="text-muted">{test.result.detail}</span>
                            {test.result.latency_ms !== null ? (
                              <span className="text-muted">· {test.result.latency_ms} ms</span>
                            ) : null}
                            {test.result.dimensions ? (
                              <span className="text-muted">· 维度 {test.result.dimensions}</span>
                            ) : null}
                            {test.result.tool_calling === false ? (
                              <span className="text-warn">· 未验证工具调用</span>
                            ) : null}
                          </div>
                        ) : (
                          <span className="text-[12px] text-danger">{test.error}</span>
                        )}
                      </div>
                    ) : null}

                    {editing ? (
                      <div className="border-t border-line bg-surface-muted/60 px-4 py-4">
                        <div className="grid gap-3.5 sm:grid-cols-2">
                          <Field label="名称">
                            <Input
                              value={endpoint.name}
                              placeholder="给这个端点起个名字"
                              onChange={(event) => update(endpoint.id, { name: event.target.value })}
                            />
                          </Field>

                          {endpoint.kind === "ocr" ? (
                            <Field label="接入方式">
                              <Select
                                value={endpoint.provider}
                                onChange={(event) =>
                                  update(endpoint.id, { provider: event.target.value as Provider })
                                }
                              >
                                <option value="ocr_http">HTTP 服务</option>
                                <option value="ocr_cli">本地命令行</option>
                              </Select>
                            </Field>
                          ) : (
                            <Field label="模型名" hint={KIND_META[endpoint.kind].modelPlaceholder}>
                              <Input
                                value={endpoint.model}
                                onChange={(event) => update(endpoint.id, { model: event.target.value })}
                              />
                            </Field>
                          )}

                          {endpoint.provider === "ocr_cli" ? (
                            <Field
                              label="命令模板"
                              className="sm:col-span-2"
                              hint="支持占位符 {image} {page} {lang}；程序会把页面图片写入临时文件后调用。"
                            >
                              <Input
                                value={endpoint.command}
                                placeholder="paddleocr ocr -i {image} --lang {lang}"
                                onChange={(event) => update(endpoint.id, { command: event.target.value })}
                              />
                            </Field>
                          ) : (
                            <Field
                              label="Base URL"
                              className="sm:col-span-2"
                              hint="OpenAI 兼容服务填写到 /v1；Reranker 与 OCR 填写完整接口地址。"
                            >
                              <Input
                                value={endpoint.base_url}
                                placeholder={KIND_META[endpoint.kind].basePlaceholder}
                                onChange={(event) => update(endpoint.id, { base_url: event.target.value })}
                              />
                            </Field>
                          )}

                          {endpoint.provider !== "ocr_cli" ? (
                            <Field
                              label="API Key"
                              className="sm:col-span-1"
                              hint={
                                endpoint.has_api_key
                                  ? "已保存。留空保持不变；填写新值会覆盖。"
                                  : "可选；部分本地服务无需密钥。"
                              }
                            >
                              <Input
                                type="password"
                                autoComplete="off"
                                value={endpoint.api_key}
                                onChange={(event) => update(endpoint.id, { api_key: event.target.value })}
                              />
                            </Field>
                          ) : null}

                          <Field label="超时（秒）">
                            <Input
                              type="number"
                              min={1}
                              value={endpoint.timeout_s}
                              onChange={(event) =>
                                update(endpoint.id, { timeout_s: Number(event.target.value) || 60 })
                              }
                            />
                          </Field>
                          <Field label="并发数">
                            <Input
                              type="number"
                              min={1}
                              value={endpoint.concurrency}
                              onChange={(event) =>
                                update(endpoint.id, { concurrency: Number(event.target.value) || 1 })
                              }
                            />
                          </Field>
                          <Field label="RPM（可选）">
                            <Input
                              type="number"
                              min={0}
                              value={endpoint.rpm ?? ""}
                              placeholder="不限"
                              onChange={(event) => update(endpoint.id, { rpm: numberOrNull(event.target.value) })}
                            />
                          </Field>

                          {endpoint.kind === "embedding" ? (
                            <>
                              <Field label="向量维度" hint="留空表示以服务实际返回为准；填写后会校验。">
                                <Input
                                  type="number"
                                  min={1}
                                  value={endpoint.dimensions ?? ""}
                                  placeholder="自动"
                                  onChange={(event) =>
                                    update(endpoint.id, {
                                      dimensions: numberOrNull(event.target.value),
                                    })
                                  }
                                />
                              </Field>
                              <Field label="批量大小">
                                <Input
                                  type="number"
                                  min={1}
                                  value={endpoint.batch_size}
                                  onChange={(event) =>
                                    update(endpoint.id, { batch_size: Number(event.target.value) || 1 })
                                  }
                                />
                              </Field>
                              <Field label="查询指令（可选）" hint="部分模型要求查询加前缀，例如“为这个句子生成表示以用于检索相关文章：”。">
                                <Input
                                  value={endpoint.query_instruction ?? ""}
                                  onChange={(event) =>
                                    update(endpoint.id, {
                                      query_instruction: event.target.value || null,
                                    })
                                  }
                                />
                              </Field>
                              <Field label="文档指令（可选）">
                                <Input
                                  value={endpoint.document_instruction ?? ""}
                                  onChange={(event) =>
                                    update(endpoint.id, {
                                      document_instruction: event.target.value || null,
                                    })
                                  }
                                />
                              </Field>
                            </>
                          ) : null}

                          <Field label="高级参数（JSON）" className="sm:col-span-2" hint="用于服务商特有字段，例如 headers、response_format、results_path 等。">
                            <textarea
                              rows={3}
                              value={endpoint.extra_text}
                              onChange={(event) => update(endpoint.id, { extra_text: event.target.value })}
                              className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[12px] leading-relaxed focus:border-accent focus:outline-none"
                            />
                          </Field>
                        </div>

                        {endpoint.has_api_key ? (
                          <label className="mt-3 flex items-center gap-2 text-[12px] text-muted">
                            <input
                              type="checkbox"
                              className="accent-[var(--accent)]"
                              checked={endpoint.clear_api_key}
                              onChange={(event) =>
                                update(endpoint.id, { clear_api_key: event.target.checked })
                              }
                            />
                            保存时清除已存储的 API Key
                          </label>
                        ) : null}
                      </div>
                    ) : null}
                  </Card>
                );
              })}
            </div>
          </section>
        );
      })}

      <div className="sticky bottom-0 -mx-1 flex items-center justify-between gap-3 border-t border-line bg-canvas/95 px-1 py-3 backdrop-blur">
        <span className="text-[12px] text-muted">
          共 {endpoints.length} 个端点。保存后立即生效，不影响已有向量索引。
        </span>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => setEditingId(null)}>
            取消编辑
          </Button>
          <Button variant="primary" onClick={() => void save()} disabled={saving}>
            {saving ? <SpinnerIcon className="size-4 animate-spin" /> : <CheckIcon className="size-4" />}
            保存全部
          </Button>
        </div>
      </div>

      <Modal
        open={deleteTarget !== null}
        title="删除端点"
        description={deleteTarget ? `确定删除「${deleteTarget.name || deleteTarget.id}」？已存储的 API Key 也会一并清除。` : ""}
        onClose={() => setDeleteTarget(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                if (!deleteTarget) return;
                setEndpoints((current) => current.filter((item) => item.id !== deleteTarget.id));
                setDefaults((current) => {
                  const next = { ...current };
                  for (const key of Object.keys(next) as (keyof Defaults)[]) {
                    if (next[key] === deleteTarget.id) next[key] = null;
                  }
                  return next;
                });
                setDeleteTarget(null);
              }}
            >
              删除
            </Button>
          </>
        }
      >
        <Banner tone="warn">
          <span className="inline-flex items-center gap-1.5">
            <AlertIcon className="size-4" />
            删除后需要重新填写配置；已有资料不受影响。
          </span>
        </Banner>
      </Modal>
    </div>
  );
}
