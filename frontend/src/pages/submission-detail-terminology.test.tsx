import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The terminology request on `/submissions/$submissionId` (FR-30, FR-31): the
 * section that shows it while the submission waits, the edit of its tracking
 * details, and the Administrator's Attach code action. Driven through the real
 * route, signed in. The Triage panel's other moves are in
 * `triage-submission.test.tsx`.
 */

const SIGNED_IN = { auth: { status: "signed-in" as const } };

const ID = "6f1d2c3e-0000-4000-8000-000000000628";
const DETAIL = `/api/v1/submissions/${ID}`;
const REQUEST = `${DETAIL}/terminology-request`;

function me(permissions: string[]): Route {
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

const REVIEWER = me(["submission.transition.review", "interest.register"]);
const MEMBER = me(["interest.register"]);

const TERMINOLOGY_REQUEST = {
  proposed_fsn: "Ferritin level (procedure)",
  target_edition: "au",
  justification: "No concept covers this test.",
  external_reference: { system: "NCTS service desk", reference: "NCTS-1042" },
  raised_date: "2026-10-01",
  expected_release: "2026-12-01",
  updated_at: "2026-10-01T02:00:00Z",
};

function detail(
  overrides: Record<string, unknown> = {},
  { review }: { review: boolean } = { review: true },
): Route {
  return {
    method: "GET",
    path: DETAIL,
    status: 200,
    body: {
      id: ID,
      kind: "new_test",
      state: "Awaiting terminology",
      entry_business_key: null,
      created_entry_business_key: null,
      preferred_term: "Ferritin level",
      synonyms: [],
      snomed_code: null,
      snomed_fsn: null,
      property_values: {},
      notes: null,
      created_at: "2026-09-01T12:00:00Z",
      is_own: false,
      has_registered_interest: false,
      interest_count: 2,
      label_provenance: {},
      row_version: 6,
      available_transitions: ["Under review"],
      reference_url: null,
      reference_checked_at: null,
      reference_status: null,
      duplicate_confirmed_at: null,
      submitter: { username: "u-submitter" },
      ...(review ? { working_copy: null, terminology_request: TERMINOLOGY_REQUEST } : {}),
      ...overrides,
    },
  };
}

async function open(routes: Route[], options?: Parameters<typeof stubApi>[1]) {
  const calls = stubApi(routes, options);
  const view = await renderRoute(`/submissions/${ID}`, SIGNED_IN);
  return { calls, ...view };
}

async function sectionOf() {
  const heading = await screen.findByRole("heading", { name: "Terminology request" });
  return heading.parentElement as HTMLElement;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the Terminology request section (FR-30, FR-31)", () => {
  it("shows the request to a Reviewer while the submission waits", async () => {
    const { container } = await open([REVIEWER, detail()]);

    const section = await sectionOf();

    expect(within(section).getByText("Ferritin level (procedure)")).toBeVisible();
    expect(within(section).getByText("Australian edition")).toBeVisible();
    expect(within(section).getByText("No concept covers this test.")).toBeVisible();
    expect(within(section).getByText("NCTS service desk: NCTS-1042")).toBeVisible();
    expect(within(section).getByText("1 October 2026")).toBeVisible();
    expect(within(section).getByText("1 December 2026")).toBeVisible();
    expect(
      within(section).getByRole("button", { name: "Edit tracking details" }),
    ).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("says no date was given when none is expected", async () => {
    await open([
      REVIEWER,
      detail({ terminology_request: { ...TERMINOLOGY_REQUEST, expected_release: null } }),
    ]);

    expect(within(await sectionOf()).getByText("No date given")).toBeVisible();
  });

  it("shows nothing to a Member, whose detail has no request", async () => {
    await open([MEMBER, detail({}, { review: false })]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(screen.queryByRole("heading", { name: "Terminology request" })).toBeNull();
  });

  it.each([
    ["Under review", null],
    ["Ready for approval", TERMINOLOGY_REQUEST],
  ])("shows nothing on a submission that is %s", async (state, terminologyRequest) => {
    await open([
      REVIEWER,
      detail({
        state,
        terminology_request: terminologyRequest,
        available_transitions: [],
      }),
    ]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(screen.queryByRole("heading", { name: "Terminology request" })).toBeNull();
  });
});

describe("editing the tracking details (FR-31)", () => {
  async function openEditor(routes: Route[], options?: Parameters<typeof stubApi>[1]) {
    const view = await open(routes, options);
    const section = await sectionOf();
    await userEvent.click(
      within(section).getByRole("button", { name: "Edit tracking details" }),
    );
    const dialog = await screen.findByRole("dialog");
    return { ...view, dialog };
  }

  async function save(dialog: HTMLElement) {
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
  }

  async function typeReason(dialog: HTMLElement, text: string) {
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), text);
  }

  const SAVED = {
    method: "PATCH",
    path: REQUEST,
    status: 200,
    body: {
      submission_id: ID,
      row_version: 7,
      changed: true,
      terminology_request: { ...TERMINOLOGY_REQUEST, expected_release: "2027-01-15" },
    },
  };

  it("offers only the tracking fields, filled with what the request holds", async () => {
    const { dialog, container } = await openEditor([REVIEWER, detail()]);

    expect(within(dialog).getByLabelText("System")).toHaveValue("NCTS service desk");
    expect(within(dialog).getByLabelText("Reference")).toHaveValue("NCTS-1042");
    expect(within(dialog).getByLabelText("Expected release")).toHaveValue("2026-12-01");
    expect(within(dialog).queryByLabelText(/Proposed fully specified name/)).toBeNull();
    expect(within(dialog).queryByLabelText(/Target edition/)).toBeNull();
    expect(within(dialog).queryByLabelText(/Justification/)).toBeNull();
    expect(within(dialog).queryByLabelText(/Date raised/)).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("sends only the expected release when only it changed, then shows the confirmation", async () => {
    // The page reads the submission again after the save, and the server then answers with the new date.
    let patched = false;
    const { calls, dialog } = await openEditor([REVIEWER, detail(), SAVED], {
      vary: (call) => {
        patched ||= call.method === "PATCH";
        return patched && call.method === "GET" && call.path === DETAIL
          ? detail({
              row_version: 7,
              terminology_request: {
                ...TERMINOLOGY_REQUEST,
                expected_release: "2027-01-15",
              },
            })
          : null;
      },
    });

    const release = within(dialog).getByLabelText("Expected release");
    await userEvent.clear(release);
    await userEvent.type(release, "2027-01-15");
    await typeReason(dialog, "  The authority gave a new date.  ");
    await save(dialog);

    const confirmation = await screen.findByText(
      "Saved. The tracking details are updated.",
    );
    expect(confirmation).toHaveFocus();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({
      expected_release: "2027-01-15",
      reason: "The authority gave a new date.",
      expected_row_version: 6,
    });
    expect(await screen.findByText("15 January 2027")).toBeVisible();
  });

  it("sends both parts of the reference when either changed", async () => {
    const { calls, dialog } = await openEditor([REVIEWER, detail(), SAVED]);

    const reference = within(dialog).getByLabelText("Reference");
    await userEvent.clear(reference);
    await userEvent.type(reference, "NCTS-2001");
    await typeReason(dialog, "The authority reissued the ticket.");
    await save(dialog);

    await screen.findByText("Saved. The tracking details are updated.");
    expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({
      external_reference: { system: "NCTS service desk", reference: "NCTS-2001" },
      reason: "The authority reissued the ticket.",
      expected_row_version: 6,
    });
  });

  it("clears the expected release by sending null", async () => {
    const { calls, dialog } = await openEditor([REVIEWER, detail(), SAVED]);

    await userEvent.clear(within(dialog).getByLabelText("Expected release"));
    await typeReason(dialog, "The authority withdrew the date.");
    await save(dialog);

    await screen.findByText("Saved. The tracking details are updated.");
    expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({
      expected_release: null,
      reason: "The authority withdrew the date.",
      expected_row_version: 6,
    });
  });

  it("sends nothing when nothing changed, and says so", async () => {
    const { calls, dialog, container } = await openEditor([REVIEWER, detail()]);

    await typeReason(dialog, "Checked.");
    await save(dialog);

    expect(
      await within(dialog).findAllByText(
        "You have not changed anything. Change a field, or cancel.",
      ),
    ).not.toHaveLength(0);
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
    await expectNoA11yViolations(container);
  });

  it("blocks the send for a missing reason and a blank reference part", async () => {
    const { calls, dialog, container } = await openEditor([REVIEWER, detail()]);

    await userEvent.clear(within(dialog).getByLabelText("System"));
    await save(dialog);

    for (const message of [
      "Enter the system that holds the request.",
      "Enter the reason for this change.",
    ]) {
      expect(await within(dialog).findAllByText(message)).not.toHaveLength(0);
    }
    expect(within(dialog).getByLabelText("System")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
    await expectNoA11yViolations(container);
  });

  it("refuses an expected release before the date raised", async () => {
    const { calls, dialog } = await openEditor([REVIEWER, detail()]);

    const release = within(dialog).getByLabelText("Expected release");
    await userEvent.clear(release);
    await userEvent.type(release, "2026-09-01");
    await typeReason(dialog, "Wrong date.");
    await save(dialog);

    expect(
      await within(dialog).findAllByText(
        /cannot be earlier than the date the request was raised/,
      ),
    ).not.toHaveLength(0);
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
  });

  it("marks the field a 422 names and keeps the dialog open", async () => {
    const { dialog, container } = await openEditor([
      REVIEWER,
      detail(),
      {
        method: "PATCH",
        path: REQUEST,
        status: 422,
        body: {
          detail: "The system has a character that cannot be shown.",
          field: "external_reference_system",
        },
      },
    ]);

    const system = within(dialog).getByLabelText("System");
    await userEvent.type(system, " 2");
    await typeReason(dialog, "Renamed.");
    await save(dialog);

    expect(
      await within(dialog).findAllByText(
        "The system has a character that cannot be shown.",
      ),
    ).not.toHaveLength(0);
    expect(system).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("dialog")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("says the page has reloaded when the version is stale, and keeps the reason", async () => {
    const { dialog } = await openEditor([
      REVIEWER,
      detail(),
      {
        method: "PATCH",
        path: REQUEST,
        status: 409,
        body: { detail: "Changed.", expected_row_version: 6, current_row_version: 7 },
      },
    ]);

    await userEvent.clear(within(dialog).getByLabelText("Expected release"));
    await typeReason(dialog, "The date is gone.");
    await save(dialog);

    expect(
      await within(dialog).findAllByText(/The page now shows the latest version/),
    ).not.toHaveLength(0);
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toHaveValue(
      "The date is gone.",
    );
  });

  it("shows the server's own sentence when the submission has moved on", async () => {
    const { dialog } = await openEditor([
      REVIEWER,
      detail(),
      {
        method: "PATCH",
        path: REQUEST,
        status: 409,
        body: { detail: "The submission is not waiting for a code." },
      },
    ]);

    await userEvent.clear(within(dialog).getByLabelText("Expected release"));
    await typeReason(dialog, "The date is gone.");
    await save(dialog);

    expect(
      await within(dialog).findAllByText("The submission is not waiting for a code."),
    ).not.toHaveLength(0);
  });

  it("closes without sending when the user cancels, and returns focus to the button", async () => {
    const { calls, dialog } = await openEditor([REVIEWER, detail()]);

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Edit tracking details" })).toHaveFocus(),
    );
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
  });
});
