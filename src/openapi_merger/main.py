import os
import secrets
from contextlib import asynccontextmanager

import structlog
import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from openapi_merger.config import load_service_config, load_sources_config, ServiceConfig
from openapi_merger.logging_config import configure_logging
from openapi_merger.orchestrator import MergeOrchestrator

log = structlog.get_logger()

_security = HTTPBasic(auto_error=False)

_service_config: ServiceConfig | None = None
_orchestrator: MergeOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service_config, _orchestrator
    svc_path = os.getenv("SERVICE_CONFIG", "/config/service.yaml")
    src_path = os.getenv("SOURCES_CONFIG", "/config/sources.yaml")
    _service_config = load_service_config(svc_path)
    sources_config = load_sources_config(src_path)
    _orchestrator = MergeOrchestrator(_service_config, sources_config)

    configure_logging()
    log.info(
        "app.startup",
        service_config=svc_path,
        sources_config=src_path,
        spec_path=_service_config.spec_path,
        sources_count=len(sources_config.sources),
        auth_enabled=_service_config.auth is not None,
    )

    async def _get_spec(
        format: str = Query("json"),
        refresh: bool = Query(False),
        credentials: HTTPBasicCredentials | None = Depends(_security),
    ):
        if _service_config.auth:
            if credentials is None:
                raise HTTPException(
                    status_code=401,
                    headers={"WWW-Authenticate": "Basic"},
                )
            valid = secrets.compare_digest(
                credentials.username, _service_config.auth.username
            ) and secrets.compare_digest(
                credentials.password, _service_config.auth.password
            )
            if not valid:
                raise HTTPException(status_code=401)

        if format not in ("json", "yaml"):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown format '{format}'. Use 'json' or 'yaml'.",
            )

        try:
            merged = await _orchestrator.get_merged(refresh=refresh)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))

        if format == "yaml":
            return Response(
                content=yaml.dump(merged, allow_unicode=True),
                media_type="text/yaml",
            )
        return merged

    app.add_api_route(
        _service_config.spec_path,
        _get_spec,
        methods=["GET"],
    )
    yield
    log.info("app.shutdown")


app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    return {"status": "ok"}
