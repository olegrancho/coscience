"""The dispatcher: a single heartbeat that schedules many sprints over the ledger."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from coscience.host_probe import ssh_argv
from coscience.ledger import Ledger
from coscience.models import BeatOutcome, ProgramStatus, SprintStatus, set_status
from coscience.pause import is_paused
from coscience.resources import LOCAL, WORKER_KEY, ResourcePool, effective_requirement, gpu_request, over_capacity
from coscience.scheduler import SchedulerPolicy
from coscience.substrate import Substrate
from coscience.worker import MAX_AGENT_FAILURES, Worker

from coscience import artifacts, host_health, wiki

_ELIGIBLE = (SprintStatus.QUEUED, SprintStatus.EXECUTING, SprintStatus.HIBERNATED)


@dataclass
class CycleReport:
    granted: int = 0
    hibernated: int = 0
    beaten: int = 0
    completed: int = 0
    waiting: int = 0                  # leaseless sprints that could be granted once room frees
    unrunnable: list[str] = field(default_factory=list)   # asking for more than the pool's total
    reconciled: int = 0
    wiki: list[str] = field(default_factory=list)   # non-empty wiki beat lines this cycle
    beat_errors: list[str] = field(default_factory=list)   # sprints whose beat raised
    failed: int = 0                    # sprints FAILed by the beat-failure cap this cycle


class _WorkerSlots:
    """The worker slot, lent to the Worker so a sleeping sprint can hand it back.

    `effective_requirement` charges one slot per sprint to bound how many agent
    processes run at once, but a sprint asleep on a detached job runs none — and
    used to hold the slot for the job's whole duration anyway. A pool that
    declares no `workers` cap is uncapped, so this is a no-op there."""

    def __init__(self, ledger: Ledger, repo_root=None):
        self.ledger = ledger
        self.repo_root = repo_root

    def _capped(self) -> bool:
        return WORKER_KEY in self.ledger.pool.capacity

    def release(self, sprint_id: str) -> None:
        if self._capped():
            self.ledger.release_key(sprint_id, WORKER_KEY)

    def acquire(self, sprint_id: str) -> bool:
        if not self._capped():
            return True
        return self.ledger.acquire_key(sprint_id, WORKER_KEY, 1.0)

    def gpus(self, sprint_id: str) -> tuple[list[int], float | None]:
        """The cards this sprint's lease holds and the VRAM share on each (None =
        whole cards), for the worker agent's instructions."""
        lease = self.ledger.lease_for(sprint_id)
        if lease is None:
            return [], None
        return list(lease.gpu_devices), gpu_request(lease.amounts)[1]

    def ssh_for(self, host_name: str) -> str:
        host = self.ledger.pool.host(host_name)
        if host is None or not host.ssh:
            return ""
        try:
            ssh_argv(host.ssh)
        except ValueError:
            # A bad ssh value should have been caught when the pool was parsed
            # (Fix C), but this is the last line of defence: "" makes the job
            # "unknown" to the caller, never a crash inside a beat.
            return ""
        return host.ssh

    def host(self, sprint_id: str) -> dict:
        """Where this sprint's lease is, for the worker agent's instructions: the host's
        name, ssh target, run root, a line of probed facts and its notes."""
        lease = self.ledger.lease_for(sprint_id)
        if lease is None or lease.host == LOCAL:
            return {"name": LOCAL, "ssh": "", "run_root": "", "facts": "", "notes": ""}
        host = self.ledger.pool.host(lease.host)
        if host is None:
            # The lease names a host the pool no longer has (removed since the
            # grant): keep its name so progress.host isn't overwritten with
            # "local" — the caller still needs to know work is stranded there.
            return {"name": lease.host, "ssh": "", "run_root": "", "facts": "", "notes": ""}
        return {"name": host.name, "ssh": host.ssh, "run_root": host.run_root,
                "facts": _probe_facts_line(self.repo_root, host.name), "notes": host.notes}


def _probe_facts_line(repo_root, host_name: str) -> str:
    """"OS · CPU · N threads · N GB memory" from the host's last onboarding probe, or ""."""
    if repo_root is None:
        return ""
    path = Path(repo_root) / ".coscience" / "host-probes" / f"{host_name}.json"
    try:
        facts = json.loads(path.read_text()).get("facts") or {}
    except (OSError, ValueError, AttributeError):
        return ""
    parts = [str(facts[k]) for k in ("os", "cpu_model") if facts.get(k)]
    if facts.get("threads"):
        parts.append(f"{facts['threads']} threads")
    if facts.get("mem_total_kb"):
        parts.append(f"{round(facts['mem_total_kb'] / 1024 / 1024)} GB memory")
    return " · ".join(parts)


