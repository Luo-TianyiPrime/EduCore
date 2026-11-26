export type Tone = "neutral" | "accent" | "ok" | "warn" | "danger";

export const DOC_STATUS: Record<string, { label: string; tone: Tone }> = {
  pending: { label: "待解析", tone: "neutral" },
  parsing: { label: "解析中", tone: "accent" },
  ready: { label: "已就绪", tone: "ok" },
  needs_ocr: { label: "需要 OCR", tone: "warn" },
  failed: { label: "失败", tone: "danger" },
  missing: { label: "已移除", tone: "neutral" },
};

export const FORMAT_LABEL: Record<string, string> = {
  pdf: "PDF",
  markdown: "Markdown",
  ocr_json: "OCR JSON",
  image: "图片",
};

export const BLOCK_KIND_LABEL: Record<string, string> = {
  paragraph: "正文",
  table: "表格",
  list_item: "列表",
  code: "代码",
  blockquote: "引用",
  html: "HTML",
};

export function isActiveJob(status: string): boolean {
  return status === "queued" || status === "running";
}