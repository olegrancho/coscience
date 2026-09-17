import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: { getSurvey: vi.fn(), surveyHost: vi.fn() },
}));

import { api } from "../api";
import type { HostSurvey } from "../api";
import SurveyPanel, { surveyRefetchInterval } from "./SurveyPanel";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const NOT_STARTED: HostSurvey = {
  name: "gpu1", started: false, pending: false,
  messages: [], proposal: null, proposal_error: "",
};

const PENDING: HostSurvey = {
  name: "gpu1", started: true, pending: true,
  messages: [{ role: "human", text: "please survey", at: 1 }],
  proposal: null, proposal_error: "",
};

const WITH_MESSAGES: HostSurvey = {
  name: "gpu1", started: true, pending: false,
  messages: [
    { role: "human", text: "go ahead", at: 1 },
    { role: "pm", text: "Checked the server. **rsync** looks fine.", at: 2 },
  ],
  proposal: null, proposal_error: "",
};

const PROPOSAL: HostSurvey = {
  name: "gpu1", started: true, pending: false,
  messages: [{ role: "pm", text: "Here's what I found.", at: 1 }],
  proposal: {
    capacity: { cpu: 12, memory_gb: 55 },
    gpus: [{ model: "X", vram_gb: 10.8 }],
    notes: "business hours only",
    overrides: [{ check: "rsync both ways", reason: "installed and verified by hand" }],
  },
  proposal_error: "",
};

const PENDING_PROPOSAL: HostSurvey = {
  ...PROPOSAL, pending: true,
};

const PROPOSAL_ERROR: HostSurvey = {
  name: "gpu1", started: true, pending: false,
  messages: [],
  proposal: null, proposal_error: "the model field was empty",
};

function renderPanel(name = "gpu1", onUseProposal = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { onUseProposal, qc, ...render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <SurveyPanel name={name} onUseProposal={onUseProposal} />
      </QueryClientProvider>
    </MantineProvider>,
  ) };
}

describe("SurveyPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows a start button when not started, and starts the survey on click", async () => {
    vi.mocked(api.getSurvey).mockResolvedValue(NOT_STARTED);
    vi.mocked(api.surveyHost).mockResolvedValue(WITH_MESSAGES);
    renderPanel();
    const button = await screen.findByRole("button", { name: "Survey with an agent" });
    expect(screen.getByText(
      "An agent logs in, checks what the probe found, and proposes capacity, cards and notes. "
      + "It runs with full access on this machine, using this backend's SSH keys, and runs "
      + "commands on the server.",
    )).toBeTruthy();
    fireEvent.click(button);
    await waitFor(() => expect(api.surveyHost).toHaveBeenCalledWith("gpu1"));
    expect(await screen.findByText("go ahead")).toBeTruthy();
  });

  it("shows the working text and no Send while pending", async () => {
    vi.mocked(api.getSurvey).mockResolvedValue(PENDING);
    renderPanel();
    expect(await screen.findByText("The agent is working…")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });

  it("renders pm messages with markdown and human messages as plain text, and sends a reply", async () => {
    vi.mocked(api.getSurvey).mockResolvedValue(WITH_MESSAGES);
    vi.mocked(api.surveyHost).mockResolvedValue(PENDING);
    renderPanel();
    expect(await screen.findByText("go ahead")).toBeTruthy();
    expect(screen.getByText("rsync")).toBeTruthy(); // rendered out of the ** markdown

    fireEvent.change(screen.getByLabelText("Message the agent"), { target: { value: "one more thing" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.surveyHost).toHaveBeenCalledWith("gpu1", "one more thing"));
    expect(await screen.findByText("The agent is working…")).toBeTruthy();
  });

  it("shows the agent's proposal, including the GPU model, and calls back with it on Use proposal", async () => {
    vi.mocked(api.getSurvey).mockResolvedValue(PROPOSAL);
    const onUseProposal = vi.fn();
    renderPanel("gpu1", onUseProposal);
    expect(await screen.findByText("Agent's proposal")).toBeTruthy();
    // Formatted locally (not via `hostOffer`), so the model is included —
    // matching the add-mode probe summary's card wording.
    expect(screen.getByText("12 CPU cores · 55 GB memory · 10.8 GB X")).toBeTruthy();
    expect(screen.getByText("business hours only")).toBeTruthy();
    expect(screen.getByText("Override rsync both ways: installed and verified by hand")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use proposal" }));
    expect(onUseProposal).toHaveBeenCalledWith(PROPOSAL.proposal);
  });

  it("hides Use proposal while pending", async () => {
    vi.mocked(api.getSurvey).mockResolvedValue(PENDING_PROPOSAL);
    renderPanel();
    expect(await screen.findByText("Agent's proposal")).toBeTruthy();
    expect(screen.getByText("The agent is working…")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Use proposal" })).toBeNull();
  });

  it("shows the proposal error in red", async () => {
    vi.mocked(api.getSurvey).mockResolvedValue(PROPOSAL_ERROR);
    renderPanel();
    expect(await screen.findByText("The agent's proposal can't be used: the model field was empty")).toBeTruthy();
  });

  it("shows a rejected surveyHost call's message", async () => {
    vi.mocked(api.getSurvey).mockResolvedValue(NOT_STARTED);
    vi.mocked(api.surveyHost).mockRejectedValue(
      new Error("422 the deployment's usage window is exhausted"));
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Survey with an agent" }));
    expect(await screen.findByText(/usage window is exhausted/)).toBeTruthy();
    // Still shows the start button — the survey never actually started.
    expect(screen.getByRole("button", { name: "Survey with an agent" })).toBeTruthy();
  });

  it("shows a getSurvey 403's text before any data has loaded", async () => {
    vi.mocked(api.getSurvey).mockRejectedValue(
      new Error("403 probing gpu1 is off: onboarding is disabled on this deployment"));
    renderPanel();
    expect(await screen.findByText(/onboarding is disabled/)).toBeTruthy();
  });

  it("shows a getSurvey 404's text before any data has loaded", async () => {
    vi.mocked(api.getSurvey).mockRejectedValue(new Error("404 gpu1 has no probe record"));
    renderPanel();
    expect(await screen.findByText(/has no probe record/)).toBeTruthy();
  });

  it("shows a getSurvey error even once earlier data is cached", async () => {
    vi.mocked(api.getSurvey).mockResolvedValueOnce(WITH_MESSAGES);
    const { qc } = renderPanel();
    expect(await screen.findByText("go ahead")).toBeTruthy();

    vi.mocked(api.getSurvey).mockRejectedValueOnce(new Error("500 the survey directory is unreadable"));
    await act(async () => { await qc.refetchQueries({ queryKey: ["survey", "gpu1"] }); });
    expect(await screen.findByText(/survey directory is unreadable/)).toBeTruthy();
    // The stale data stays on screen alongside the error.
    expect(screen.getByText("go ahead")).toBeTruthy();
  });

  it("stops polling once the survey is no longer pending", () => {
    expect(surveyRefetchInterval(true)).toBe(3000);
    expect(surveyRefetchInterval(false)).toBe(false);
  });
});
