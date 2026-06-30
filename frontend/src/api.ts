export interface ProgramRow { id: string; title: string; status: string; goals: string }
export interface SprintRef { id: string; status: string; goals: string; title: string; results: string[] }
export interface Program extends ProgramRow {
  report: string; cycle: number; sprints: SprintRef[];
}
export interface GuidanceNote { id: string; text: string; added_at: number }
export interface IdeaComment { id: string; text: string; added_at: number }
export interface Idea {
  id: string; text: string; source: "pm" | "human";
  pinned: boolean; protected: boolean; comments: IdeaComment[]; created_at: number;
}
export interface IdeaPool { summary: string; ideas: Idea[] }
export interface SprintRow {
  id: string; status: string; title: string; summary: string;
  goals: string; program: string | null;
  priority: number; steps: number; results: string[];
  rationale: string; resources_required: Record<string, number>;
  started_at: number | null;
}
export interface UsageWindow { pct: number; resets: string }
export interface RunAgg { total: number; last_hour: number; last_day: number; last: number | null }
export interface Usage {
  budget: { windows: Record<string, UsageWindow>; live: boolean } | null;
  runs: { pm: RunAgg; worker: RunAgg };
}
export interface Sprint {
  id: string; status: string; title: string; summary: string;
  goals: string; priority: number; preemptible: boolean;
  resources_required: Record<string, number>; rationale: string; plan: string[];
  program: string | null; results: string[]; comments: IdeaComment[];
  agent_running: boolean; started_at: number | null; lease: unknown | null;
}
export interface SprintFile {
  name: string; label: string; kind: string; size: number;
  content: string; truncated: boolean; binary: boolean;
}
export interface ResultRow { id: string; sprint: string; summary: string; program?: string | null; completed_at?: number | null }
export interface Ledger {
  capacity: Record<string, number>; used: Record<string, number>;
  available: Record<string, number>; leases: unknown[];
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

export interface SprintPatch {
  goals?: string; plan?: string[]; priority?: number;
  resources_required?: Record<string, number>; preemptible?: boolean;
}

export const api = {
  getVersion: () => fetch("/api/version").then(j<{ sha: string }>),
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
  listIdeas: (id: string) => fetch(`/api/programs/${id}/ideas`).then(j<IdeaPool>),
  addIdea: (id: string, text: string) =>
    fetch(`/api/programs/${id}/ideas`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j<Idea>),
  deleteIdea: (id: string, ideaId: string) =>
    fetch(`/api/programs/${id}/ideas/${ideaId}`, { method: "DELETE" }).then(j<void>),
  setIdeaPin: (id: string, ideaId: string, pinned: boolean) =>
    fetch(`/api/programs/${id}/ideas/${ideaId}/pin`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned }),
    }).then(j<Idea>),
  addIdeaComment: (id: string, ideaId: string, text: string) =>
    fetch(`/api/programs/${id}/ideas/${ideaId}/comments`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j<Idea>),
  listSprints: () => fetch("/api/sprints").then(j<SprintRow[]>),
  getSprint: (id: string) => fetch(`/api/sprints/${id}`).then(j<Sprint>),
  getSprintFiles: (id: string) => fetch(`/api/sprints/${id}/files`).then(j<SprintFile[]>),
  addSprintComment: (id: string, text: string) =>
    fetch(`/api/sprints/${id}/comments`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j<IdeaComment>),
  submitSprint: (body: { id: string; goals: string; plan: string[]; program?: string;
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
  getUsage: () => fetch("/api/usage").then(j<Usage>),
};
