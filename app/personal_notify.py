"""Read-only formatter for the personal ticket. Never mutates lots or cash."""

from __future__ import annotations

import os
from typing import Any

NOTIFY_URL_ENV = "PERSONAL_NOTIFY_WEBHOOK"


def format_ticket_notice(ticket: dict[str, Any]) -> str:
    action = ticket.get("action")
    symbol = ticket.get("symbol") or "-"
    lots = ticket.get("lots")
    cash = ticket.get("cash")
    return (
        f"个人凭证 action={action} symbol={symbol} lots={lots} cash={cash}\n"
        "非正式有效 不自动下单"
    )


def notify_credentials_present() -> bool:
    return bool(str(os.getenv(NOTIFY_URL_ENV) or "").strip())


def send_ticket_notice(
    ticket: dict[str, Any],
    *,
    transport: Any = None,
) -> dict[str, Any]:
    text = format_ticket_notice(ticket)
    if transport is None and not notify_credentials_present():
        return {"sent": False, "reason": "missing_credentials", "text": text}
    if transport is not None:
        transport(text)
        return {"sent": True, "reason": None, "text": text}
    return {"sent": False, "reason": "missing_credentials", "text": text}
