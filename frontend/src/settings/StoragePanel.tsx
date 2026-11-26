import { useEffect, useState } from "react";
import { SpinnerIcon } from "./settings-icons";
import { Banner, Button, Card, Modal } from "../components/ui";
import { ApiError, api } from "../lib/api";
import type { SystemInfo } from "../types";

function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index >= 3 ? 1 : 0)} ${units[index]}`;
}

export function StoragePanel() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmQuit, setConfirmQuit] = useState(false);
  const [quitting, setQuitting] = useState(false);
  const [quitMessage, setQuitMessage] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setInfo(await api.systemInfo());
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "读取系统信息失败");
      }
    })();
  }, []);

  if (!info) {
    return error ? (
      <Banner tone="danger">{error}</Banner>
    ) : (
      <div className="flex items-center gap-2 text-[13px] text-muted">
        <SpinnerIcon className="animate-spin" /> 正在读取系统信息…
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {info.secret_backend === "local_file" ? (
        <Banner tone="warn" title="凭据以本地文件降级保存">
          当前环境没有可用的系统凭据存储，API Key 会写入受限权限文件。请勿共享该数据目录。
        </Banner>
      ) : (
        <Banner tone="ok" title="凭据由操作系统保护">
          API Key 存在系统凭据存储中；设置文件仅保留引用。
        </Banner>
      )}

      <Card className="p-5">
        <h2 className="text-[14px] font-semibold text-ink">本地数据目录</h2>
        <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
          数据库、向量、缓存、任务检查点、成果与日志都位于此目录。原始资料仍保存在你选择的文件夹中，不被复制或修改。
        </p>
        <dl className="mt-4 grid gap-3 text-[12.5px] sm:grid-cols-2">
          <div className="rounded-lg border border-line bg-surface-muted px-3 py-2.5">
            <dt className="text-[11px] text-muted">数据目录</dt>
            <dd className="mt-0.5 break-all font-mono text-[11.5px] text-ink">{info.data_dir}</dd>
          </div>
          <div className="rounded-lg border border-line bg-surface-muted px-3 py-2.5">
            <dt className="text-[11px] text-muted">数据库</dt>
            <dd className="mt-0.5 break-all font-mono text-[11.5px] text-ink">{info.database}</dd>
          </div>
          <div className="rounded-lg border border-line bg-surface-muted px-3 py-2.5">
            <dt className="text-[11px] text-muted">磁盘可用</dt>
            <dd className="mt-0.5 font-mono text-[11.5px] text-ink">
              {formatBytes(info.disk_free_bytes)} / {formatBytes(info.disk_total_bytes)}
            </dd>
          </div>
          <div className="rounded-lg border border-line bg-surface-muted px-3 py-2.5">
            <dt className="text-[11px] text-muted">运行环境</dt>
            <dd className="mt-0.5 font-mono text-[11.5px] text-ink">
              {info.system} · {info.machine} · Python {info.python}
            </dd>
          </div>
        </dl>
      </Card>

      <Card className="p-5">
        <h2 className="text-[14px] font-semibold text-ink">网络与数据边界</h2>
        <ul className="mt-3 space-y-2 text-[12.5px] leading-relaxed text-ink-soft">
          <li>· 应用只监听 127.0.0.1，不对外开放端口。</li>
          <li>· 除你配置的模型服务外，不发起其他网络请求。</li>
          <li>· 调用云端 Embedding / Reranker / LLM / OCR 时，对应文本或图像会发送到服务商，无法声称数据完全不离开本机。</li>
          <li>· 本地服务失败时不会自动切换到未授权的外部服务。</li>
        </ul>
      </Card>

      <Card className="p-5">
        <h2 className="text-[14px] font-semibold text-ink">退出应用</h2>
        <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
          关闭浏览器标签不会结束本地服务。请通过此按钮（或在启动器中）明确退出，任务检查点与索引会保留在数据目录。
        </p>
        {quitMessage ? (
          <Banner tone={quitMessage.startsWith("无法") ? "danger" : "ok"} className="mt-3">
            {quitMessage}
          </Banner>
        ) : null}
        <Button variant="secondary" className="mt-3" onClick={() => setConfirmQuit(true)}>
          退出 EduCore 服务
        </Button>
      </Card>

      <Modal
        open={confirmQuit}
        title="退出 EduCore"
        description="正在运行的索引或 Agent 任务会中断，并可从检查点恢复。确定退出本地服务？"
        onClose={() => {
          if (!quitting) setConfirmQuit(false);
        }}
        footer={
          <>
            <Button variant="secondary" disabled={quitting} onClick={() => setConfirmQuit(false)}>
              取消
            </Button>
            <Button
              variant="danger"
              disabled={quitting}
              onClick={async () => {
                setQuitting(true);
                setQuitMessage(null);
                try {
                  await api.quitApp();
                  setConfirmQuit(false);
                  setQuitMessage("本地服务已停止，可以关闭浏览器标签。");
                } catch (err) {
                  setConfirmQuit(false);
                  setQuitMessage(
                    err instanceof ApiError ? `无法退出：${err.message}` : "无法退出：服务未响应",
                  );
                } finally {
                  setQuitting(false);
                }
              }}
            >
              确认退出
            </Button>
          </>
        }
      >
        <Banner tone="warn">
          退出不会删除资料、索引或成果；下次启动会标记未完成任务为中断，可手动继续。
        </Banner>
      </Modal>
    </div>
  );
}
