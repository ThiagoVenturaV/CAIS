from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=6, max_length=128)


class SignalCreate(BaseModel):
    source: str = Field(default="COP", min_length=2, max_length=80)
    local: str = Field(min_length=2, max_length=180)
    category: str = Field(default="ordem_publica", min_length=2, max_length=80)
    description: str = Field(min_length=5, max_length=1200)
    dia_semana: int = Field(ge=0, le=6)
    hora_dia: int = Field(ge=0, le=23)
    eventos_proximos: int = Field(ge=0, le=1)
    historico_ocorrencias_7d: int = Field(ge=0, le=10000)
    iluminacao_ativa_pct: float = Field(ge=0, le=100)
    iluminacao_fonte: str = Field(default="nao_informada", min_length=2, max_length=60)
    densidade_pessoas: int = Field(ge=0, le=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    external_id: str | None = Field(default=None, max_length=120)

    @field_validator("source", "local", "category", "description", "iluminacao_fonte")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class PredictionInput(BaseModel):
    local: str = Field(min_length=2, max_length=180)
    dia_semana: int = Field(ge=0, le=6)
    hora_dia: int = Field(ge=0, le=23)
    eventos_proximos: int = Field(ge=0, le=1)
    historico_ocorrencias_7d: int = Field(ge=0, le=10000)
    iluminacao_ativa_pct: float = Field(ge=0, le=100)
    iluminacao_fonte: str = Field(min_length=2, max_length=60)
    densidade_pessoas: int = Field(ge=0, le=100)


class ApprovalRequest(BaseModel):
    guard_ids: list[str] | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=600)


class RejectionRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=600)


class DispatchResponseRequest(BaseModel):
    action: Literal["acknowledge", "complete"]
    note: str | None = Field(default=None, max_length=600)


class AssistantFilters(BaseModel):
    status: str | None = Field(default=None, max_length=50)
    criticality: Literal["low", "medium", "high"] | None = None
    local: str | None = Field(default=None, max_length=180)
    category: str | None = Field(default=None, max_length=80)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    filters: AssistantFilters | None = None
    top_k: int = Field(default=5, ge=1, le=10)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1500)
    filters: AssistantFilters | None = None
    top_k: int = Field(default=5, ge=1, le=10)
    conversation_id: str | None = Field(default=None, max_length=100)


class ReportGenerationRequest(BaseModel):
    lookback_days: int = Field(default=7, ge=1, le=365)
    title: str | None = Field(default=None, min_length=5, max_length=180)


class ApiMessage(BaseModel):
    message: str
    details: dict[str, Any] | None = None
