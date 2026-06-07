import importlib.metadata
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager

import structlog
import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from openapi_merger.build_info import collect_build_info
from openapi_merger.config import ServiceConfig, load_service_config, load_sources_config
from openapi_merger.logging_config import configure_logging
from openapi_merger.mergers import MERGER_REGISTRY
from openapi_merger.orchestrator import MergeOrchestrator

log = structlog.get_logger()

_REQUEST_ID_RE = re.compile(r"[^A-Za-z0-9._\-]")


def _sanitize_request_id(raw: str | None) -> str:
    if not raw:
        return uuid.uuid4().hex
    cleaned = _REQUEST_ID_RE.sub("", raw.strip())[:64]
    return cleaned or uuid.uuid4().hex


_security = HTTPBasic(auto_error=False)

_service_config: ServiceConfig | None = None
_orchestrators: dict[str, MergeOrchestrator] = {}


def _check_auth(service_config: ServiceConfig, credentials: HTTPBasicCredentials | None) -> None:
    if service_config.auth is None:
        return
    if credentials is None:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    valid = secrets.compare_digest(credentials.username, service_config.auth.username) and \
            secrets.compare_digest(credentials.password, service_config.auth.password)
    if not valid:
        raise HTTPException(status_code=401)


def _make_spec_handler(merger_key: str):
    async def _get_spec(
        format: str = Query("json"),
        refresh: bool = Query(False),
        credentials: HTTPBasicCredentials | None = Depends(_security),
    ):
        _check_auth(_service_config, credentials)
        orch = _orchestrators.get(merger_key)
        if orch is None:
            raise HTTPException(status_code=503, detail=f"merger '{merger_key}' is not available (binary missing?)")
        if format not in ("json", "yaml"):
            raise HTTPException(status_code=400, detail=f"Unknown format '{format}'. Use 'json' or 'yaml'.")
        try:
            merged = await orch.get_merged(refresh=refresh)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))
        if format == "yaml":
            return Response(content=yaml.dump(merged, allow_unicode=True), media_type="text/yaml")
        return merged
    return _get_spec


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service_config, _orchestrators
    configure_logging()
    log.info("app.build_info", **collect_build_info())
    svc_path = os.getenv("SERVICE_CONFIG", "/config/service.yaml")
    src_path = os.getenv("SOURCES_CONFIG", "/config/sources.yaml")
    _service_config = load_service_config(svc_path)
    sources_config = load_sources_config(src_path)

    _orchestrators = {}
    for key, cls in MERGER_REGISTRY.items():
        available = cls.is_available()
        if not available:
            log.warning("merger.unavailable", merger=key, binary=getattr(cls, "binary", None))
            continue
        _orchestrators[key] = MergeOrchestrator(_service_config, sources_config, strategy=cls())

    if _service_config.default_merger not in _orchestrators:
        log.warning(
            "default_merger.unavailable",
            default_merger=_service_config.default_merger,
            available=sorted(_orchestrators.keys()),
        )

    log.info(
        "app.startup",
        service_config=svc_path,
        sources_config=src_path,
        spec_path=_service_config.spec_path,
        default_merger=_service_config.default_merger,
        available_mergers=sorted(_orchestrators.keys()),
        sources_count=len(sources_config.sources),
        auth_enabled=_service_config.auth is not None,
        version=importlib.metadata.version("openapi-merger"),
    )

    for key in MERGER_REGISTRY:
        path = f"/{key}{_service_config.spec_path}"
        app.add_api_route(path, _make_spec_handler(key), methods=["GET"])

    async def _default_handler(
        format: str = Query("json"),
        refresh: bool = Query(False),
        credentials: HTTPBasicCredentials | None = Depends(_security),
    ):
        handler = _make_spec_handler(_service_config.default_merger)
        return await handler(format=format, refresh=refresh, credentials=credentials)

    app.add_api_route(_service_config.spec_path, _default_handler, methods=["GET"])

    async def _clear_cache(
        credentials: HTTPBasicCredentials | None = Depends(_security),
    ):
        _check_auth(_service_config, credentials)
        for orch in _orchestrators.values():
            orch.clear_cache()
        return Response(status_code=204)

    app.add_api_route("/admin/cache/clear", _clear_cache, methods=["POST"])
    yield
    log.info("app.shutdown")


app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)


@app.middleware("http")
async def _request_log_middleware(request: Request, call_next):
    request_id = _sanitize_request_id(request.headers.get("x-request-id"))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.exception("request.failed", method=request.method, path=request.url.path, duration_ms=duration_ms)
        raise
    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info("request.completed", method=request.method, path=request.url.path, status=response.status_code, duration_ms=duration_ms)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}
