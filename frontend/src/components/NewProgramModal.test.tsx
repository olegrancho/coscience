import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";

const navigate = vi.fn();
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useNavigate: () => navigate,
}));

vi.mock("../api", () => ({
  api: { createProgram: vi.fn().mockResolvedValue({ id: "p7" }) },
}));

import { api } from "../api";
import NewProgramModal from "./NewProgramModal";

// jsdom has no matchMedia; MantineProvider's color-scheme effect needs it.
beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <NewProgramModal opened onClose={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("NewProgramModal", () => {
  it("blocks submit and shows an error when title or goals is blank", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /create program/i }));
    await waitFor(() => expect(screen.getByText(/required/i)).toBeTruthy());
    expect(api.createProgram).not.toHaveBeenCalled();
  });

  it("calls api.createProgram with the entered values on a valid submit", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "  Aging  " } });
    fireEvent.change(screen.getByLabelText("Goals"), { target: { value: "Reverse it" } });
    fireEvent.click(screen.getByRole("button", { name: /create program/i }));
    await waitFor(() =>
      expect(api.createProgram).toHaveBeenCalledWith({ title: "Aging", goals: "Reverse it", workdir: "" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/programs/p7"));
  });
});
