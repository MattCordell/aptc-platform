import { screen, within } from "@testing-library/react";
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

const DEFINITIONS: Route = {
  method: "GET",
  path: PROPERTIES,
  status: 200,
  body: {
    items: [
      { key: "assay_method", label: "Assay method" },
      { key: "specimen", label: "Specimen" },
    ],
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
