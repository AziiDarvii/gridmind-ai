# GridMind AI

Multi-agent electrical circuit analysis, violation detection, verified correction, and data-center energy
optimization, built on an AC power-flow engine (pandapower) with MongoDB Atlas persistence and Claude for agent
reasoning.

The central screen is the **interactive single-line diagram** of the complete modeled network. It is generated from
the circuit model stored in MongoDB and colored from persisted power-flow results. You can switch it between three
states: **A · Original**, **B · Proposed**, and **C · Verified**.

## What it does (end to end)

1. Loads the circuit model (75 components) from MongoDB and validates every required parameter.
2. Solves the AC Newton-Raphson power flow and stores bus, branch, load, and grid results.
3. Checks every bus, line, transformer, and load against configurable limits at **peak and light load**. The checks
   cover undervoltage, overvoltage, line and transformer overload, power factor, losses, data-center connection
   capacity, and convergence failure.
4. Diagnoses each violation with numbers. It decomposes the voltage drop along the supply path
   (ΔV ≈ (R·P + X·Q)/V²), checks transformer tap headroom, solves the network again without the data center, and
   lists available tie switches. Claude reviews the evidence through read-only solver tools.
5. Generates remedies that depend on the diagnosed cause, and sizes each one by simulation:
   - capacitor banks sized from a dV/dQ sensitivity
   - OLTC and off-circuit taps chosen by a full tap sweep
   - 32-step voltage regulators
   - feeder reinforcement and transformer upgrades
   - tie-switch load transfer
   - data-center AI-training deferral and battery (BESS) dispatch
6. Ranks combinations of remedies with an explicit cost objective. A beam search evaluates every plan with AC power
   flow, and the method is documented.
7. **Independently verifies** the selected plan. It reloads the model from MongoDB, solves it again at peak and light
   load, and compares before and after for every bus and branch. It reports which violations were resolved, which
   remain, and any new ones. A plan is never labeled fixed unless verification passes.
8. Coordinates the data center. A 24-hour AC time series uses public load and weather data and emulates the OLTC
   automatic voltage regulator. An LP then reschedules deferrable AI training, and the AC solver checks the result
   hour by hour.
9. Stores structured lessons in MongoDB. Later runs retrieve verified lessons: they reuse measured sensitivities and
   evaluate previously verified plan types first.
10. Writes an engineering report (Markdown) covering evidence, calculations, changes, verified results, and
    limitations.

The loop is bounded: **Corrective Action → Optimization → Verification** repeats until verification passes, the
iteration budget runs out (default 3), no improving action exists, or a hard constraint cannot be met. Agents
never operate real equipment, and every plan requires engineer approval.

## Demo network and public data

| Item | Source |
|---|---|
| 20 kV MV feeder, 110/20 kV transformers, lines, loads, tie switches | **CIGRE TF C6.04.02 MV benchmark** (Rudion et al., IEEE PES GM 2006), via `pandapower.networks.create_cigre_network_mv`. Parameters copied verbatim; a unit test checks that bus voltages match the reference solution. |
| Residential and commercial daily load shapes | **SimBench** standard profiles `lv_urban6` and `G0-A` (CC BY 4.0), peak day 2016-01-22 |
| Outdoor temperature and solar irradiance | **Open-Meteo** hourly API for San Jose, CA (CC BY 4.0), fetched at seed time |
| ~4 MW AI data-center campus at Bus 14 (IT, AI training, AI inference, cooling, UPS losses, auxiliary loads, BESS, PV, backup generator), OLTC data on the 110/20 kV transformers | Documented example assumptions, flagged `assumed` in each equipment record's `data_quality` field |
| Cost figures and TOU tariff | Illustrative planning assumptions (`backend/app/config.py`), labeled as such in the UI and report |

## Project structure

