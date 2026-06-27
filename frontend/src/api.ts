export interface ProgramRow { id: string; title: string; status: string; goals: string }
export interface SprintRef { id: string; status: string; goals: string }
export interface Program extends ProgramRow {
  report: string; cycle: number; sprints: SprintRef[];
}
export interface GuidanceNote { id: string; text: string; added_at: number }
export interface SprintRow {
  id: string; status: string; goals: string; program: string | null;
  priority: number; steps: number; results: string[];
  rationale: string; resources_required: Record<string, number>;
}
export interface Step { id: string; run: string }
export interface Sprint {
  id: string; status: string; goals: string; priority: number; preemptible: boolean;
  resources_required: Record<string, number>; plan: Step[];
  completed_steps: string[]; detached: Record<string, string>;
  outputs: Record<string, string>; lease: unknown | null;
}
export interface ResultRow { id: string; sprint: string; summary: string }
export interface Ledger {
  capacity: Record<string, number>; used: Record<string, number>;
  available: Record<string, number>; leases: unknown[];
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

export interface SprintPatch {
  goals?: string; plan?: Step[]; priority?: number;
  resources_required?: Record<string, number>; preemptible?: boolean;
}

export const api = {
  listPrograms: () => fetch("/api/programs").then(j<ProgramRow[]>),
  getProgram: (id: string) => fetch(`/api/programs/${id}`).then(j<Program>),
  setProgramStatus: (id: string, status: string) =>
    fetch(`/api/programs/${id}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }).then(j<Program>),
  listGuidance: (id: string) => fetch(`/api/programs/${id}/guidance`).then(j<GuidanceNote[]>),
  addGuidance: (id: string, text: string) =>
    fetch(`/api/programs/${id}/guidance`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j<GuidanceNote>),
  removeGuidance: (id: string, noteId: string) =>
    fetch(`/api/programs/${id}/guidance/${noteId}`, { method: "DELETE" }).then(j<void>),
  listSprints: () => fetch("/api/sprints").then(j<SprintRow[]>),
  getSprint: (id: string) => fetch(`/api/sprints/${id}`).then(j<Sprint>),
  submitSprint: (body: { id: string; goals: string; plan: Step[]; program?: string;
                         priority?: number; resources_required?: Record<string, number> }) =>
    fetch("/api/sprints", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Sprint>),
  approveSprint: (id: string) =>
    fetch(`/api/sprints/${id}/approve`, { method: "POST" }).then(j<Sprint>),
  rejectSprint: (id: string) =>
    fetch(`/api/sprints/${id}/reject`, { method: "POST" }).then(j<Sprint>),
  editSprint: (id: string, patch: SprintPatch) =>
    fetch(`/api/sprints/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<Sprint>),
  listResults: () => fetch("/api/results").then(j<ResultRow[]>),
  getResult: (id: string) => fetch(`/api/results/${id}`).then(j<ResultRow>),
  getLedger: () => fetch("/api/ledger").then(j<Ledger>),
};
