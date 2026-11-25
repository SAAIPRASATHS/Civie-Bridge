"""FastAPI application entrypoint.

Exposes the infrastructure probes plus the Phase 3/4/5 agent endpoints:

    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.agents import router as agents_router
from app.api.duplicates import router as duplicates_router
from app.api.evidence import router as evidence_router
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
        "Autonomous multi-agent system for citizen grievance resolution and "
        "public service delivery. Phase 6: complaint intake, classification, "
        "duplicate detection, evidence verification, department routing and "
        "resolution recommendations, orchestrated as one observable LangGraph "
        "workflow at /api/v1/workflow/run. Every consequential decision - a "
        "duplicate link, a department assignment, a resolution - requires a human "
        "officer; the workflow recommends and never acts."
    ),
)

app.include_router(agents_router)
app.include_router(duplicates_router)
app.include_router(evidence_router)
app.include_router(routing_router)
app.include_router(workflow_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["meta"])
def root() -> dict[str, object]:
    return {
        "application": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "supported_languages": settings.languages,
        "phase": "6 - LangGraph multi-agent orchestration over the intake, "
        "classification, duplicate, evidence, routing and resolution agents",
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
