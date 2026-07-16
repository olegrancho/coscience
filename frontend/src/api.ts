export interface CurrentUser { username: string; name: string; initials: string }
export interface MeResponse { user: CurrentUser | null; required: boolean }
export interface ProgramRow { id: string; title: string; status: string; goals: string }
export interface SprintRef { id: string; status: string; goals: string; title: string; results: string[]; model: string; last_status_at: number | null; votes: VoteTally }
export interface PMActivation { at: number; cycle: number; triggers: string[]; submitted: string[]; forced: boolean }
export interface Program extends ProgramRow {
  report: string; cycle: number; sprints: SprintRef[]; pm_model: string; workdir: string;
  activations: PMActivation[]; last_run: number | null;
}
export interface Idea {
  id: string; text: string; source: "pm" | "human"; by?: string;
  pinned: boolean; protected: boolean; threads: FeedbackThreadT[]; created_at: number;
  demoted: boolean;
}
export interface IdeaPool { summary: string; ideas: Idea[] }
export interface ChatMessage { role: "user" | "pm"; text: string; at: number; by?: string }
export type ChatScope = "read" | "full";
export interface ChatThreadSummary {
  id: string; title: string; scope: ChatScope; created_at: number;
  busy: boolean; messages: number; last_at: number;
}
export interface ChatThread {
  id: string; title: string; scope: ChatScope; created_at: number;
  turns_done: number; busy: boolean; messages: ChatMessage[]; live: string;
}
export interface FeedbackMessage { role: "human" | "pm" | "worker"; text: string; by?: string; at: number }
export interface FeedbackThreadT { id: string; target: "pm" | "worker"; status: "open" | "complete"; agent_unseen: boolean; created_at: number; messages: FeedbackMessage[] }
export interface SprintActivity { label: string; active: boolean; at: number }
export interface VoteTally { up: number; down: number; mine: number }
export interface SprintRow {
  id: string; status: string; title: string; summary: string;
  goals: string; program: string | null;
  priority: number; steps: number; results: string[];
  rationale: string; resources_required: Record<string, number>;
  started_at: number | null; last_status_at: number | null;
  model: string; activity: SprintActivity | null;
  votes: VoteTally;
}
export interface UsageWindow { pct: number; resets: string }
export interface RunAgg {
  total: number; last_hour: number; last_day: number; last: number | null;
  cost: number; cost_day: number; tokens: number;
}
export interface Usage {
  budget: { windows: Record<string, UsageWindow>; live: boolean } | null;
  runs: { pm: RunAgg; worker: RunAgg };
}
export interface Sprint {
  id: string; status: string; title: string; summary: string;
  goals: string; priority: number; preemptible: boolean;
  resources_required: Record<string, number>; rationale: string; plan: string[];
  program: string | null; results: string[]; threads: FeedbackThreadT[];
  agent_running: boolean; started_at: number | null; error: string; lease: unknown | null;
  model: string; activity: SprintActivity | null; votes: VoteTally;
  decisions?: { by: string; action: string; at: number }[];
  status_history?: { status: string; at: number; by: string; action: string }[];
  created_at?: number | null;
  agent_state?: "running" | "sleeping" | "idle";
  job?: { note: string; out_file: string; started_at: number | null;
          expected_seconds: number; next_wake: number; max_seconds: number } | null;
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
  resources_required?: Record<string, number>; preemptible?: boolean; model?: string;
}

