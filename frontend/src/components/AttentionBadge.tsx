import { Badge } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";

/** Header pill: how many sprints are waiting on a human to answer an
 *  escalation (§8.5). Polls independently of whatever page is open so it
 *  surfaces the moment something needs a person, and links straight to the
 *  oldest one waiting. Renders nothing when the queue is empty. */
export default function AttentionBadge() {
  const attention = useQuery({
    queryKey: ["attention"],
    queryFn: api.getAttention,
    refetchInterval: 10_000,
  });
  const rows = attention.data?.escalated_to_human ?? [];
  if (rows.length === 0) return null;
  return (
    <Badge component={Link} to={`/sprints/${rows[0].sprint_id}`}
      color="signal" variant="filled" size="lg"
      style={{ cursor: "pointer", textDecoration: "none" }}>
      {rows.length} need you
    </Badge>
  );
}
