import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/query-client.ts";
import { useSession } from "../api/queries.ts";
import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { stubApi, type Route } from "../test/stub-api.ts";
import type { SubmissionState } from "./state-options.ts";
import { WithdrawSubmission } from "./withdraw-submission.tsx";

/**
 * The withdraw action (FR-28, FR-44, NFR-20, NFR-31). It shows only for the
 * submitter holding `submission.withdraw.own`, on a submission whose
 * `available_transitions` include `Withdrawn`, and every refusal the server
 * still makes reaches the dialog.
 */

const ID = "6f1d2c3e-0000-4000-8000-000000000031";
const WITHDRAW = `/api/v1/submissions/${ID}/withdraw`;

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

function session(permissions: string[]): Route {
  return {
    method: "GET",
    path: "/auth/me",
    status: 200,
    body: {
      authenticated: true,
      user: {
        username: "jo",
        display_name: "Jo Citizen",
        organisation: null,
        status: "active",
      },
      roles: [],
      permissions,
      mfa_satisfied: false,
    },
  };
}

const SUBMITTER = session(["submission.withdraw.own", "interest.register"]);

function submission(overrides: Record<string, unknown> = {}) {
  return {
    id: ID,
    is_own: true,
    state: "Submitted",
    available_transitions: ["Withdrawn"] as SubmissionState[],
    row_version: 3,
    preferred_term: "Ferritin level",
    ...overrides,
  };
}

/** Renders once the session has landed. The components under test read the same
 * query, so they have rendered with it by then, and an assertion that something
 * is absent cannot pass merely because the session was still loading. */
function SessionProbe() {
  const session = useSession();
  return session.data === undefined ? null : <span data-testid="session-ready" hidden />;
}

function tree(client: QueryClient, props: ReturnType<typeof submission>) {
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={AUTH}>
        <SessionProbe />
        <WithdrawSubmission submission={props} />
      </AuthContext.Provider>
    </QueryClientProvider>
  );
}

function renderWithdraw(props: ReturnType<typeof submission>) {
  const client = createQueryClient();
  const view = render(tree(client, props));
  return {
    ...view,
    showAnother: (next: ReturnType<typeof submission>) =>
      view.rerender(tree(client, next)),
  };
}

async function settled() {
  await screen.findByTestId("session-ready");
}

