from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "cais_http_requests_total",
            "Total de requisições HTTP",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "cais_http_request_duration_seconds",
            "Duração das requisições HTTP",
            ["method", "route"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=self.registry,
        )
        self.predictions = Counter(
            "cais_predictions_total",
            "Inferências do modelo por criticidade",
            ["criticality"],
            registry=self.registry,
        )
        self.prediction_duration = Histogram(
            "cais_prediction_duration_seconds",
            "Duração da inferência do modelo local",
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
            registry=self.registry,
        )
        self.gemini_requests = Counter(
            "cais_gemini_requests_total",
            "Chamadas ao Gemini por resultado",
            ["operation", "result"],
            registry=self.registry,
        )
        self.domain_events = Counter(
            "cais_domain_events_total",
            "Eventos de domínio publicados",
            ["event_type"],
            registry=self.registry,
        )
        self.dispatches = Counter(
            "cais_dispatches_total",
            "Despachos por transição",
            ["action"],
            registry=self.registry,
        )
        self.reports = Counter(
            "cais_reports_total",
            "Relatórios por origem e resultado",
            ["reason", "result"],
            registry=self.registry,
        )
        self.dispatch_ack_duration = Histogram(
            "cais_dispatch_ack_duration_seconds",
            "Tempo entre envio e confirmação de recebimento",
            buckets=(5, 15, 30, 60, 120, 300, 600, 1800, 3600, 14400),
            registry=self.registry,
        )
        self.pending_approval = Gauge(
            "cais_pending_approval_events",
            "Eventos aguardando decisão do gestor",
            registry=self.registry,
        )
        self.acknowledgement_rate = Gauge(
            "cais_dispatch_acknowledgement_ratio",
            "Proporção de despachos com recebimento confirmado",
            registry=self.registry,
        )
        self.available_reports = Gauge(
            "cais_available_reports",
            "Relatórios gerenciais persistidos",
            registry=self.registry,
        )
        self.event_queue_depth = Gauge(
            "cais_event_queue_depth",
            "Eventos aguardando processamento",
            registry=self.registry,
        )
