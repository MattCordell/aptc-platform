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

const ADMINISTRATOR = me([
  "submission.transition.review",
  "submission.transition.approve",
  "interest.register",
]);

const CODE = "0391483001";
const FSN = "Ferritin measurement (procedure)";
const ATTACH = `${REQUEST}/attach-code`;

const PROCEDURES: Route = {
  method: "GET",
  path: "/terminology/procedures",
  status: 200,
  body: {
    items: [
      { code: CODE, au_preferred_term: "Ferritin measurement", label_provenance: {} },
    ],
    total: 1,
  },
};

const CONCEPT: Route = {
  method: "GET",
  path: `/terminology/concepts/${CODE}`,
  status: 200,
  body: {
    system: "http://snomed.info/sct",
    code: CODE,
    fsn: FSN,
    au_preferred_term: "Ferritin measurement",
    active: true,
    edition: "au",
    resolved_version: "http://snomed.info/sct/32506021000036107/version/20260101",
  },
};

const ATTACHED: Route = {
  method: "POST",
  path: ATTACH,
  status: 200,
  body: {
    id: ID,
    state: "Ready for approval",
    row_version: 7,
    available_transitions: ["Approved", "Rejected"],
    snomed_code: CODE,
    snomed_fsn: FSN,
    label_provenance: {},
  },
};

/** What the page reads after the attach: the code is in the working copy and the state has moved. */
const AFTER_ATTACH = detail({
  state: "Ready for approval",
  row_version: 7,
  available_transitions: ["Approved", "Rejected"],
  working_copy: {
    preferred_term: "Ferritin level",
    synonyms: [],
    snomed_code: CODE,
    snomed_fsn: FSN,
    property_values: {},
    notes: null,
    label_provenance: {},
    updated_at: "2026-10-17T09:00:00Z",
  },
});

