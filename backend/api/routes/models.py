"""Model endpoint configuration and connectivity tests."""

from __future__ import annotations

import base64
import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from backend.api.deps import RuntimeDep
from backend.api.schemas import (
    Defaults,
    EndpointOut,
    ModelsPayload,
    ModelsResponse,
    TestRequest,
)
from backend.core.config import AppConfig, EndpointConfig, new_id
from backend.core.models.gateway import make_probe_image

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/models", tags=["models"])

_DEFAULT_FIELDS = {
    "llm": "default_llm",
    "embedding": "default_embedding",
    "reranker": "default_reranker",
    "ocr": "default_ocr",
    "vlm": "default_vlm",
}


def _has_key(runtime, endpoint: EndpointConfig) -> bool:
    if not endpoint.api_key_ref:
        return False
    try:
        return bool(runtime.secrets.get(endpoint.api_key_ref))
    except Exception:
        return False


def _response(runtime) -> ModelsResponse:
    return ModelsResponse(
        endpoints=[
            EndpointOut.from_config(endpoint, _has_key(runtime, endpoint))
            for endpoint in runtime.config.endpoints
        ],
        defaults=Defaults(
            llm=runtime.config.default_llm,
            embedding=runtime.config.default_embedding,
            reranker=runtime.config.default_reranker,
            ocr=runtime.config.default_ocr,
            vlm=runtime.config.default_vlm,
        ),
        secret_backend=runtime.secrets.backend,
    )


@router.get("", response_model=ModelsResponse)
def get_models(runtime: RuntimeDep) -> ModelsResponse:
    return _response(runtime)


@router.put("", response_model=ModelsResponse)
def put_models(payload: ModelsPayload, runtime: RuntimeDep) -> ModelsResponse:
    existing = {endpoint.id: endpoint for endpoint in runtime.config.endpoints}
    endpoints: list[EndpointConfig] = []

    for item in payload.endpoints:
        data = item.model_dump(exclude={"api_key", "clear_api_key"})
        try:
            endpoint = EndpointConfig.model_validate(data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        key_ref = f"endpoint:{endpoint.id}:api_key"
        if item.clear_api_key:
            runtime.secrets.delete(key_ref)
            endpoint.api_key_ref = None
        elif item.api_key:
            runtime.secrets.set(key_ref, item.api_key)
            endpoint.api_key_ref = key_ref
        elif endpoint.id in existing:
            # Preserve the stored reference; the key itself never travels back.
            endpoint.api_key_ref = existing[endpoint.id].api_key_ref
        else:
            endpoint.api_key_ref = None
        endpoints.append(endpoint)

    kept_ids = {endpoint.id for endpoint in endpoints}
    for dropped_id, dropped in existing.items():
        if dropped_id not in kept_ids and dropped.api_key_ref:
            runtime.secrets.delete(dropped.api_key_ref)

    # Validate default selections against the submitted endpoint list.
    defaults = payload.defaults.model_dump()
    by_id = {endpoint.id: endpoint for endpoint in endpoints}
    for kind, value in defaults.items():
        if value and (value not in by_id or by_id[value].kind != kind):
            raise HTTPException(status_code=400, detail=f"默认 {kind} 指向无效端点: {value}")

    current = runtime.config.model_dump()
    current["endpoints"] = [endpoint.model_dump(mode="json") for endpoint in endpoints]
    current["default_llm"] = defaults["llm"]
    current["default_embedding"] = defaults["embedding"]
    current["default_reranker"] = defaults["reranker"]
    current["default_ocr"] = defaults["ocr"]
    current["default_vlm"] = defaults["vlm"]
    try:
        config = AppConfig.model_validate(current)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    runtime.save_config(config)
    return _response(runtime)


@router.post("/test")
async def test_model(payload: TestRequest, runtime: RuntimeDep) -> dict:
    image: bytes | None = None
    if payload.image_base64:
        try:
            image = base64.b64decode(payload.image_base64, validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="image_base64 无效") from exc

    temp_ref: str | None = None
    try:
        if payload.endpoint_id:
            endpoint = runtime.config.endpoint(payload.endpoint_id)
            if endpoint is None:
                raise HTTPException(status_code=404, detail="端点不存在")
        elif payload.endpoint is not None:
            data = payload.endpoint.model_dump(exclude={"api_key", "clear_api_key"})
            try:
                endpoint = EndpointConfig.model_validate(data)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            # Unique id keeps the gateway adapter cache from reusing an old key.
            endpoint.id = f"probe-{new_id()}"
            if payload.endpoint.api_key:
                temp_ref = f"{endpoint.id}:api_key"
                runtime.secrets.set(temp_ref, payload.endpoint.api_key)
                endpoint.api_key_ref = temp_ref
        else:
            raise HTTPException(status_code=400, detail="需要 endpoint_id 或 endpoint")

        if endpoint.kind in {"ocr", "vlm"} and image is None:
            image = make_probe_image()

        result = await runtime.gateway.test_endpoint(endpoint, image=image)
        return asdict(result)
    finally:
        if temp_ref:
            runtime.secrets.delete(temp_ref)


@router.post("/test-all")
async def test_all(runtime: RuntimeDep) -> dict:
    results = []
    for endpoint in runtime.config.endpoints:
        if not endpoint.enabled:
            continue
        image = make_probe_image() if endpoint.kind in {"ocr", "vlm"} else None
        results.append(asdict(await runtime.gateway.test_endpoint(endpoint, image=image)))
    return {"results": results}