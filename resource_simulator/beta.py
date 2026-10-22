"""Beta follows Alpha's actual delta; boundaries use observed JST wall time.

A crossing is observable only between consecutive committed states in one Worker,
with a gap <= 2.5 seconds. Restart, sleep and missed boundaries never synthesize
an observation. All persisted instants remain UTC.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from .config import ValidationError, finite
from .engine import State, stamp, utc

JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
MAX_OBSERVATION_GAP = 2.5


def boundary(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour), JST).astimezone(timezone.utc)


@dataclass(frozen=True)
class Schedule:
    target_date: str
    morning_value: float | None = None
    evening_value: float | None = None
    night_multiplier: float | None = None
    night_start: str | None = None
    valid_until: str | None = None
    mode: str = "day"
    multiplier: float = 5.0
    acquisition: str = "pending"

    def __post_init__(self):
        day = date.fromisoformat(self.target_date)
        for key in ("morning_value", "evening_value", "night_multiplier", "multiplier"):
            value = getattr(self, key)
            if value is not None and finite(value, key) < 0:
                raise ValidationError("negative Beta schedule value")
        complete = self.morning_value is not None and self.evening_value is not None
        if complete:
            if (self.night_multiplier != abs(self.evening_value - self.morning_value)
                    or self.night_start != stamp(boundary(day, 18))
                    or self.valid_until != stamp(boundary(day + timedelta(days=1), 4))):
                raise ValidationError("invalid night baseline or validity")
        elif any(x is not None for x in (self.night_multiplier, self.night_start, self.valid_until)):
            raise ValidationError("night multiplier requires both baselines")
        expected = self.night_multiplier if self.mode == "night" and complete else 5.0
        status = "acquired" if complete else "missing" if self.mode == "night" else "pending"
        if self.mode not in ("day", "night") or self.multiplier != expected or self.acquisition != status:
            raise ValidationError("inconsistent Beta mode")


def schedule_at(previous: Schedule | None, before: datetime, at: datetime,
                beta_value: float, *, continuous: bool) -> Schedule:
    at, before = utc(at), utc(before)
    if at < before:
        raise ValidationError("clock moved backwards")
    local = at.astimezone(JST)
    day = local.date() - timedelta(days=1 if local.hour < 4 else 0)
    morning, evening = None, None
    if previous is not None and previous.target_date == day.isoformat():
        morning, evening = previous.morning_value, previous.evening_value
    observed = continuous and 0 <= (at - before).total_seconds() <= MAX_OBSERVATION_GAP
    if observed:
        if before < boundary(day, 4) <= at:
            morning = beta_value
        if before < boundary(day, 18) <= at:
            evening = beta_value
    mode = "day" if 4 <= local.hour < 18 else "night"
    complete = morning is not None and evening is not None
    x = finite(abs(evening - morning), "night multiplier") if complete else None
    return Schedule(day.isoformat(), morning, evening, x,
                    stamp(boundary(day, 18)) if complete else None,
                    stamp(boundary(day + timedelta(days=1), 4)) if complete else None,
                    mode, x if mode == "night" and complete else 5.0,
                    "acquired" if complete else "missing" if mode == "night" else "pending")


@dataclass(frozen=True)
class BetaState:
    step: int
    resource: float
    requested_delta: float
    actual_delta: float
    calculated_at: datetime
    schedule: Schedule

    def __post_init__(self):
        if type(self.step) is not int or not 0 <= self.step < 2**63:
            raise ValidationError("invalid Beta step")
        for key in ("resource", "requested_delta", "actual_delta"):
            object.__setattr__(self, key, finite(getattr(self, key), key))
        if self.resource < 0:
            raise ValidationError("negative Beta")
        object.__setattr__(self, "calculated_at", utc(self.calculated_at))
        local = self.calculated_at.astimezone(JST)
        day = local.date() - timedelta(days=1 if local.hour < 4 else 0)
        if (self.schedule.target_date != day.isoformat()
                or self.schedule.mode != ("day" if 4 <= local.hour < 18 else "night")):
            raise ValidationError("Beta schedule does not match observation")


def initial_beta(value: float, alpha: State) -> BetaState:
    return BetaState(alpha.step, value, 0, 0, alpha.calculated_at,
                     schedule_at(None, alpha.calculated_at, alpha.calculated_at, value, continuous=False))


def advance_beta(previous: BetaState, alpha: State, *, continuous: bool) -> BetaState:
    if alpha.step != previous.step + 1:
        raise ValidationError("Beta and Alpha steps disagree")
    schedule = schedule_at(previous.schedule, previous.calculated_at, alpha.calculated_at,
                           previous.resource, continuous=continuous)
    requested = finite(schedule.multiplier * alpha.actual_delta, "Beta requested delta")
    value = max(0.0, finite(previous.resource + requested, "Beta before lower bound"))
    return BetaState(alpha.step, value, requested, value - previous.resource, alpha.calculated_at, schedule)
