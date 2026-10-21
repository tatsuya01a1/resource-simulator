"""Local lifecycle. Scheduling uses monotonic time, observations use UTC."""

from datetime import datetime, timezone
import logging
import random
import time
from threading import Event

from .engine import advance
from .persistence import Session, Store


def run(store: Store, session: Session, stop: Event, *, steps: int | None = None,
        sample=random.random, now=lambda: datetime.now(timezone.utc),
        monotonic=time.monotonic, wait=None) -> Session:
    wait = wait or stop.wait
    count = 0
    deadline = monotonic() + 1.0
    while steps is None or count < steps:
        if wait(max(0.0, deadline - monotonic())) or stop.is_set():
            break
        state = advance(session.state, session.config, sample(), now())
        session = store.commit_step(session, state)
        count += 1
        # Keep the planned cadence while work fits in the interval. If the next
        # deadline was missed (also after sleep), discard missed slots and wait
        # a full interval instead of executing catch-up steps.
        deadline += 1.0
        finished_at = monotonic()
        if finished_at >= deadline:
            deadline = finished_at + 1.0
    logging.info("Worker stopped run=%s step=%s", session.run_id, session.state.step)
    return session