```
gridmind-ai/
├── .env.example                 # copy to .env (never committed)
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py              # FastAPI factory; serves frontend/dist; marks interrupted runs
│   │   ├── config.py            # env settings, thresholds (ANSI C84.1 Range A), cost assumptions
│   │   ├── llm.py               # Claude client: narrate() + bounded tool-use loop, deterministic fallback
│   │   ├── api/routes.py        # REST API (thin);  api/schemas.py  Pydantic validation
│   │   ├── db/mongo.py          # collections, JSON-schema validators, indexes
│   │   ├── db/repositories.py   # repository layer (all MongoDB access) + BSON sanitizer
│   │   ├── engine/              # electrical engine (no LLM)
│   │   │   ├── network.py       # equipment docs -> pandapower model; interventions
│   │   │   ├── powerflow.py     # AC NR solve, result extraction, non-convergence diagnosis
│   │   │   ├── violations.py    # limit checks, severity with numeric basis, unsupported studies
│   │   │   ├── simulate.py      # peak + light-load verification
│   │   │   ├── topology.py      # graph, supply paths, downstream sets, SLD layout
│   │   │   ├── rootcause.py     # path ΔV decomposition, counterfactuals
│   │   │   ├── remediation.py   # candidate generation and sizing
│   │   │   ├── optimization.py  # beam search, explicit objective, trade-offs
│   │   │   ├── datacenter.py    # DC demand model, 24 h time series, OLTC AVR emulation, LP scheduler
│   │   │   └── comparison.py    # before/after from two persisted results
│   │   ├── agents/              # 10 agents + orchestrator (DAG, loop, resume) + report builder
│   │   └── seed/                # CIGRE + DC model, public data fetchers, seed script
│   └── tests/                   # unit (engine) + integration (MongoDB, workflow, API)
└── frontend/                    # React 18 + TypeScript + Vite + React Flow + Recharts
    └── src/
        ├── components/CircuitDiagram.tsx  # interactive single-line diagram (10 layers, 3 states)
        ├── components/DetailPanel.tsx     # click-to-inspect: params (input/assumed/missing), values, history
        └── pages/                         # 12 pages (overview ... engineering report)
```

## MongoDB collections

`circuits`, `equipment`, `measurements`, `simulation_scenarios`, `simulation_results`, `detected_faults`,
`recommendations`, `agent_runs`, `agent_memory`, `data_center_profiles`, `optimization_runs`, `audit_logs`.

- Collections are created with JSON-schema validators and indexed by circuit, scenario, result, equipment, fault
  type, timestamp, and verification status.
- Records refer to each other by ID: run → result → faults → recommendation → verified result.

## Setup

Requirements: Python 3.12 (the macOS system Python 3.9 is too old for pandapower 3), Node 18+, and a MongoDB Atlas
cluster or a local MongoDB.

```bash
cd ~/gridmind-ai
cp .env.example .env              # then set MONGODB_URI (and optionally ANTHROPIC_API_KEY)

# backend
cd backend
uv venv -p 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m app.seed.seed --reset-history     # create collections, load model + public data

# frontend
cd ../frontend && npm install && npm run build
```

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `MONGODB_URI` | yes | Atlas SRV string or `mongodb://localhost:27017` |
| `MONGODB_DB` | no (default `gridmind`) | Database name. Tests use `<name>_test`. |
| `ANTHROPIC_API_KEY` | no | Enables Claude in the Root Cause and Report agents. Without it, explanations use deterministic templates and all calculations run the same way. |
| `ANTHROPIC_MODEL` | no (default `claude-opus-5-5`) | Model for agent reasoning |
| `GRIDMIND_MAX_ITERATIONS` | no (default 3) | Budget for the correction loop |
| `GRIDMIND_DISABLE_LLM` | no | `1` forces deterministic mode |

Credentials are read only by the backend from `.env`. The frontend never receives them.

## Run

