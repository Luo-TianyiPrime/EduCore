# EduCore

本地运行的教育资料智能处理工具：导入自己的教材、真题与教研资料，建立结构化索引，
由单个自主 Agent 完成检索、全量阅读、跨文档分析与成果生成，并导出 Markdown / DOCX / JSON / PDF。

> 它始终是一个**教育文件内容处理工具**，不是教务系统、在线教学平台，也不是通用 Agent 框架。

## 功能概览

- **资料**：多文件夹/单文件导入、递归扫描、增量更新、PDF / Markdown / OCR JSON / 图片、OCR 适配与页面缓存。
- **检索**：多 Embedding 模型空间共存（同维度不同模型也不混用）、BM25（jieba FTS5 + trigram）、RRF 融合、统一精排、父级与相邻上下文扩展、来源回跳。
- **Agent**：Pydantic AI 工具调用，精准检索 / 全量阅读 / 混合研究三种模式；批次清单、覆盖统计、检查点、暂停/继续/取消/重试、SSE 进度。
- **成果**：统一章节结构、版本化编辑、按章节局部重生成、结构/引用/总分校验，导出四种格式（PDF 中文可搜索）。
- **本地性**：仅监听 127.0.0.1，文件与索引留在本机；只访问用户配置的模型端点；无遥测。

## 快速开始（开发）

```bash
uv sync --all-extras
cd frontend && npm install && npm run build && cd ..
uv run python -m backend.main --port 8765 --open-browser
```

前端热更新：

```bash
cd frontend && npm run dev   # 代理到 127.0.0.1:8765
```

数据目录默认 `%LOCALAPPDATA%\EduCore\EduCore-Data`，可用 `--data-dir` 或 `EDUCORE_DATA_DIR` 覆盖。

## 配置

「系统设置 → 模型服务」分别配置 LLM、Embedding、Reranker、OCR（可选 VLM），
API Key 存入系统凭据存储；每个端点可独立连接测试。没有 Embedding 时检索退化为关键词模式并明确提示；
没有配置 OCR 时扫描页标记为「需要 OCR」。

## 打包（Windows）

```bash
cd frontend && npm run build && cd ..
uv run pyinstaller packaging/educore.spec --noconfirm --clean --distpath dist --workpath build/pyinstaller
dist/EduCore/EduCore.exe
```

安装包脚本：`packaging/windows/educore.iss`（需要 Inno Setup 6）。macOS 构建见 `.github/workflows/release.yml`。

## 文档

- [使用指南](docs/USER_GUIDE.md)
- [实现状态与 AC01–AC21 验收表](docs/IMPLEMENTATION_STATUS.md)
- [性能基准（合成数据）](docs/PERFORMANCE.md)

## 当前验证状态（摘要）

- 自动化测试：44 项通过；ruff 通过；前端构建通过。
- 合成数据基准：500 页索引 4.8 s、检索 P50 3.9 ms；5,000 页索引 43.2 s、P50 21.4 ms（仅 BM25，无向量/精排）。
- Windows portable 打包版已实机启动、扫描、检索、退出。
- **未验证**：真实模型（无授权凭据）、真实 OCR 服务、macOS 真机、干净 Windows 安装包、L/XL 规模。
  详细清单与原因见 `docs/IMPLEMENTATION_STATUS.md`，不以 Mock 或自检结果冒充通过。

## 许可证

本项目采用 [MIT License](LICENSE)。Copyright (c) 2026 Luo-TianyiPrime。