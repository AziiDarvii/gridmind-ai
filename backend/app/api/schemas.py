"""Pydantic request schemas: all API input is validated here before reaching the engine."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ID = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:\-]+$")

_REQUIRED_BY_TYPE = {
    "capacitor": ("bus", "q_mvar"), "tap": ("transformer", "tap_pos"), "regulator": ("line",),
    "line_upgrade": ("line",), "transformer_upgrade": ("transformer", "sn_mva"), "switch": ("switch", "closed"),
    "capacitor_switching": ("capacitor", "steps_on"), "load_adjust": ("load", "delta_mw"),
    "storage_dispatch": ("storage", "p_mw"),
}


class Intervention(BaseModel):
    type: Literal["capacitor", "tap", "regulator", "line_upgrade", "transformer_upgrade", "switch",
                  "capacitor_switching", "load_adjust", "storage_dispatch"]
    id: str | None = Field(default=None, max_length=80)
    bus: str | None = Field(default=None, max_length=80)
    line: str | None = Field(default=None, max_length=80)
    transformer: str | None = Field(default=None, max_length=80)
    switch: str | None = Field(default=None, max_length=80)
    capacitor: str | None = Field(default=None, max_length=80)
    load: str | None = Field(default=None, max_length=80)
    storage: str | None = Field(default=None, max_length=80)
    q_mvar: float | None = Field(default=None, gt=0, le=50)
    tap_pos: int | None = Field(default=None, ge=-32, le=32)
    sn_mva: float | None = Field(default=None, gt=0, le=500)
    closed: bool | None = None
    steps_on: int | None = Field(default=None, ge=0, le=64)
    delta_mw: float | None = Field(default=None, ge=-100, le=100)
    p_mw: float | None = Field(default=None, ge=-100, le=100)

    @model_validator(mode="after")
    def _required_fields(self) -> "Intervention":
        missing = [f for f in _REQUIRED_BY_TYPE[self.type] if getattr(self, f) is None]
        if missing:
            raise ValueError(f"intervention '{self.type}' requires: {', '.join(missing)}")
        return self


class Conditions(BaseModel):
    global_scale: float | None = Field(default=None, ge=0, le=3)
    sgen_scale: float | None = Field(default=None, ge=0, le=1.5)
    category_scale: dict[str, float] | None = None

    @model_validator(mode="after")
    def _scale_bounds(self) -> "Conditions":
        for key, value in (self.category_scale or {}).items():
            if not 0 <= value <= 5 or len(key) > 40:
                raise ValueError(f"category_scale[{key}] must be within 0..5")
        return self


class ScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    conditions: Conditions = Conditions()
    interventions: list[Intervention] = Field(default_factory=list, max_length=20)


class SimulateRequest(BaseModel):
    interventions: list[Intervention] = Field(default_factory=list, max_length=20)
    label: str | None = Field(default=None, max_length=120)


class RunRequest(BaseModel):
    circuit_id: str = ID
    scenario_id: str = ID
    max_iterations: int | None = Field(default=None, ge=1, le=5)


class WhatIfRequest(BaseModel):
    scenario_id: str = ID
    dc_scale: float = Field(default=1.0, ge=0, le=3)
    training_scale: float | None = Field(default=None, ge=0, le=3)
    interventions: list[Intervention] = Field(default_factory=list, max_length=20)


class VoltageLimits(BaseModel):
    lower_pu: float | None = Field(default=None, ge=0.8, le=1.0)
    upper_pu: float | None = Field(default=None, ge=1.0, le=1.2)
    warning_margin_pu: float | None = Field(default=None, ge=0, le=0.05)
    severe_lower_pu: float | None = Field(default=None, ge=0.7, le=1.0)
    severe_upper_pu: float | None = Field(default=None, ge=1.0, le=1.3)


class ThresholdUpdate(BaseModel):
    voltage: VoltageLimits | None = None
