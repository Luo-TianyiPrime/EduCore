from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from backend.core.config import AppConfig, EndpointConfig
from backend.core.models.gateway import ModelGateway, estimate_cost
from backend.core.models.types import (
    ModelBadResponse,
    ModelNotConfigured,
    RerankCandidate,
    Usage,
)
from backend.storage.db import Database


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, ref: str) -> str | None:
        return self.values.get(ref)

    def set(self, ref: str, value: str) -> None:
        self.values[ref] = value

    def delete(self, ref: str) -> None:
        self.values.pop(ref, None)


def make_gateway(tmp_path: Path, endpoints: list[EndpointConfig], **defaults) -> ModelGateway:
    config = AppConfig(endpoints=endpoints, **defaults)
    secrets = FakeSecrets()
    db = Database(tmp_path / "usage.sqlite")
    db.init()
    return ModelGateway(config, secrets, db)


def llm_endpoint() -> EndpointConfig:
    return EndpointConfig(
        id="llm",
        kind="llm",
        provider="openai_compat",
        base_url="https://example.test/v1",
        model="test-llm",
        timeout_s=5,
    )


def embedding_endpoint() -> EndpointConfig:
    return EndpointConfig(
        id="emb",
        kind="embedding",
        provider="openai_compat",
        base_url="https://example.test/v1",
        model="test-emb",
        dimensions=3,
        batch_size=2,
        document_instruction="文档：",
        query_instruction="查询：",
    )


def rerank_endpoint() -> EndpointConfig:
    return EndpointConfig(
        id="rr",
        kind="reranker",
        provider="rerank_http",
        base_url="https://example.test/v1",
        model="test-rr",
    )


@pytest.mark.asyncio
async def test_llm_parses_tool_calls(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path, [llm_endpoint()], default_llm="llm")
    payload = {
        "model": "test-llm",
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "search_knowledge", "arguments": '{"q": "光合作用"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 21, "completion_tokens": 7},
    }
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://example.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=payload)
        )
        response = await gateway.llm([{"role": "user", "content": "hi"}])
        assert route.called

    assert response.tool_calls[0].name == "search_knowledge"
    assert response.tool_calls[0].parsed_arguments() == {"q": "光合作用"}
    assert response.usage.input_tokens == 21
    assert gateway._db.usage_summary("task") is not None
    await gateway.aclose()


@pytest.mark.asyncio
async def test_embedding_batches_and_applies_instructions(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path, [embedding_endpoint()], default_embedding="emb")
    batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        batches.append(body["input"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [0.1, 0.2, 0.3]}
                    for index, _ in enumerate(body["input"])
                ],
                "model": "test-emb",
            },
        )

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://example.test/v1/embeddings").mock(side_effect=handler)
        response = await gateway.embed(["甲", "乙", "丙"], role="query")

    assert [len(batch) for batch in batches] == [2, 1]
    assert all(text.startswith("查询：") for batch in batches for text in batch)
    assert response.dimensions == 3
    assert len(response.vectors) == 3
    await gateway.aclose()


@pytest.mark.asyncio
async def test_rerank_maps_indices(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path, [rerank_endpoint()], default_reranker="rr")
    payload = {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]}
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://example.test/v1/rerank").mock(
            return_value=httpx.Response(200, json=payload)
        )
        response = await gateway.rerank(
            "光合作用",
            [RerankCandidate("a", "无关"), RerankCandidate("b", "相关")],
        )

    assert [hit.id for hit in response.hits] == ["b", "a"]
    await gateway.aclose()


@pytest.mark.asyncio
async def test_retry_on_rate_limit(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path, [llm_endpoint()], default_llm="llm")
    payload = {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://example.test/v1/chat/completions")
        route.side_effect = [
            httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"}),
            httpx.Response(200, json=payload),
        ]
        response = await gateway.llm([{"role": "user", "content": "hi"}])

    assert response.text == "ok"
    assert route.call_count == 2
    await gateway.aclose()


@pytest.mark.asyncio
async def test_not_configured(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path, [])
    with pytest.raises(ModelNotConfigured):
        await gateway.llm([{"role": "user", "content": "hi"}])
    await gateway.aclose()


@pytest.mark.asyncio
async def test_dimension_mismatch_is_reported(tmp_path: Path) -> None:
    gateway = make_gateway(tmp_path, [embedding_endpoint()], default_embedding="emb")
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://example.test/v1/embeddings").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]},
            )
        )
        with pytest.raises(ModelBadResponse, match="维度"):
            await gateway.embed(["甲"])
    await gateway.aclose()


def test_estimate_cost_only_when_priced() -> None:
    endpoint = llm_endpoint()
    assert estimate_cost(endpoint, Usage(calls=1, input_tokens=1000, output_tokens=1000)) is None

    priced = endpoint.model_copy(update={"price_input_per_mtok": 1.0, "price_output_per_mtok": 2.0})
    assert estimate_cost(priced, Usage(calls=1, input_tokens=1_000_000, output_tokens=500_000)) == 2.0
    assert estimate_cost(priced, Usage(calls=3)) == 0.0