"""Public input data for the demo: load profiles, weather, and the data-center profile.

* Residential / commercial load shapes: SimBench standard profiles
  (https://simbench.de, CC BY 4.0) - ``lv_urban6`` aggregated urban LV grid
  and ``G0-A`` general commercial, 15-min values averaged to hours on the
  annual peak day of the residential profile, normalized to the daily max.
* Outdoor temperature and solar irradiance: Open-Meteo API
  (https://open-meteo.com, CC BY 4.0) for San Jose, CA, previous day.
If a source is unreachable, a documented fallback is stored and flagged.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger(__name__)

HOURS = 24
DC_LOCATION = {"city": "San Jose, CA", "lat": 37.34, "lon": -121.89}
OPEN_METEO_URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                  "&hourly=temperature_2m,shortwave_radiation&past_days=1&forecast_days=1&timezone=America%2FLos_Angeles")
FALLBACK_TEMPERATURE_C = [13, 12.5, 12, 11.6, 11.3, 11, 11.2, 12.5, 14.5, 16.8, 19, 21, 22.5, 23.5, 24, 24, 23.2,
                          21.8, 20, 18.3, 16.8, 15.5, 14.6, 13.8]
FALLBACK_PV = [0, 0, 0, 0, 0, 0, 0.02, 0.12, 0.3, 0.5, 0.66, 0.77, 0.82, 0.8, 0.72, 0.58, 0.4, 0.2, 0.05, 0, 0, 0, 0, 0]
# Illustrative time-of-use energy price (USD/MWh), labelled as an assumption.
TOU_PRICE_USD_PER_MWH = [78] * 7 + [92] * 7 + [118] * 2 + [215] * 5 + [120] * 2 + [85]
STUDY_HOUR = 19


def _now() -> datetime:
    return datetime.now(timezone.utc)


def simbench_profiles() -> dict:
    import simbench as sb

    net = sb.get_simbench_net("1-MV-urban--0-sw")
    lp = net.profiles["load"].copy()
    lp["time"] = lp["time"].astype("datetime64[ns]") if "time" in lp else lp.index
    res = lp["lv_urban6_pload"].to_numpy()
    com = lp["G0-A_pload"].to_numpy()
    days = len(res) // 96
    peak_day = max(range(days), key=lambda d: res[d * 96:(d + 1) * 96].max())
    sl = slice(peak_day * 96, (peak_day + 1) * 96)

    def hourly(arr) -> list[float]:
        vals = [float(arr[sl][h * 4:(h + 1) * 4].mean()) for h in range(HOURS)]
        peak = max(vals)
        return [round(v / peak, 4) for v in vals]

    date = str(lp["time"].iloc[peak_day * 96])[:10] if "time" in lp else f"day {peak_day}"
    return {"residential": hourly(res), "commercial": hourly(com), "reference_date": date}


def open_meteo_weather() -> dict:
    url = OPEN_METEO_URL.format(**DC_LOCATION)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 - fixed https URL
            data = json.loads(resp.read().decode())
        temps = data["hourly"]["temperature_2m"][:HOURS]
        rad = data["hourly"]["shortwave_radiation"][:HOURS]
        if len(temps) != HOURS or any(t is None for t in temps):
            raise ValueError("incomplete hourly data")
        return {"temperature_c": [float(t) for t in temps], "pv": [round(min(1.0, (r or 0) / 1000.0), 4) for r in rad],
                "reference_date": data["hourly"]["time"][0][:10], "source_url": url, "fallback": False}
    except Exception as exc:  # network failure -> documented fallback, flagged
        log.warning("Open-Meteo unavailable (%s); using fallback profile", exc)
        return {"temperature_c": FALLBACK_TEMPERATURE_C, "pv": FALLBACK_PV, "reference_date": None,
                "source_url": url, "fallback": True}


def measurement_docs(circuit_id: str) -> list[dict]:
    try:
        sbp = simbench_profiles()
        sb_fallback = False
    except Exception as exc:
        log.warning("SimBench unavailable (%s); using flat fallback", exc)
        sbp = {"residential": [1.0] * HOURS, "commercial": [1.0] * HOURS, "reference_date": None}
        sb_fallback = True
    wx = open_meteo_weather()
    retrieved = _now()
    sb_src = {"source": "SimBench standard load profiles (CC BY 4.0)", "source_url": "https://simbench.de",
              "dataset": "1-MV-urban--0-sw", "fallback": sb_fallback}
    wx_src = {"source": "Open-Meteo hourly weather API (CC BY 4.0)", "source_url": wx["source_url"],
              "location": DC_LOCATION, "fallback": wx["fallback"]}
    return [
        {"_id": f"{circuit_id}:profile:residential", "circuit_id": circuit_id, "kind": "load_profile",
         "category": "residential", "values": sbp["residential"], "unit": "pu of daily peak",
         "profile_column": "lv_urban6_pload", "reference_date": sbp["reference_date"], "retrieved_at": retrieved, **sb_src},
        {"_id": f"{circuit_id}:profile:commercial", "circuit_id": circuit_id, "kind": "load_profile",
         "category": "commercial", "values": sbp["commercial"], "unit": "pu of daily peak",
         "profile_column": "G0-A_pload", "reference_date": sbp["reference_date"], "retrieved_at": retrieved, **sb_src},
        {"_id": f"{circuit_id}:weather:temperature", "circuit_id": circuit_id, "kind": "weather",
         "category": "temperature", "values": wx["temperature_c"], "unit": "degC",
         "reference_date": wx["reference_date"], "retrieved_at": retrieved, **wx_src},
        {"_id": f"{circuit_id}:weather:pv", "circuit_id": circuit_id, "kind": "weather", "category": "pv",
         "values": wx["pv"], "unit": "pu of 1 kW/m2 (shortwave radiation)", "reference_date": wx["reference_date"],
         "retrieved_at": retrieved, **wx_src},
        {"_id": f"{circuit_id}:price:tou", "circuit_id": circuit_id, "kind": "price", "category": "energy_price",
         "values": TOU_PRICE_USD_PER_MWH, "unit": "USD/MWh", "reference_date": None, "retrieved_at": retrieved,
         "source": "Illustrative time-of-use tariff (assumption, not a published rate)", "source_url": None,
         "fallback": False},
    ]


def datacenter_profile(circuit_id: str) -> dict:
    inference = [0.55, 0.5, 0.48, 0.47, 0.48, 0.52, 0.6, 0.7, 0.8, 0.86, 0.9, 0.92, 0.93, 0.92, 0.92, 0.93, 0.95,
                 0.98, 1.0, 1.0, 0.97, 0.9, 0.78, 0.65]
    cloud_it = [0.9, 0.89, 0.88, 0.88, 0.88, 0.89, 0.91, 0.94, 0.97, 0.98, 0.99, 0.99, 0.99, 0.99, 0.99, 0.99, 0.99,
                1.0, 1.0, 1.0, 0.99, 0.97, 0.94, 0.92]
    return {
        "_id": f"{circuit_id}:dc-profile", "circuit_id": circuit_id, "name": "AI Data Center Campus",
        "connection_bus": "B14", "connection_limit_mw": 5.0, "location": DC_LOCATION, "study_hour": STUDY_HOUR,
        "components": [
            {"eq_id": "DC-IT", "kind": "it", "base_mw": 1.2, "shape": cloud_it, "flexible": False},
            {"eq_id": "DC-TRAIN", "kind": "ai_training", "base_mw": 1.0, "shape": [1.0] * HOURS, "flexible": True,
             "min_mw": 0.3, "max_mw": 2.0, "daily_energy_mwh": 24.0, "deadline_hour": 24,
             "notes": "Checkpointed training jobs; must deliver 24 MWh of compute energy per day."},
            {"eq_id": "DC-INFER", "kind": "ai_inference", "base_mw": 0.8, "shape": inference, "flexible": False,
             "notes": "Latency-critical; not curtailable (SLA 99.99 %)."},
            {"eq_id": "DC-COOL", "kind": "cooling", "flexible": False,
             "model": {"base_ratio": 0.18, "per_degC": 0.012, "ref_temp_c": 15.0,
                       "description": "cooling = IT x (0.18 + 0.012 x max(0, T - 15 degC))"}},
            {"eq_id": "DC-UPS", "kind": "ups_losses", "flexible": False, "model": {"efficiency": 0.955}},
            {"eq_id": "DC-AUX", "kind": "auxiliary", "base_mw": 0.1, "shape": [1.0] * HOURS, "flexible": False},
        ],
        "sla": {"inference": "not curtailable", "training": "24 MWh/day completed by 24:00, 0.3-2.0 MW"},
        "assumptions": ["Component sizes and shapes are documented demo assumptions.",
                        "Cooling model calibrated to a PUE of about 1.3 at 20 degC.",
                        "UPS efficiency 95.5 % (double-conversion, typical vendor data)."],
        "created_at": _now(),
    }
