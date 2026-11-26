"""FastAPI application entrypoint.

Exposes the infrastructure probes, the Phase 3/4/5 agent endpoints, the Phase 6
orchestration workflow and the Phase 7 authentication, citizen and officer
interfaces:

    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.agents import router as agents_router
from app.api.auth import router as auth_router
from app.api.citizens import router as citizens_router
from app.api.duplicates import router as duplicates_router
from app.api.evidence import router as evidence_router
from app.api.officers import router as officers_router
from app.api.routing import router as routing_router
from app.api.workflow import router as workflow_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.database.session import check_database
from app.services.ollama_service import check_ollama_service, find_model

configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Intelligent Citizen Grievance Management and Governance Platform. An "
        "autonomous multi-agent system for citizen grievance resolution and "
        "public service delivery. Phase 7: a citizen portal (submit a "
        "complaint in English or Tamil, upload optional evidence, track its "
        "status) and an officer dashboard (review the queue, inspect agent "
        "suggestions, and approve or reject). Authentication and role "
        "authorisation are enforced on the server through a signed session "
        "cookie. Every consequential decision - a duplicate link, a department "
        "assignment, a resolution - requires a human officer; the agents "
        "recommend and never act."
    ),
)

app.include_router(auth_router)
app.include_router(citizens_router)
app.include_router(officers_router)
app.include_router(agents_router)
app.include_router(duplicates_router)
app.include_router(evidence_router)
app.include_router(routing_router)
app.include_router(workflow_router)

#: Credentialed CORS: the browser sends the session cookie, so the allowed
#: origins are an explicit list from configuration - never a wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer any unhandled error with a generic 500.

    A traceback, a database message or an internal identifier is never returned
    to a caller, whatever ``DEBUG`` is set to; the detail is logged server-side
    instead.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "an internal error occurred"})


@app.get("/", tags=["meta"])
def root() -> dict[str, object]:
    return {
        "application": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "supported_languages": settings.languages,
        "phase": "7 - citizen portal and officer dashboard over the Phase 6 "
        "LangGraph workflow, with server-side session authentication and "
        "role-based authorisation",
    }


@app.get("/health", tags=["meta"])
def health() -> dict[str, object]:
    """Liveness probe covering both PostgreSQL and Ollama."""
    database = check_database()
    ollama = check_ollama_service()
    model = find_model(ollama, settings.ollama_model) if ollama.reachable else None

    checks = {
        "postgresql": database.connected,
        "ollama": ollama.reachable,
        "configured_model": model is not None,
    }
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "checks": checks,
        "database": {
            "database": database.database_name,
            "server_version": database.server_version,
            "extensions": database.installed_extensions,
            "error": database.error,
        },
        "ollama": {
            "base_url": ollama.base_url,
            "version": ollama.version,
            "configured_model": settings.ollama_model,
            "model_available": model is not None,
            "models": ollama.model_names,
            "error": ollama.error,
        },
    }


@app.on_event("startup")
def on_startup() -> None:
    database = check_database()
    ollama = check_ollama_service()
    logger.info(
        "Startup | postgresql=%s | ollama=%s | model=%s (%s)",
        "connected" if database.connected else "unavailable",
        "reachable" if ollama.reachable else "unavailable",
        settings.ollama_model,
        "verified" if find_model(ollama, settings.ollama_model) else "missing",
    )