async function openDialog() {
  await userEvent.click(
    await screen.findByRole("button", { name: "Withdraw submission" }),
  );
  return screen.findByRole("dialog");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WithdrawSubmission visibility", () => {
  it.each([["Submitted"], ["Triage"], ["Under review"]])(
    "offers the button to the submitter in the %s state",
    async (state) => {
      stubApi([SUBMITTER]);
      const { container } = renderWithdraw(submission({ state }));

      expect(
        await screen.findByRole("button", { name: "Withdraw submission" }),
      ).toBeVisible();
      await expectNoA11yViolations(container);
    },
  );

  it.each([
    ["Awaiting terminology"],
    ["Ready for approval"],
    ["Approved"],
    ["Published in release"],
    ["Rejected"],
    ["Withdrawn"],
  ])(
    "is absent in the %s state, where the server offers no withdrawal",
    async (state) => {
      stubApi([SUBMITTER]);
      const { container } = renderWithdraw(
        submission({ state, available_transitions: [] }),
      );

      await settled();
      expect(screen.queryByRole("button")).toBeNull();
      expect(screen.queryByRole("heading")).toBeNull();
      await expectNoA11yViolations(container);
    },
  );

  it("follows the server's list and not the name of the state", async () => {
    stubApi([SUBMITTER]);
    renderWithdraw(submission({ state: "Triage", available_transitions: [] }));

    await settled();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("is absent for another Member, who is not the submitter", async () => {
    stubApi([SUBMITTER]);
    const { container } = renderWithdraw(submission({ is_own: false }));

    await settled();
    expect(screen.queryByRole("button")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("is absent without submission.withdraw.own, even for the submitter", async () => {
    stubApi([session(["interest.register"])]);
    const { container } = renderWithdraw(submission());

    await settled();
    expect(screen.queryByRole("button")).toBeNull();
    await expectNoA11yViolations(container);
  });
});

describe("WithdrawSubmission dialog", () => {
  it("asks for a reason before it sends anything", async () => {
    const calls = stubApi([SUBMITTER]);
    const { container } = renderWithdraw(submission());
    const dialog = await openDialog();

    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    expect(
      await within(dialog).findAllByText(
        "Enter the reason for withdrawing this submission.",
      ),
    ).not.toHaveLength(0);
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(calls.some((call) => call.method === "POST")).toBe(false);
    await expectNoA11yViolations(container);
  });

  it("says who reads the reason, and does not call it unpublished", async () => {
    stubApi([SUBMITTER]);
    renderWithdraw(submission());
    const dialog = await openDialog();

    expect(within(dialog).getByText(/shown in this submission's history/)).toBeVisible();
    expect(within(dialog).queryByText(/not published/)).toBeNull();
  });

  it("refuses a reason of spaces and sends nothing", async () => {
    const calls = stubApi([SUBMITTER]);
    renderWithdraw(submission());
    const dialog = await openDialog();

    await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "   ");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    expect(
      await within(dialog).findAllByText(
        "Enter the reason for withdrawing this submission.",
      ),
    ).not.toHaveLength(0);
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("sends the trimmed reason with the version the page holds, then closes", async () => {
    const calls = stubApi([
      SUBMITTER,
      {
        method: "POST",
        path: WITHDRAW,
        status: 200,
        body: { id: ID, state: "Withdrawn", row_version: 4 },
      },
    ]);
    const { container } = renderWithdraw(submission());
    const dialog = await openDialog();

    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "  Sent in error.  ",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    const confirmation = await screen.findByText("You withdrew this submission.");
    expect(confirmation).toHaveFocus();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      reason: "Sent in error.",
      expected_row_version: 3,
    });
    await expectNoA11yViolations(container);
  });

  it("tells the user to reload when the version is stale", async () => {
    stubApi([
      SUBMITTER,
      {
        method: "POST",
        path: WITHDRAW,
        status: 409,
        body: { detail: "Changed.", expected_row_version: 3, current_row_version: 4 },
      },
    ]);
    const { container } = renderWithdraw(submission());
    const dialog = await openDialog();

    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Sent in error.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    expect(
      await within(dialog).findAllByText(/The page now shows the latest version/),
    ).not.toHaveLength(0);
    expect(screen.getByRole("dialog")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("shows the server's own sentence for any other 409", async () => {
    stubApi([
      SUBMITTER,
      {
        method: "POST",
        path: WITHDRAW,
        status: 409,
        body: { detail: "This submission is already closed." },
      },
    ]);
    renderWithdraw(submission());
    const dialog = await openDialog();

    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Sent in error.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    expect(
      await within(dialog).findAllByText("This submission is already closed."),
    ).not.toHaveLength(0);
    expect(within(dialog).queryByText(/changed since you opened it/)).toBeNull();
  });

  it("marks the reason when the server refuses it", async () => {
    stubApi([
      SUBMITTER,
      {
        method: "POST",
        path: WITHDRAW,
        status: 422,
        body: {
          detail: "The reason has a character that cannot be shown.",
          field: "reason",
        },
      },
    ]);
    const { container } = renderWithdraw(submission());
    const dialog = await openDialog();

    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Sent in error.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

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

  it("does not carry a confirmation over to another submission", async () => {
    stubApi([
      SUBMITTER,
      {
        method: "POST",
        path: WITHDRAW,
        status: 200,
        body: { id: ID, state: "Withdrawn", row_version: 4 },
      },
    ]);
    const { showAnother } = renderWithdraw(submission());
    const dialog = await openDialog();
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Sent in error.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );
    await screen.findByText("You withdrew this submission.");

    showAnother(submission({ id: "6f1d2c3e-0000-4000-8000-000000000032" }));

    expect(screen.queryByText("You withdrew this submission.")).toBeNull();
    expect(screen.getByRole("button", { name: "Withdraw submission" })).toBeVisible();
  });

  it("closes without sending when the user cancels", async () => {
    const calls = stubApi([SUBMITTER]);
    renderWithdraw(submission());
    const dialog = await openDialog();

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Withdraw submission" })).toBeVisible();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });
});
