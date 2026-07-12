"""SQLModel database models — the shared Alert/Case connective tissue.

These tables are intentionally provider-aware (every balance and alert is tagged
with a `provider`) so that the strict provider-data-separation constraint in
Section 2 of the brief is enforced at the storage layer, not only in the UI.
"""
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, SQLModel, JSON, Column
from sqlmodel import Relationship
from sqlalchemy import Index
from sqlalchemy.orm import Mapped, relationship as sa_relationship


# ---------------------------------------------------------------------------
# Identity & access
# ---------------------------------------------------------------------------

class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    display_name: str
    role: str  # "agent" | "ops" | "risk" | "provider" | "management"
    provider: Optional[str] = None  # for role="provider", which provider they represent
    area: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Agents & provider balances
# ---------------------------------------------------------------------------

class Agent(SQLModel, table=True):
    __tablename__ = "agents"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)  # synthetic ID, e.g. "AGT-DHK-014"
    display_name: str
    area: str
    contact_name: str = "Outlet contact"
    contact_phone: str = "+8801700000000"  # synthetic demo contact
    field_officer_name: str = "Assigned Field Officer"
    field_officer_phone: str = "+8801800000000"  # synthetic demo contact
    area_manager_name: str = "Area Operations Manager"
    area_manager_phone: str = "+8801900000000"  # synthetic demo contact
    physical_cash: float = 100_000.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProviderBalance(SQLModel, table=True):
    __tablename__ = "provider_balances"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(foreign_key="agents.id", index=True)
    provider: str = Field(index=True)  # "bkash" | "nagad" | "rocket"
    balance: float
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(foreign_key="agents.id", index=True)
    provider: str = Field(index=True)
    tx_type: str  # "cash_in" | "cash_out"
    amount: float
    counterparty_id: str  # synthetic customer ID
    area: str
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    status: str = "success"
    flagged: bool = False
    ground_truth_anomaly: bool = False  # injected scenario labels

    __table_args__ = (
        # Dominant access pattern: "recent transactions for this (agent, provider)",
        # used by every rule head and the Isolation Forest fit.
        Index("ix_tx_agent_provider_ts", "agent_id", "provider", "ts"),
    )


class BalanceHistory(SQLModel, table=True):
    """Time-series snapshot of provider balance for sparklines + ML training."""

    __tablename__ = "balance_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(foreign_key="agents.id", index=True)
    provider: str = Field(index=True)
    balance: float
    physical_cash: float
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)

    __table_args__ = (
        # Sparklines, LGBM training, and rate projection all filter on
        # (agent, provider, ts DESC) — composite index lets the planner
        # use a single index range scan instead of three.
        Index("ix_bh_agent_provider_ts", "agent_id", "provider", "ts"),
    )


# ---------------------------------------------------------------------------
# Forecast & anomaly outputs
# ---------------------------------------------------------------------------

class ForecastSnapshot(SQLModel, table=True):
    __tablename__ = "forecast_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(foreign_key="agents.id", index=True)
    provider: str = Field(index=True)
    hours_to_shortage: Optional[float] = None  # None = no projection
    confidence: float
    summary: str = ""  # curated one-line basis for UI / alert copy
    reasons_json: str = "[]"  # JSON-encoded list[str] (technical/audit trail)
    method: str  # "rate_projection" | "rate_projection+lgbm"
    feature_importance_json: str = "{}"
    data_quality: float = 1.0  # 0..1, 1 = healthy
    burn_rate_per_min: float = 0.0  # stored with ETA so read paths stay internally consistent
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)

    __table_args__ = (
        # Dashboard batch loader fetches the most recent forecast per
        # (agent, provider) — composite keeps the LIMIT cheap.
        Index("ix_fc_agent_provider_ts", "agent_id", "provider", "ts"),
    )


