from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ..domain import DomainEvent
from ..schemas import (
    ApprovalRequest,
    ChatRequest,
    DispatchResponseRequest,
    LoginRequest,
    PredictionInput,
    RejectionRequest,
    ReportGenerationRequest,
    SearchRequest,
    SignalCreate,
)
from ..security import verify_password
from ..services.reports import render_procurement_markdown
from .dependencies import current_user, guard_user, manager_user


router = APIRouter()


@router.get("/health", tags=["Operação"])
async def health(request: Request) -> dict[str, Any]:
    database = request.app.state.database
    predictor = request.app.state.predictor
    bus = request.app.state.event_bus
    gemini = request.app.state.gemini
    healthy = database.ping() and predictor.ready and bus.running
    payload = {
        "status": "operational" if healthy else "degraded",
        "database": "up" if database.ping() else "down",
        "model": {
            "loaded": predictor.ready,
            "version": predictor.version,
            "error": predictor.load_error,
        },
        "event_bus": {
            "running": bus.running,
            "queue_depth": bus.queue.qsize(),
            "last_error": bus.last_error,
        },
        "report_scheduler": request.app.state.report_scheduler.status(),
        "gemini": {
            "mode": request.app.state.settings.gemini_mode,
            "available": gemini.available,
            "model": request.app.state.settings.gemini_model,
            "fallback_ready": True,
        },
    }
    if not healthy:
        payload["status"] = "degraded"
    return payload


@router.post("/auth/login", tags=["Acesso"])
async def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    database = request.app.state.database
    user = database.get_user_by_email(payload.email)
    if not user or not user.get("active") or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha inválidos")
    token, expires_at = database.create_session(user["id"], request.app.state.settings.session_hours)
    public_user = {key: user[key] for key in ("id", "email", "name", "role")}
    guard = database.get_guard_for_user(user["id"]) if user["role"] == "guard" else None
    return {"access_token": token, "token_type": "bearer", "expires_at": expires_at, "user": public_user, "guard": guard}


