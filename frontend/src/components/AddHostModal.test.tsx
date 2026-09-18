import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: {
    probeHost: vi.fn(), confirmHost: vi.fn().mockResolvedValue({}),
    updateHost: vi.fn().mockResolvedValue({}), detectLocal: vi.fn(),
    setCapacity: vi.fn().mockResolvedValue({}),
    setHostPrograms: vi.fn().mockResolvedValue({}),
    listPrograms: vi.fn().mockResolvedValue([{ id: "p2" }, { id: "p4" }, { id: "p5" }]),
    // SurveyPanel's own query/mutation — given a default in this file's
    // beforeEach so it renders quietly under the probe result block.
    getSurvey: vi.fn(), surveyHost: vi.fn(),
  },
}));

vi.mock("@mantine/notifications", () => ({
  notifications: { show: vi.fn() },
}));

import { api } from "../api";
import { notifications } from "@mantine/notifications";
import type { HostSurvey, LedgerHost, SurveyProposal } from "../api";
import AddHostModal from "./AddHostModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

// The programs `listPrograms` resolves to in every test here — the default
// list every "add" and "no list yet" seed ticks.
const ALL_PROGRAM_IDS = ["p2", "p4", "p5"];

/** Waits until the ProgramAccessInput has finished seeding: every id in
 *  `ids` shows up as a ticked pill. Needed before reading or acting on the
 *  program list — it seeds asynchronously, once `listPrograms` resolves. */
async function awaitProgramsSeeded(ids: string[] = ALL_PROGRAM_IDS) {
  await waitFor(() => {
    const labels = [...document.querySelectorAll(".mantine-Pill-label")].map((el) => el.textContent);
    ids.forEach((id) => expect(labels).toContain(id));
  });
}

/** Opens the ProgramAccessInput dropdown and clicks one program's option. */
async function pickProgram(id: string) {
  fireEvent.click(screen.getByRole("textbox", { name: /Programs this server runs/ }));
  fireEvent.click(await screen.findByRole("option", { name: id }));
}

/** Unticks one program by clicking its pill's remove button. */
function untickProgram(id: string) {
  const label = screen.getAllByText(id, { selector: ".mantine-Pill-label" })[0];
  const pill = label.closest(".mantine-Pill-root") as HTMLElement;
  fireEvent.click(pill.querySelector("button")!);
}

const OK_PROBE = {
  name: "gpu1", ok: true, error: "",
  facts: { os: "Example Linux 9", cpu_model: "Example CPU", threads: 12, mem_total_kb: 65000000 },
  probed_at: 1,
  declared: { ssh: "gpu1", run_root: "~/coscience-runs", shared: false, programs: [], owner: "", notes: "" },
  checks: [{ name: "rsync both ways", ok: true, detail: "" }],
  warnings: ["glibc 2.17 is old: many current Python wheels and binaries will not run"],
  proposal: { capacity: { cpu: 12, memory_gb: 55 }, gpus: [{ model: "X", vram_gb: 10.8 }] },
};

const REMOTE: LedgerHost = {
  name: "gpu1", ssh: "gpu1", placeable: true, programs: ["p2"], run_root: "~/coscience-runs",
  capacity: { cpu: 10, memory_gb: 50 }, available: {},
  gpus: [{ index: 0, model: "X", vram_gb: 10.8, whole: false, shared_gb: 0 }],
  shared: false, owner: "alice", notes: "business hours only",
  drain: false, removing: false, waiting_on: [], used: {},
};

const LOCAL: LedgerHost = {
  // programs: null — this machine's list has never been set, so the dialog
  // seeds every program ticked.
  name: "local", ssh: "", placeable: true, programs: null, run_root: "",
  capacity: { cpu: 24, gpu: 1 }, available: {},
  gpus: [{ index: 0, model: "", vram_gb: null, whole: true, shared_gb: 0 }],
  drain: false, removing: false, waiting_on: [], used: {},
};

const SURVEY_NOT_STARTED: HostSurvey = {
  name: "", started: false, pending: false, messages: [], proposal: null, proposal_error: "",
};

