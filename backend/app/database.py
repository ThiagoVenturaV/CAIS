from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Sequence

from .domain import make_id, utc_now
from .security import hash_password, new_token


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('manager', 'guard')),
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guards (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    specialization TEXT NOT NULL,
    availability TEXT NOT NULL DEFAULT 'available',
    latitude REAL,
    longitude REAL,
    contact TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    local TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    dia_semana INTEGER NOT NULL,
    hora_dia INTEGER NOT NULL,
    eventos_proximos INTEGER NOT NULL,
    historico_ocorrencias_7d INTEGER NOT NULL,
    iluminacao_ativa_pct REAL NOT NULL,
    iluminacao_fonte TEXT NOT NULL,
    densidade_pessoas INTEGER NOT NULL,
    latitude REAL,
    longitude REAL,
    status TEXT NOT NULL DEFAULT 'processing',
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operational_events (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE REFERENCES signals(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    conversational_summary TEXT NOT NULL,
    local TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT NOT NULL,
    probability REAL NOT NULL,
    criticality TEXT NOT NULL CHECK (criticality IN ('low', 'medium', 'high')),
    requires_preventive_action INTEGER NOT NULL,
    recommended_action TEXT NOT NULL,
    indicators_json TEXT NOT NULL,
    drivers_json TEXT NOT NULL,
    model_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    approved_by TEXT REFERENCES users(id),
    approved_at TEXT,
    rejected_by TEXT REFERENCES users(id),
    rejected_at TEXT,
    rejection_reason TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispatches (
    id TEXT PRIMARY KEY,
    operational_event_id TEXT NOT NULL REFERENCES operational_events(id) ON DELETE CASCADE,
    guard_id TEXT NOT NULL REFERENCES guards(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('sent', 'acknowledged', 'completed')),
    sent_at TEXT NOT NULL,
    acknowledged_at TEXT,
    completed_at TEXT,
    note TEXT,
    UNIQUE (operational_event_id, guard_id)
);

CREATE TABLE IF NOT EXISTS event_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    report_type TEXT NOT NULL,
    generation_reason TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('generated')),
    executive_summary TEXT NOT NULL,
    statistics_json TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    solution_options_json TEXT NOT NULL,
    procurement_draft_json TEXT NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    provider TEXT NOT NULL,
    created_by TEXT REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_operational_events_status ON operational_events(status);
CREATE INDEX IF NOT EXISTS idx_operational_events_created ON operational_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_operational_events_criticality ON operational_events(criticality);
CREATE INDEX IF NOT EXISTS idx_dispatches_guard_status ON dispatches(guard_id, status);
CREATE INDEX IF NOT EXISTS idx_journal_aggregate ON event_journal(aggregate_id, sequence);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_period ON reports(period_start, period_end);
"""


def _decode_json_fields(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in list(result):
        if key.endswith("_json"):
            clean_key = key.removesuffix("_json")
            try:
                result[clean_key] = json.loads(result.pop(key))
            except (TypeError, json.JSONDecodeError):
                result[clean_key] = result.pop(key)
    for key in ("requires_preventive_action", "active"):
        if key in result:
            result[key] = bool(result[key])
    return result


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
        self.seed_demo_users()
        self.seed_knowledge()

    def ping(self) -> bool:
        try:
            with self.connect() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def fetch_one(self, query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return _decode_json_fields(dict(row)) if row else None

    def fetch_all(self, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def execute(self, query: str, params: Sequence[Any] = ()) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(query, params)
            return cursor.rowcount

    def seed_demo_users(self) -> None:
        if self.fetch_one("SELECT id FROM users LIMIT 1"):
            return
        now = utc_now()
        people = [
            ("usr_manager", "gestora@cais.recife.br", "Marina Alves", "manager", "cais2026"),
            ("usr_guard12", "guarda12@cais.recife.br", "Ana Souza", "guard", "cais2026"),
            ("usr_guard07", "guarda07@cais.recife.br", "Carlos Lima", "guard", "cais2026"),
            ("usr_guard21", "guarda21@cais.recife.br", "Rafaela Silva", "guard", "cais2026"),
        ]
        guards = [
            ("grd_12", "usr_guard12", "GCM-12", "Ana Souza", "Ronda Turística", -8.0618, -34.8715),
            ("grd_07", "usr_guard07", "GCM-07", "Carlos Lima", "Patrimônio", -8.0642, -34.8741),
            ("grd_21", "usr_guard21", "GCM-21", "Rafaela Silva", "GTO", -8.0587, -34.8726),
        ]
        with self.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO users (id, email, name, role, password_hash, active, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                [(uid, email, name, role, hash_password(password), now) for uid, email, name, role, password in people],
            )
            connection.executemany(
                """
                INSERT INTO guards
                    (id, user_id, code, name, specialization, availability, latitude, longitude, contact, created_at)
                VALUES (?, ?, ?, ?, ?, 'available', ?, ?, NULL, ?)
                """,
                [(*guard, now) for guard in guards],
            )

    def seed_knowledge(self) -> None:
        if self.fetch_one("SELECT id FROM knowledge_documents LIMIT 1"):
            return
        now = utc_now()
        documents = [
            (
                "doc_product",
                "O que é o CAIS",
                "O CAIS é a Central de Alerta e Inteligência para Segurança. Ele transforma dados já existentes em inteligência operacional para a Guarda Municipal, com previsão de demanda, detecção de padrões, explicações e aprovação humana.",
                "produto",
                {"topic": "produto"},
            ),
            (
                "doc_governance",
                "Governança e decisão humana",
                "O modelo estima demanda operacional e não prevê crime. O score apoia o gestor, que deve revisar evidências e aprovar ou rejeitar o alerta. O Gemini explica os dados, mas não muda score, criticidade ou destinatários.",
                "governanca",
                {"topic": "governanca"},
            ),
            (
                "doc_workflow",
                "Fluxo operacional",
                "Um sinal é recebido, processado pelo modelo local, enriquecido em linguagem operacional e exibido ao gestor. Após aprovação, o alerta é enviado simultaneamente aos guardas selecionados. Cada guarda confirma o recebimento sem bloquear os demais.",
                "arquitetura",
                {"topic": "fluxo"},
            ),
            (
                "doc_data",
                "Dados e limitações do modelo",
                "O protótipo usa dados públicos municipais combinados com variáveis sintéticas para demonstração. As métricas medem validação do protótipo e não desempenho comprovado em produção. Qualquer implantação real exige validação temporal, auditoria de viés e monitoramento de drift.",
                "modelo",
                {"topic": "dados"},
            ),
        ]
        with self.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO knowledge_documents (id, title, content, source, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(doc_id, title, content, source, json.dumps(meta, ensure_ascii=False), now) for doc_id, title, content, source, meta in documents],
            )

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT id, email, name, role, password_hash, active FROM users WHERE lower(email)=lower(?)",
            (email,),
        )

    def get_user_by_token(self, token: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """
            SELECT u.id, u.email, u.name, u.role, u.active, s.expires_at
            FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token=? AND s.expires_at>?
            """,
            (token, utc_now()),
        )

    def create_session(self, user_id: str, hours: int) -> tuple[str, str]:
        token = new_token()
        now = datetime.now(UTC)
        expires = now + timedelta(hours=hours)
        self.execute(
            "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, expires.isoformat(), now.isoformat()),
        )
        return token, expires.isoformat()

    def create_signal(self, payload: dict[str, Any]) -> str:
        signal_id = make_id("sig")
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO signals (
                    id, source, local, category, description, dia_semana, hora_dia,
                    eventos_proximos, historico_ocorrencias_7d, iluminacao_ativa_pct,
                    iluminacao_fonte, densidade_pessoas, latitude, longitude, status,
                    raw_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?)
                """,
                (
                    signal_id,
                    payload["source"], payload["local"], payload["category"], payload["description"],
                    payload["dia_semana"], payload["hora_dia"], payload["eventos_proximos"],
                    payload["historico_ocorrencias_7d"], payload["iluminacao_ativa_pct"],
                    payload["iluminacao_fonte"], payload["densidade_pessoas"],
                    payload.get("latitude"), payload.get("longitude"),
                    json.dumps(payload, ensure_ascii=False), now, now,
                ),
            )
        return signal_id

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM signals WHERE id=?", (signal_id,))

    def create_operational_event(self, signal: dict[str, Any], insight: dict[str, Any]) -> str:
        event_id = make_id("op")
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO operational_events (
                    id, signal_id, title, summary, conversational_summary, local, category,
                    source, probability, criticality, requires_preventive_action,
                    recommended_action, indicators_json, drivers_json, model_version,
                    provider, status, latitude, longitude, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'pending_approval', ?, ?, ?, ?)
                """,
                (
                    event_id, signal["id"], insight["title"], insight["summary"],
                    insight["conversational_summary"], signal["local"], signal["category"],
                    signal["source"], insight["probability"], insight["criticality"],
                    int(insight["requires_preventive_action"]), insight["recommended_action"],
                    json.dumps(insight["indicators"], ensure_ascii=False),
                    json.dumps(insight["drivers"], ensure_ascii=False), insight["model_version"],
                    insight["provider"], signal.get("latitude"), signal.get("longitude"), now, now,
                ),
            )
            connection.execute(
                "UPDATE signals SET status='enriched', updated_at=? WHERE id=?", (now, signal["id"])
            )
        return event_id

    def get_operational_event(self, event_id: str) -> dict[str, Any] | None:
        event = self.fetch_one("SELECT * FROM operational_events WHERE id=?", (event_id,))
        if not event:
            return None
        event["dispatches"] = self.fetch_all(
            """
            SELECT d.*, g.code AS guard_code, g.name AS guard_name, g.specialization
            FROM dispatches d JOIN guards g ON g.id=d.guard_id
            WHERE d.operational_event_id=? ORDER BY d.sent_at, g.code
            """,
            (event_id,),
        )
        return event

    def list_events(
        self,
        *,
        status: str | None = None,
        criticality: str | None = None,
        local: str | None = None,
        source: str | None = None,
        category: str | None = None,
        query_text: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("status", status), ("criticality", criticality), ("source", source), ("category", category)
        ):
            if value:
                clauses.append(f"{column}=?")
                values.append(value)
        if local:
            clauses.append("lower(local) LIKE lower(?)")
            values.append(f"%{local}%")
        if query_text:
            clauses.append("(lower(title) LIKE lower(?) OR lower(summary) LIKE lower(?) OR lower(local) LIKE lower(?))")
            values.extend([f"%{query_text}%"] * 3)
        if date_from:
            clauses.append("created_at>=?")
            values.append(date_from)
        if date_to:
            clauses.append("created_at<=?")
            values.append(date_to)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total_row = self.fetch_one(f"SELECT count(*) AS total FROM operational_events{where}", values)
        rows = self.fetch_all(
            f"SELECT * FROM operational_events{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*values, limit, offset],
        )
        return rows, int(total_row["total"] if total_row else 0)

    def approve_event(self, event_id: str, user_id: str) -> bool:
        now = utc_now()
        return self.execute(
            """
            UPDATE operational_events
            SET status='approved', approved_by=?, approved_at=?, updated_at=?
            WHERE id=? AND status='pending_approval'
            """,
            (user_id, now, now, event_id),
        ) == 1

    def reject_event(self, event_id: str, user_id: str, reason: str) -> bool:
        now = utc_now()
        return self.execute(
            """
            UPDATE operational_events
            SET status='rejected', rejected_by=?, rejected_at=?, rejection_reason=?, updated_at=?
            WHERE id=? AND status='pending_approval'
            """,
            (user_id, now, reason, now, event_id),
        ) == 1

    def update_event_status(self, event_id: str, status: str) -> None:
        now = utc_now()
        completed = now if status == "completed" else None
        self.execute(
            "UPDATE operational_events SET status=?, completed_at=COALESCE(?, completed_at), updated_at=? WHERE id=?",
            (status, completed, now, event_id),
        )

    def list_guards(self, availability: str | None = None) -> list[dict[str, Any]]:
        if availability:
            return self.fetch_all(
                "SELECT * FROM guards WHERE availability=? ORDER BY code", (availability,)
            )
        return self.fetch_all("SELECT * FROM guards ORDER BY code")

    def get_guard_for_user(self, user_id: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM guards WHERE user_id=?", (user_id,))

    def create_dispatches(self, event_id: str, guard_ids: list[str]) -> list[dict[str, Any]]:
        now = utc_now()
        created: list[str] = []
        with self.transaction() as connection:
            for guard_id in dict.fromkeys(guard_ids):
                dispatch_id = make_id("dsp")
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO dispatches
                        (id, operational_event_id, guard_id, status, sent_at)
                    VALUES (?, ?, ?, 'sent', ?)
                    """,
                    (dispatch_id, event_id, guard_id, now),
                )
                if cursor.rowcount:
                    created.append(dispatch_id)
            status = "distributed" if created or connection.execute(
                "SELECT 1 FROM dispatches WHERE operational_event_id=? LIMIT 1", (event_id,)
            ).fetchone() else "unassigned"
            connection.execute(
                "UPDATE operational_events SET status=?, updated_at=? WHERE id=?",
                (status, now, event_id),
            )
        if not created:
            return []
        placeholders = ",".join("?" for _ in created)
        return self.fetch_all(
            f"""
            SELECT d.*, g.code AS guard_code, g.name AS guard_name, g.specialization
            FROM dispatches d JOIN guards g ON g.id=d.guard_id
            WHERE d.id IN ({placeholders}) ORDER BY g.code
            """,
            created,
        )

    def list_dispatches_for_guard(self, guard_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT d.*, e.title, e.summary, e.conversational_summary, e.local,
                   e.category, e.criticality, e.probability, e.recommended_action,
                   e.indicators_json, e.drivers_json, e.latitude, e.longitude,
                   e.created_at AS event_created_at
            FROM dispatches d JOIN operational_events e ON e.id=d.operational_event_id
            WHERE d.guard_id=? ORDER BY d.sent_at DESC
            """,
            (guard_id,),
        )

    def get_dispatch(self, dispatch_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """
            SELECT d.*, g.user_id, g.code AS guard_code, e.status AS event_status
            FROM dispatches d
            JOIN guards g ON g.id=d.guard_id
            JOIN operational_events e ON e.id=d.operational_event_id
            WHERE d.id=?
            """,
            (dispatch_id,),
        )

    def respond_to_dispatch(self, dispatch_id: str, action: str, note: str | None) -> bool:
        now = utc_now()
        if action == "acknowledge":
            return self.execute(
                """
                UPDATE dispatches SET status='acknowledged', acknowledged_at=?, note=COALESCE(?, note)
                WHERE id=? AND status='sent'
                """,
                (now, note, dispatch_id),
            ) == 1
        if action == "complete":
            return self.execute(
                """
                UPDATE dispatches SET status='completed', completed_at=?,
                    acknowledged_at=COALESCE(acknowledged_at, ?), note=COALESCE(?, note)
                WHERE id=? AND status IN ('sent', 'acknowledged')
                """,
                (now, now, note, dispatch_id),
            ) == 1
        return False

    def all_dispatches_completed(self, event_id: str) -> bool:
        row = self.fetch_one(
            """
            SELECT count(*) AS total,
                   sum(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed
            FROM dispatches WHERE operational_event_id=?
            """,
            (event_id,),
        )
        return bool(row and row["total"] and row["total"] == row["completed"])

    def append_journal(self, event: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT OR IGNORE INTO event_journal
                (event_id, aggregate_id, event_type, actor_id, payload_json, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"], event["aggregate_id"], event["event_type"],
                event.get("actor_id"), json.dumps(event.get("payload", {}), ensure_ascii=False),
                event["occurred_at"],
            ),
        )

    def timeline(self, aggregate_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT * FROM event_journal WHERE aggregate_id=? ORDER BY sequence", (aggregate_id,)
        )

    def dashboard_summary(self) -> dict[str, Any]:
        totals = self.fetch_one(
            """
            SELECT count(*) AS total,
                sum(CASE WHEN status='pending_approval' THEN 1 ELSE 0 END) AS pending,
                sum(CASE WHEN status='distributed' THEN 1 ELSE 0 END) AS distributed,
                sum(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                avg(probability) AS average_probability
            FROM operational_events
            """
        ) or {}
        by_criticality = self.fetch_all(
            "SELECT criticality, count(*) AS count FROM operational_events GROUP BY criticality"
        )
        by_status = self.fetch_all(
            "SELECT status, count(*) AS count FROM operational_events GROUP BY status"
        )
        acknowledgements = self.fetch_one(
            """
            SELECT count(*) AS sent,
                sum(CASE WHEN status IN ('acknowledged','completed') THEN 1 ELSE 0 END) AS acknowledged
            FROM dispatches
            """
        ) or {"sent": 0, "acknowledged": 0}
        sent = acknowledgements.get("sent") or 0
        acknowledged = acknowledgements.get("acknowledged") or 0
        return {
            "total_events": totals.get("total") or 0,
            "pending_approval": totals.get("pending") or 0,
            "distributed": totals.get("distributed") or 0,
            "completed": totals.get("completed") or 0,
            "average_probability": round(totals.get("average_probability") or 0, 3),
            "acknowledgement_rate": round(acknowledged / sent, 3) if sent else 0,
            "by_criticality": {row["criticality"]: row["count"] for row in by_criticality},
            "by_status": {row["status"]: row["count"] for row in by_status},
            "reports_generated": self.count_reports(),
        }

    def report_evidence(self, period_start: str, period_end: str) -> dict[str, Any]:
        events = self.fetch_all(
            """
            SELECT id, title, summary, local, category, source, probability, criticality,
                   status, recommended_action, created_at
            FROM operational_events
            WHERE created_at>=? AND created_at<=?
            ORDER BY probability DESC, created_at DESC
            """,
            (period_start, period_end),
        )
        by_local = self.fetch_all(
            """
            SELECT local, count(*) AS count, avg(probability) AS average_probability,
                   sum(CASE WHEN criticality='high' THEN 1 ELSE 0 END) AS high_count
            FROM operational_events
            WHERE created_at>=? AND created_at<=?
            GROUP BY local ORDER BY count DESC, average_probability DESC LIMIT 10
            """,
            (period_start, period_end),
        )
        by_category = self.fetch_all(
            """
            SELECT category, count(*) AS count, avg(probability) AS average_probability
            FROM operational_events
            WHERE created_at>=? AND created_at<=?
            GROUP BY category ORDER BY count DESC, average_probability DESC LIMIT 10
            """,
            (period_start, period_end),
        )
        by_criticality = self.fetch_all(
            """
            SELECT criticality, count(*) AS count
            FROM operational_events WHERE created_at>=? AND created_at<=?
            GROUP BY criticality
            """,
            (period_start, period_end),
        )
        by_status = self.fetch_all(
            """
            SELECT status, count(*) AS count
            FROM operational_events WHERE created_at>=? AND created_at<=?
            GROUP BY status
            """,
            (period_start, period_end),
        )
        dispatches = self.fetch_one(
            """
            SELECT count(d.id) AS sent,
                   sum(CASE WHEN d.status IN ('acknowledged','completed') THEN 1 ELSE 0 END) AS acknowledged,
                   sum(CASE WHEN d.status='completed' THEN 1 ELSE 0 END) AS completed
            FROM dispatches d
            JOIN operational_events e ON e.id=d.operational_event_id
            WHERE e.created_at>=? AND e.created_at<=?
            """,
            (period_start, period_end),
        ) or {"sent": 0, "acknowledged": 0, "completed": 0}
        return {
            "events": events,
            "by_local": by_local,
            "by_category": by_category,
            "by_criticality": {
                row["criticality"]: row["count"] for row in by_criticality
            },
            "by_status": {row["status"]: row["count"] for row in by_status},
            "dispatches": dispatches,
        }

    def create_report(self, report: dict[str, Any]) -> str:
        report_id = make_id("rpt")
        now = utc_now()
        self.execute(
            """
            INSERT INTO reports (
                id, title, report_type, generation_reason, period_start, period_end,
                status, executive_summary, statistics_json, findings_json,
                solution_options_json, procurement_draft_json, evidence_event_ids_json,
                provider, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'generated', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id, report["title"], report["report_type"],
                report["generation_reason"], report["period_start"], report["period_end"],
                report["executive_summary"],
                json.dumps(report["statistics"], ensure_ascii=False),
                json.dumps(report["findings"], ensure_ascii=False),
                json.dumps(report["solution_options"], ensure_ascii=False),
                json.dumps(report["procurement_draft"], ensure_ascii=False),
                json.dumps(report["evidence_event_ids"], ensure_ascii=False),
                report["provider"], report.get("created_by"), now,
            ),
        )
        return report_id

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM reports WHERE id=?", (report_id,))

    def list_reports(self, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        total = self.count_reports()
        items = self.fetch_all(
            """
            SELECT id, title, report_type, generation_reason, period_start, period_end,
                   status, executive_summary, statistics_json, solution_options_json,
                   provider, created_at
            FROM reports ORDER BY created_at DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return items, total

    def count_reports(self) -> int:
        row = self.fetch_one("SELECT count(*) AS count FROM reports")
        return int(row["count"] if row else 0)

    def search_documents(self) -> list[dict[str, Any]]:
        documents = self.fetch_all("SELECT * FROM knowledge_documents ORDER BY updated_at DESC")
        events = self.fetch_all(
            """
            SELECT id, title, summary, conversational_summary, local, category, source,
                   probability, criticality, status, recommended_action, indicators_json,
                   drivers_json, created_at
            FROM operational_events ORDER BY created_at DESC LIMIT 500
            """
        )
        for event in events:
            documents.append(
                {
                    "id": event["id"],
                    "title": event["title"],
                    "content": (
                        f"{event['summary']} {event['conversational_summary']} Local: {event['local']}. "
                        f"Criticidade: {event['criticality']}. Probabilidade: {event['probability']:.0%}. "
                        f"Status: {event['status']}. Recomendação: {event['recommended_action']}."
                    ),
                    "source": "evento_operacional",
                    "metadata": {
                        "status": event["status"], "criticality": event["criticality"],
                        "local": event["local"], "category": event["category"],
                        "created_at": event["created_at"],
                    },
                    "updated_at": event["created_at"],
                }
            )
        reports = self.fetch_all(
            """
            SELECT id, title, executive_summary, statistics_json, findings_json,
                   solution_options_json, period_start, period_end, created_at
            FROM reports ORDER BY created_at DESC LIMIT 100
            """
        )
        for report in reports:
            findings_text = " ".join(
                f"{item.get('title', '')}: {item.get('interpretation', '')}"
                for item in report.get("findings", [])
            )
            options_text = " ".join(
                f"{item.get('title', '')}: {item.get('description', '')}"
                for item in report.get("solution_options", [])
            )
            documents.append(
                {
                    "id": report["id"],
                    "title": report["title"],
                    "content": (
                        f"{report['executive_summary']} {findings_text} Alternativas: {options_text}"
                    ),
                    "source": "relatorio_operacional",
                    "metadata": {
                        "period_start": report["period_start"],
                        "period_end": report["period_end"],
                        "created_at": report["created_at"],
                    },
                    "updated_at": report["created_at"],
                }
            )
        return documents

    def count_signals(self) -> int:
        row = self.fetch_one("SELECT count(*) AS count FROM signals")
        return int(row["count"] if row else 0)
