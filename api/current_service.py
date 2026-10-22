"""One noncached, whitelisted read of a private Table entity."""

from datetime import datetime, timezone
import json

try:
    from .current_contract import public_state, timestamp
except ImportError:
    from current_contract import public_state, timestamp


def read_current(client, partition="resource-simulator", now=lambda: datetime.now(timezone.utc)):
    entity = client.get_entity(partition, "current")
    result = public_state(json.loads(entity["payload"]))
    # Table service Timestamp is the actual commit time; PC clock is not authoritative.
    committed_at = getattr(entity, "metadata", {}).get("timestamp")
    published_value = committed_at.isoformat() if committed_at else entity["published_at"]
    published = timestamp(published_value)
    server_time = now()
    age = max((server_time - published).total_seconds(),
              (server_time - timestamp(result["calculated_at"])).total_seconds(), 0)
    result.update(published_at=published_value, api_time=server_time.isoformat(),
                  simulation_elapsed_seconds=result["step"], stale=age >= 10,
                  stale_after_seconds=10, history_available=False)
    return result
