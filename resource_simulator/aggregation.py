"""UTC half-open minute aggregation using actual completed observations."""

from dataclasses import dataclass, replace
from datetime import datetime

from .config import ValidationError, finite
from .engine import utc


def minute(at: datetime) -> datetime:
    return utc(at).replace(second=0, microsecond=0)


@dataclass(frozen=True)
class Candle:
    minute_start: datetime
    open: float
    high: float
    low: float
    close: float
    sample_count: int
    first_observed_at: datetime
    last_observed_at: datetime

    def __post_init__(self) -> None:
        for name in ("minute_start", "first_observed_at", "last_observed_at"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, finite(getattr(self, name), name))
        if (type(self.sample_count) is not int or self.sample_count < 1
                or not 0 <= self.low <= min(self.open, self.close)
                or self.high < max(self.open, self.close, self.low)
                or self.first_observed_at > self.last_observed_at
                or minute(self.first_observed_at) != self.minute_start
                or minute(self.last_observed_at) != self.minute_start):
            raise ValidationError("invalid OHLC state")

    @classmethod
    def start(cls, value: float, at: datetime) -> "Candle":
        return cls(minute(at), value, value, value, value, 1, at, at)

    def add(self, value: float, at: datetime) -> "Candle":
        if minute(at) != self.minute_start or utc(at) < self.last_observed_at:
            raise ValidationError("observation is outside the open candle or moved backwards")
        return replace(self, high=max(self.high, value), low=min(self.low, value),
                       close=value, sample_count=self.sample_count + 1, last_observed_at=at)