class Dispatcher:
    def __init__(self, substrate: Substrate, agent,
                 pool: ResourcePool, policy: SchedulerPolicy | None = None,
                 usage_gate=None, wiki_agent=None, host_runner=None):
        self.substrate = substrate
        self.agent = agent
        self.policy = policy or SchedulerPolicy()
        self._usage_gate = usage_gate
        cos = substrate.repo_root / ".coscience"
        self.ledger = Ledger(pool, cos / "leases.json")
        self.worker = Worker(substrate, agent, usage_gate=usage_gate,
                             slots=_WorkerSlots(self.ledger, repo_root=substrate.repo_root))
        self._queue_path = cos / "queue.json"
        self._wiki_agent = wiki_agent      # None -> built lazily on first use
        self._host_runner = host_runner

    def _load_queue(self) -> dict[str, float]:
        if self._queue_path.is_file():
            return {str(k): float(v) for k, v in json.loads(self._queue_path.read_text()).items()}
        return {}

    def _save_queue(self, queue: dict[str, float]) -> None:
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._queue_path.with_name(self._queue_path.name + ".tmp")
        tmp.write_text(json.dumps(queue, indent=2))
        tmp.replace(self._queue_path)

    def run_one_cycle(self, now: float | None = None) -> CycleReport:
        now = time.time() if now is None else float(now)
        report = CycleReport()
        ttl = self.policy.default_ttl

        self.ledger.load()
        self.ledger.expire(now)

        # Which remote hosts answer. A quiet one takes no new grants this cycle; its
        # leases, jobs and liveness are untouched (spec §7).
        entries = host_health.check(self.substrate.repo_root, self.ledger.pool, now,
                                    runner=self._host_runner)
        self.ledger.pool.closed = {name: "quiet" for name in host_health.quiet(entries, now)}

        eligible = self.substrate.iter_sprints()
        eligible = [s for s in eligible if s.status in _ELIGIBLE]
        eligible_ids = {s.id for s in eligible}

        queue = self._load_queue()
        for s in eligible:
            queue.setdefault(s.id, now)
        queue = {k: v for k, v in queue.items() if k in eligible_ids}

        # --- grants ---
        # A sprint bound to artifacts is grantable only when none of its bound
        # artifacts is locked by another holder (the artifact is a capacity-1
        # resource). Filter those out before the pool scheduler runs.
        # Pause and usage exhaustion both suppress new grants, letting in-flight
        # work drain to completion via the beat step below.
        # The exception is LIVENESS: re-adopt a sprint whose agent/job is still
        # physically running but lost its lease (dispatcher outage past the TTL).
        # Without re-adoption, reconcile below kills the live process — wasting
        # the work it already did.
        paused = is_paused(self.substrate.repo_root)
        usage_ok = self._usage_gate() if self._usage_gate else True
        blocked = paused or not usage_ok
        needs = [s for s in eligible if self.ledger.lease_for(s.id) is None
                 and not artifacts.sprint_blocked(self.substrate, s)
                 and (not blocked or self.worker.agent_running(s.id)
                      or self.substrate.load_progress(s.id).job_token)]
        # A sprint that has launched anything stays on that host: its files, and any
        # job still running, are there.
        pinned = {s.id: host for s in needs if (host := self.substrate.load_progress(s.id).host)}
        # LIVENESS re-adoption (below) must not be blocked by a drained or quiet
        # host: a physically running agent or job is not new work, and killing it
        # because its host stopped answering health checks would violate spec §7.
        readopt = {s.id for s in needs if self.worker.agent_running(s.id)
                  or self.substrate.load_progress(s.id).job_token}
        for sprint in self.policy.select_grants(needs, queue, self.ledger, now, pinned=pinned,
                                                readopt=readopt):
            eff = self.policy.effective_priority(sprint, queue.get(sprint.id, now), now)
            progress = self.substrate.load_progress(sprint.id)
            live = self.worker.agent_running(sprint.id) or bool(progress.job_token)
            # A sprint re-adopted with its agent or job still running keeps the cards
            # that work is already using; fresh placement could put a second job on them.
            if self.ledger.acquire(sprint.id,
                                   effective_requirement(sprint.resources_required,
                                                         self.ledger.pool),
                                   now, ttl,
                                   priority=eff, preemptible=sprint.preemptible,
                                   program=sprint.program,
                                   prefer_cards=progress.gpu_devices if live else (),
                                   host=pinned.get(sprint.id),
                                   readopt=sprint.id in readopt):
                # Acquire the sprint's artifact locks (instantiating create-targets).
                # If a same-cycle race lost the atomic acquire, give the lease back
                # and leave the sprint queued for a later cycle.
                if not artifacts.acquire_for_sprint(self.substrate, sprint, now):
                    self.ledger.release(sprint.id)
                    continue
                report.granted += 1
                if sprint.status in (SprintStatus.QUEUED, SprintStatus.HIBERNATED):
                    set_status(sprint, SprintStatus.EXECUTING)
                    self.substrate.save_sprint(sprint)

        # --- refresh lease priorities ---
        # Aging increments effective priority over time; the lease stores a snapshot
        # from the last renew. Refresh it here so the yield step below compares
        # current values, not stale ones from a previous cycle.
        eligible_by_id = {s.id: s for s in eligible}
        for lease in self.ledger.all_leases():
            s = eligible_by_id.get(lease.sprint_id)
            if s is not None:
                eff = self.policy.effective_priority(s, queue.get(s.id, now), now)
                if lease.priority != eff:
                    lease.priority = eff

        # --- yield: hibernate a safe-point sprint to free a starved QUEUED candidate ---
        # Cooperative preemption: never hard-kill. Only a QUEUED candidate can
        # trigger a yield (hibernated sprints re-enter from free capacity only, so
        # there is no hibernate ping-pong). Victims are chosen only among leases at
        # a safe yield point (no running agent, no live job); the freed capacity is
        # granted on the NEXT cycle's grant step.
        starved = [s for s in eligible
                   if s.status == SprintStatus.QUEUED and self.ledger.lease_for(s.id) is None]
        if starved:
            starved.sort(
                key=lambda s: -self.policy.effective_priority(s, queue.get(s.id, now), now))
            cand = starved[0]
            cand_eff = self.policy.effective_priority(cand, queue.get(cand.id, now), now)
            yieldable = {l.sprint_id for l in self.ledger.all_leases()
                         if self.worker.is_yieldable(l.sprint_id)}
            victims = self.policy.select_yield_victims(
                cand, cand_eff, self.ledger, yieldable,
                pinned_host=self.substrate.load_progress(cand.id).host or None)
            for v in victims:
                self.ledger.release(v.sprint_id)
                self.worker.hibernate_sprint(self.substrate.load_sprint(v.sprint_id))
                report.hibernated += 1

        # --- reconcile: no lease => no running job ---
        # Grants/preemption above re-adopted any leaseless running sprint that
        # still fits; kill the detached jobs of those that remain leaseless
        # (e.g. expired across a dispatcher outage) so physical use matches the
        # ledger.
        for sprint in eligible:
            if sprint.status != SprintStatus.EXECUTING:
                continue                          # hibernated: intentionally leaseless, no agent/job
            if self.ledger.lease_for(sprint.id) is None:
                # A sleeping sprint (no live agent, but a tracked detached job) must
                # also be reaped when leaseless — else its job runs with no lease.
                if self.worker.agent_running(sprint.id) \
                        or self.substrate.load_progress(sprint.id).job_token:
                    self.worker.stop_sprint(sprint)
                    report.reconciled += 1

        # --- run one beat per leased, executing sprint ---
        for lease in self.ledger.all_leases():
            sprint = self.substrate.load_sprint(lease.sprint_id)
            if sprint.status != SprintStatus.EXECUTING:
                continue
            gave_up = False
            try:
                outcome = self.worker.run_sprint_beat(sprint)
            except Exception as exc:          # one sprint's fault must not stall every other sprint
                progress = self.substrate.load_progress(sprint.id)
                progress.last_error = f"beat failed: {type(exc).__name__}: {exc}"
                progress.beat_failures += 1
                report.beat_errors.append(sprint.id)
                outcome = None
                if progress.beat_failures >= MAX_AGENT_FAILURES:
                    # A beat that never succeeds must not hold its lease forever,
                    # renewed every cycle: stop it, fail it, and let the lease go.
                    gave_up = True
                    self.substrate.save_progress(progress)
                    try:
                        self.worker.stop_sprint(sprint)
                    except Exception as stop_exc:
                        # A stop that itself raises (e.g. an unreachable remote host)
                        # must not abort the whole cycle — note it and carry on failing
                        # the sprint; a human can still see the job may be orphaned.
                        progress.last_error += f" (stopping it also failed: {stop_exc})"
                        self.substrate.save_progress(progress)
                    # Matches the worker's own FAILED path (worker.py, MAX_AGENT_FAILURES
                    # cap on agent failures): release the sprint's artifact locks before
                    # it goes terminal, or every other sprint bound to them stays blocked.
                    artifacts.release_for_sprint(self.substrate, sprint, now)
                    set_status(sprint, SprintStatus.FAILED)
                    self.substrate.save_sprint(sprint)
                    self.ledger.release(lease.sprint_id)
                    queue.pop(lease.sprint_id, None)
                    report.failed += 1
                else:
                    self.substrate.save_progress(progress)
            else:
                # A beat that returns normally clears a lingering beat-failure state
                # from an earlier transient exception — one bad cycle must not show
                # forever once the sprint is beating cleanly again.
                progress = self.substrate.load_progress(sprint.id)
                if progress.beat_failures or progress.last_error.startswith("beat failed:"):
                    progress.beat_failures = 0
                    if progress.last_error.startswith("beat failed:"):
                        progress.last_error = ""
                    self.substrate.save_progress(progress)

            report.beaten += 1
            if gave_up:
                continue          # do not renew a lease that was just released
            eff = self.policy.effective_priority(sprint, queue.get(sprint.id, now), now)
            self.ledger.renew(lease.sprint_id, now, ttl, priority=eff)
            if outcome == BeatOutcome.COMPLETED:
                self.ledger.release(lease.sprint_id)
                queue.pop(lease.sprint_id, None)
                report.completed += 1

        # A request above the pool's total is not waiting — no amount of waiting grants
        # it — so it is named separately instead of hiding inside the waiting count.
        for s in eligible:
            if self.ledger.lease_for(s.id) is not None:
                continue
            if over_capacity(s.resources_required, self.ledger.pool, s.program,
                             only_host=self.substrate.load_progress(s.id).host or None):
                report.unrunnable.append(s.id)
            else:
                report.waiting += 1

        # Release chat locks left idle past the inactivity window (cuts a final
        # version), so a walked-away editing session frees the artifact.
        reaped = 0
        for program in self.substrate.iter_programs():
            # Before the reaper: a collected turn is no longer busy, so its lock
            # can be judged on idleness like any other.
            self._collect_chats(program.id)
            reaped += len(artifacts.reap_stale_chat_locks(
                self.substrate, program.id, now, holder_busy=self._chat_busy(program.id)))
            if program.status == ProgramStatus.ACTIVE:
                line = self._wiki_beat(program, now)
                if line:
                    report.wiki.append(line)

        self._save_queue(queue)
        if (report.granted or report.completed or report.hibernated
                or report.reconciled or reaped or report.wiki or report.failed):
            self.substrate.commit("dispatch cycle")
        return report

    def _wiki_beat(self, program, now: float) -> str:
        """Wiki maintenance is the lowest-priority thing this loop does, so it is
        also the thing least allowed to break it: a wiki failure is reported as a
        line, never raised into sprint supervision."""
        try:
            if self._wiki_agent is None:
                from coscience.wiki_agent import WikiAgent
                self._wiki_agent = WikiAgent()
            return wiki.beat(self.substrate, program, now, self._wiki_agent)
        except Exception as exc:
            return f"wiki: error — {exc}"[:200]

    def _collect_chats(self, program_id: str) -> None:
        """Collect finished chat turns without waiting for someone to open the thread,
        so every Claude call gets its end within a cycle of finishing. Like the wiki
        beat, never allowed to break sprint supervision."""
        from coscience import chat_agent
        try:
            for thread in self.substrate.list_chat_threads(program_id):
                if thread.pending:
                    chat_agent.collect_thread(self.substrate, program_id, thread)
        except Exception:
            pass

    def _chat_busy(self, program_id: str):
        """Predicate for the reaper: a lock holder id 'chat:<tid>' is 'busy' (protect
        it) if that chat currently has a turn in flight (pending)."""
        def busy(holder_id: str) -> bool:
            if not holder_id.startswith("chat:"):
                return False
            t = self.substrate.load_chat_thread(program_id, holder_id[len("chat:"):])
            return bool(t and t.pending)
        return busy
