import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { AgentTask, Circuit, Recommendation, Run, Scenario, SimResult } from "./types";

const CIRCUIT_ID = "cigre-mv-dc";
const POLL_MS = 2000;
const SCENARIO_KEY = "gridmind.scenario";

function readScenario(): string {
  try {
    return localStorage.getItem(SCENARIO_KEY) || "scn-base-peak";
  } catch {
    return "scn-base-peak";
  }
}

interface Store {
  circuit: Circuit | null;
  scenarios: Scenario[];
  scenarioId: string;
  setScenarioId: (id: string) => void;
  runs: Run[];
  run: Run | null;
  tasks: AgentTask[];
  recommendations: Recommendation[];
  selectRun: (id: string) => void;
  startRun: (maxIterations?: number) => Promise<void>;
  resumeRun: () => Promise<void>;
  getResult: (id: string | undefined | null) => Promise<SimResult | null>;
  error: string | null;
  setError: (e: string | null) => void;
  llm: { enabled: boolean; model: string | null };
}

const Ctx = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [circuit, setCircuit] = useState<Circuit | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenarioId, setScenarioIdState] = useState(readScenario);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [llm, setLlm] = useState({ enabled: false, model: null as string | null });
  const cache = useRef(new Map<string, SimResult>());

  const setScenarioId = useCallback((id: string) => {
    setScenarioIdState(id);
    try {
      localStorage.setItem(SCENARIO_KEY, id);
    } catch {
      /* storage unavailable */
    }
  }, []);

  useEffect(() => {
    Promise.all([api.circuit(CIRCUIT_ID), api.scenarios(CIRCUIT_ID), api.runs(CIRCUIT_ID), api.health()])
      .then(([c, s, r, h]) => {
        setCircuit(c);
        setScenarios(s);
        setRuns(r);
        setLlm({ enabled: h.llm_enabled, model: h.llm_model });
      })
      .catch((e) => setError(`Cannot reach the GridMind API: ${e.message}`));
  }, []);

  useEffect(() => {
    const latest = runs.find((r) => r.scenario_id === scenarioId);
    setRunId(latest ? latest._id : null);
  }, [scenarioId, runs]);

  const loadRun = useCallback(async (id: string) => {
    const [r, t, recs] = await Promise.all([api.run(id), api.tasks(id), api.recommendations(id)]);
    setRun(r);
    setTasks(t);
    setRecommendations(recs);
    return r;
  }, []);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      setTasks([]);
      setRecommendations([]);
      return;
    }
    let timer: number | undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await loadRun(runId);
        if (!cancelled && (r.status === "running" || r.status === "queued")) {
          timer = window.setTimeout(tick, POLL_MS);
        } else if (!cancelled) {
          api.runs(CIRCUIT_ID).then(setRuns).catch(() => undefined);
        }
      } catch (e) {
        setError((e as Error).message);
      }
    };
    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [runId, loadRun]);

  const startRun = useCallback(async (maxIterations?: number) => {
    try {
      const r = await api.startRun(CIRCUIT_ID, scenarioId, maxIterations);
      setRuns((prev) => [r, ...prev]);
      setRunId(r._id);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [scenarioId]);

  const resumeRun = useCallback(async () => {
    if (!runId) return;
    try {
      await api.resume(runId);
      setRuns(await api.runs(CIRCUIT_ID));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [runId]);

  const getResult = useCallback(async (id: string | undefined | null) => {
    if (!id) return null;
    const hit = cache.current.get(id);
    if (hit) return hit;
    const res = await api.result(id);
    cache.current.set(id, res);
    return res;
  }, []);

  const value = useMemo<Store>(() => ({
    circuit, scenarios, scenarioId, setScenarioId, runs, run, tasks, recommendations, selectRun: setRunId,
    startRun, resumeRun, getResult, error, setError, llm,
  }), [circuit, scenarios, scenarioId, setScenarioId, runs, run, tasks, recommendations, startRun, resumeRun, getResult, error, llm]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useStore must be used inside StoreProvider");
  return ctx;
}

export function useResult(id: string | undefined | null): SimResult | null {
  const { getResult, setError } = useStore();
  const [res, setRes] = useState<SimResult | null>(null);
  useEffect(() => {
    let alive = true;
    setRes(null);
    getResult(id).then((r) => alive && setRes(r)).catch((e) => setError(e.message));
    return () => {
      alive = false;
    };
  }, [id, getResult, setError]);
  return res;
}

/** Result ids for the three circuit states of the active run. */
export function useRunStates() {
  const { run } = useStore();
  const ctx = run?.context;
  const last = ctx?.iterations?.[ctx.iterations.length - 1];
  return {
    original: ctx?.base_result_id ?? null,
    proposed: last?.proposed_result_id ?? null,
    verified: last?.verified_result_id ?? null,
    verifiedLight: last?.verified_light_result_id ?? null,
    iteration: last ?? null,
  };
}

export { CIRCUIT_ID };
