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
 * The Reviewer's request for a new SNOMED CT code (FR-30, FR-31, NFR-20, NFR-31).
 * It replaces the plain reason dialog for the one move to Awaiting terminology.
 * Every required field blocks the send, and every refusal the server still makes
 * reaches the dialog.
 */

const ID = "6f1d2c3e-0000-4000-8000-000000000628";
const DETAIL = `/api/v1/submissions/${ID}`;
const REQUEST = `${DETAIL}/terminology-request`;

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

const TERMINOLOGY_REQUEST = {
  proposed_fsn: "Ferritin level (procedure)",
  target_edition: "au",
  justification: "No concept covers this test.",
  external_reference: { system: "NCTS service desk", reference: "NCTS-1042" },
  raised_date: "2026-10-01",
  expected_release: "2026-12-01",
  updated_at: "2026-10-01T02:00:00Z",
};

function submission(overrides: Record<string, unknown> = {}) {
  return {
    id: ID,
    state: "Under review",
    available_transitions: [
      "Ready for approval",
      "Awaiting terminology",
      "Rejected",
      "Withdrawn",
    ] as SubmissionState[],
    row_version: 5,
    preferred_term: "Ferritin level",
    kind: "new_test",
    entry_business_key: null,
    ...overrides,
  };
}

function detailRoute(overrides: Record<string, unknown> = {}) {
  return { method: "GET", path: DETAIL, status: 200, body: submission(overrides) };
}

function Harness() {
  const detail = useSubmissionDetail(ID);
  return detail.data === undefined ? null : <TriageSubmission submission={detail.data} />;
}

function renderPanel() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <AuthContext.Provider value={AUTH}>
        <Harness />
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

/** The detail the server answers once the request is recorded. */
const AFTER = detailRoute({
  state: "Awaiting terminology",
  row_version: 6,
  available_transitions: ["Under review"],
});

async function openDialog() {
  await userEvent.click(
    await screen.findByRole("button", { name: "Request a new code" }),
  );
  return screen.findByRole("dialog");
}

async function fillRequest(dialog: HTMLElement, skip: string[] = []) {
  const type = async (label: RegExp, text: string, key: string) => {
    if (!skip.includes(key)) {
      await userEvent.type(within(dialog).getByLabelText(label), text);
    }
  };
  await type(/Proposed fully specified name/, "Ferritin level (procedure)", "fsn");
  if (!skip.includes("edition")) {
    await userEvent.selectOptions(
      within(dialog).getByLabelText(/Target edition/),
      "Australian edition",
    );
  }
  await type(/Justification/, "No concept covers this test.", "justification");
  await type(/^System/, "NCTS service desk", "system");
  await type(/^Reference/, "NCTS-1042", "reference");
  await type(/Date raised/, "2026-10-01", "raised");
  await type(/Expected release/, "2026-12-01", "release");
  await type(/Reason/, "  No SNOMED CT concept exists.  ", "reason");
}