export const api = {
  me: () => fetch("/api/me").then(j<MeResponse>),
  listUsers: () => fetch("/api/users").then(j<CurrentUser[]>),
  login: (username: string) =>
    fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username }),
    }).then(j<CurrentUser>),
  logout: () => fetch("/api/logout", { method: "POST" }).then(j<{ ok: boolean }>),
  getVersion: () => fetch("/api/version").then(j<{ sha: string }>),
  listPrograms: () => fetch("/api/programs").then(j<ProgramRow[]>),
  getProgram: (id: string) => fetch(`/api/programs/${id}`).then(j<Program>),
  setProgramStatus: (id: string, status: string) =>
    fetch(`/api/programs/${id}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }).then(j<Program>),
  setProgramModel: (id: string, model: string) =>
    fetch(`/api/programs/${id}/model`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    }).then(j<{ id: string; pm_model: string }>),
  listChats: (id: string) => fetch(`/api/programs/${id}/chats`).then(j<ChatThreadSummary[]>),
  createChat: (id: string, title = "") =>
    fetch(`/api/programs/${id}/chats`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then(j<ChatThread>),
  getChatThread: (id: string, tid: string) =>
    fetch(`/api/programs/${id}/chats/${tid}`).then(j<ChatThread>),
  sendChatMessage: (id: string, tid: string, message: string) =>
    fetch(`/api/programs/${id}/chats/${tid}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    }).then(j<ChatThread>),
  patchChat: (id: string, tid: string, patch: { title?: string; scope?: ChatScope }) =>
    fetch(`/api/programs/${id}/chats/${tid}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<ChatThread>),
  deleteChat: (id: string, tid: string) =>
    fetch(`/api/programs/${id}/chats/${tid}`, { method: "DELETE" }).then(j<void>),
  replan: (id: string) =>
    fetch(`/api/programs/${id}/replan`, { method: "POST" }).then(
      j<{ program: string; cycle: number; submitted: string[]; skipped?: boolean; busy?: boolean; throttled?: boolean }>),
  pmDirective: (id: string, mode: "compress" | "brainstorm") =>
    fetch(`/api/programs/${id}/ideas/${mode}`, { method: "POST" }).then(
      j<{ program: string; cycle: number; submitted: string[]; skipped?: boolean; busy?: boolean; throttled?: boolean; ideas_added?: number; ideas_removed?: number; pool_size?: number }>),
  setProgramWorkdir: (id: string, workdir: string) =>
    fetch(`/api/programs/${id}/workdir`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workdir }),
    }).then(j<{ id: string; workdir: string; exists: boolean }>),
  listGuidance: (id: string) => fetch(`/api/programs/${id}/guidance`).then(j<FeedbackThreadT[]>),
  addGuidance: (id: string, text: string, threadId?: string) =>
    fetch(`/api/programs/${id}/guidance`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, thread_id: threadId ?? "" }),
    }).then(j<FeedbackThreadT>),
  completeGuidanceThread: (id: string, tid: string) =>
    fetch(`/api/programs/${id}/guidance/${tid}/complete`, { method: "POST" }).then(j<FeedbackThreadT>),
  reopenGuidanceThread: (id: string, tid: string) =>
    fetch(`/api/programs/${id}/guidance/${tid}/reopen`, { method: "POST" }).then(j<FeedbackThreadT>),
  seenGuidanceThread: (id: string, tid: string) =>
    fetch(`/api/programs/${id}/guidance/${tid}/seen`, { method: "POST" }).then(j<FeedbackThreadT>),
  deleteGuidance: (id: string, tid: string) =>
    fetch(`/api/programs/${id}/guidance/${tid}`, { method: "DELETE" }).then(j<void>),
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
  setIdeaDemoted: (id: string, ideaId: string, demoted: boolean) =>
    fetch(`/api/programs/${id}/ideas/${ideaId}/demote`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ demoted }),
    }).then(j<Idea>),
  demoteSprint: (id: string) =>
    fetch(`/api/sprints/${id}/demote`, { method: "POST" }).then(j<{ sprint_id: string; idea: Idea }>),
  addIdeaComment: (id: string, ideaId: string, text: string, threadId?: string) =>
    fetch(`/api/programs/${id}/ideas/${ideaId}/comments`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, thread_id: threadId ?? "" }),
    }).then(j<FeedbackThreadT>),
  completeIdeaThread: (id: string, ideaId: string, tid: string) =>
    fetch(`/api/programs/${id}/ideas/${ideaId}/threads/${tid}/complete`, { method: "POST" }).then(j<FeedbackThreadT>),
  reopenIdeaThread: (id: string, ideaId: string, tid: string) =>
    fetch(`/api/programs/${id}/ideas/${ideaId}/threads/${tid}/reopen`, { method: "POST" }).then(j<FeedbackThreadT>),
  seenIdeaThread: (id: string, ideaId: string, tid: string) =>
    fetch(`/api/programs/${id}/ideas/${ideaId}/threads/${tid}/seen`, { method: "POST" }).then(j<FeedbackThreadT>),
  deleteIdeaThread: (id: string, ideaId: string, tid: string) =>
    fetch(`/api/programs/${id}/ideas/${ideaId}/threads/${tid}`, { method: "DELETE" }).then(j<void>),
  listSprints: () => fetch("/api/sprints").then(j<SprintRow[]>),
  getSprint: (id: string, viewer?: string) =>
    fetch(`/api/sprints/${id}${viewer ? `?viewer=${encodeURIComponent(viewer)}` : ""}`).then(j<Sprint>),
  getSprintFiles: (id: string) => fetch(`/api/sprints/${id}/files`).then(j<SprintFile[]>),
  getSprintFile: (id: string, name: string) =>
    fetch(`/api/sprints/${id}/files/${encodeURIComponent(name)}`).then(j<SprintFile>),
  addSprintComment: (id: string, text: string, target: "worker" | "pm", threadId?: string) =>
    fetch(`/api/sprints/${id}/comments`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, target, thread_id: threadId ?? "" }),
    }).then(j<FeedbackThreadT>),
  completeSprintThread: (id: string, tid: string) =>
    fetch(`/api/sprints/${id}/threads/${tid}/complete`, { method: "POST" }).then(j<FeedbackThreadT>),
  reopenSprintThread: (id: string, tid: string) =>
    fetch(`/api/sprints/${id}/threads/${tid}/reopen`, { method: "POST" }).then(j<FeedbackThreadT>),
  seenSprintThread: (id: string, tid: string) =>
    fetch(`/api/sprints/${id}/threads/${tid}/seen`, { method: "POST" }).then(j<FeedbackThreadT>),
  deleteSprintThread: (id: string, tid: string) =>
    fetch(`/api/sprints/${id}/threads/${tid}`, { method: "DELETE" }).then(j<void>),
  submitSprint: (body: { id: string; goals: string; plan: string[]; program?: string;
                         priority?: number; resources_required?: Record<string, number> }) =>
    fetch("/api/sprints", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Sprint>),
  approveSprint: (id: string) =>
    fetch(`/api/sprints/${id}/approve`, { method: "POST" }).then(j<Sprint>),
  runSprint: (id: string) =>
    fetch(`/api/sprints/${id}/run`, { method: "POST" }).then(j<Sprint>),
  sendBackSprint: (id: string) =>
    fetch(`/api/sprints/${id}/send_back`, { method: "POST" }).then(j<Sprint>),
  rejectSprint: (id: string) =>
    fetch(`/api/sprints/${id}/reject`, { method: "POST" }).then(j<Sprint>),
  voteSprint: (id: string, by: string, value: number) =>
    fetch(`/api/sprints/${id}/vote`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ by, value }),
    }).then(j<VoteTally>),
  wakeSprint: (id: string) =>
    fetch(`/api/sprints/${id}/wake`, { method: "POST" }).then(j<Sprint>),
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
