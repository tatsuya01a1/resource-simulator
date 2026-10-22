"""Versioned public whitelist. DB version and public schema version are independent."""
from datetime import date, datetime, time, timedelta, timezone
import math

PUBLIC_SCHEMA_VERSION = 2
MODEL_KEYS = ("initial_resource", "initial_parameter", "parameter_min", "parameter_max",
              "coefficient", "interval_seconds")
VALUES = ("resource", "requested_delta", "actual_delta")


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid public number")
    return value


def ordinal(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("invalid ordinal")
    return value


def public_state(value):
    version = value["schema_version"]
    if type(version) is not int or version not in (1, PUBLIC_SCHEMA_VERSION):
        raise ValueError("unknown public schema")
    result = {key: value[key] for key in (
        "schema_version", "run_id", "run_generation", "step", "calculated_at")}
    ordinal(result["run_generation"], 1)
    ordinal(result["step"])
    if not isinstance(result["run_id"], str) or len(result["run_id"]) != 36:
        raise ValueError("invalid run id")
    timestamp(result["calculated_at"])
    result["model"] = {key: number(value["model"][key]) for key in MODEL_KEYS}
    if "initial_beta" in value["model"]:
        result["model"]["initial_beta"] = number(value["model"]["initial_beta"])
    if result["model"]["interval_seconds"] != 1:
        raise ValueError("invalid model")
    if version == 1:
        result.update({key: number(value[key]) for key in (*VALUES, "parameter")})
        if result["resource"] < 0:
            raise ValueError("invalid resource")
        return result
    source = value["resources"]
    if not isinstance(source, list) or len(source) != 2 or [r["id"] for r in source] != ["resource", "beta"]:
        raise ValueError("expected Alpha and Beta in one snapshot")
    resources = []
    for resource, name in zip(source, ("Alpha", "Beta")):
        item = {"id": resource["id"], "name": name, "available": resource["available"]}
        if type(item["available"]) is not bool or (name == "Alpha" and not item["available"]):
            raise ValueError("invalid availability")
        if item["available"]:
            item.update({key: number(resource[key]) for key in VALUES})
            if item["resource"] < 0:
                raise ValueError("invalid resource")
            item["start_step"] = ordinal(resource["start_step"])
            if item["start_step"] > result["step"]:
                raise ValueError("invalid resource start")
            if name == "Alpha":
                item["parameter"] = number(resource["parameter"])
            else:
                item["schedule"] = public_schedule(resource["schedule"], result["calculated_at"])
        resources.append(item)
    result["resources"] = resources
    # Transitional Alpha aliases let an already-open old Web continue during API rollout.
    result.update({key: resources[0][key] for key in (*VALUES, "parameter")})
    return result


def public_schedule(schedule, calculated_at):
    result = {key: schedule[key] for key in (
        "target_date", "morning_value", "evening_value", "night_multiplier",
        "night_start", "valid_until", "mode", "multiplier", "acquisition")}
    for key in ("morning_value", "evening_value", "night_multiplier", "multiplier"):
        if result[key] is not None and number(result[key]) < 0:
            raise ValueError("invalid baseline")
    jst = timezone(timedelta(hours=9))
    local = timestamp(calculated_at).astimezone(jst)
    day = local.date() - timedelta(days=1 if local.hour < 4 else 0)
    mode = "day" if 4 <= local.hour < 18 else "night"
    if date.fromisoformat(result["target_date"]) != day or result["mode"] != mode:
        raise ValueError("schedule does not match snapshot")
    complete = result["morning_value"] is not None and result["evening_value"] is not None
    if complete:
        start = datetime.combine(day, time(18), jst).astimezone(timezone.utc)
        until = datetime.combine(day + timedelta(days=1), time(4), jst).astimezone(timezone.utc)
        if (result["night_multiplier"] != abs(result["evening_value"] - result["morning_value"])
                or timestamp(result["night_start"]) != start or timestamp(result["valid_until"]) != until):
            raise ValueError("invalid night validity")
    elif any(result[key] is not None for key in ("night_multiplier", "night_start", "valid_until")):
        raise ValueError("night multiplier requires both baselines")
    expected = result["night_multiplier"] if complete and mode == "night" else 5
    acquisition = "acquired" if complete else "missing" if mode == "night" else "pending"
    if result["multiplier"] != expected or result["acquisition"] != acquisition:
        raise ValueError("invalid night multiplier")
    return result


def upgrade_public(value):
    result = public_state(value)
    if result["schema_version"] == 1:
        result.update(schema_version=PUBLIC_SCHEMA_VERSION, resources=[
            {"id": "resource", "name": "Alpha", "available": True, "start_step": 0,
             **{key: result[key] for key in (*VALUES, "parameter")}},
            {"id": "beta", "name": "Beta", "available": False}])
    return result


def should_replace(candidate, current):
    """ETag protects the whole pair. Fence generations, UUIDs, steps and schema downgrades."""
    if candidate["schema_version"] < current["schema_version"]:
        return False
    if candidate["run_generation"] != current["run_generation"]:
        return candidate["run_generation"] > current["run_generation"]
    return candidate["run_id"] == current["run_id"] and candidate["step"] > current["step"]
