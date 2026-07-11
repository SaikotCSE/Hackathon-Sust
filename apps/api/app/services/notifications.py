"""Durable stakeholder inbox events for case coordination."""
from typing import Optional

from sqlmodel import Session, select

from ..models.database import Agent, Alert, Case, StakeholderNotification


def add_notification(
    session: Session, *, alert: Alert, case: Case, recipient_role: str,
    event: str, message: str, actor: str,
    recipient_agent_id: Optional[int] = None,
) -> StakeholderNotification:
    agent = session.get(Agent, alert.agent_id)
    row = StakeholderNotification(
        alert_id=alert.id, case_id=case.id, recipient_role=recipient_role,
        recipient_agent_id=recipient_agent_id,
        recipient_provider=alert.provider if recipient_role == "provider" else None,
        recipient_area=agent.area if agent and recipient_role == "ops" else None,
        event=event, title=f"{event.replace('_', ' ').title()} · {alert.provider or 'case'}",
        message=message, actor=actor,
    )
    session.add(row)
    return row


def backfill_open_case_assignments(session: Session) -> int:
    """Create one assignment inbox event for cases created before inbox support."""
    existing_case_ids = set(session.exec(
        select(StakeholderNotification.case_id)
        .where(StakeholderNotification.event == "case_assigned")
    ).all())
    existing_routed_ids = set(session.exec(
        select(StakeholderNotification.case_id)
        .where(StakeholderNotification.event == "case_routed")
    ).all())
    count = 0
    for case in session.exec(select(Case)).all():
        if case.state in ("resolved", "closed"):
            continue
        alert = session.get(Alert, case.alert_id)
        if alert is None:
            continue
        if case.id not in existing_case_ids:
            add_notification(
                session, alert=alert, case=case, recipient_role=case.owner_role,
                recipient_agent_id=alert.agent_id if case.owner_role == "agent" else None,
                event="case_assigned",
                message=f"Existing case #{case.id} is assigned to {case.owner_label}; open it to review the recommended action.",
                actor="migration",
            )
            count += 1
        if case.owner_role != "agent" and case.id not in existing_routed_ids:
            add_notification(
                session, alert=alert, case=case, recipient_role="agent",
                recipient_agent_id=alert.agent_id, event="case_routed",
                message=f"Existing case #{case.id} is currently owned by {case.owner_label}.",
                actor="migration",
            )
            count += 1
    if count:
        session.commit()
    return count
