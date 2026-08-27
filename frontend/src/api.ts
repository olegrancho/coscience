export interface CurrentUser { username: string; name: string; initials: string }
export interface MeResponse { user: CurrentUser | null; required: boolean }
export interface ProgramRow { id: string; title: string; status: string; goals: string }
export interface SprintRef { id: string; status: string; goals: string; title: string; results: string[]; model: string; last_status_at: number | null; votes: VoteTally }
export interface PMActivation { at: number; cycle: number; triggers: string[]; submitted: string[]; forced: boolean }
export interface Program extends ProgramRow {
  report: string; cycle: number; sprints: SprintRef[]; pm_model: string; workdir: string;
  wiki_model: string;     // model for this program's wiki runs; separate from pm_model
  wiki_enabled: boolean;  // false opts the program out of wiki ingest entirely
  wiki_merge: "auto" | "propose";  // auto = merge duplicates unattended; propose = queue for a human
  instructions: string;   // standing house rules, in every PM prompt
  max_proposed: number;   // cap on sprints awaiting review; 0 = platform default
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
  // Per-component split. `tokens` is their sum, but the components price very
  // differently — a cache read is a tenth of fresh input, output five times it —
  // so the total alone says nothing about spend. Rows recorded before the split
  // existed contribute 0 here while still counting in `tokens`.
  input_tokens: number; output_tokens: number;
  cache_creation_input_tokens: number; cache_read_input_tokens: number;
  thinking_tokens: number;
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
  paused: boolean;
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
export interface ArtifactRow { id: string; title: string; kind: string; current: string; archived: boolean; lock: ArtifactLock; version_count: number; files: string[]; excerpt: string; tags: string[] }
export interface LinkedSprint { id: string; status: string; title: string }
export interface ArtifactDetailT {
  id: string; program: string; title: string; kind: string; current: string;
  archived: boolean; lock: ArtifactLock; versions: ArtifactVersionT[]; tags: string[];
  threads: FeedbackThreadT[]; current_files: string[]; linked_sprints: LinkedSprint[];
}
export interface ArtifactFileT { name: string; size: number; content: string; binary: boolean }
export interface DirEntry { name: string; path: string }
export interface DirRoot { label: string; path: string }
export interface DirListing {
  path: string | null; parent: string | null; roots: DirRoot[]; entries: DirEntry[];
}

export type WikiTrust = "unverified" | "machine-confirmed" | "human-reviewed";
export interface WikiSummary {
  counts: Record<string, number>;
  trust: Record<WikiTrust, number>;
  pages: number; pending: number; quarantined: string[];
  // forced_by is absent on an unattended beat: only a human pressing the button
  // puts a name on the window it spends.
  run: { id: string; kind: string; forced_by?: string } | null;
  last_run: { id: string; kind: string; status: string; at: number;
              pages_created: number; pages_updated: number; notes: string;
              escaped: string[] } | null;
  ingests_since_lint: number;
  lint: Record<string, number>;
  wiki_model: string;     // model this program's wiki runs use
  wiki_enabled: boolean;  // false = the beat skips this program entirely
  wiki_merge: "auto" | "propose";
  merge_proposals: number;
  index_md: string;
}
export interface WikiMergeProposal {
  id: string; winner: string; loser: string; why: string; run: string; at: number;
}
export interface WikiRun {
  id: string; kind: string; status: string; at: number;
  pages_created?: number; pages_updated?: number;
  // New shape names the commit that performed the merge (spec 9.1/11.3); old
  // runs recorded before that were captured are still [loser, winner] pairs.
  merged: ({ loser: string; winner: string; commit?: string } | [string, string])[];
}
export interface WikiPageRow {
  path: string; slug: string; type: string; title: string;
  status: string; trust: WikiTrust; stale_after: string; tags: string[];
}
export interface WikiRelation {
  type: string; target: string; title: string; exists: boolean;
  confidence: string; source: string;
}
export interface WikiBacklink { path: string; title: string; type: string; typed: string[] }
export interface WikiSource {
  id: string; kind: "result" | "sprint" | "artifact" | "unknown";
  href: string; resource: string; title: string;
}
export interface WikiPage extends WikiPageRow {
  description: string; aliases: string[]; body: string; human_notes: string;
  verified: { by: string; at: number }[];
  relations: WikiRelation[]; backlinks: WikiBacklink[]; sources: WikiSource[];
}
export interface WikiHit {
  path: string; title: string; type: string; trust: WikiTrust;
  score: number; excerpt: string;
}
export interface WikiLintFinding {
  rule: string; severity: string; path: string; message: string;
}
export interface WikiLintReport {
  counts: Record<string, number>; findings: WikiLintFinding[];
  reports: { date: string; text: string }[];
}

/** A page address is a path ("concepts/auth-gate"), so each segment is encoded
 *  on its own — encodeURIComponent on the whole slug would turn the separator
 *  into %2F and the route would never match. */
const slugPath = (slug: string) =>
  slug.split("/").map(encodeURIComponent).join("/");

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
  createProgram: (body: { title: string; goals: string; workdir?: string }) =>
    fetch("/api/programs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Program>),
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
  setProgramWikiModel: (id: string, model: string) =>
    fetch(`/api/programs/${id}/wiki-model`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    }).then(j<{ id: string; wiki_model: string }>),
  setProgramWikiEnabled: (id: string, enabled: boolean) =>
    fetch(`/api/programs/${id}/wiki-enabled`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }).then(j<{ id: string; wiki_enabled: boolean }>),
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
  releaseChat: (id: string, tid: string) =>
    fetch(`/api/programs/${id}/chats/${tid}/release`, { method: "POST" })
      .then(j<{ thread: ChatThread; saved: Record<string, string | null> }>),
  listArtifactWorkFiles: (id: string, aid: string) =>
    fetch(`/api/programs/${id}/artifacts/${aid}/work`).then(j<string[]>),
  readArtifactWorkFile: (id: string, aid: string, name: string) =>
    fetch(`/api/programs/${id}/artifacts/${aid}/work/${name}`).then(j<ArtifactFileT>),
  artifactWorkRawUrl: (id: string, aid: string, name: string) =>
    `/api/programs/${id}/artifacts/${aid}/work-raw/${name}`,
  // A single file out of a committed version. Use for images: artifactDownloadUrl
  // zips versions holding more than one file (e.g. a figure beside its generator).
  artifactVersionRawUrl: (pid: string, aid: string, vid: string, name: string) =>
    `/api/programs/${pid}/artifacts/${aid}/versions/${vid}/raw/${name}`,
  replan: (id: string) =>
    fetch(`/api/programs/${id}/replan`, { method: "POST" }).then(
      j<{ program: string; cycle: number; submitted: string[]; skipped?: boolean; busy?: boolean; throttled?: boolean; backoff?: boolean; paused?: boolean }>),
  pmDirective: (id: string, mode: "compress" | "brainstorm") =>
    fetch(`/api/programs/${id}/ideas/${mode}`, { method: "POST" }).then(
      j<{ program: string; cycle: number; submitted: string[]; skipped?: boolean; busy?: boolean; throttled?: boolean; backoff?: boolean; paused?: boolean; ideas_added?: number; ideas_removed?: number; pool_size?: number }>),
  setProgramWorkdir: (id: string, workdir: string) =>
    fetch(`/api/programs/${id}/workdir`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workdir }),
    }).then(j<{ id: string; workdir: string; exists: boolean }>),
  setProgramGoals: (id: string, goals: string) =>
    fetch(`/api/programs/${id}/goals`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goals }),
    }).then(j<Program>),
  setProgramInstructions: (id: string, text: string) =>
    fetch(`/api/programs/${id}/instructions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j<Program>),
  setProgramMaxProposed: (id: string, n: number) =>
    fetch(`/api/programs/${id}/max_proposed`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ n }),
    }).then(j<{ id: string; max_proposed: number }>),
  listDirs: (path?: string | null) =>
    fetch(`/api/fs/dirs${path ? `?path=${encodeURIComponent(path)}` : ""}`).then(j<DirListing>),
  createDir: (parent: string, name: string) =>
    fetch("/api/fs/dirs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent, name }),
    }).then(j<{ path: string }>),
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
  sprintFileRawUrl: (id: string, name: string) =>
    `/api/sprints/${id}/file-raw/${encodeURIComponent(name)}`,
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
  setCapacity: (capacity: Record<string, number>) =>
    fetch("/api/capacity", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ capacity }),
    }).then(j<Ledger>),
  setPause: (paused: boolean) =>
    fetch("/api/pause", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused }),
    }).then(j<Ledger>),
  getUsage: () => fetch("/api/usage").then(j<Usage>),
  listArtifacts: (pid: string, includeArchived = false) =>
    fetch(`/api/programs/${pid}/artifacts${includeArchived ? "?include_archived=true" : ""}`).then(j<ArtifactRow[]>),
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
  listArtifactVersionFiles: (pid: string, aid: string, vid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/versions/${vid}/files`).then(j<string[]>),
  listArtifactTags: (pid: string) =>
    fetch(`/api/programs/${pid}/artifact-tags`).then(j<string[]>),
  setArtifactTags: (pid: string, aid: string, tags: string[]) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/tags`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags }),
    }).then(j<ArtifactDetailT>),

  getWikiSummary: (id: string) =>
    fetch(`/api/programs/${id}/wiki`).then(j<WikiSummary>),
  listWikiPages: (id: string) =>
    fetch(`/api/programs/${id}/wiki/pages`).then(j<WikiPageRow[]>),
  getWikiPage: (id: string, slug: string) =>
    fetch(`/api/programs/${id}/wiki/pages/${slugPath(slug)}`).then(j<WikiPage>),
  searchWiki: (id: string, q: string) =>
    fetch(`/api/programs/${id}/wiki/search?q=${encodeURIComponent(q)}`).then(j<WikiHit[]>),
  getWikiLog: (id: string) =>
    fetch(`/api/programs/${id}/wiki/log`).then(j<{ text: string }>),
  getWikiLint: (id: string) =>
    fetch(`/api/programs/${id}/wiki/lint`).then(j<WikiLintReport>),
  runWiki: (id: string, kind: "ingest" | "lint") =>
    fetch(`/api/programs/${id}/wiki/run`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    }).then(j<{ line: string }>),
  unquarantineWiki: (id: string) =>
    fetch(`/api/programs/${id}/wiki/unquarantine`, { method: "POST" })
      .then(j<{ cleared: string[] }>),
  verifyWikiPage: (id: string, slug: string) =>
    fetch(`/api/programs/${id}/wiki/pages/${slugPath(slug)}/verify`, { method: "POST" })
      .then(j<WikiPage>),
  setWikiPageStatus: (id: string, slug: string, status: string) =>
    fetch(`/api/programs/${id}/wiki/status/${slugPath(slug)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }).then(j<WikiPage>),
  setWikiHumanNotes: (id: string, slug: string, text: string) =>
    fetch(`/api/programs/${id}/wiki/notes/${slugPath(slug)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then(j<WikiPage>),
  deleteWikiPage: (id: string, slug: string) =>
    fetch(`/api/programs/${id}/wiki/pages/${slugPath(slug)}`, { method: "DELETE" })
      .then(j<{ deleted: string; relations_dropped: unknown[] }>),
  listWikiMerges: (id: string) =>
    fetch(`/api/programs/${id}/wiki/merges`).then(j<WikiMergeProposal[]>),
  acceptWikiMerge: (id: string, mid: string) =>
    fetch(`/api/programs/${id}/wiki/merges/${mid}/accept`, { method: "POST" })
      .then(j<{ applied: boolean; winner: string; loser: string; rewritten: string[];
               commit?: string }>),
  rejectWikiMerge: (id: string, mid: string) =>
    fetch(`/api/programs/${id}/wiki/merges/${mid}/reject`, { method: "POST" })
      .then(j<{ rejected: string[] }>),
  getWikiActivity: (id: string) =>
    fetch(`/api/programs/${id}/wiki/activity`).then(j<WikiRun[]>),
  setWikiMergePolicy: (id: string, policy: string) =>
    fetch(`/api/programs/${id}/wiki-merge-policy`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy }),
    }).then(j<{ id: string; wiki_merge: string }>),
};