describe("Attach code (FR-30)", () => {
  const WAITING_FOR_ADMINISTRATOR = detail({
    available_transitions: ["Under review", "Ready for approval"],
  });

  async function openAttach(routes: Route[], options?: Parameters<typeof stubApi>[1]) {
    const view = await open([ADMINISTRATOR, ...routes], options);
    await userEvent.click(await screen.findByRole("button", { name: "Attach code" }));
    const dialog = await screen.findByRole("dialog");
    return { ...view, dialog };
  }

  async function pickCode(dialog: HTMLElement) {
    await userEvent.type(within(dialog).getByLabelText("SNOMED CT code"), "ferr");
    const option = await screen.findByRole(
      "option",
      { name: new RegExp(CODE) },
      { timeout: 2000 },
    );
    // A modal dialog sits above the page, so a list outside it cannot be clicked.
    expect(dialog).toContainElement(option);
    await userEvent.click(option);
    await within(dialog).findByText(FSN, {}, { timeout: 2000 });
  }

  async function typeReason(dialog: HTMLElement, text: string) {
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), text);
  }

  async function attach(dialog: HTMLElement) {
    await userEvent.click(within(dialog).getByRole("button", { name: "Attach code" }));
  }

  it("shows an Administrator the action and a Reviewer none", async () => {
    const administrator = await open([ADMINISTRATOR, WAITING_FOR_ADMINISTRATOR]);

    expect(await screen.findByRole("button", { name: "Attach code" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Resume review" })).toBeVisible();
    await expectNoA11yViolations(administrator.container);
    administrator.unmount();

    // The server lists the move only for a caller who may make it.
    const reviewer = await open([REVIEWER, detail()]);

    expect(await screen.findByRole("button", { name: "Resume review" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Attach code" })).toBeNull();
    await expectNoA11yViolations(reviewer.container);
  });

  it("opens a form with the code picker and a reason", async () => {
    const { dialog, container } = await openAttach([WAITING_FOR_ADMINISTRATOR]);

    expect(within(dialog).getByRole("heading", { name: "Attach code" })).toBeVisible();
    expect(within(dialog).getByLabelText("SNOMED CT code")).toBeVisible();
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("blocks the send without a code and without a reason", async () => {
    const { calls, dialog, container } = await openAttach([WAITING_FOR_ADMINISTRATOR]);

    await attach(dialog);

    for (const message of [
      "Choose the code the terminology authority issued.",
      "Enter the reason for attaching this code.",
    ]) {
      expect(await within(dialog).findAllByText(message)).not.toHaveLength(0);
    }
    expect(within(dialog).getByLabelText("SNOMED CT code")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(calls.some((call) => call.method === "POST")).toBe(false);
    await expectNoA11yViolations(container);
  });

  it("shows the name the server returned for the pick, then attaches it and shows Ready for approval", async () => {
    let attached = false;
    const { calls, dialog, container } = await openAttach(
      [WAITING_FOR_ADMINISTRATOR, PROCEDURES, CONCEPT, ATTACHED],
      {
        vary: (call) => {
          attached ||= call.method === "POST" && call.path.endsWith("/attach-code");
          return attached && call.method === "GET" && call.path === DETAIL
            ? AFTER_ATTACH
            : null;
        },
      },
    );

    await pickCode(dialog);
    expect(within(dialog).getByText("Chosen code")).toBeVisible();
    await typeReason(dialog, "  Issued by the authority.  ");
    await attach(dialog);

    const confirmation = await screen.findByText(
      `Attached ${CODE}, ${FSN}. Moved to Ready for approval.`,
    );
    expect(confirmation).toHaveFocus();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      snomed_code: CODE,
      reason: "Issued by the authority.",
      expected_row_version: 6,
    });
    // The page now shows the state and the code the server wrote.
    const details = (await screen.findByRole("heading", { name: "Details" }))
      .parentElement as HTMLElement;
    expect(await within(details).findByText("Ready for approval")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Terminology request" })).toBeNull();
    expect(screen.getByRole("button", { name: "Approve" })).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("marks the code field when the server refuses the code, and keeps the dialog", async () => {
    const { dialog, container } = await openAttach([
      WAITING_FOR_ADMINISTRATOR,
      PROCEDURES,
      CONCEPT,
      {
        method: "POST",
        path: ATTACH,
        status: 422,
        body: {
          detail: `${CODE} is not active in the Australian edition.`,
          field: "snomed_code",
        },
      },
    ]);

    await pickCode(dialog);
    await typeReason(dialog, "Issued by the authority.");
    await attach(dialog);

    expect(
      await within(dialog).findAllByText(
        `${CODE} is not active in the Australian edition.`,
      ),
    ).not.toHaveLength(0);
    expect(within(dialog).getByLabelText("SNOMED CT code")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toHaveValue(
      "Issued by the authority.",
    );
    expect(screen.getByRole("dialog")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("stops showing a refusal once the user clears the code it was about", async () => {
    const { dialog } = await openAttach([
      WAITING_FOR_ADMINISTRATOR,
      PROCEDURES,
      CONCEPT,
      {
        method: "POST",
        path: ATTACH,
        status: 422,
        body: { detail: "That code is inactive.", field: "snomed_code" },
      },
    ]);

    await pickCode(dialog);
    await typeReason(dialog, "Issued by the authority.");
    await attach(dialog);
    await within(dialog).findAllByText("That code is inactive.");

    await userEvent.click(within(dialog).getByRole("button", { name: "Clear the code" }));

    await waitFor(() =>
      expect(within(dialog).queryByText("That code is inactive.")).toBeNull(),
    );
    expect(within(dialog).getByLabelText("SNOMED CT code")).not.toHaveAttribute(
      "aria-invalid",
    );
  });

  it("says nothing changed when the terminology server cannot be reached", async () => {
    const { dialog } = await openAttach([
      WAITING_FOR_ADMINISTRATOR,
      PROCEDURES,
      CONCEPT,
      {
        method: "POST",
        path: ATTACH,
        status: 503,
        body: {
          detail:
            "The terminology server could not be reached. Nothing was changed, and you can send the same request again.",
          field: "snomed_code",
        },
      },
    ]);

    await pickCode(dialog);
    await typeReason(dialog, "Issued by the authority.");
    await attach(dialog);

    expect(await within(dialog).findAllByText(/Nothing was changed/)).not.toHaveLength(0);
    expect(within(dialog).getByLabelText("SNOMED CT code")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("marks the reason when the server refuses it", async () => {
    const { dialog } = await openAttach([
      WAITING_FOR_ADMINISTRATOR,
      PROCEDURES,
      CONCEPT,
      {
        method: "POST",
        path: ATTACH,
        status: 422,
        body: {
          detail: "The reason has a character that cannot be shown.",
          field: "reason",
        },
      },
    ]);

    await pickCode(dialog);
    await typeReason(dialog, "Issued by the authority.");
    await attach(dialog);

    expect(
      await within(dialog).findAllByText(
        "The reason has a character that cannot be shown.",
      ),
    ).not.toHaveLength(0);
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("will not send a code the lookup could not name, and says the server is out of reach", async () => {
    const { calls, dialog } = await openAttach([
      WAITING_FOR_ADMINISTRATOR,
      PROCEDURES,
      {
        method: "GET",
        path: `/terminology/concepts/${CODE}`,
        status: 503,
        body: { detail: "The terminology server could not be reached." },
      },
    ]);

    await userEvent.type(within(dialog).getByLabelText("SNOMED CT code"), "ferr");
    await userEvent.click(
      await screen.findByRole("option", { name: new RegExp(CODE) }, { timeout: 2000 }),
    );
    await typeReason(dialog, "Issued by the authority.");
    await attach(dialog);

    expect(
      await within(dialog).findAllByText(
        "The terminology server could not be reached, so the code could not be checked. Try again shortly.",
      ),
    ).not.toHaveLength(0);
    expect(within(dialog).queryByText(/send the form without one/)).toBeNull();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("says the page has reloaded when the version is stale, and keeps the code and reason", async () => {
    const { calls, dialog } = await openAttach([
      WAITING_FOR_ADMINISTRATOR,
      PROCEDURES,
      CONCEPT,
      {
        method: "POST",
        path: ATTACH,
        status: 409,
        body: { detail: "Changed.", expected_row_version: 6, current_row_version: 7 },
      },
    ]);

    await pickCode(dialog);
    await typeReason(dialog, "Issued by the authority.");
    await attach(dialog);

    expect(
      await within(dialog).findAllByText(/The page now shows the latest version/),
    ).not.toHaveLength(0);
    expect(within(dialog).getByText(FSN)).toBeVisible();
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toHaveValue(
      "Issued by the authority.",
    );
    await waitFor(() =>
      expect(
        calls.filter((call) => call.method === "GET" && call.path === DETAIL).length,
      ).toBeGreaterThan(1),
    );
  });

  it.each([
    [403, "Only an Administrator may attach a code."],
    [409, "This submission is not Awaiting terminology."],
  ])("shows the server's own sentence for a %s", async (status, sentence) => {
    const { dialog } = await openAttach([
      WAITING_FOR_ADMINISTRATOR,
      PROCEDURES,
      CONCEPT,
      { method: "POST", path: ATTACH, status, body: { detail: sentence } },
    ]);

    await pickCode(dialog);
    await typeReason(dialog, "Issued by the authority.");
    await attach(dialog);

    expect(await within(dialog).findAllByText(sentence)).not.toHaveLength(0);
    expect(
      within(dialog).queryByText(/The page now shows the latest version/),
    ).toBeNull();
  });

  it("closes without sending when the user cancels", async () => {
    const { calls, dialog } = await openAttach([WAITING_FOR_ADMINISTRATOR]);

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Attach code" })).toBeVisible();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });
});
