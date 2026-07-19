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
  busy: boolean; messages: number; last_at: number; artifacts: string[];
}
export interface ChatThread {
  id: string; title: string; scope: ChatScope; created_at: number;
  turns_done: number; busy: boolean; messages: ChatMessage[]; live: string; artifacts: string[];
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
  artifacts_bound: string[];
  artifacts_create: { aid: string; title: string; kind: string }[];
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
export interface GraphNode {
  id: string; kind: "idea" | "experiment"; stage: "idea" | "experiment" | "result"; label: string;
  status: string;   // sprint status ("" for ideas); used to dim parked nodes
}
export interface GraphEdge {
  id: string; type: string; src: string; dst: string; source: string;
  by: string; at: number; rationale: string; confidence: string; evidence: string;
}
export interface Graph { nodes: GraphNode[]; edges: GraphEdge[] }
export interface ArtifactVersionT { id: string; parent: string; created_at: number; created_by: string; archived: boolean; note: string }
export interface ArtifactLock { holder_kind?: string; holder_id?: string; acquired_at?: number; last_activity?: number }
export interface ArtifactRow { id: string; title: string; kind: string; current: string; archived: boolean; lock: ArtifactLock; version_count: number }
export interface LinkedSprint { id: string; status: string; title: string }
export interface ArtifactDetailT {
  id: string; program: string; title: string; kind: string; current: string;
  archived: boolean; lock: ArtifactLock; versions: ArtifactVersionT[];
  threads: FeedbackThreadT[]; current_files: string[]; linked_sprints: LinkedSprint[];
}
export interface ArtifactFileT { name: string; size: number; content: string; binary: boolean }

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
  getGraph: (id: string) => fetch(`/api/programs/${id}/graph`).then(j<Graph>),
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
  createChat: (id: string, title = "", artifacts?: string[]) =>
    fetch(`/api/programs/${id}/chats`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, ...(artifacts && artifacts.length ? { artifacts } : {}) }),
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
  saveChatVersion: (id: string, tid: string) =>
    fetch(`/api/programs/${id}/chats/${tid}/save`, { method: "POST" }).then(j<Record<string, string | null>>),
  listArtifactWorkFiles: (id: string, aid: string) =>
    fetch(`/api/programs/${id}/artifacts/${aid}/work`).then(j<string[]>),
  readArtifactWorkFile: (id: string, aid: string, name: string) =>
    fetch(`/api/programs/${id}/artifacts/${aid}/work/${name}`).then(j<ArtifactFileT>),
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
                         priority?: number; resources_required?: Record<string, number>;
                         artifacts_bound?: string[];
                         artifacts_create?: { aid: string; title: string; kind: string }[] }) =>
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
  parkSprint: (id: string) =>
    fetch(`/api/sprints/${id}/park`, { method: "POST" }).then(j<Sprint>),
  unparkSprint: (id: string) =>
    fetch(`/api/sprints/${id}/unpark`, { method: "POST" }).then(j<Sprint>),
  cancelParkedSprint: (id: string) =>
    fetch(`/api/sprints/${id}/cancel`, { method: "POST" }).then(j<Sprint>),
  resumeSprint: (id: string) =>
    fetch(`/api/sprints/${id}/resume`, { method: "POST" }).then(j<Sprint>),
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
  listArtifacts: (pid: string) => fetch(`/api/programs/${pid}/artifacts`).then(j<ArtifactRow[]>),
  getArtifact: (pid: string, aid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}`).then(j<ArtifactDetailT>),
  readArtifactFile: (pid: string, aid: string, vid: string, name: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/versions/${vid}/files/${encodeURIComponent(name)}`).then(j<ArtifactFileT>),
  revertArtifact: (pid: string, aid: string, vid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/revert`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vid }),
    }).then(j<ArtifactDetailT>),
  archiveArtifact: (pid: string, aid: string, archived: boolean) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/archive`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived }),
    }).then(j<ArtifactDetailT>),
  archiveArtifactVersion: (pid: string, aid: string, vid: string, archived: boolean) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/versions/${vid}/archive`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived }),
    }).then(j<ArtifactDetailT>),
  addArtifactComment: (pid: string, aid: string, text: string, threadId?: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/comments`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, thread_id: threadId ?? "" }),
    }).then(j<FeedbackThreadT>),
  completeArtifactThread: (pid: string, aid: string, tid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/threads/${tid}/complete`, { method: "POST" }).then(j<FeedbackThreadT>),
  reopenArtifactThread: (pid: string, aid: string, tid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/threads/${tid}/reopen`, { method: "POST" }).then(j<FeedbackThreadT>),
  seenArtifactThread: (pid: string, aid: string, tid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/threads/${tid}/seen`, { method: "POST" }).then(j<FeedbackThreadT>),
  deleteArtifactThread: (pid: string, aid: string, tid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/threads/${tid}`, { method: "DELETE" }).then(j<void>),
  artifactDownloadUrl: (pid: string, aid: string, vid: string) =>
    `/api/programs/${pid}/artifacts/${aid}/versions/${vid}/download`,
  artifactPageUrl: (pid: string, aid: string, vid: string, path: string) =>
    `/api/programs/${pid}/artifacts/${aid}/versions/${vid}/page/${path}`,
};