async function send(dialog: HTMLElement) {
  await userEvent.click(within(dialog).getByRole("button", { name: "Send request" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Request a new code (FR-30, FR-31)", () => {
  it("opens an accessible form from the Triage panel", async () => {
    stubApi([detailRoute()]);
    const { container } = renderPanel();
    const dialog = await openDialog();

    expect(
      within(dialog).getByRole("heading", { name: "Request a new code" }),
    ).toBeVisible();
    expect(
      within(dialog).getByText(/Move Ferritin level to Awaiting terminology/),
    ).toBeVisible();
    expect(
      within(dialog).getByRole("group", { name: /External reference/ }),
    ).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("blocks the send and marks every required field that is empty", async () => {
    const calls = stubApi([detailRoute()]);
    const { container } = renderPanel();
    const dialog = await openDialog();

    await send(dialog);

    for (const message of [
      "Enter the proposed fully specified name.",
      "Choose the edition the code is for.",
      "Enter why the catalogue needs a new code.",
      "Enter the system that holds the request.",
      "Enter the reference the request has there.",
      "Enter the date the request was raised.",
      "Enter the reason for this request.",
    ]) {
      expect(await within(dialog).findAllByText(message)).not.toHaveLength(0);
    }
    expect(
      within(dialog).getByLabelText(/Proposed fully specified name/),
    ).toHaveAttribute("aria-invalid", "true");
    expect(within(dialog).getByLabelText(/Expected release/)).not.toHaveAttribute(
      "aria-invalid",
    );
    expect(calls.some((call) => call.method === "POST")).toBe(false);
    await expectNoA11yViolations(container);
  });

  it.each([
    ["the proposed name", "fsn", "Enter the proposed fully specified name."],
    ["the edition", "edition", "Choose the edition the code is for."],
    ["the justification", "justification", "Enter why the catalogue needs a new code."],
    ["the system", "system", "Enter the system that holds the request."],
    ["the reference", "reference", "Enter the reference the request has there."],
    ["the date raised", "raised", "Enter the date the request was raised."],
    ["the reason", "reason", "Enter the reason for this request."],
  ])("sends nothing while %s is empty", async (_name, key, message) => {
    const calls = stubApi([detailRoute()]);
    renderPanel();
    const dialog = await openDialog();

    await fillRequest(dialog, [key]);
    await send(dialog);

    expect(await within(dialog).findAllByText(message)).not.toHaveLength(0);
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("refuses an expected release before the date raised", async () => {
    const calls = stubApi([detailRoute()]);
    renderPanel();
    const dialog = await openDialog();

    await fillRequest(dialog, ["release"]);
    await userEvent.type(within(dialog).getByLabelText(/Expected release/), "2026-09-01");
    await send(dialog);

    expect(
      await within(dialog).findAllByText(
        /cannot be earlier than the date the request was raised/,
      ),
    ).not.toHaveLength(0);
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("sends the trimmed fields and the version the page holds, then confirms", async () => {
    const calls = stubApi(
      [
        detailRoute(),
        {
          method: "POST",
          path: REQUEST,
          status: 200,
          body: {
            id: ID,
            state: "Awaiting terminology",
            row_version: 6,
            available_transitions: ["Under review"],
            terminology_request: TERMINOLOGY_REQUEST,
          },
        },
      ],
      {
        vary: (call, prior) =>
          call.method === "GET" && call.path === DETAIL && prior > 0 ? AFTER : null,
      },
    );
    const { container } = renderPanel();
    const dialog = await openDialog();

    await fillRequest(dialog);
    await send(dialog);

    const confirmation = await screen.findByText("Moved to Awaiting terminology.");
    expect(confirmation).toHaveFocus();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      proposed_fsn: "Ferritin level (procedure)",
      target_edition: "au",
      justification: "No concept covers this test.",
      external_reference: { system: "NCTS service desk", reference: "NCTS-1042" },
      raised_date: "2026-10-01",
      expected_release: "2026-12-01",
      reason: "No SNOMED CT concept exists.",
      expected_row_version: 5,
    });
    // The answer carried the next list, so the panel offers the next move.
    expect(screen.getByRole("button", { name: "Resume review" })).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("leaves the expected release out of the request when it is empty", async () => {
    const calls = stubApi([
      detailRoute(),
      {
        method: "POST",
        path: REQUEST,
        status: 200,
        body: {
          id: ID,
          state: "Awaiting terminology",
          row_version: 6,
          available_transitions: ["Under review"],
          terminology_request: { ...TERMINOLOGY_REQUEST, expected_release: null },
        },
      },
    ]);
    renderPanel();
    const dialog = await openDialog();

    await fillRequest(dialog, ["release"]);
    await send(dialog);

    await screen.findByText("Moved to Awaiting terminology.");
    expect(calls.find((call) => call.method === "POST")?.body).not.toHaveProperty(
      "expected_release",
    );
  });

  it("marks the field a 422 names and keeps what the user typed", async () => {
    stubApi([
      detailRoute(),
      {
        method: "POST",
        path: REQUEST,
        status: 422,
        body: {
          detail: "The proposed name has a character that cannot be shown.",
          field: "proposed_fsn",
        },
      },
    ]);
    const { container } = renderPanel();
    const dialog = await openDialog();

    await fillRequest(dialog);
    await send(dialog);

    expect(
      await within(dialog).findAllByText(
        "The proposed name has a character that cannot be shown.",
      ),
    ).not.toHaveLength(0);
    const field = within(dialog).getByLabelText(/Proposed fully specified name/);
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveValue("Ferritin level (procedure)");
    expect(screen.getByRole("dialog")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it.each([
    ["external_reference_system", /^System/],
    ["external_reference_ref", /^Reference/],
    ["expected_release", /Expected release/],
    ["justification", /Justification/],
    ["reason", /Reason/],
  ])("marks %s when the server names it", async (field, label) => {
    stubApi([
      detailRoute(),
      {
        method: "POST",
        path: REQUEST,
        status: 422,
        body: { detail: "The server refused this field.", field },
      },
    ]);
    renderPanel();
    const dialog = await openDialog();

    await fillRequest(dialog);
    await send(dialog);

    await within(dialog).findAllByText("The server refused this field.");
    expect(within(dialog).getByLabelText(label)).toHaveAttribute("aria-invalid", "true");
  });

  it("says the page has reloaded when the version is stale, and re-reads the submission", async () => {
    const calls = stubApi([
      detailRoute(),
      {
        method: "POST",
        path: REQUEST,
        status: 409,
        body: { detail: "Changed.", expected_row_version: 5, current_row_version: 6 },
      },
    ]);
    renderPanel();
    const dialog = await openDialog();

    await fillRequest(dialog);
    await send(dialog);

    expect(
      await within(dialog).findAllByText(/The page now shows the latest version/),
    ).not.toHaveLength(0);
    expect(within(dialog).getByLabelText(/Proposed fully specified name/)).toHaveValue(
      "Ferritin level (procedure)",
    );
    await waitFor(() =>
      expect(calls.filter((call) => call.method === "GET").length).toBeGreaterThan(1),
    );
  });

  it.each([
    [403, "You may not request a code."],
    [409, "This submission is not Under review."],
  ])("shows the server's own sentence for a %s", async (status, sentence) => {
    stubApi([
      detailRoute(),
      { method: "POST", path: REQUEST, status, body: { detail: sentence } },
    ]);
    renderPanel();
    const dialog = await openDialog();

    await fillRequest(dialog);
    await send(dialog);

    expect(await within(dialog).findAllByText(sentence)).not.toHaveLength(0);
    expect(
      within(dialog).queryByText(/The page now shows the latest version/),
    ).toBeNull();
  });

  it("offers no second attempt once the re-read shows the move is gone", async () => {
    const calls = stubApi(
      [
        detailRoute(),
        {
          method: "POST",
          path: REQUEST,
          status: 409,
          body: { detail: "Changed.", expected_row_version: 5, current_row_version: 6 },
        },
      ],
      {
        vary: (call, prior) =>
          call.method === "GET" && call.path === DETAIL && prior > 0
            ? detailRoute({
                state: "Rejected",
                row_version: 6,
                available_transitions: ["Triage"],
              })
            : null,
      },
    );
    renderPanel();
    const dialog = await openDialog();

    await fillRequest(dialog);
    await send(dialog);
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Send request" }),
      ).toHaveAttribute("aria-disabled", "true"),
    );
    await send(dialog);

    expect(
      await within(dialog).findAllByText(/no longer offers a request for a new code/),
    ).not.toHaveLength(0);
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });

  it("closes without sending when the user cancels", async () => {
    const calls = stubApi([detailRoute()]);
    renderPanel();
    const dialog = await openDialog();

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Request a new code" })).toBeVisible();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });
});
