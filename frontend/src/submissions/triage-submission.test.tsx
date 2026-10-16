import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useSubmissionDetail } from "../api/queries.ts";
import { createQueryClient } from "../api/query-client.ts";
import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { stubApi } from "../test/stub-api.ts";
import type { SubmissionState } from "./state-options.ts";
import { TriageSubmission } from "./triage-submission.tsx";

/**
 * The reviewer's moves on the submission page (FR-28, FR-81, NFR-20, NFR-31).
 * The panel offers one button for each state the server lists, apart from the
 * moves that have their own screen, and every refusal the server still makes
 * reaches the dialog.
 */

const ID = "6f1d2c3e-0000-4000-8000-000000000041";
const DETAIL = `/api/v1/submissions/${ID}`;
const TRANSITIONS = `${DETAIL}/transitions`;

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

function submission(overrides: Record<string, unknown> = {}) {
  return {
    id: ID,
    state: "Triage",
    available_transitions: ["Under review", "Rejected", "Withdrawn"] as SubmissionState[],
    row_version: 3,
    preferred_term: "Ferritin level",
    ...overrides,
  };
}

function detailRoute(overrides: Record<string, unknown> = {}) {
  return { method: "GET", path: DETAIL, status: 200, body: submission(overrides) };
}

/** Reads the submission through the cache, as the page does, so a successful
 * move shows in the props the panel receives. */
function Harness() {
  const detail = useSubmissionDetail(ID);
  return detail.data === undefined ? null : <TriageSubmission submission={detail.data} />;
}

function renderPanel() {
  const client = createQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={AUTH}>
        <Harness />
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

async function openDialog(button: string) {
  await userEvent.click(await screen.findByRole("button", { name: button }));
  return screen.findByRole("dialog");
}

async function typeReason(dialog: HTMLElement, reason: string) {
  await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), reason);
}

