import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * A reviewer's working copy on `/submissions/$submissionId` (FR-23): what the page
 * shows once one exists, and how it is edited. Driven through the real route,
 * signed in. The page's other behaviour is in `submission-detail.test.tsx`.
 */

const SIGNED_IN = { auth: { status: "signed-in" as const } };

const ID = "6f1d2c3e-0000-4000-8000-000000000027";
const DETAIL = `/api/v1/submissions/${ID}`;
const PROPERTIES = "/api/v1/registry/properties";

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

function definition(overrides: Record<string, unknown>) {
  return {
    key: "assay_method",
    label: "Assay method",
    datatype: "alpha",
    cardinality: "0..1",
    scope: "submission",
    required_for_submission: true,
    required_for_publication: false,
    binding_target: null,
    value_set_uri: null,
    strength: null,
    edition: null,
    local_code_system_key: null,
    filterable: false,
    origin: "admin",
    status: "active",
    display_order: 10,
    constraints: {},
    row_version: 1,
    form_control: { control: "text", params: {} },
    ...overrides,
  };
}

const DEFINITIONS: Route = {
  method: "GET",
  path: PROPERTIES,
  status: 200,
  body: {
    items: [
      definition({}),
      definition({
        key: "specimen",
        label: "Specimen",
        required_for_submission: false,
        display_order: 20,
      }),
    ],
  },
};

const NEW_CODE = "0391483001";
const NEW_FSN = "Microscopy (procedure)";

const PROCEDURES: Route = {
  method: "GET",
  path: "/terminology/procedures",
  status: 200,
  body: {
    items: [{ code: NEW_CODE, au_preferred_term: "Microscopy", label_provenance: {} }],
    total: 1,
  },
};

const NEW_CONCEPT: Route = {
  method: "GET",
  path: `/terminology/concepts/${NEW_CODE}`,
  status: 200,
  body: {
    system: "http://snomed.info/sct",
    code: NEW_CODE,
    fsn: NEW_FSN,
    au_preferred_term: "Microscopy",
    active: true,
    edition: "au",
    resolved_version: "http://snomed.info/sct/32506021000036107/version/20260101",
  },
};

/** What the submitter sent. */
const ORIGINAL = {
  preferred_term: "Ferritin level",
  synonyms: ["Serum ferritin", "Ferritin assay"],
  snomed_code: "0123456789",
  snomed_fsn: "Ferritin measurement (procedure)",
  property_values: {
    assay_method: [{ value: "Immunoassay", justification: "Most labs use it" }],
    specimen: [{ value: "Serum", justification: null }],
  },
  notes: "First line.",
};

function workingCopy(overrides: Record<string, unknown> = {}) {
  return {
    ...ORIGINAL,
    label_provenance: {},
    updated_at: "2026-10-16T09:00:00Z",
    ...overrides,
  };
}

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
      state: "Triage",
      entry_business_key: null,
      created_entry_business_key: null,
      ...ORIGINAL,
      created_at: "2026-09-01T12:00:00Z",
      is_own: false,
      has_registered_interest: false,
      interest_count: 2,
      label_provenance: {},
      row_version: 4,
      available_transitions: ["Under review", "Rejected"],
      reference_url: "https://example.org/ferritin",
      reference_checked_at: "2026-09-01T12:00:05Z",
      reference_status: 200,
      duplicate_confirmed_at: null,
      submitter: { username: "u-submitter" },
      ...(review ? { working_copy: null, terminology_request: null } : {}),
      ...overrides,
    },
  };
}

async function open(routes: Route[]) {
  const calls = stubApi(routes);
  const view = await renderRoute(`/submissions/${ID}`, SIGNED_IN);
  return { calls, ...view };
}

