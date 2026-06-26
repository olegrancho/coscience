export type SprintStatus = "proposed" | "approved" | "executing" | "done" | "canceled";
export type Action = "approve" | "reject" | "edit";

export function availableActions(status: SprintStatus): Action[] {
  if (status === "proposed") return ["approve", "reject", "edit"];
  if (status === "approved" || status === "executing") return ["edit"];
  return [];
}

export interface EditableFields {
  goals: boolean; plan: boolean; priority: boolean;
  resources: boolean; preemptible: boolean;
}

export function editableFields(status: SprintStatus): EditableFields {
  const proposed = status === "proposed";
  const scheduler = proposed || status === "approved" || status === "executing";
  return { goals: proposed, plan: proposed, priority: scheduler,
           resources: scheduler, preemptible: scheduler };
}