async function confirm(dialog: HTMLElement) {
  await userEvent.click(within(dialog).getByRole("button", { name: "Confirm move" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TriageSubmission buttons", () => {
  it("offers a Reviewer Under review and Rejected on a Triage submission", async () => {
    stubApi([detailRoute()]);
    const { container } = renderPanel();

    expect(await screen.findByRole("button", { name: "Start review" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Reject" })).toBeVisible();
    expect(screen.getAllByRole("button")).toHaveLength(2);
    await expectNoA11yViolations(container);
  });

  it.each([
    ["Submitted", ["Triage", "Withdrawn"], ["Start triage"]],
    ["Rejected", ["Triage"], ["Reopen"]],
    [
      "Under review",
      ["Ready for approval", "Awaiting terminology", "Rejected", "Withdrawn"],
      ["Mark ready for approval", "Reject"],
    ],
    ["Ready for approval", ["Approved", "Rejected"], ["Reject"]],
    ["Awaiting terminology", ["Under review", "Ready for approval"], ["Resume review"]],
  ])(
    "offers each move the server lists from %s, and skips the ones with their own screen",
    async (state, available, labels) => {
      stubApi([detailRoute({ state, available_transitions: available })]);
      renderPanel();

      await screen.findByRole("heading", { name: "Triage" });
      expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual(
        labels,
      );
    },
  );

  it.each([
    ["a Member, who may only withdraw", "Submitted", ["Withdrawn"]],
    ["an Administrator on an approved submission", "Approved", ["Withdrawn"]],
    ["a submission ready for approval", "Ready for approval", ["Approved"]],
    ["a closed submission", "Withdrawn", []],
  ])("shows no panel for %s", async (_who, state, available) => {
    const calls = stubApi([detailRoute({ state, available_transitions: available })]);
    const { container } = renderPanel();

    await waitFor(() => expect(calls.some((call) => call.method === "GET")).toBe(true));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("follows the server's list and not the name of the state", async () => {
    stubApi([detailRoute({ state: "Triage", available_transitions: [] })]);
    renderPanel();

    await waitFor(() => expect(screen.queryByRole("heading")).toBeNull());
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("TriageSubmission dialog", () => {
  it("asks for a reason before it sends anything", async () => {
    const calls = stubApi([detailRoute()]);
    const { container } = renderPanel();
    const dialog = await openDialog("Reject");

    await confirm(dialog);

    expect(
      await within(dialog).findAllByText("Enter the reason for this move."),
    ).not.toHaveLength(0);
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(calls.some((call) => call.method === "POST")).toBe(false);
    await expectNoA11yViolations(container);
  });

  it("refuses a reason of spaces and sends nothing", async () => {
    const calls = stubApi([detailRoute()]);
    renderPanel();
    const dialog = await openDialog("Reject");

    await typeReason(dialog, "   ");
    await confirm(dialog);

    expect(
      await within(dialog).findAllByText("Enter the reason for this move."),
    ).not.toHaveLength(0);
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("names the move and says who reads the reason", async () => {
    stubApi([detailRoute()]);
    renderPanel();
    const dialog = await openDialog("Reject");

    expect(
      within(dialog).getByText(
        /from Triage to Rejected\. The submitter reads your reason/,
      ),
    ).toBeVisible();
    expect(within(dialog).getByText(/shown in this submission's history/)).toBeVisible();
    expect(within(dialog).queryByText(/not published/)).toBeNull();
  });

  it("sends the target, the trimmed reason and the version the page holds, then confirms", async () => {
    const calls = stubApi([
      detailRoute(),
      {
        method: "POST",
        path: TRANSITIONS,
        status: 200,
        body: {
          id: ID,
          state: "Under review",
          row_version: 4,
          available_transitions: ["Ready for approval", "Awaiting terminology"],
        },
      },
    ]);
    const { container } = renderPanel();
    const dialog = await openDialog("Start review");

    await typeReason(dialog, "  Complete enough to assess.  ");
    await confirm(dialog);

    const confirmation = await screen.findByText("Moved to Under review.");
    expect(confirmation).toHaveFocus();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      to_state: "Under review",
      reason: "Complete enough to assess.",
      expected_row_version: 3,
    });
    // The answer carried the next list, so the panel offers the next move.
    expect(screen.getByRole("button", { name: "Mark ready for approval" })).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("keeps the panel on screen after a move that leaves nothing to offer", async () => {
    stubApi([
      detailRoute({
        state: "Under review",
        available_transitions: ["Ready for approval"],
      }),
      {
        method: "POST",
        path: TRANSITIONS,
        status: 200,
        body: {
          id: ID,
          state: "Ready for approval",
          row_version: 4,
          available_transitions: ["Approved"],
        },
      },
    ]);
    renderPanel();
    const dialog = await openDialog("Mark ready for approval");

    await typeReason(dialog, "Checked.");
    await confirm(dialog);

    const confirmation = await screen.findByText("Moved to Ready for approval.");
    expect(confirmation).toHaveFocus();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("tells the user the page has reloaded when the version is stale, and keeps the reason", async () => {
    stubApi([
      detailRoute(),
      {
        method: "POST",
        path: TRANSITIONS,
        status: 409,
        body: { detail: "Changed.", expected_row_version: 3, current_row_version: 4 },
      },
    ]);
    const { container } = renderPanel();
    const dialog = await openDialog("Reject");

    await typeReason(dialog, "Out of scope.");
    await confirm(dialog);

    expect(
      await within(dialog).findAllByText(/The page now shows the latest version/),
    ).not.toHaveLength(0);
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toHaveValue(
      "Out of scope.",
    );
    await expectNoA11yViolations(container);
  });

  it("re-reads the submission after a stale version", async () => {
    const calls = stubApi([
      detailRoute(),
      {
        method: "POST",
        path: TRANSITIONS,
        status: 409,
        body: { detail: "Changed.", expected_row_version: 3, current_row_version: 4 },
      },
    ]);
    renderPanel();
    const dialog = await openDialog("Reject");

    await typeReason(dialog, "Out of scope.");
    await confirm(dialog);

    await waitFor(() =>
      expect(calls.filter((call) => call.method === "GET").length).toBeGreaterThan(1),
    );
  });

  it("offers no second attempt once the re-read shows the move is gone", async () => {
    const calls = stubApi(
      [
        detailRoute(),
        {
          method: "POST",
          path: TRANSITIONS,
          status: 409,
          body: { detail: "Changed.", expected_row_version: 3, current_row_version: 4 },
        },
      ],
      {
        vary: (call, prior) =>
          call.method === "GET" && call.path === DETAIL && prior > 0
            ? detailRoute({
                state: "Rejected",
                row_version: 4,
                available_transitions: ["Triage"],
              })
            : null,
      },
    );
    renderPanel();
    const dialog = await openDialog("Reject");

    await typeReason(dialog, "Out of scope.");
    await confirm(dialog);

    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Confirm move" }),
      ).toHaveAttribute("aria-disabled", "true"),
    );
    await confirm(dialog);

    expect(
      await within(dialog).findAllByText(/The platform no longer offers this move/),
    ).not.toHaveLength(0);
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });

  it("offers no second attempt when the re-read moved the submission to another state that still lists the target", async () => {
    const calls = stubApi(
      [
        detailRoute(),
        {
          method: "POST",
          path: TRANSITIONS,
          status: 409,
          body: { detail: "Changed.", expected_row_version: 3, current_row_version: 4 },
        },
      ],
      {
        // Awaiting terminology also lists Under review (Resume review), which
        // is not the move the open dialog describes.
        vary: (call, prior) =>
          call.method === "GET" && call.path === DETAIL && prior > 0
            ? detailRoute({
                state: "Awaiting terminology",
                row_version: 4,
                available_transitions: ["Under review"],
              })
            : null,
      },
    );
    renderPanel();
    const dialog = await openDialog("Start review");

    await typeReason(dialog, "Complete enough to assess.");
    await confirm(dialog);
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Confirm move" }),
      ).toHaveAttribute("aria-disabled", "true"),
    );
    await confirm(dialog);

    expect(
      await within(dialog).findAllByText(/The platform no longer offers this move/),
    ).not.toHaveLength(0);
    expect(within(dialog).getByText(/from Triage to Under review/)).toBeVisible();
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });

  it.each([
    [403, "You may not make this move."],
    [409, "This move is not permitted from the current state."],
  ])("shows the server's own sentence for a %s", async (status, sentence) => {
    stubApi([
      detailRoute(),
      { method: "POST", path: TRANSITIONS, status, body: { detail: sentence } },
    ]);
    renderPanel();
    const dialog = await openDialog("Reject");

    await typeReason(dialog, "Out of scope.");
    await confirm(dialog);

    expect(await within(dialog).findAllByText(sentence)).not.toHaveLength(0);
    expect(
      within(dialog).queryByText(/The page now shows the latest version/),
    ).toBeNull();
    expect(screen.getByRole("dialog")).toBeVisible();
  });

  it("marks the reason when the server refuses it", async () => {
    stubApi([
      detailRoute(),
      {
        method: "POST",
        path: TRANSITIONS,
        status: 422,
        body: {
          detail: "The reason has a character that cannot be shown.",
          field: "reason",
        },
      },
    ]);
    const { container } = renderPanel();
    const dialog = await openDialog("Reject");

    await typeReason(dialog, "Out of scope.");
    await confirm(dialog);

    expect(
      await within(dialog).findAllByText(
        "The reason has a character that cannot be shown.",
      ),
    ).not.toHaveLength(0);
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await expectNoA11yViolations(container);
  });

  it("closes without sending when the user cancels", async () => {
    const calls = stubApi([detailRoute()]);
    renderPanel();
    const dialog = await openDialog("Reject");

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Reject" })).toBeVisible();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("starts each dialog with an empty reason", async () => {
    stubApi([detailRoute()]);
    renderPanel();
    const first = await openDialog("Reject");
    await typeReason(first, "Out of scope.");
    await userEvent.click(within(first).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    const second = await openDialog("Start review");

    expect(within(second).getByRole("textbox", { name: /Reason/ })).toHaveValue("");
  });
});