@router.get("/auth/me", tags=["Acesso"])
async def me(request: Request, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    public_user = {key: user[key] for key in ("id", "email", "name", "role")}
    guard = request.app.state.database.get_guard_for_user(user["id"]) if user["role"] == "guard" else None
    return {"user": public_user, "guard": guard}


def _can_ingest(
    request: Request,
    authorization: str | None,
    api_key: str | None,
) -> bool:
    configured_key = request.app.state.settings.ingest_api_key
    if configured_key and api_key == configured_key:
        return True
    if authorization and authorization.lower().startswith("bearer "):
        user = request.app.state.database.get_user_by_token(authorization.split(" ", 1)[1])
        return bool(user and user["role"] == "manager")
    return False


@router.post("/signals", status_code=status.HTTP_202_ACCEPTED, tags=["Ingestão"])
async def ingest_signal(
    payload: SignalCreate,
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    if not _can_ingest(request, authorization, x_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credencial de ingestão inválida")
    signal_id = request.app.state.database.create_signal(payload.model_dump())
    domain_event = DomainEvent(
        event_type="signal.received",
        aggregate_id=signal_id,
        payload={"source": payload.source, "local": payload.local},
    )
    request.app.state.metrics.domain_events.labels(domain_event.event_type).inc()
    await request.app.state.event_bus.publish(domain_event)
    return {
        "signal_id": signal_id,
        "status": "processing",
        "message": "Sinal recebido para processamento assíncrono",
    }


@router.post("/predicao-risco", tags=["Modelo"])
async def legacy_prediction(
    payload: PredictionInput,
    request: Request,
    _: dict[str, Any] = Depends(manager_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    result = await asyncio.to_thread(request.app.state.predictor.predict, payload.model_dump())
    request.app.state.metrics.prediction_duration.observe(time.perf_counter() - started)
    request.app.state.metrics.predictions.labels(result["criticality"]).inc()
    return {
        "local": payload.local,
        "acao_preventiva_necessaria": result["requires_preventive_action"],
        "probabilidade_risco": result["probability"],
        "nivel_criticidade": result["criticality"].upper(),
        "recomendacao_operacional": result["recommended_action"],
        "indicadores": result["indicators"],
        "fatores": result["drivers"],
        "modelo": result["model_version"],
    }


@router.get("/model/metrics", tags=["Modelo"])
async def model_metrics(
    request: Request, _: dict[str, Any] = Depends(manager_user)
) -> dict[str, Any]:
    return request.app.state.predictor.metrics()


@router.get("/events", tags=["Gestão"])
async def list_events(
    request: Request,
    _: dict[str, Any] = Depends(manager_user),
    event_status: str | None = Query(default=None, alias="status"),
    criticality: str | None = None,
    local: str | None = None,
    source: str | None = None,
    category: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = request.app.state.database.list_events(
        status=event_status,
        criticality=criticality,
        local=local,
        source=source,
        category=category,
        query_text=q,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/events/{event_id}", tags=["Gestão"])
async def event_detail(
    event_id: str, request: Request, _: dict[str, Any] = Depends(manager_user)
) -> dict[str, Any]:
    event = request.app.state.database.get_operational_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return event


@router.get("/events/{event_id}/timeline", tags=["Gestão"])
async def event_timeline(
    event_id: str, request: Request, _: dict[str, Any] = Depends(manager_user)
) -> dict[str, Any]:
    if not request.app.state.database.get_operational_event(event_id):
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return {"items": request.app.state.database.timeline(event_id)}


@router.post("/events/{event_id}/approve", status_code=202, tags=["Gestão"])
async def approve_event(
    event_id: str,
    payload: ApprovalRequest,
    request: Request,
    user: dict[str, Any] = Depends(manager_user),
) -> dict[str, Any]:
    if not request.app.state.database.approve_event(event_id, user["id"]):
        event = request.app.state.database.get_operational_event(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Evento não encontrado")
        raise HTTPException(status_code=409, detail=f"Evento está em estado {event['status']}")
    domain_event = DomainEvent(
        event_type="event.approved",
        aggregate_id=event_id,
        actor_id=user["id"],
        payload={"guard_ids": payload.guard_ids or [], "note": payload.note},
    )
    request.app.state.metrics.domain_events.labels(domain_event.event_type).inc()
    await request.app.state.event_bus.publish(domain_event)
    return {"event_id": event_id, "status": "approved", "message": "Aprovação registrada; distribuição iniciada"}


@router.post("/events/{event_id}/reject", tags=["Gestão"])
async def reject_event(
    event_id: str,
    payload: RejectionRequest,
    request: Request,
    user: dict[str, Any] = Depends(manager_user),
) -> dict[str, Any]:
    if not request.app.state.database.reject_event(event_id, user["id"], payload.reason):
        event = request.app.state.database.get_operational_event(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Evento não encontrado")
        raise HTTPException(status_code=409, detail=f"Evento está em estado {event['status']}")
    domain_event = DomainEvent(
        event_type="event.rejected",
        aggregate_id=event_id,
        actor_id=user["id"],
        payload={"reason": payload.reason},
    )
    request.app.state.metrics.domain_events.labels(domain_event.event_type).inc()
    await request.app.state.event_bus.publish(domain_event)
    return {"event_id": event_id, "status": "rejected"}


@router.get("/dashboard/summary", tags=["Gestão"])
async def dashboard_summary(
    request: Request, _: dict[str, Any] = Depends(manager_user)
) -> dict[str, Any]:
    return request.app.state.database.dashboard_summary()


@router.get("/reports", tags=["Relatórios"])
async def list_reports(
    request: Request,
    _: dict[str, Any] = Depends(manager_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = request.app.state.database.list_reports(limit=limit, offset=offset)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "schedule": request.app.state.report_scheduler.status(),
    }


@router.get("/reports/schedule", tags=["Relatórios"])
async def report_schedule(
    request: Request, _: dict[str, Any] = Depends(manager_user)
) -> dict[str, Any]:
    return request.app.state.report_scheduler.status()


@router.post("/reports/generate", status_code=202, tags=["Relatórios"])
async def generate_report(
    payload: ReportGenerationRequest,
    request: Request,
    user: dict[str, Any] = Depends(manager_user),
) -> dict[str, Any]:
    job_id = await request.app.state.report_scheduler.trigger(
        reason="manual",
        lookback_days=payload.lookback_days,
        actor_id=user["id"],
        title=payload.title,
    )
    return {
        "job_id": job_id,
        "status": "processing",
        "message": "Geração solicitada; o relatório será publicado pelo fluxo de eventos",
    }


@router.get("/reports/{report_id}", tags=["Relatórios"])
async def report_detail(
    report_id: str,
    request: Request,
    _: dict[str, Any] = Depends(manager_user),
) -> dict[str, Any]:
    report = request.app.state.database.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")
    return report


@router.get("/reports/{report_id}/draft.md", tags=["Relatórios"])
async def procurement_draft_markdown(
    report_id: str,
    request: Request,
    _: dict[str, Any] = Depends(manager_user),
) -> PlainTextResponse:
    report = request.app.state.database.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")
    content = render_procurement_markdown(report)
    return PlainTextResponse(
        content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="cais-minuta-{report_id}.md"'
        },
    )


@router.get("/guards", tags=["Gestão"])
async def list_guards(
    request: Request,
    _: dict[str, Any] = Depends(manager_user),
    availability: str | None = None,
) -> dict[str, Any]:
    return {"items": request.app.state.database.list_guards(availability)}


@router.get("/guards/me/dispatches", tags=["Guarda"])
async def my_dispatches(
    request: Request, user: dict[str, Any] = Depends(guard_user)
) -> dict[str, Any]:
    guard = request.app.state.database.get_guard_for_user(user["id"])
    if not guard:
        raise HTTPException(status_code=404, detail="Perfil operacional não encontrado")
    return {"guard": guard, "items": request.app.state.database.list_dispatches_for_guard(guard["id"])}


@router.post("/dispatches/{dispatch_id}/respond", tags=["Guarda"])
async def respond_dispatch(
    dispatch_id: str,
    payload: DispatchResponseRequest,
    request: Request,
    user: dict[str, Any] = Depends(guard_user),
) -> dict[str, Any]:
    dispatch = request.app.state.database.get_dispatch(dispatch_id)
    if not dispatch:
        raise HTTPException(status_code=404, detail="Despacho não encontrado")
    if dispatch["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Este alerta pertence a outro destinatário")
    expected_status = "acknowledged" if payload.action == "acknowledge" else "completed"
    if dispatch["status"] == expected_status or (payload.action == "acknowledge" and dispatch["status"] == "completed"):
        return {"dispatch_id": dispatch_id, "status": dispatch["status"], "idempotent": True}
    if not request.app.state.database.respond_to_dispatch(dispatch_id, payload.action, payload.note):
        raise HTTPException(status_code=409, detail=f"Ação incompatível com estado {dispatch['status']}")

    event_type = "dispatch.acknowledged" if payload.action == "acknowledge" else "dispatch.completed"
    request.app.state.metrics.dispatches.labels(payload.action).inc()
    if payload.action == "acknowledge":
        try:
            sent_at = datetime.fromisoformat(dispatch["sent_at"])
            request.app.state.metrics.dispatch_ack_duration.observe(
                max(0.0, (datetime.now(UTC) - sent_at).total_seconds())
            )
        except (TypeError, ValueError):
            pass
    domain_event = DomainEvent(
        event_type=event_type,
        aggregate_id=dispatch["operational_event_id"],
        actor_id=user["id"],
        payload={"dispatch_id": dispatch_id, "guard_id": dispatch["guard_id"], "note": payload.note},
    )
    request.app.state.metrics.domain_events.labels(domain_event.event_type).inc()
    await request.app.state.event_bus.publish(domain_event)

    if payload.action == "complete" and request.app.state.database.all_dispatches_completed(dispatch["operational_event_id"]):
        request.app.state.database.update_event_status(dispatch["operational_event_id"], "completed")
        await request.app.state.event_bus.publish(
            DomainEvent(
                event_type="event.completed",
                aggregate_id=dispatch["operational_event_id"],
                actor_id=user["id"],
                payload={"reason": "Todos os destinatários concluíram o atendimento"},
            )
        )
    return {"dispatch_id": dispatch_id, "status": expected_status, "idempotent": False}


@router.post("/assistant/search", tags=["Inteligência"])
async def assistant_search(
    payload: SearchRequest,
    request: Request,
    _: dict[str, Any] = Depends(manager_user),
) -> dict[str, Any]:
    filters = payload.filters.model_dump(exclude_none=True) if payload.filters else {}
    return await asyncio.to_thread(
        request.app.state.search.search,
        payload.query,
        filters=filters,
        top_k=payload.top_k,
    )


@router.post("/assistant/chat", tags=["Inteligência"])
async def assistant_chat(
    payload: ChatRequest,
    request: Request,
    _: dict[str, Any] = Depends(manager_user),
) -> dict[str, Any]:
    filters = payload.filters.model_dump(exclude_none=True) if payload.filters else {}
    retrieval = await asyncio.to_thread(
        request.app.state.search.search,
        payload.question,
        filters=filters,
        top_k=payload.top_k,
    )
    answer, provider = await request.app.state.gemini.answer(payload.question, retrieval["results"])
    return {
        "answer": answer,
        "sources": retrieval["results"],
        "provider": provider,
        "conversation_id": payload.conversation_id,
        "retrieval": {key: value for key, value in retrieval.items() if key != "results"},
        "notice": "Resposta de apoio à decisão; valide o contexto operacional antes de agir.",
    }


@router.get("/stream", tags=["Tempo real"])
async def stream_events(request: Request, token: str = Query(min_length=20)) -> StreamingResponse:
    user = request.app.state.database.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada")
    guard = request.app.state.database.get_guard_for_user(user["id"]) if user["role"] == "guard" else None
    queue = request.app.state.event_bus.add_subscriber()

    async def generate():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if user["role"] == "guard":
                    if not message["event_type"].startswith("dispatch."):
                        continue
                    if message.get("payload", {}).get("guard_id") != (guard or {}).get("id"):
                        continue
                yield f"event: {message['event_type']}\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
        finally:
            request.app.state.event_bus.remove_subscriber(queue)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    request.app.state.metrics.event_queue_depth.set(request.app.state.event_bus.queue.qsize())
    summary = request.app.state.database.dashboard_summary()
    request.app.state.metrics.pending_approval.set(summary["pending_approval"])
    request.app.state.metrics.acknowledgement_rate.set(summary["acknowledgement_rate"])
    request.app.state.metrics.available_reports.set(summary["reports_generated"])
    return Response(generate_latest(request.app.state.metrics.registry), media_type=CONTENT_TYPE_LATEST)
