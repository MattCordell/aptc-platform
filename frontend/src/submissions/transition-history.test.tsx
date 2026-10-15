import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/query-client.ts";
import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { stubApi } from "../test/stub-api.ts";
import { TransitionHistory } from "./transition-history.tsx";

/**
 * The History section (FR-81, FR-34, NFR-31): every change of state, oldest
 * first, with the reason and the actor exactly as the server sent them.
 */

const ID = "6f1d2c3e-0000-4000-8000-000000000051";
const HISTORY = `/api/v1/submissions/${ID}/transitions`;

const AUTH: AuthContextValue = {
  status: "signed-in",
  getAccessToken: () => Promise.resolve("test-token"),
  signIn: () => Promise.resolve(),
  stepUp: () => Promise.resolve("interaction-required"),
  signOut: () => Promise.resolve(),
  register: () => Promise.resolve(),
  restore: () => Promise.resolve(),
  completeCallback: () => Promise.resolve(null),
};

function renderHistory() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <AuthContext.Provider value={AUTH}>
        <TransitionHistory submissionId={ID} />
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

function item(overrides: Record<string, unknown> = {}) {
  return {
    from_state: "Submitted",
    to_state: "Triage",
    reason: "Looks complete.",
    created_at: "2026-10-01T02:00:00Z",
    actor: { username: "rev-one" },
    ...overrides,
  };
}

function route(items: unknown[]) {
  return { method: "GET", path: HISTORY, status: 200, body: { items } };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TransitionHistory", () => {
  it("lists the moves oldest first, as the server ordered them", async () => {
    stubApi([
      route([
        item(),
        item({
          from_state: "Triage",
          to_state: "Under review",
          reason: "Needs assessment.",
          created_at: "2026-10-02T02:00:00Z",
        }),
        item({
          from_state: "Under review",
          to_state: "Ready for approval",
          reason: "Assessed.",
          created_at: "2026-10-03T02:00:00Z",
        }),
      ]),
    ]);
    const { container } = renderHistory();

    const moves = await screen.findAllByRole("listitem");
    expect(moves.map((move) => within(move).getByText(/ to /).textContent)).toEqual([
      "Submitted to Triage",
      "Triage to Under review",
      "Under review to Ready for approval",
    ]);
    expect(within(moves[1]!).getByText("Needs assessment.")).toBeVisible();
    expect(within(moves[0]!).getByText("1 October 2026")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("shows a plain actor as a username", async () => {
    stubApi([route([item()])]);
    renderHistory();

    const move = await screen.findByRole("listitem");
    expect(within(move).getByText(/^By\s*rev-one$/)).toBeVisible();
  });

  it("shows an identified actor with a name and organisation, and a closed account's placeholder as sent", async () => {
    stubApi([
      route([
        item({
          actor: {
            username: "rev-one",
            display_name: "Rae Viewer",
            organisation: "RCPA-QAP",
          },
        }),
        item({
          from_state: "Triage",
          to_state: "Rejected",
          actor: {
            username: "closed-account",
            display_name: "Former user",
            organisation: null,
          },
        }),
      ]),
    ]);
    const { container } = renderHistory();

    const [first, second] = await screen.findAllByRole("listitem");
    expect(within(first!).getByText("Rae Viewer")).toBeVisible();
    expect(within(first!).getByText("RCPA-QAP")).toBeVisible();
    expect(within(second!).getByText("Former user")).toBeVisible();
    expect(within(second!).queryByText("closed-account")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("keeps the line breaks of a reason", async () => {
    stubApi([route([item({ reason: "First line.\nSecond line." })])]);
    renderHistory();

    const reason = await screen.findByText(/First line\./);
    expect(reason).toHaveClass("whitespace-pre-line");
    expect(reason.textContent).toBe("First line.\nSecond line.");
  });

  it("says so when the submission has not changed state", async () => {
    stubApi([route([])]);
    const { container } = renderHistory();

    expect(
      await screen.findByText("This submission has not changed state yet."),
    ).toBeVisible();
    expect(screen.queryByRole("list")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("shows a loading line while the history is read", async () => {
    stubApi([
      { method: "GET", path: HISTORY, status: 200, body: {}, neverSettles: true },
    ]);
    renderHistory();

    expect(await screen.findByText("Loading the history…")).toBeVisible();
    expect(screen.getByRole("heading", { name: "History" })).toBeVisible();
  });

  it("offers a retry when the read fails, and shows the history once it works", async () => {
    stubApi([route([item()])], {
      vary: (call, prior) =>
        call.path.endsWith("/transitions") && prior === 0
          ? { method: "GET", path: HISTORY, status: 500, body: { detail: "Boom." } }
          : null,
    });
    const { container } = renderHistory();

    expect(
      await screen.findByText("The history could not be loaded. Try again in a moment."),
    ).toBeVisible();
    await expectNoA11yViolations(container);

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Submitted to Triage")).toBeVisible();
  });

  it("shows the server's sentence and no retry when it refuses the caller", async () => {
    stubApi([
      {
        method: "GET",
        path: HISTORY,
        status: 403,
        body: { detail: "You may not read this submission." },
      },
    ]);
    renderHistory();

    expect(await screen.findByText("You may not read this submission.")).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("button")).toBeNull());
  });
});