```bash
cd ~/gridmind-ai/backend && .venv/bin/uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000        (built UI served by FastAPI)
# or, for UI development:  cd frontend && npm run dev   -> http://localhost:5173 (proxies /api)
```

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q --cov=app
```

Latest result: **21 passed**, **88 % line coverage** of `app/`. The suite runs against the `gridmind_test` database
on Atlas and takes about 3.5 minutes.

- **Unit tests:** the model matches the CIGRE reference power flow; missing-parameter validation; voltage and
  loading bands; non-convergence is reported, never fabricated; capacitor and tap physics; capacitor sizing math;
  the tap sweep; light-load verification catches capacitor overvoltage; regulator insertion; resolved/remaining/new
  comparison; path-drop decomposition; topology and layout; data-center energy balance and cooling model;
  data-center capacity check.
- **Integration tests:** seed persistence and indexes; the full 10-agent workflow completes and is **independently
  verified** (a separate result document re-solved from the database); agents run in dependency order; fault
  resolution is tracked; a **second scenario retrieves lessons** from the first; API endpoints, including 422
  validation and 404s.

## Hackathon demo script

1. Start the server and open `http://127.0.0.1:8000`.
2. **Overview:** the Evening-peak scenario shows 17 violations (14 undervoltage, 3 transformer overloads), Vmin
   0.895 pu, and max loading 104.5 %. All values come from the solver results stored in MongoDB.
3. **Single-line diagram → Faults layer:**
   - Every bus, line, transformer, load, and data-center device is drawn, and the violations are colored in place.
   - Switch layers (Voltage, Loading, Current, Active P, Reactive Q, Losses, Data center).
   - Search for `B11`. Click it to see every parameter, labeled input or assumed, plus all calculated values and
     its supply path.
4. **Violations:** click a row. The diagram focuses on and highlights that bus or branch.
5. **Run multi-agent analysis** (about 60 s). Watch **Agents** for live task status, outputs, and durations.
6. **Root cause:** feeder impedance drop on feeder 1; data-center demand at Bus 14; the R·P and X·Q split per
   segment.
7. **Corrective actions:** 19 sized candidates (capacitor, tap, regulator, reinforcement, transformer upgrade, tie
   transfer, DC flexibility, BESS) and the ranked plans with trade-offs.
8. **Single-line diagram → B · Proposed**, then **C · Verified**:
   - Proposed changes are drawn in purple: the new capacitor `CAP-NEW-B11`, `T0-12` at tap −3, the AI-training
     shift, and BESS discharge.
   - The verified state shows 0 violations at peak and light load.
9. **Before / after:** the same layout side by side, plus the bus and branch table. In the reference run, 17 were
   resolved, 0 remain, and 0 are new; losses went from 0.654 to 0.482 MW and Vmin from 0.895 to 0.953 pu.
10. **Data center:**
    - component demand and PUE against temperature;
    - the 24 h AC time series;
    - the what-if slider (e.g. DC × 1.4, then run AC power flow);
    - the coordinated LP training schedule against the TOU price.
11. Switch the scenario to **AI training surge** and run again. **Agents → Learning (retrieve)** lists the verified
    lessons from run 1, and **Optimization** shows the plan seeded from memory.
12. **History & memory:** runs, `agent_memory` lessons, and the audit log. **Engineering report:** download the
    Markdown.

## Known limitations

- **Model scope:** steady-state, balanced positive-sequence model. Phase imbalance, harmonics/resonance,
  short-circuit levels, protection coordination, and dynamic or transient stability are reported as
  **unsupported or needing further study**, not computed.
- **Search optimality:** the beam search is heuristic, so the chosen plan is the best of the evaluated combinations,
  not a proven global optimum. The LP is optimal only for its linearized constraints, and its AC check is the result
  that counts.
- **Assumed data:** data-center component sizes, OLTC data, costs, and the TOU tariff are documented assumptions.
  Replace them with utility or operator data for real studies.
- **Single study point:** the base scenarios use the CIGRE coincident peak. The 24 h time series uses SimBench shapes,
  so its hourly peaks are lower than the design case.
- **Performance:** a full agent run takes about 60 s, dominated by about 170 AC power flows in the optimizer and the
  24 h LP verification.