describe("a working copy on the page (FR-23)", () => {
  it("shows the working copy's content, and under each changed field what the submitter sent", async () => {
    const { container } = await open([
      REVIEWER,
      DEFINITIONS,
      detail({
        working_copy: workingCopy({
          preferred_term: "Serum ferritin level",
          synonyms: ["Ferritin assay", "Ferritin test"],
          snomed_code: "0123456790",
          snomed_fsn: "Ferritin level (procedure)",
          property_values: {
            assay_method: [{ value: "Immunoassay", justification: "Most labs use it" }],
            specimen: [{ value: "Plasma", justification: null }],
          },
          notes: "Second line.",
        }),
      }),
    ]);

    // The heading keeps the submitter's term, so the submission's identity holds.
    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    const main = screen.getByRole("main");
    expect(
      within(main).getByText(
        "A reviewer changed some of this content. Each changed field shows what the submitter sent.",
      ),
    ).toBeVisible();

    const nameRow = within(main).getByText("Test name").nextElementSibling as HTMLElement;
    expect(within(nameRow).getByText("Serum ferritin level")).toBeVisible();
    expect(within(nameRow).getByText("Changed from the original:")).toBeVisible();
    expect(within(nameRow).getByText("Ferritin level")).toBeVisible();

    const namesRow = within(main).getByText("Other names")
      .nextElementSibling as HTMLElement;
    expect(within(namesRow).getByText("Ferritin test")).toBeVisible();
    expect(within(namesRow).getByText("Serum ferritin, Ferritin assay")).toBeVisible();

    const codeRow = within(main).getByText("SNOMED CT code")
      .nextElementSibling as HTMLElement;
    expect(within(codeRow).getByText("0123456790")).toBeVisible();
    expect(within(codeRow).getByText("Ferritin level (procedure)")).toBeVisible();
    expect(within(codeRow).getByText("0123456789")).toBeVisible();

    const specimenRow = (await within(main).findByText("Specimen"))
      .nextElementSibling as HTMLElement;
    expect(within(specimenRow).getByText("Plasma")).toBeVisible();
    expect(within(specimenRow).getByText("Serum")).toBeVisible();

    const notes = within(main).getByRole("heading", { name: "Notes" }).parentElement!;
    expect(within(notes).getByText("Second line.")).toBeVisible();
    expect(within(notes).getByText("First line.")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("marks only the fields that changed", async () => {
    await open([
      REVIEWER,
      DEFINITIONS,
      detail({
        working_copy: workingCopy({
          synonyms: ["Serum ferritin", "Ferritin assay", "Ferritin test"],
        }),
      }),
    ]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    const main = screen.getByRole("main");
    expect(within(main).getAllByText("Changed from the original:")).toHaveLength(1);
    expect(within(main).queryByText("Test name")).toBeNull();
  });

  it("shows a property the reviewer emptied as none, with what the submitter sent", async () => {
    await open([
      REVIEWER,
      DEFINITIONS,
      detail({
        working_copy: workingCopy({
          property_values: {
            assay_method: [{ value: "Immunoassay", justification: "Most labs use it" }],
          },
        }),
      }),
    ]);

    const specimenRow = (await screen.findByText("Specimen"))
      .nextElementSibling as HTMLElement;
    expect(within(specimenRow).getByText("None")).toBeVisible();
    expect(within(specimenRow).getByText("Changed from the original:")).toBeVisible();
    expect(within(specimenRow).getByText("Serum")).toBeVisible();
  });

  it("shows a note the reviewer cleared as none, with what the submitter sent", async () => {
    await open([
      REVIEWER,
      DEFINITIONS,
      detail({ working_copy: workingCopy({ notes: null }) }),
    ]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    const notes = screen.getByRole("heading", { name: "Notes" }).parentElement!;
    expect(within(notes).getByText("No notes were added.")).toBeVisible();
    expect(within(notes).getByText("First line.")).toBeVisible();
  });

  it("adds no mark and no notice when the working copy equals the original", async () => {
    await open([REVIEWER, DEFINITIONS, detail({ working_copy: workingCopy() })]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(screen.queryByText("Changed from the original:")).toBeNull();
    expect(screen.queryByText(/A reviewer changed some of this content/)).toBeNull();
  });

  it("shows the original as it is when there is no working copy yet", async () => {
    await open([REVIEWER, DEFINITIONS, detail()]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(screen.getByText("Serum ferritin")).toBeVisible();
    expect(screen.queryByText("Changed from the original:")).toBeNull();
  });

  it("shows a Member the original only, because the server sends no working copy", async () => {
    await open([MEMBER, DEFINITIONS, detail({}, { review: false })]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(screen.getByText("Serum ferritin")).toBeVisible();
    expect(screen.queryByText("Changed from the original:")).toBeNull();
  });
});

describe("the Edit action (FR-23, FR-44, NFR-20)", () => {
  it("shows a Reviewer the Edit button on a submission in Triage", async () => {
    const { container } = await open([REVIEWER, DEFINITIONS, detail()]);

    expect(await screen.findByRole("button", { name: "Edit" })).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it.each(["Triage", "Under review", "Ready for approval"])(
    "offers it in %s",
    async (state) => {
      await open([REVIEWER, DEFINITIONS, detail({ state })]);

      expect(await screen.findByRole("button", { name: "Edit" })).toBeVisible();
    },
  );

  it.each(["Submitted", "Awaiting terminology", "Approved", "Rejected", "Withdrawn"])(
    "offers it in no %s submission",
    async (state) => {
      await open([REVIEWER, DEFINITIONS, detail({ state })]);

      await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
      expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    },
  );

  it("offers a Member no Edit button, because the server sends no working copy", async () => {
    await open([MEMBER, DEFINITIONS, detail({}, { review: false })]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("offers an Observer no Edit button", async () => {
    await open([me(["submission.view"]), DEFINITIONS, detail({}, { review: false })]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("offers no Edit button when the server sent no working copy, whatever the session says", async () => {
    await open([REVIEWER, DEFINITIONS, detail({}, { review: false })]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });
});

type Calls = ReturnType<typeof stubApi>;
type User = ReturnType<typeof userEvent.setup>;

const PATCH = `${DETAIL}/working-copy`;

const SAVED = {
  ...workingCopy({
    preferred_term: "Serum ferritin level",
    snomed_code: NEW_CODE,
    snomed_fsn: NEW_FSN,
    property_values: {
      assay_method: [{ value: "Chemiluminescence", justification: "Most labs use it" }],
      specimen: [{ value: "Serum", justification: null }],
    },
  }),
  submission_id: ID,
  row_version: 5,
  changed: true,
};

const OK: Route = { method: "PATCH", path: PATCH, status: 200, body: SAVED };

function refused(status: number, body: unknown): Route {
  return { method: "PATCH", path: PATCH, status, body };
}

/**
 * A stub whose detail changes once a write has landed, so only a read after it sees
 * the new content. `first` answers the first write and `then` each one after it.
 */
function stubEditing(
  first: Route,
  {
    then = [],
    after = {},
    base = {},
    before = [],
  }: {
    then?: Route[];
    after?: Record<string, unknown>;
    base?: Record<string, unknown>;
    /** Routes that answer ahead of the usual ones, to change one of them. */
    before?: Route[];
  } = {},
): Calls {
  const answers = [first, ...then];
  const calls: Calls = stubApi(
    [...before, REVIEWER, DEFINITIONS, PROCEDURES, NEW_CONCEPT, detail(base)],
    {
      vary: (call, prior) => {
        if (call.method === "PATCH" && call.path === PATCH) {
          return answers[Math.min(prior, answers.length - 1)];
        }
        const written = calls.some((sent) => sent.method === "PATCH");
        return call.method === "GET" && call.path === DETAIL && written
          ? detail({ ...base, ...after })
          : null;
      },
    },
  );
  return calls;
}

async function openForm() {
  const view = await renderRoute(`/submissions/${ID}`, SIGNED_IN);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await screen.findByRole("heading", { name: "Edit this submission" });
  return { user, ...view };
}

async function replace(user: User, label: string, text: string) {
  const box = screen.getByLabelText(label);
  await user.clear(box);
  await user.type(box, text);
}

async function save(user: User, reason: string) {
  if (reason !== "") {
    await user.type(screen.getByRole("textbox", { name: "Reason" }), reason);
  }
  await user.click(screen.getByRole("button", { name: "Save" }));
}

async function pickNewCode(user: User) {
  await user.type(screen.getByLabelText("Choose a different SNOMED CT code"), "micro");
  await user.click(
    await screen.findByRole("option", { name: new RegExp(NEW_CODE) }, { timeout: 2000 }),
  );
  await screen.findByText(NEW_FSN, {}, { timeout: 2000 });
}

function writes(calls: Calls) {
  return calls.filter((call) => call.method === "PATCH");
}

describe("editing a new test's working copy (FR-23)", () => {
  it("opens the form on the content, in place of the read view", async () => {
    const calls = stubEditing(OK);
    const { container } = await openForm();

    expect(screen.getByLabelText("Test name (required)")).toHaveValue("Ferritin level");
    expect(screen.getByLabelText("Assay method (required)")).toHaveValue("Immunoassay");
    expect(screen.getByLabelText("Specimen")).toHaveValue("Serum");
    expect(screen.getByLabelText("Notes")).toHaveValue("First line.");
    expect(screen.getByText("Serum ferritin")).toBeVisible();
    // The current code shows as it stands, with no lookup to name it.
    expect(screen.getByText("Current code")).toBeVisible();
    expect(screen.getByText("Ferritin measurement (procedure)")).toBeVisible();
    expect(calls.some((call) => call.path.includes("/terminology/concepts/"))).toBe(
      false,
    );
    expect(screen.queryByRole("heading", { name: "Proposed test" })).toBeNull();
    expect(screen.getByLabelText("Test name (required)")).toHaveFocus();
    await expectNoA11yViolations(container);
  });

  it("saves a new term, code and property, sends nothing else, and shows the working copy", async () => {
    const calls = stubEditing(OK, { after: { working_copy: SAVED, row_version: 5 } });
    const { user, container } = await openForm();

    await replace(user, "Test name (required)", "Serum ferritin level");
    await replace(user, "Assay method (required)", "Chemiluminescence");
    await pickNewCode(user);
    await save(user, "Matches the provider's wording.");

    const saved = await screen.findByText(
      "Saved. Changed: Test name, SNOMED CT code, Properties.",
    );
    expect(saved).toHaveFocus();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0].body).toEqual({
      preferred_term: "Serum ferritin level",
      snomed_code: NEW_CODE,
      property_values: {
        assay_method: [{ value: "Chemiluminescence", justification: "Most labs use it" }],
        specimen: [{ value: "Serum", justification: null }],
      },
      reason: "Matches the provider's wording.",
      expected_row_version: 4,
    });
    // The form has gone, and the page shows the working copy beside the original.
    expect(screen.queryByRole("heading", { name: "Edit this submission" })).toBeNull();
    const main = screen.getByRole("main");
    expect(await within(main).findByText("Serum ferritin level")).toBeVisible();
    expect(
      within(main).getAllByText("Changed from the original:").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByRole("heading", { level: 1, name: "Ferritin level" }),
    ).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("sends only the field that changed", async () => {
    const calls = stubEditing(OK);
    const { user } = await openForm();

    await replace(user, "Notes", "A new note.");
    await save(user, "Tidied the note.");

    await screen.findByText("Saved. Changed: Notes.");
    expect(writes(calls)[0].body).toEqual({
      notes: "A new note.",
      reason: "Tidied the note.",
      expected_row_version: 4,
    });
  });

  it("clears the note by sending it blank", async () => {
    const calls = stubEditing(OK);
    const { user } = await openForm();

    await user.clear(screen.getByLabelText("Notes"));
    await save(user, "The note was wrong.");

    await screen.findByText("Saved. Changed: Notes.");
    expect(writes(calls)[0].body).toMatchObject({ notes: "" });
  });

  it("keeps the current code when the user picks another and then goes back", async () => {
    const calls = stubEditing(OK);
    const { user } = await openForm();

    await replace(user, "Notes", "A new note.");
    await pickNewCode(user);
    await user.click(screen.getByRole("button", { name: "Keep the current code" }));
    expect(screen.getByText("Current code")).toBeVisible();
    await save(user, "Tidied the note.");

    await screen.findByText("Saved. Changed: Notes.");
    expect(writes(calls)[0].body).not.toHaveProperty("snomed_code");
  });

  it.each([
    ["blank", ""],
    ["spaces", "   "],
  ])("sends nothing for a %s reason and marks the field", async (_name, reason) => {
    const calls = stubEditing(OK);
    const { user, container } = await openForm();

    await replace(user, "Test name (required)", "Serum ferritin level");
    await save(user, reason);

    expect(
      await screen.findByRole("link", { name: "Enter the reason for this edit." }),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Reason" })).toBeInvalid();
    expect(writes(calls)).toHaveLength(0);
    await expectNoA11yViolations(container);
  });

  it("sends nothing and says so when nothing was changed", async () => {
    const calls = stubEditing(OK);
    const { user } = await openForm();

    await save(user, "No change needed.");

    expect(
      await screen.findByText(
        "You have not changed anything. Change a field, or cancel.",
      ),
    ).toBeVisible();
    expect(writes(calls)).toHaveLength(0);
  });

  it("asks for a test name before sending", async () => {
    const calls = stubEditing(OK);
    const { user } = await openForm();

    await user.clear(screen.getByLabelText("Test name (required)"));
    await save(user, "Removing it.");

    expect(
      await screen.findByRole("link", { name: "Enter the test name." }),
    ).toBeVisible();
    expect(writes(calls)).toHaveLength(0);
  });

  it("closes the form without a request and returns focus to Edit on Cancel", async () => {
    const calls = stubEditing(OK);
    const { user } = await openForm();

    await replace(user, "Test name (required)", "Something else");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(await screen.findByRole("heading", { name: "Proposed test" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Edit" })).toHaveFocus();
    expect(writes(calls)).toHaveLength(0);
    expect(screen.queryByText("Something else")).toBeNull();
  });

  it("marks the field a 422 names, and keeps what the user typed", async () => {
    stubEditing(
      refused(422, { detail: "That test name cannot be used.", field: "preferred_term" }),
    );
    const { user, container } = await openForm();

    await replace(user, "Test name (required)", "Bad name");
    await save(user, "A reason.");

    expect(
      await screen.findByRole("link", { name: "That test name cannot be used." }),
    ).toBeVisible();
    expect(screen.getByLabelText("Test name (required)")).toBeInvalid();
    expect(screen.getByLabelText("Test name (required)")).toHaveValue("Bad name");
    expect(screen.getByRole("textbox", { name: "Reason" })).toHaveValue("A reason.");
    await expectNoA11yViolations(container);
  });

  it("marks the property row a property issue names", async () => {
    stubEditing(
      refused(422, {
        detail: "A property value is not valid.",
        issues: [
          {
            property_key: "assay_method",
            label: "Assay method",
            code: "invalid_value",
            message: "Assay method must be a method on the list.",
            ordinal: 0,
          },
        ],
      }),
    );
    const { user } = await openForm();

    await replace(user, "Assay method (required)", "Guess");
    await save(user, "A reason.");

    expect(
      await screen.findByRole("link", {
        name: "Assay method must be a method on the list.",
      }),
    ).toBeVisible();
    expect(screen.getByLabelText("Assay method (required)")).toBeInvalid();
    expect(screen.getByLabelText("Specimen")).not.toBeInvalid();
  });

  it("marks the reason when the server refuses it", async () => {
    stubEditing(
      refused(422, {
        detail: "The reason has a character that cannot be kept.",
        field: "reason",
      }),
    );
    const { user } = await openForm();

    await replace(user, "Notes", "A new note.");
    await save(user, "Odd reason");

    expect(
      await screen.findByRole("link", {
        name: "The reason has a character that cannot be kept.",
      }),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Reason" })).toBeInvalid();
  });

  it("re-reads on a stale version, keeps the text and reason, and saves again on the new version", async () => {
    const calls = stubEditing(
      refused(409, {
        detail: "The submission changed after you read it.",
        current_row_version: 5,
      }),
      { then: [OK], after: { row_version: 5 } },
    );
    const { user } = await openForm();
    const reads = () =>
      calls.filter((call) => call.method === "GET" && call.path === DETAIL).length;

    await replace(user, "Test name (required)", "Serum ferritin level");
    await save(user, "Wording.");

    expect(
      await screen.findByText(/This submission changed since you opened the form\./),
    ).toBeVisible();
    await waitFor(() => expect(reads()).toBeGreaterThan(1));
    expect(screen.getByLabelText("Test name (required)")).toHaveValue(
      "Serum ferritin level",
    );
    expect(screen.getByRole("textbox", { name: "Reason" })).toHaveValue("Wording.");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("Saved. Changed: Test name.");
    expect(
      writes(calls).map(
        (call) => (call.body as Record<string, unknown>).expected_row_version,
      ),
    ).toEqual([4, 5]);
  });

  it("keeps the form and blocks Save once the submission has left an editable state", async () => {
    const calls = stubEditing(
      refused(409, { detail: "A submission in Approved cannot be edited." }),
      { after: { state: "Approved", row_version: 6, available_transitions: [] } },
    );
    const { user } = await openForm();

    await replace(user, "Test name (required)", "Serum ferritin level");
    await save(user, "Wording.");

    expect(
      await screen.findByText("A submission in Approved cannot be edited."),
    ).toBeVisible();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute(
        "aria-disabled",
        "true",
      ),
    );
    expect(screen.getByLabelText("Test name (required)")).toHaveValue(
      "Serum ferritin level",
    );

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText(/This submission can no longer be edited\./),
    ).toBeVisible();
    expect(writes(calls)).toHaveLength(1);
  });

  it("says the terminology server is down, and offers the current code back, when a picked code cannot be checked", async () => {
    stubEditing(OK, {
      before: [
        {
          method: "GET",
          path: `/terminology/concepts/${NEW_CODE}`,
          status: 503,
          body: { detail: "The terminology server could not be reached." },
        },
      ],
    });
    const { user } = await openForm();

    await user.type(screen.getByLabelText("Choose a different SNOMED CT code"), "micro");
    await user.click(
      await screen.findByRole(
        "option",
        { name: new RegExp(NEW_CODE) },
        { timeout: 2000 },
      ),
    );

    expect(
      await screen.findByText(
        /The terminology server could not be reached, so the new code could not be checked\. Keep the current code to save the other changes/,
        {},
        { timeout: 2000 },
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Keep the current code" }));
    expect(screen.getByText("Current code")).toBeVisible();
  });

  it("words a terminology outage at save for a form that holds a code", async () => {
    stubEditing(
      refused(503, {
        detail: "The terminology server could not be reached.",
        field: "snomed_code",
      }),
    );
    const { user } = await openForm();

    await replace(user, "Notes", "A new note.");
    await save(user, "A reason.");

    expect(
      await screen.findByRole("link", {
        name: /Keep the current code to save the other changes/,
      }),
    ).toBeVisible();
  });

  it("sends an empty list when the reviewer removes every other name", async () => {
    const calls = stubEditing(OK);
    const { user } = await openForm();

    await user.click(screen.getByRole("button", { name: "Remove Serum ferritin" }));
    await user.click(screen.getByRole("button", { name: "Remove Ferritin assay" }));
    await save(user, "Neither name is in use.");

    await screen.findByText("Saved. Changed: Other names.");
    expect(writes(calls)[0].body).toEqual({
      synonyms: [],
      reason: "Neither name is in use.",
      expected_row_version: 4,
    });
  });

  it("shows the server's sentence when the caller may not edit", async () => {
    stubEditing(
      refused(403, { detail: "You do not have permission to edit this submission." }),
    );
    const { user } = await openForm();

    await replace(user, "Notes", "A new note.");
    await save(user, "A reason.");

    expect(
      await screen.findByText("You do not have permission to edit this submission."),
    ).toBeVisible();
  });

  it("says nothing changed when the server found the edit made no difference", async () => {
    stubEditing({
      method: "PATCH",
      path: PATCH,
      status: 200,
      body: { ...SAVED, changed: false },
    });
    const { user } = await openForm();

    await replace(user, "Notes", "A new note.");
    await save(user, "A reason.");

    expect(
      await screen.findByText(
        "Nothing changed: the working copy already held these values.",
      ),
    ).toBeVisible();
  });
});

describe("editing an amendment's working copy (FR-23, FR-35)", () => {
  const amendment = {
    kind: "amendment",
    entry_business_key: "NPTC-000247",
    preferred_term: "Full blood count",
    snomed_code: null,
    snomed_fsn: null,
    property_values: {},
    notes: null,
  };

  it("shows only the other names and the code, and sends only what changed", async () => {
    const calls = stubEditing(OK, { base: amendment });
    const { user } = await openForm();

    expect(screen.queryByLabelText("Test name (required)")).toBeNull();
    expect(screen.queryByLabelText("Notes")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Properties" })).toBeNull();
    expect(screen.getByLabelText("Other names")).toHaveFocus();
    expect(screen.getByLabelText("SNOMED CT code")).toBeVisible();
    expect(calls.some((call) => call.path === PROPERTIES)).toBe(false);

    await user.type(screen.getByLabelText("Other names"), "FBC{Enter}");
    await save(user, "Common abbreviation.");

    await screen.findByText("Saved. Changed: Other names.");
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0].body).toEqual({
      synonyms: ["Serum ferritin", "Ferritin assay", "FBC"],
      reason: "Common abbreviation.",
      expected_row_version: 4,
    });
  });

  it("will not remove the last name when the amendment holds no code", async () => {
    const calls = stubEditing(OK, { base: { ...amendment, synonyms: ["FBC"] } });
    const { user } = await openForm();

    await user.click(screen.getByRole("button", { name: "Remove FBC" }));
    await save(user, "Removing it.");

    expect(
      await screen.findByRole("link", {
        name: "Keep at least one other name or a SNOMED CT code.",
      }),
    ).toBeVisible();
    expect(writes(calls)).toHaveLength(0);
  });
});