const SURVEY_PROPOSAL: SurveyProposal = {
  capacity: { cpu: 6, memory_gb: 40 }, gpus: [{ model: "A100", vram_gb: 80 }],
  notes: "agent-verified", overrides: [{ check: "rsync both ways", reason: "installed and verified by hand" }],
};

function surveyWithProposal(proposal: SurveyProposal): HostSurvey {
  return { name: "gpu1", started: true, pending: false, messages: [], proposal, proposal_error: "" };
}

const OK_DETECT = {
  ok: true, error: "",
  facts: { os: "Example Linux 9", cpu_model: "Example CPU", threads: 32, mem_total_kb: 58720256 },
  warnings: ["glibc 2.17 is old: many current Python wheels and binaries will not run"],
  proposal: { capacity: { cpu: 24, memory_gb: 56 }, gpus: [{ model: "NVIDIA GeForce RTX 4090", vram_gb: 24 }] },
};

interface ModalProps {
  opened?: boolean; onClose?: () => void;
  host?: LedgerHost; local?: boolean; localCapacity?: Record<string, number>;
}

function renderModal(props: ModalProps = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <AddHostModal opened onClose={() => {}} {...props} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function declare() {
  fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "gpu1" } });
  fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu1" } });
}

describe("AddHostModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // SurveyPanel's own query; individual tests override this to exercise
    // the agent's proposal / overrides.
    vi.mocked(api.getSurvey).mockResolvedValue(SURVEY_NOT_STARTED);
  });

  describe("edit mode", () => {
    it("prefills and updates values without a probe", async () => {
      const onClose = vi.fn();
      renderModal({ host: REMOTE, onClose });
      expect(screen.getByText("Configure gpu1")).toBeTruthy();
      const nameInput = screen.getByLabelText(/^Name/) as HTMLInputElement;
      expect(nameInput.value).toBe("gpu1");
      expect(nameInput.disabled).toBe(true);
      expect((screen.getByLabelText(/^SSH target/) as HTMLInputElement).value).toBe("gpu1");
      expect((screen.getByLabelText(/^Run root/) as HTMLInputElement).value).toBe("~/coscience-runs");
      await awaitProgramsSeeded(["p2"]);
      expect((screen.getByLabelText(/^Owner/) as HTMLInputElement).value).toBe("alice");
      expect((screen.getByLabelText(/^Notes/) as HTMLInputElement).value).toBe("business hours only");
      expect((screen.getByLabelText("CPU cores") as HTMLInputElement).value).toBe("10");
      expect((screen.getByLabelText("Memory (GB)") as HTMLInputElement).value).toBe("50");
      expect((screen.getByLabelText("GPU 1 VRAM (GB)") as HTMLInputElement).value).toBe("10.8");

      fireEvent.change(screen.getByLabelText(/^Notes/), { target: { value: "24/7 now" } });
      fireEvent.change(screen.getByLabelText("CPU cores"), { target: { value: "12" } });
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      expect(api.updateHost).toHaveBeenCalledWith("gpu1", expect.objectContaining({
        notes: "24/7 now", capacity: { cpu: 12, memory_gb: 50 }, programs: ["p2"],
      }));
      expect(onClose).toHaveBeenCalled();
    });

    it("needs a probe once the SSH target changes", async () => {
      renderModal({ host: REMOTE });
      fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu2" } });
      expect((screen.getByRole("button", { name: "Update configuration" }) as HTMLButtonElement).disabled).toBe(true);
      expect(screen.getByText("Probe the new SSH target before updating")).toBeTruthy();

      vi.mocked(api.probeHost).mockResolvedValue({ ...OK_PROBE, name: "gpu2" } as never);
      fireEvent.click(screen.getByRole("button", { name: "Re-probe" }));
      await waitFor(() => expect(api.probeHost).toHaveBeenCalled());
      expect(api.probeHost).toHaveBeenCalledWith(expect.objectContaining({ ssh: "gpu2" }));
      await waitFor(() =>
        expect((screen.getByRole("button", { name: "Update configuration" }) as HTMLButtonElement).disabled).toBe(false));
      expect(screen.queryByText("Probe the new SSH target before updating")).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      expect(api.updateHost).toHaveBeenCalledWith("gpu1", expect.objectContaining({ ssh: "gpu2", probed_at: 1 }));
    });

    it("shows checks and facts on re-probe, and a probe error as the error text", async () => {
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      renderModal({ host: REMOTE });
      fireEvent.click(screen.getByRole("button", { name: "Re-probe" }));
      expect(await screen.findByText(/rsync both ways/)).toBeTruthy();
      expect(screen.getByText("Example Linux 9 · Example CPU · 12 threads · 62 GB memory")).toBeTruthy();

      vi.mocked(api.probeHost).mockRejectedValue(
        new Error("403 probing gpu1 is off: onboarding is disabled on this deployment"));
      fireEvent.click(screen.getByRole("button", { name: "Re-probe" }));
      expect(await screen.findByText(/onboarding is disabled/)).toBeTruthy();
    });

    it("says when lowering below what's in use", async () => {
      const busy = { ...REMOTE, used: { cpu: 8 } };
      renderModal({ host: busy });
      fireEvent.change(screen.getByLabelText("CPU cores"), { target: { value: "4" } });
      expect(screen.getByText(
        "8 CPU cores in use — lowering below that lets running work finish and blocks new grants.",
      )).toBeTruthy();
    });

    it("never echoes the derived gpu count back in capacity", async () => {
      const withGpuCount = { ...REMOTE, capacity: { ...REMOTE.capacity, gpu: 1 } };
      renderModal({ host: withGpuCount });
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      const [, body] = vi.mocked(api.updateHost).mock.calls[0] as [string, { capacity: Record<string, number> }];
      expect(body.capacity).toEqual({ cpu: 10, memory_gb: 50 });
    });

    it("blocks Update on a half-filled card row and unblocks once it's whole", async () => {
      const noCard = { ...REMOTE, gpus: [] };
      renderModal({ host: noCard, onClose: vi.fn() });
      fireEvent.click(screen.getByRole("button", { name: "+ add card" }));
      fireEvent.change(screen.getByLabelText("GPU 1 model"), { target: { value: "A100" } });
      expect((screen.getByRole("button", { name: "Update configuration" }) as HTMLButtonElement).disabled).toBe(true);
      expect(screen.getByText("Every GPU card needs a model and its VRAM (GB)")).toBeTruthy();

      fireEvent.change(screen.getByLabelText("GPU 1 VRAM (GB)"), { target: { value: "80" } });
      expect((screen.getByRole("button", { name: "Update configuration" }) as HTMLButtonElement).disabled).toBe(false);
      expect(screen.queryByText("Every GPU card needs a model and its VRAM (GB)")).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      expect(api.updateHost).toHaveBeenCalledWith("gpu1", expect.objectContaining({
        gpus: [{ model: "A100", vram_gb: 80 }],
      }));
    });

    it("only invalidates a probe when the SSH target or run root changes", async () => {
      vi.mocked(api.probeHost).mockResolvedValue({ ...OK_PROBE, name: "gpu2" } as never);
      renderModal({ host: REMOTE });
      fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu2" } });
      fireEvent.click(screen.getByRole("button", { name: "Re-probe" }));
      await waitFor(() =>
        expect((screen.getByRole("button", { name: "Update configuration" }) as HTMLButtonElement).disabled).toBe(false));

      fireEvent.change(screen.getByLabelText(/^Notes/), { target: { value: "swap day tomorrow" } });
      expect((screen.getByRole("button", { name: "Update configuration" }) as HTMLButtonElement).disabled).toBe(false);
      expect(screen.queryByText("Probe the new SSH target before updating")).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      expect(api.updateHost).toHaveBeenCalledWith("gpu1", expect.objectContaining({
        ssh: "gpu2", notes: "swap day tomorrow",
      }));
    });

    it("shows every program ticked for a server with no list, and sends them all on Update", async () => {
      const noList = { ...REMOTE, programs: null };
      renderModal({ host: noList });
      await awaitProgramsSeeded(ALL_PROGRAM_IDS);
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      expect(api.updateHost).toHaveBeenCalledWith("gpu1", expect.objectContaining({
        programs: ALL_PROGRAM_IDS,
      }));
    });

    it("sends an empty list once every program is unticked", async () => {
      renderModal({ host: REMOTE });
      await awaitProgramsSeeded(["p2"]);
      fireEvent.click(screen.getByRole("button", { name: "clear" }));
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      expect(api.updateHost).toHaveBeenCalledWith("gpu1", expect.objectContaining({ programs: [] }));
    });

    it("raises a notification when the update result cuts off pinned work", async () => {
      vi.mocked(api.updateHost).mockResolvedValue({ cut_off: [{ sprint_id: "s1", host: "gpu1" }] } as never);
      renderModal({ host: REMOTE });
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(notifications.show).toHaveBeenCalledWith(expect.objectContaining({
        color: "yellow", title: "Pinned work is cut off",
        message: "s1 is pinned to gpu1. It keeps its work there and waits until the program is allowed back on that server or the sprint is stopped.",
      })));
    });

    it("leaves Update enabled on a failed check when the SSH target and run root are unchanged (O10 rule)", async () => {
      const failingProbe = {
        ...OK_PROBE, checks: [{ name: "rsync both ways", ok: false, detail: "rsync not found" }],
      };
      vi.mocked(api.probeHost).mockResolvedValue(failingProbe as never);
      renderModal({ host: REMOTE });
      // Re-probed the same target just to look — SSH and run root untouched.
      fireEvent.click(screen.getByRole("button", { name: "Re-probe" }));
      expect(await screen.findByText(/rsync not found/)).toBeTruthy();
      expect(screen.queryByText(/Fix the failed checks/)).toBeNull();
      expect((screen.getByRole("button", { name: "Update configuration" }) as HTMLButtonElement).disabled).toBe(false);

      fireEvent.change(screen.getByLabelText(/^Notes/), { target: { value: "still fine" } });
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      expect(api.updateHost).toHaveBeenCalledWith("gpu1", expect.objectContaining({ notes: "still fine" }));
      expect(api.updateHost).not.toHaveBeenCalledWith("gpu1", expect.objectContaining({ accept_overrides: true }));
    });

    it("blocks Update when the changed SSH target's probe has failed checks and no proposal covers them", async () => {
      const failingProbe = {
        ...OK_PROBE, name: "gpu2", checks: [{ name: "rsync both ways", ok: false, detail: "rsync not found" }],
      };
      vi.mocked(api.probeHost).mockResolvedValue(failingProbe as never);
      renderModal({ host: REMOTE });
      fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu2" } });
      fireEvent.click(screen.getByRole("button", { name: "Re-probe" }));
      expect(await screen.findByText(/rsync not found/)).toBeTruthy();
      expect(screen.getByText("Probe the new SSH target before updating")).toBeTruthy();
      expect((screen.getByRole("button", { name: "Update configuration" }) as HTMLButtonElement).disabled).toBe(true);
      expect(screen.queryByRole("button", { name: "Update with the agent's overrides" })).toBeNull();
    });

    it("unblocks a changed SSH target's failed checks with 'Update with the agent's overrides' once the proposal covers them", async () => {
      const failingProbe = {
        ...OK_PROBE, name: "gpu2", checks: [{ name: "rsync both ways", ok: false, detail: "rsync not found" }],
      };
      vi.mocked(api.probeHost).mockResolvedValue(failingProbe as never);
      vi.mocked(api.getSurvey).mockResolvedValue(surveyWithProposal(SURVEY_PROPOSAL));
      vi.mocked(api.updateHost).mockResolvedValue({} as never);
      renderModal({ host: REMOTE });
      fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu2" } });
      fireEvent.click(screen.getByRole("button", { name: "Re-probe" }));
      expect(await screen.findByText(/rsync not found/)).toBeTruthy();
      expect(screen.getByText("Probe the new SSH target before updating")).toBeTruthy();
      expect((screen.getByRole("button", { name: "Update configuration" }) as HTMLButtonElement).disabled).toBe(true);

      fireEvent.click(await screen.findByRole("button", { name: "Use proposal" }));
      expect(screen.queryByText("Probe the new SSH target before updating")).toBeNull();
      expect(screen.getByText(
        "Failed checks will be accepted on the agent's written reasons shown above.",
      )).toBeTruthy();
      // The override itself is shown once, inside SurveyPanel's own proposal block.
      expect(screen.getAllByText("Override rsync both ways: installed and verified by hand").length).toBe(1);
      const updateButton = screen.getByRole("button", { name: "Update with the agent's overrides" });
      expect((updateButton as HTMLButtonElement).disabled).toBe(false);

      fireEvent.click(updateButton);
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      expect(api.updateHost).toHaveBeenCalledWith("gpu1", expect.objectContaining({ accept_overrides: true }));
    });

    it("keeps existing 'Override …' notes lines and puts the agent's fresh notes before them", async () => {
      // A plain TextInput strips embedded newlines from its own displayed
      // value (real HTML behavior, not a test artifact) — so this asserts on
      // the request body, which carries the join React state actually holds.
      const withOverrideNote = {
        ...REMOTE, notes: "swap day tomorrow\nOverride disk space: verified manually by ops",
      };
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      vi.mocked(api.getSurvey).mockResolvedValue(surveyWithProposal(SURVEY_PROPOSAL));
      vi.mocked(api.updateHost).mockResolvedValue({} as never);
      renderModal({ host: withOverrideNote });

      fireEvent.click(screen.getByRole("button", { name: "Re-probe" }));
      fireEvent.click(await screen.findByRole("button", { name: "Use proposal" }));
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      expect(api.updateHost).toHaveBeenCalledWith("gpu1", expect.objectContaining({
        notes: "agent-verified\nOverride disk space: verified manually by ops",
      }));
    });
  });

  describe("local mode", () => {
    const localCapacity = { cpu: 24, gpu: 1, workers: 3, housekeepers: 2 };

    it("fills cards from Detect without silently raising CPU or declaring memory", async () => {
      vi.mocked(api.detectLocal).mockResolvedValue(OK_DETECT as never);
      renderModal({ local: true, host: LOCAL, localCapacity });
      expect(screen.getByText("Configure this machine")).toBeTruthy();
      expect(screen.queryByLabelText(/^SSH target/)).toBeNull();
      expect(screen.queryByLabelText(/^Run root/)).toBeNull();
      expect(screen.queryByRole("button", { name: "Probe" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Re-probe" })).toBeNull();
      // Before Detect: CPU seeded from localCapacity, memory empty (not declared).
      expect((screen.getByLabelText("CPU cores") as HTMLInputElement).value).toBe("24");
      expect((screen.getByLabelText("Memory (GB)") as HTMLInputElement).value).toBe("");
      expect(screen.getByText(
        "Declaring memory lets sprints request memory on this machine.",
      )).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: "Detect" }));
      await waitFor(() => expect(api.detectLocal).toHaveBeenCalled());
      expect((await screen.findByLabelText("GPU 1 model") as HTMLInputElement).value).toBe("NVIDIA GeForce RTX 4090");
      expect((screen.getByLabelText("GPU 1 VRAM (GB)") as HTMLInputElement).value).toBe("24");
      // Detect fills the cards but does not touch CPU/memory.
      expect((screen.getByLabelText("CPU cores") as HTMLInputElement).value).toBe("24");
      expect((screen.getByLabelText("Memory (GB)") as HTMLInputElement).value).toBe("");
      expect(screen.getByText("Detected: 32 threads")).toBeTruthy();
      expect(screen.getByText("Detected: 56 GB")).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: "Use detected CPU cores" }));
      expect((screen.getByLabelText("CPU cores") as HTMLInputElement).value).toBe("32");
      // Memory's "use detected" is there too but wasn't clicked — Update
      // below must send no memory_gb at all.
      expect(screen.getByRole("button", { name: "Use detected memory" })).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.setCapacity).toHaveBeenCalled());
      expect(api.setCapacity).toHaveBeenCalledWith(
        { cpu: 32, workers: 3, housekeepers: 2 },
        [{ model: "NVIDIA GeForce RTX 4090", vram_gb: 24 }],
      );
    });

    it("does not call setHostPrograms when the program list is left unchanged", async () => {
      renderModal({ local: true, host: LOCAL, localCapacity });
      await awaitProgramsSeeded(ALL_PROGRAM_IDS);
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.setCapacity).toHaveBeenCalled());
      expect(api.setHostPrograms).not.toHaveBeenCalled();
    });

    it("calls setHostPrograms with the remaining programs after unticking one, once setCapacity resolves", async () => {
      renderModal({ local: true, host: LOCAL, localCapacity });
      await awaitProgramsSeeded(ALL_PROGRAM_IDS);
      untickProgram("p4");
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.setHostPrograms).toHaveBeenCalled());
      expect(api.setHostPrograms).toHaveBeenCalledWith("local", ["p2", "p5"]);
      // setCapacity resolved first — setHostPrograms only fires once local
      // capacity is saved.
      const capacityOrder = vi.mocked(api.setCapacity).mock.invocationCallOrder[0];
      const programsOrder = vi.mocked(api.setHostPrograms).mock.invocationCallOrder[0];
      expect(capacityOrder).toBeLessThan(programsOrder);
    });
  });

  describe("add mode", () => {
    it("probes with every program ticked by default", async () => {
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      renderModal();
      declare();
      await awaitProgramsSeeded(ALL_PROGRAM_IDS);
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      await waitFor(() => expect(api.probeHost).toHaveBeenCalled());
      expect(api.probeHost).toHaveBeenCalledWith({
        name: "gpu1", ssh: "gpu1", run_root: "~/coscience-runs", shared: false,
        programs: ALL_PROGRAM_IDS, owner: "", notes: "" });
    });

    it("probes with only the programs picked, after clearing the default list", async () => {
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      renderModal();
      declare();
      await awaitProgramsSeeded(ALL_PROGRAM_IDS);
      fireEvent.click(screen.getByRole("button", { name: "clear" }));
      await pickProgram("p2");
      await pickProgram("p5");
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      await waitFor(() => expect(api.probeHost).toHaveBeenCalled());
      expect(api.probeHost).toHaveBeenCalledWith({
        name: "gpu1", ssh: "gpu1", run_root: "~/coscience-runs", shared: false,
        programs: ["p2", "p5"], owner: "", notes: "" });
    });

    it("shows the checks and warnings, and confirms the adjusted offer", async () => {
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      renderModal();
      declare();
      await awaitProgramsSeeded(ALL_PROGRAM_IDS);
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      expect(await screen.findByText(/rsync both ways/)).toBeTruthy();
      expect(screen.getByText(/glibc 2.17 is old/)).toBeTruthy();
      expect(screen.getByText("Example Linux 9 · Example CPU · 12 threads · 62 GB memory")).toBeTruthy();
      expect((screen.getByLabelText("CPU cores offered") as HTMLInputElement).value).toBe("12");
      // Cards are read-only text in add mode — there's nothing to edit and no
      // "+ add card" to click — but the probe's cards are still sent on Add.
      expect(screen.getByText(/10\.8 GB X\./)).toBeTruthy();
      expect(screen.queryByLabelText("GPU 1 model")).toBeNull();
      expect(screen.queryByLabelText("GPU 1 VRAM (GB)")).toBeNull();
      expect(screen.queryByText("+ add card")).toBeNull();
      fireEvent.change(screen.getByLabelText("CPU cores offered"), { target: { value: "10" } });
      fireEvent.click(screen.getByRole("button", { name: "Add to the pool" }));
      await waitFor(() => expect(api.confirmHost).toHaveBeenCalled());
      expect(api.confirmHost).toHaveBeenCalledWith({
        name: "gpu1", capacity: { cpu: 10, memory_gb: 55 },
        gpus: [{ model: "X", vram_gb: 10.8 }], notes: "", probed_at: 1, programs: ALL_PROGRAM_IDS,
      });
    });

    it("offers nothing to confirm when a check failed", async () => {
      vi.mocked(api.probeHost).mockResolvedValue({
        ...OK_PROBE, checks: [{ name: "rsync both ways", ok: false, detail: "rsync not found" }],
      } as never);
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      expect(await screen.findByText(/Fix the failed checks/)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
    });

    it("explains a failed probe and offers nothing to confirm", async () => {
      vi.mocked(api.probeHost).mockResolvedValue({
        ...OK_PROBE, ok: false, checks: [], warnings: [], proposal: {},
        error: "the server's host key is not known yet: connect once by hand (ssh gpu1), accept the key, then probe again",
      } as never);
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      expect(await screen.findByText(/connect once by hand/)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
    });

    it("forgets the probe when a declared field changes, so nothing unchecked is added", async () => {
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      expect(await screen.findByRole("button", { name: "Add to the pool" })).toBeTruthy();
      fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu2" } });
      expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
      expect(screen.queryByText(/rsync both ways/)).toBeNull();
    });

    it("ignores a probe that finishes after a declared field changed", async () => {
      let finish: (value: unknown) => void = () => {};
      vi.mocked(api.probeHost).mockImplementation(() => new Promise((resolve) => { finish = resolve; }) as never);
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      await waitFor(() => expect(api.probeHost).toHaveBeenCalled());
      fireEvent.change(screen.getByLabelText(/^SSH target/), { target: { value: "gpu2" } });
      await act(async () => { finish(OK_PROBE); });
      expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
      expect(screen.queryByText(/rsync both ways/)).toBeNull();
    });

    it("fills CPU, memory, cards and notes from the agent's Use proposal", async () => {
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      vi.mocked(api.getSurvey).mockResolvedValue(surveyWithProposal(SURVEY_PROPOSAL));
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      await screen.findByRole("button", { name: "Add to the pool" });

      fireEvent.click(await screen.findByRole("button", { name: "Use proposal" }));
      expect((screen.getByLabelText("CPU cores offered") as HTMLInputElement).value).toBe("6");
      expect((screen.getByLabelText("Memory offered (GB)") as HTMLInputElement).value).toBe("40");
      expect((screen.getByLabelText(/^Notes/) as HTMLInputElement).value).toBe("agent-verified");
      // Shown both by the modal's own card summary and by SurveyPanel's
      // proposal block; match the modal's specific wording.
      expect(screen.getByText(/80 GB A100\. A server added now waits/)).toBeTruthy();
    });

    it("sends the proposal's gpus and notes, not just capacity, when adding after Use proposal", async () => {
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      vi.mocked(api.getSurvey).mockResolvedValue(surveyWithProposal(SURVEY_PROPOSAL));
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      await screen.findByRole("button", { name: "Add to the pool" });

      fireEvent.click(await screen.findByRole("button", { name: "Use proposal" }));
      fireEvent.click(screen.getByRole("button", { name: "Add to the pool" }));
      await waitFor(() => expect(api.confirmHost).toHaveBeenCalled());
      expect(api.confirmHost).toHaveBeenCalledWith(expect.objectContaining({
        capacity: { cpu: 6, memory_gb: 40 },
        gpus: [{ model: "A100", vram_gb: 80 }],
        notes: "agent-verified",
      }));
    });

    it("shows 'Add with the agent's overrides' and sends accept_overrides when the proposal covers the failed check", async () => {
      const failingProbe = {
        ...OK_PROBE, checks: [{ name: "rsync both ways", ok: false, detail: "rsync not found" }],
      };
      vi.mocked(api.probeHost).mockResolvedValue(failingProbe as never);
      vi.mocked(api.getSurvey).mockResolvedValue(surveyWithProposal(SURVEY_PROPOSAL));
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      expect(await screen.findByText(/Fix the failed checks/)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();

      fireEvent.click(await screen.findByRole("button", { name: "Use proposal" }));
      expect(screen.queryByText(/Fix the failed checks/)).toBeNull();
      expect(screen.getByText(
        "Failed checks will be accepted on the agent's written reasons shown above.",
      )).toBeTruthy();
      // The override itself is shown once, inside SurveyPanel's own proposal block.
      expect(screen.getAllByText("Override rsync both ways: installed and verified by hand").length).toBe(1);

      fireEvent.click(screen.getByRole("button", { name: "Add with the agent's overrides" }));
      await waitFor(() => expect(api.confirmHost).toHaveBeenCalled());
      expect(api.confirmHost).toHaveBeenCalledWith(expect.objectContaining({ accept_overrides: true }));
    });

    it("keeps the refusal text when the proposal doesn't cover the failed check", async () => {
      const failingProbe = {
        ...OK_PROBE, checks: [{ name: "rsync both ways", ok: false, detail: "rsync not found" }],
      };
      const proposalWithoutOverride: SurveyProposal = { ...SURVEY_PROPOSAL, overrides: [] };
      vi.mocked(api.probeHost).mockResolvedValue(failingProbe as never);
      vi.mocked(api.getSurvey).mockResolvedValue(surveyWithProposal(proposalWithoutOverride));
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      expect(await screen.findByText(/Fix the failed checks/)).toBeTruthy();

      fireEvent.click(await screen.findByRole("button", { name: "Use proposal" }));
      expect(screen.getByText(/Fix the failed checks/)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Add with the agent's overrides" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Add to the pool" })).toBeNull();
    });
  });

  // Fix round 1, Finding 1: `programs` seeds asynchronously once `listPrograms`
  // resolves. A submit that beats that (or a `listPrograms` that never
  // resolves, or rejects) must never send the not-yet-seeded `[]` — that
  // would silently wipe an existing host's access, or start a new one
  // admitting nothing — so `programs` must be omitted from the request body
  // entirely instead. Every test here clicks/submits with no
  // `awaitProgramsSeeded()` beforehand, on purpose.
  describe("seeding race (fix round 1, Finding 1)", () => {
    it("omits programs from the probe body before listPrograms resolves", async () => {
      let resolvePrograms: (v: unknown) => void = () => {};
      vi.mocked(api.listPrograms).mockImplementationOnce(
        () => new Promise((resolve) => { resolvePrograms = resolve; }) as never,
      );
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      await waitFor(() => expect(api.probeHost).toHaveBeenCalled());
      const [body] = vi.mocked(api.probeHost).mock.calls[0] as [Record<string, unknown>];
      expect(body).not.toHaveProperty("programs");
      await act(async () => { resolvePrograms([{ id: "p2" }]); });
    });

    it("omits programs from updateHost when Update is clicked before listPrograms resolves", async () => {
      let resolvePrograms: (v: unknown) => void = () => {};
      vi.mocked(api.listPrograms).mockImplementationOnce(
        () => new Promise((resolve) => { resolvePrograms = resolve; }) as never,
      );
      renderModal({ host: REMOTE });
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      const [, body] = vi.mocked(api.updateHost).mock.calls[0] as [string, Record<string, unknown>];
      expect(body).not.toHaveProperty("programs");
      await act(async () => { resolvePrograms([{ id: "p2" }]); });
    });

    it("omits programs from confirmHost when Add is confirmed before listPrograms resolves", async () => {
      let resolvePrograms: (v: unknown) => void = () => {};
      vi.mocked(api.listPrograms).mockImplementationOnce(
        () => new Promise((resolve) => { resolvePrograms = resolve; }) as never,
      );
      vi.mocked(api.probeHost).mockResolvedValue(OK_PROBE as never);
      renderModal();
      declare();
      fireEvent.click(screen.getByRole("button", { name: "Probe" }));
      await screen.findByRole("button", { name: "Add to the pool" });
      fireEvent.click(screen.getByRole("button", { name: "Add to the pool" }));
      await waitFor(() => expect(api.confirmHost).toHaveBeenCalled());
      const [body] = vi.mocked(api.confirmHost).mock.calls[0] as [Record<string, unknown>];
      expect(body).not.toHaveProperty("programs");
      await act(async () => { resolvePrograms([{ id: "p2" }]); });
    });

    it("never seeds and never sends an empty programs list once listPrograms rejects", async () => {
      vi.mocked(api.listPrograms).mockRejectedValueOnce(new Error("network error"));
      renderModal({ host: REMOTE });
      fireEvent.click(screen.getByRole("button", { name: "Update configuration" }));
      await waitFor(() => expect(api.updateHost).toHaveBeenCalled());
      const [, body] = vi.mocked(api.updateHost).mock.calls[0] as [string, Record<string, unknown>];
      expect(body).not.toHaveProperty("programs");
    });
  });
});
