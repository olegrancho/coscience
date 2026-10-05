import { useContext, useMemo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { LinkTargetsContext } from "../sprintLinks";

/** On a program's pages, an idea id named in markdown links to that idea in the
 *  ideas list (K1), or, once the idea has become a sprint, to that sprint (K4). Ideas
 *  have random short ids, so only this program's are linked, and only on its pages.
 *  Reads the same cached query the ideas page polls. */
export default function ProgramLinks({ programId, children }:
  { programId: string; children: ReactNode }) {
  const parent = useContext(LinkTargetsContext);
  const pool = useQuery({ queryKey: ["ideas", programId], queryFn: () => api.listIdeas(programId),
                          enabled: !!programId });
  const targets = useMemo(() => {
    const m = new Map(parent);
    for (const [idea, sprint] of Object.entries(pool.data?.promoted ?? {})) m.set(idea, `/sprints/${sprint}`);
    for (const i of pool.data?.ideas ?? []) m.set(i.id, `/programs/${programId}/ideas#${i.id}`);
    return m;
  }, [parent, pool.data, programId]);
  return <LinkTargetsContext.Provider value={targets}>{children}</LinkTargetsContext.Provider>;
}
