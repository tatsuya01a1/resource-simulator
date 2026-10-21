"""Strict local configuration, independent of saved simulation state."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import tomllib


class ValidationError(ValueError):
    pass


def finite(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise ValidationError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValidationError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class ModelConfig:
    initial_resource: float = 100.0
    initial_parameter: float = 0.0
    parameter_min: float = -1.0
    parameter_max: float = 1.0
    coefficient: float = 10.0
    interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            object.__setattr__(self, key, finite(value, key))
        if self.initial_resource < 0:
            raise ValidationError("initial_resource must be >= 0")
        if not self.parameter_min < self.parameter_max:
            raise ValidationError("parameter_min must be < parameter_max")
        finite(self.parameter_max - self.parameter_min, "parameter span")
        if not self.parameter_min <= self.initial_parameter < self.parameter_max:
            raise ValidationError("initial_parameter must be in [parameter_min, parameter_max)")
        if self.coefficient < 0 or self.interval_seconds != 1:
            raise ValidationError("coefficient must be >= 0 and interval_seconds must be 1")

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def rules_hash(self) -> str:
        rules = asdict(self)
        del rules["initial_resource"], rules["initial_parameter"]
        return hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()


def load_config(path: Path) -> ModelConfig:
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    if set(document) != {"model"} or not isinstance(document["model"], dict):
        raise ValidationError("configuration must contain only the [model] table")
    values = document["model"]
    if set(values) != set(ModelConfig.__dataclass_fields__):
        raise ValidationError("[model] must contain exactly the documented model keys")
    return ModelConfig(**values)