class AnomalyEvent(SQLModel, table=True):
    __tablename__ = "anomaly_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(foreign_key="agents.id", index=True)
    provider: str = Field(index=True)
    rule: str  # e.g. "repeated_amount" | "velocity_spike" | "structuring"
    confidence: float
    count: int = 1
    amount: Optional[float] = None
    window_minutes: int = 0
    reasons_json: str = "[]"
    iforest_score: Optional[float] = None
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)

    __table_args__ = (
        # Metrics page groups anomalies per provider within a time window;
        # composite keeps the WHERE+ORDER BY cheap.
        Index("ix_ae_agent_provider_ts", "agent_id", "provider", "ts"),
    )


# ---------------------------------------------------------------------------
# Alerts (the central entity)
# ---------------------------------------------------------------------------

class Alert(SQLModel, table=True):
    __tablename__ = "alerts"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(foreign_key="agents.id", index=True)
    provider: Optional[str] = Field(default=None, index=True)
    severity: str = Field(index=True)  # "normal" | "low" | "high" | "critical"
    priority_score: int = 0  # 0..100
    title: str
    summary: str
    reasons_json: str = "[]"
    evidence_json: str = "[]"
    confidence: float
    recommended_actions_json: str = "[]"  # ranked actions from Module 4
    fused_explanation: str = ""
    owner_role: str  # "ops" | "risk" | "provider" | "management"
    owner_label: str  # human-readable
    initial_owner: str  # role that triggered ownership (liquidity / anomaly / data-quality)
    status: str = Field(default="open", index=True)  # open | assigned | ack | review | resolved | escalated | closed
    # Legacy nullable column retained for additive SQLite compatibility.
    # Predictions never populate it; evaluation truth lives in ScenarioEvent.
    ground_truth_severity: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution_reason: Optional[str] = None

    cases: List["Case"] = Relationship(back_populates="alert")


class Case(SQLModel, table=True):
    """Module 6 state-machine entity. One per Alert that reaches Assigned+."""

    __tablename__ = "cases"

    id: Optional[int] = Field(default=None, primary_key=True)
    alert_id: int = Field(foreign_key="alerts.id", index=True)
    state: str = Field(default="assigned", index=True)
    owner_role: str
    owner_label: str
    assigned_to: str = "Operations queue"
    assigned_contact_type: str = "operations"
    resolution_code: Optional[str] = None
    resolution_summary: Optional[str] = None
    closed_at: Optional[datetime] = None
    risk_recommendation: Optional[str] = None
    risk_recommendation_note: Optional[str] = None
    risk_recommended_by: Optional[str] = None
    risk_recommended_at: Optional[datetime] = None
    notes_json: str = "[]"  # list[{ts, role, user, text}]
    audit_json: str = "[]"  # list[{ts, from_state, to_state, actor, reason}]
    explanation_json: str = "{}"  # validated AI/fallback explanation payload
    explanation_provider: str = "fallback"  # gemini | grok | fallback
    explanation_model: str = "deterministic-evidence-v1"
    explanation_status: str = "pending"  # pending | generated | fallback
    explanation_error: Optional[str] = None
    explanation_generated_at: Optional[datetime] = None
    explanation_language: str = "en"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    alert: Optional["Alert"] = Relationship(back_populates="cases")


class CashSupportRequest(SQLModel, table=True):
    """Agent-to-provider liquidity support request and provider notification."""

    __tablename__ = "cash_support_requests"

    id: Optional[int] = Field(default=None, primary_key=True)
    alert_id: int = Field(foreign_key="alerts.id", index=True)
    agent_id: int = Field(foreign_key="agents.id", index=True)
    provider: str = Field(index=True)
    requested_by: str
    amount: Optional[float] = None
    forecast_balance: Optional[float] = None
    forecast_burn_rate_per_min: Optional[float] = None
    coverage_hours: float = 8.0
    target_balance: Optional[float] = None
    calculation: str = ""
    applied_amount: float = 0.0
    balance_after: Optional[float] = None
    applied_at: Optional[datetime] = None
    note: str = ""
    status: str = Field(default="requested", index=True)  # requested|acknowledged|approved|rejected|fulfilled
    provider_note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    acknowledged_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class StakeholderNotification(SQLModel, table=True):
    """Durable, role-scoped inbox event emitted by case routing/actions."""

    __tablename__ = "stakeholder_notifications"

    id: Optional[int] = Field(default=None, primary_key=True)
    alert_id: int = Field(foreign_key="alerts.id", index=True)
    case_id: int = Field(foreign_key="cases.id", index=True)
    recipient_role: str = Field(index=True)
    recipient_agent_id: Optional[int] = Field(default=None, index=True)
    recipient_provider: Optional[str] = Field(default=None, index=True)
    recipient_area: Optional[str] = Field(default=None, index=True)
    event: str
    title: str
    message: str
    actor: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    read_at: Optional[datetime] = None


