"""Pydantic AI agent runner for precise and mixed research tasks."""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from backend.core.agents.deps import AgentDeps
from backend.core.agents.tools import register_tools
from backend.core.config import EndpointConfig
from backend.core.models.gateway import ModelGateway, estimate_cost
from backend.core.models.types import Message, Usage
from backend.core.prompts import load_prompt
from backend.storage.db import Database

logger = logging.getLogger(__name__)


def build_model(endpoint: EndpointConfig, api_key: str | None):
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(
        base_url=endpoint.base_url,
        api_key=api_key or "not-required",
    )
    return OpenAIChatModel(endpoint.model, provider=provider)


class AgentRunner:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        db: Database,
        max_steps: int = 120,
    ) -> None:
        self.gateway = gateway
        self.db = db
        self.max_steps = max(1, max_steps)

    def build_agent(
        self,
        *,
        endpoint: EndpointConfig,
        deps: AgentDeps,
        extra_instructions: str = "",
        model: Any | None = None,
    ) -> Agent:
        resolved_model = model or build_model(endpoint, self.gateway.api_key(endpoint))
        instructions = load_prompt("agent_system.md")
        if extra_instructions:
            instructions = f"{instructions}\n\n{extra_instructions}"
        agent = Agent(
            resolved_model,
            deps_type=AgentDeps,
            instructions=instructions,
        )
        register_tools(agent)
        return agent

    async def run(
        self,
        *,
        endpoint: EndpointConfig,
        deps: AgentDeps,
        instruction: str,
        extra_instructions: str = "",
        model: Any | None = None,
    ) -> dict[str, Any]:
        agent = self.build_agent(
            endpoint=endpoint,
            deps=deps,
            extra_instructions=extra_instructions,
            model=model,
        )
        result = await agent.run(
            instruction,
            deps=deps,
            usage_limits=UsageLimits(request_limit=self.max_steps),
            model_settings={"temperature": 0.3, "max_tokens": 8000},
        )
        self._record_usage(endpoint, result, deps.task_id)
        return {
            "text": result.output,
            "messages": [
                _message_to_dict(message) for message in result.all_messages()
            ],
            "usage": _usage_dict(result),
        }

    def _record_usage(self, endpoint: EndpointConfig, result: Any, task_id: str) -> None:
        try:
            usage = _run_usage(result)
            recorded = Usage(
                calls=int(getattr(usage, "requests", 1) or 1),
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            )
            self.db.record_usage(
                endpoint_id=endpoint.id,
                endpoint_kind="llm",
                model=endpoint.model,
                task_id=task_id,
                calls=recorded.calls,
                input_tokens=recorded.input_tokens,
                output_tokens=recorded.output_tokens,
                cost_estimate=estimate_cost(endpoint, recorded),
            )
        except Exception as exc:  # pragma: no cover - accounting must not fail tasks
            logger.warning("记录 Agent 用量失败: %s", exc)


def _run_usage(result: Any) -> Any:
    # Pydantic AI >= 2 exposes usage as a property.
    return getattr(result, "usage", None)


def _message_to_dict(message: Any) -> dict[str, Any]:
    try:
        if hasattr(message, "model_dump"):
            return message.model_dump(mode="json")
        return {"repr": repr(message)[:2000]}
    except Exception:  # pragma: no cover
        return {"repr": repr(message)[:2000]}


def _usage_dict(result: Any) -> dict[str, int]:
    try:
        usage = _run_usage(result)
        return {
            "requests": int(getattr(usage, "requests", 0) or 0),
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
    except Exception:  # pragma: no cover
        return {"requests": 0, "input_tokens": 0, "output_tokens": 0}


def build_scope_instructions(deps: AgentDeps) -> str:
    lines = ["任务范围（只能引用这些文档）："]
    for item in deps.inputs:
        lines.append(
            f"- {item.get('title')}（{item.get('format')}，{item.get('page_count')} 页）"
        )
    return "\n".join(lines)


def batch_messages(instruction: str, document_title: str, batch_label: str, text: str) -> list[Message]:
    return [
        {"role": "system", "content": load_prompt("full_batch.md")},
        {
            "role": "user",
            "content": (
                f"用户任务：{instruction}\n\n"
                f"批次：{batch_label}\n文档：{document_title}\n\n"
                f"批次原文：\n{text}"
            ),
        },
    ]