from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select, update

from ..db import get_conn, outbox_events

Publish = Callable[[str, dict[str, Any]], None]


def process_once(publish: Publish | None = None, limit: int = 50) -> int:
    """Mark pending outbox events as processed.

    The POC publisher is intentionally a no-op log hook. A later deployment can
    replace it with an HTTP call, cloud event bus, or notification service
    without changing the transactional write path that created the event.
    """
    processed = 0
    with get_conn() as conn:
        rows = conn.execute(
            select(outbox_events)
            .where(outbox_events.c.status == "PENDING")
            .limit(limit)
        ).fetchall()
        for row in rows:
            if publish:
                publish(row.topic, dict(row.payload_json or {}))
            else:
                print(f"outbox: {row.topic} {row.id}", flush=True)
            conn.execute(
                update(outbox_events)
                .where(outbox_events.c.id == row.id)
                .values(status="PROCESSED", processed_at=datetime.now(timezone.utc))
            )
            processed += 1
        conn.commit()
    return processed


if __name__ == "__main__":
    print(f"processed={process_once()}")
