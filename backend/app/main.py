from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import router
from .config import Settings, get_settings
from .database import Database
from .domain import DomainEvent
from .event_bus import EventBus
from .observability import Metrics
from .services.gemini_service import GeminiService
from .services.hybrid_search import HybridSearch
from .services.predictor import Predictor
from .services.reports import ReportScheduler, ReportWorkflow
from .services.workflow import DispatchWorkflow, InsightWorkflow


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


DEMO_SIGNALS: list[dict[str, Any]] = [
    {
        "source": "COP",
        "local": "Praça do Arsenal",
        "category": "aglomeracao",
        "description": "Três sinais relacionados registrados em dez minutos durante evento no entorno.",
        "dia_semana": 5,
        "hora_dia": 21,
        "eventos_proximos": 1,
        "historico_ocorrencias_7d": 12,
        "iluminacao_ativa_pct": 48,
        "iluminacao_fonte": "real",
        "densidade_pessoas": 88,
        "latitude": -8.0611,
        "longitude": -34.8711,
        "external_id": "demo-arsenal",
    },
    {
        "source": "EMLURB 156",
        "local": "Parque Treze de Maio",
        "category": "patrimonio_publico",
        "description": "Aumento de chamados sobre iluminação e possível dano ao patrimônio.",
        "dia_semana": 4,
        "hora_dia": 19,
        "eventos_proximos": 0,
        "historico_ocorrencias_7d": 9,
        "iluminacao_ativa_pct": 41,
        "iluminacao_fonte": "real",
        "densidade_pessoas": 62,
        "latitude": -8.0585,
        "longitude": -34.8811,
        "external_id": "demo-treze-maio",
    },
]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings.database_path)
        database.initialize()
        metrics = Metrics()
        predictor = Predictor(settings)
        bus = EventBus(database)
        gemini = GeminiService(settings, metrics)
        search = HybridSearch(database)
        insight_workflow = InsightWorkflow(database, bus, predictor, gemini, metrics)
        dispatch_workflow = DispatchWorkflow(database, bus, metrics)
        report_workflow = ReportWorkflow(database, bus, gemini, metrics)
        report_scheduler = ReportScheduler(settings, database, bus, metrics)
        bus.subscribe_handler("signal.received", insight_workflow.on_signal_received)
        bus.subscribe_handler("event.approved", dispatch_workflow.on_event_approved)
        bus.subscribe_handler("report.generation.requested", report_workflow.on_report_requested)

        app.state.settings = settings
        app.state.database = database
        app.state.metrics = metrics
        app.state.predictor = predictor
        app.state.event_bus = bus
        app.state.gemini = gemini
        app.state.search = search
        app.state.report_scheduler = report_scheduler
        await bus.start()

        if settings.seed_demo_data and database.count_signals() == 0 and predictor.ready:
            for payload in DEMO_SIGNALS:
                signal_id = database.create_signal(payload)
                await bus.publish(
                    DomainEvent(
                        event_type="signal.received",
                        aggregate_id=signal_id,
                        payload={"source": payload["source"], "local": payload["local"], "demo": True},
                    )
                )
        await report_scheduler.start()
        yield
        await report_scheduler.stop()
        await bus.stop()

    app = FastAPI(
        title=settings.app_name,
        description="API orientada a eventos para previsão de demanda operacional, revisão humana, distribuição de alertas e relatórios gerenciais.",
        version="2.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=not settings.allow_all_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
    )

    @app.middleware("http")
    async def observe_requests(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            route = getattr(request.scope.get("route"), "path", request.url.path)
            if hasattr(request.app.state, "metrics"):
                request.app.state.metrics.http_requests.labels(request.method, route, "500").inc()
                request.app.state.metrics.http_duration.labels(request.method, route).observe(time.perf_counter() - started)
            raise
        route = getattr(request.scope.get("route"), "path", request.url.path)
        if hasattr(request.app.state, "metrics"):
            request.app.state.metrics.http_requests.labels(request.method, route, str(response.status_code)).inc()
            request.app.state.metrics.http_duration.labels(request.method, route).observe(time.perf_counter() - started)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception):
        logger.exception("unhandled_api_error")
        return JSONResponse(status_code=500, content={"detail": "Erro interno do CAIS", "type": type(exc).__name__})

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"name": settings.app_name, "docs": "/docs", "health": f"{settings.api_prefix}/health"}

    app.include_router(router, prefix=settings.api_prefix)
    return app


app = create_app()
