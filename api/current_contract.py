"""Public whitelist shared by the publisher and read service; no credentials."""

from datetime import datetime, timezone
import math


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def public_state(value):
    result = {key: value[key] for key in (
        "schema_version", "run_id", "run_generation", "step", "resource", "parameter",
        "requested_delta", "actual_delta", "calculated_at", "model")}
    if result["schema_version"] != 1:
        raise ValueError("unknown public schema")
    for key in ("run_generation", "step"):
        if type(result[key]) is not int or result[key] < (1 if key == "run_generation" else 0):
            raise ValueError("invalid ordinal")
    if not isinstance(result["run_id"], str) or len(result["run_id"]) != 36:
        raise ValueError("invalid run id")
    timestamp(result["calculated_at"])
    model_keys = ("initial_resource", "initial_parameter", "parameter_min", "parameter_max",
                  "coefficient", "interval_seconds")
    result["model"] = {key: result["model"][key] for key in model_keys}
    for number in [result[key] for key in ("resource", "parameter", "requested_delta", "actual_delta")] + list(result["model"].values()):
        if type(number) not in (int, float) or not math.isfinite(number):
            raise ValueError("invalid public number")
    if result["resource"] < 0 or result["model"]["interval_seconds"] != 1:
        raise ValueError("invalid model")
    return result


def should_replace(candidate, current):
    """Generations fence old runs. An equal generation must retain its UUID."""
    if candidate["run_generation"] != current["run_generation"]:
        return candidate["run_generation"] > current["run_generation"]
    return candidate["run_id"] == current["run_id"] and candidate["step"] > current["step"]
