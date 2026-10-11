"""Application configuration: environment settings and engineering thresholds.

All engineering limits are configurable. Defaults follow ANSI C84.1 Range A
(+/-5 % service voltage) and conventional thermal-loading practice.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str = field(default_factory=lambda: os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    mongodb_db: str = field(default_factory=lambda: os.getenv("MONGODB_DB", "gridmind"))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))
    llm_enabled: bool = field(
        default_factory=lambda: bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("GRIDMIND_DISABLE_LLM") != "1"
    )
    max_iterations: int = field(default_factory=lambda: int(os.getenv("GRIDMIND_MAX_ITERATIONS", "3")))
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")


SETTINGS = Settings()

# Engineering thresholds (defaults; persisted per circuit in MongoDB and editable).
DEFAULT_THRESHOLDS: dict = {
    "voltage": {
        "lower_pu": 0.95,           # ANSI C84.1 Range A lower limit
        "upper_pu": 1.05,           # ANSI C84.1 Range A upper limit
        "warning_margin_pu": 0.01,  # amber band inside the limits
        "severe_lower_pu": 0.92,    # below this -> red (beyond Range B 0.917 pu)
        "severe_upper_pu": 1.08,
    },
    "loading": {
        "line": {"warning": 90.0, "limit": 100.0, "severe": 120.0},
        "transformer": {"warning": 90.0, "limit": 100.0, "severe": 120.0},
    },
    "power_factor": {"min": 0.90},
    "losses": {"max_percent_of_load": 5.0},
    "datacenter": {"connection_limit_mw": 5.0},
    "verification": {"light_load_factor": 0.35},
}

# Illustrative planning-level cost assumptions (USD). Labelled as assumptions
# in the UI and report; replace with utility cost data for real studies.
COST_ASSUMPTIONS: dict = {
    "capacitor_fixed_usd": 15000.0,
    "capacitor_per_kvar_usd": 30.0,
    "tap_change_usd": 2000.0,
    "regulator_usd": 120000.0,
    "line_upgrade_per_km_usd": 400000.0,
    "transformer_upgrade_usd_per_mva": 45000.0,
    "switching_usd": 5000.0,
    "dc_shift_usd_per_mwh": 18.0,
    "energy_price_usd_per_mwh": 95.0,
    "annuity_factor": 0.1175,                  # 10 % discount rate, 20-year life
    "loss_equivalent_hours_per_year": 1800.0,  # converts peak losses to annual energy
}

STANDARD_CAPACITOR_STEP_MVAR = 0.3
STANDARD_TRANSFORMER_SIZES_MVA = (31.5, 40.0, 50.0, 63.0)
UPGRADE_CABLE = {
    "name": "NA2XS2Y 1x240 RM/25 12/20 kV",
    "r_ohm_per_km": 0.122,
    "x_ohm_per_km": 0.112,
    "c_nf_per_km": 304.0,
    "max_i_ka": 0.421,
    "source": "pandapower standard type library",
}