class ExplanationCall(SQLModel, table=True):
    """Secret-free server audit of explanation vendor requests and responses."""

    __tablename__ = "explanation_calls"

    id: Optional[int] = Field(default=None, primary_key=True)
    alert_id: int = Field(foreign_key="alerts.id", index=True)
    case_id: int = Field(foreign_key="cases.id", index=True)
    provider: str
    model: str
    language: str
    endpoint: str
    request_json: str = "{}"
    response_json: str = "{}"
    status: str  # generated | fallback
    error: Optional[str] = None
    latency_ms: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# Data-quality (Module 8) & scenarios (debug panel)
# ---------------------------------------------------------------------------

class DataQualityEvent(SQLModel, table=True):
    __tablename__ = "data_quality_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True)
    issue: str  # "delay" | "outage" | "inconsistent"
    note: str = ""
    started_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None


class OperationalContextEvent(SQLModel, table=True):
    """Observed business context available to the detector as an input feature.

    This is deliberately separate from ``ScenarioEvent``. Scenario rows are
    evaluation labels; context rows represent information an operator could
    genuinely know in advance, such as a salary day or local campaign.
    """

    __tablename__ = "operational_context_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(foreign_key="agents.id", index=True)
    provider: str = Field(index=True)
    kind: str  # salary_day | demand_surge | campaign | local_event
    note: str = ""
    source: str = "operations-calendar"
    started_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    ends_at: datetime = Field(index=True)


class ScenarioEvent(SQLModel, table=True):
    """Log of what-if scenarios injected via the debug panel — used as ground truth
    for anomaly precision/recall and Module 4 priority classification."""

    __tablename__ = "scenario_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(foreign_key="agents.id", index=True)
    provider: str = Field(index=True)
    kind: str  # "bkash_surge" | "repeated_amount" | "structuring" | "rocket_delay" | "salary_day"
    intended_severity: str = "normal"  # for Module 4 priority-classification metric
    is_anomaly_ground_truth: bool = False
    note: str = ""
    duration_minutes: int = 5
    injected_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Metrics (Module 9) — running tallies updated each tick
# ---------------------------------------------------------------------------

class MetricTick(SQLModel, table=True):
    __tablename__ = "metric_ticks"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    value: float
    extra_json: str = "{}"
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# API response shapes (non-table)
# ---------------------------------------------------------------------------

class AgentSnapshot(SQLModel):
    """Composite read for Module 1 — returned in one shot so the dashboard
    doesn't have to assemble provider balances from N queries."""
    agent_id: int
    agent_code: str
    display_name: str
    area: str
    physical_cash: float
    overall_score: int  # 0..100
    overall_reason: str
    providers: List["ProviderSnapshot"]


class ProviderSnapshot(SQLModel):
    provider: str
    balance: float
    health: str  # 🟢 | 🟡 | 🟠 | 🔴 (rendered as a label here)
    burn_rate_per_min: float
    hours_to_shortage: Optional[float]
    forecast_confidence: float
    forecast_summary: str = ""  # curated one-line basis for the UI / alert copy
    forecast_reasons: List[str]
    data_quality: float
    history: List[float]  # last N balances for sparkline
