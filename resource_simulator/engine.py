"""Pure single-step engine. No database, clocks, or global random source."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math

from .config import ModelConfig, ValidationError, finite


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("observation time must have a timezone")
    return value.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return utc(value).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class State:
    step: int
    resource: float
    parameter: float
    requested_delta: float
    actual_delta: float
    calculated_at: datetime

    def __post_init__(self) -> None:
        if type(self.step) is not int or not 0 <= self.step < 2**63:
            raise ValidationError("step must be a nonnegative SQLite integer")
        for name in ("resource", "parameter", "requested_delta", "actual_delta"):
            object.__setattr__(self, name, finite(getattr(self, name), name))
        if self.resource < 0:
            raise ValidationError("resource must be >= 0")
        object.__setattr__(self, "calculated_at", utc(self.calculated_at))

    @property
    def simulation_elapsed_seconds(self) -> int:
        return self.step


def initial(config: ModelConfig, at: datetime) -> State:
    return State(0, config.initial_resource, config.initial_parameter, 0, 0, at)


def advance(state: State, config: ModelConfig, sample: float, at: datetime) -> State:
    sample = finite(sample, "random sample")
    if not 0 <= sample < 1:
        raise ValidationError("random sample must be in [0, 1)")
    if utc(at) < state.calculated_at:
        raise ValidationError("clock moved backwards")
    parameter = finite(config.parameter_min + (config.parameter_max - config.parameter_min) * sample,
                       "parameter")
    # Floating point addition can round the largest sample up to the excluded endpoint.
    parameter = min(parameter, math.nextafter(config.parameter_max, config.parameter_min))
    requested = finite(config.coefficient * parameter, "requested_delta")
    candidate = finite(state.resource + requested, "resource before lower bound")
    resource = max(0.0, candidate)
    return State(state.step + 1, resource, parameter, requested, resource - state.resource, at)
