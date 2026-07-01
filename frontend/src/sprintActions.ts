export type SprintStatus =
  | "proposed" | "approved" | "queued" | "executing" | "done" | "canceled" | "failed";

// Human lifecycle actions. `approve`/`run` are the primary (state-advancing) ones;
// the rest are secondary and live in the ⋯ overflow menu. `reject` reads as
// "Cancel" once a sprint is queued.
export type Action = "approve" | "run" | "sendBack" | "reject" | "edit" | "demote";

export function availableActions(status: SprintStatus): Action[] {
  if (status === "proposed") return ["approve", "edit", "reject", "demote"];
  if (status === "approved") return ["run", "sendBack", "edit", "reject", "demote"];
  if (status === "queued") return ["reject", "edit"];
  if (status === "executing") return ["edit"];
  return [];
}

// Which actions render as filled primary buttons (vs. the ⋯ menu).
export const PRIMARY_ACTIONS: Action[] = ["approve", "run", "sendBack"];

export interface EditableFields {
  goals: boolean; plan: boolean; priority: boolean;
  resources: boolean; preemptible: boolean;
}

export function editableFields(status: SprintStatus): EditableFields {
  const proposed = status === "proposed";
  // scheduler knobs stay editable through queued (still pre-lease) and executing
  const scheduler = proposed || status === "approved" || status === "queued" || status === "executing";
  return { goals: proposed, plan: proposed, priority: scheduler,
           resources: scheduler, preemptible: scheduler };
}
