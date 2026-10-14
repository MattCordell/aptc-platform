import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The `/submissions/$submissionId` page (FR-26, FR-27, FR-28, FR-32, FR-34,
 * FR-42, NFR-31), driven through the real route, signed in. The interest
 * button's own visibility rules are in `interest-button.test.tsx`; this file
 * covers the page and the register and revoke round trip through it.
 */

const SIGNED_IN = { auth: { status: "signed-in" as const } };

const ID = "6f1d2c3e-0000-4000-8000-000000000021";
const DETAIL = `/api/v1/submissions/${ID}`;
const INTEREST = `${DETAIL}/interest`;
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

function detail(overrides: Record<string, unknown> = {}): Route {
  return {
    method: "GET",
    path: DETAIL,
    status: 200,
    body: {
      id: ID,
      kind: "new_test",
      state: "Submitted",
      entry_business_key: null,
      preferred_term: "Ferritin level",
      snomed_code: "0123456789",
      snomed_fsn: "Ferritin measurement (procedure)",
      created_at: "2026-09-01T12:00:00Z",
      is_own: false,
      has_registered_interest: false,
      interest_count: 2,
      label_provenance: {},
      row_version: 1,
      synonyms: ["Serum ferritin", "Ferritin assay"],
      property_values: {
        assay_method: [{ value: "Immunoassay", justification: "Most labs use it" }],
        retired_key: [{ value: "Kept", justification: null }],
      },
      notes: "First line.\nSecond line.",
      reference_url: "https://example.org/ferritin",
      reference_checked_at: "2026-09-01T12:00:05Z",
      reference_status: 200,
      duplicate_confirmed_at: null,
      submitter: { username: "u-submitter" },
      ...overrides,
    },
  };
}

async function open(routes: Route[]) {
  const calls = stubApi(routes);
  const view = await renderRoute(`/submissions/${ID}`, SIGNED_IN);
  return { calls, ...view };
}

describe("submission detail content (FR-26, FR-27, FR-28, FR-34)", () => {
  it("shows what a new test proposes, labelled with the registry's labels", async () => {
    const { container } = await open([MEMBER, DEFINITIONS, detail()]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    const main = screen.getByRole("main");
    expect(within(main).getByRole("heading", { name: "Proposed test" })).toBeVisible();
    expect(within(main).getByText("Serum ferritin")).toBeVisible();
    expect(within(main).getByText("Ferritin assay")).toBeVisible();
    expect(within(main).getByText("0123456789")).toBeVisible();
    expect(within(main).getByText("Ferritin measurement (procedure)")).toBeVisible();
    expect(await within(main).findByText("Assay method")).toBeVisible();
    expect(within(main).getByText("Immunoassay")).toBeVisible();
    expect(within(main).getByText("Justification: Most labs use it")).toBeVisible();
    // A key the registry no longer lists falls back to the key itself.
    expect(within(main).getByText("retired_key")).toBeVisible();
    expect(within(main).getByText(/First line\./).textContent).toBe(
      "First line.\nSecond line.",
    );
    await expectNoA11yViolations(container);
  });

  it("shows each property under its key when the caller may not read the registry", async () => {
    await open([
      me([]),
      { method: "GET", path: PROPERTIES, status: 403, body: { detail: "no" } },
      detail(),
    ]);

    expect(await screen.findByText("assay_method")).toBeVisible();
    expect(screen.getByText("Immunoassay")).toBeVisible();
    expect(screen.queryByText("Assay method")).toBeNull();
  });

  it("does not ask for the property definitions when there is no value to label", async () => {
    const { calls } = await open([me([]), detail({ property_values: {} })]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(calls.some((call) => call.path === PROPERTIES)).toBe(false);
  });

  it("keeps a leading-zero code as the string it was sent (FR-06)", async () => {
    await open([MEMBER, DEFINITIONS, detail({ snomed_code: "0000012345" })]);

    expect(await screen.findByText("0000012345")).toBeVisible();
  });

  it("shows the state, date and submitter exactly as the server returned them", async () => {
    await open([MEMBER, DEFINITIONS, detail()]);

    const details = await screen.findByRole("complementary", {
      name: "Submission details",
    });
    expect(within(details).getByText("Submitted", { selector: "span" })).toBeVisible();
    expect(within(details).getByText("New test")).toBeVisible();
    expect(within(details).getByText("1 September 2026")).toBeVisible();
    expect(within(details).getByText("u-submitter")).toBeVisible();
  });

  it("shows the name and organisation when the server returns the identified submitter", async () => {
    await open([
      MEMBER,
      DEFINITIONS,
      detail({
        submitter: {
          username: "u-submitter",
          display_name: "Sam Submitter",
          organisation: "Acme Pathology",
        },
      }),
    ]);

    expect(await screen.findByText("Sam Submitter")).toBeVisible();
    expect(screen.getByText("Acme Pathology")).toBeVisible();
  });

  it("links an amendment to its entry and says the heading is not a rename", async () => {
    const { container } = await open([
      MEMBER,
      DEFINITIONS,
      detail({
        kind: "amendment",
        entry_business_key: "NPTC-000247",
        preferred_term: "Full blood count",
        property_values: {},
        snomed_code: null,
        snomed_fsn: null,
      }),
    ]);

    await screen.findByRole("heading", { level: 1, name: "Full blood count" });
    expect(screen.getByRole("heading", { name: "Proposed change" })).toBeVisible();
    expect(
      screen.getByText(
        /The heading shows the entry.s term when this change was proposed/,
      ),
    ).toBeVisible();
    const entryLink = screen.getByRole("link", { name: "NPTC-000247" });
    expect(entryLink).toHaveAttribute("href", "/catalogue/NPTC-000247");
    // The link sits inside a sentence, so colour alone cannot tell it apart
    // (axe `link-in-text-block`, which jsdom cannot evaluate).
    expect(entryLink).toHaveClass("underline");
    expect(screen.queryByRole("heading", { name: "Proposed properties" })).toBeNull();
    expect(screen.getByText("None proposed")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("shows the reference link with the time and status of its check", async () => {
    await open([MEMBER, DEFINITIONS, detail()]);

    const link = await screen.findByRole("link", {
      name: "https://example.org/ferritin",
    });
    expect(link).toHaveAttribute("href", "https://example.org/ferritin");
    expect(
      screen.getByText(/The server checked this link on/, { exact: false }),
    ).toHaveTextContent(
      "The server checked this link on 1 September 2026. The page answered with HTTP status 200.",
    );
  });

  it("says so plainly when there is no reference", async () => {
    await open([
      MEMBER,
      DEFINITIONS,
      detail({
        reference_url: null,
        reference_checked_at: null,
        reference_status: null,
      }),
    ]);

    expect(await screen.findByText("No supporting reference was given.")).toBeVisible();
  });

  it("says so plainly when a reference has no check yet", async () => {
    await open([
      MEMBER,
      DEFINITIONS,
      detail({ reference_checked_at: null, reference_status: null }),
    ]);

    expect(
      await screen.findByText("The server has not checked this link."),
    ).toBeVisible();
  });

  it("never turns a stored address that is not a web address into a link", async () => {
    await open([MEMBER, DEFINITIONS, detail({ reference_url: "javascript:alert(1)" })]);

    expect(await screen.findByText("javascript:alert(1)")).toBeVisible();
    expect(screen.queryByRole("link", { name: "javascript:alert(1)" })).toBeNull();
  });

  it("says so when no notes were added", async () => {
    await open([MEMBER, DEFINITIONS, detail({ notes: null })]);

    expect(await screen.findByText("No notes were added.")).toBeVisible();
  });
});

describe("submission detail interest (FR-32, FR-34, FR-42)", () => {
  it("shows the count to every signed-in user, with the line that interest does not decide approval", async () => {
    const { container } = await open([me([]), DEFINITIONS, detail()]);

    const interest = await screen.findByRole("heading", { name: "Interest" });
    const section = interest.parentElement as HTMLElement;
    expect(within(section).getByText("2")).toBeVisible();
    expect(
      within(section).getByText(
        "Interest helps reviewers set priority. It does not decide whether a submission is approved.",
      ),
    ).toBeVisible();
    expect(within(section).queryByRole("button")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("shows a withheld count as withheld, never as zero", async () => {
    await open([me([]), DEFINITIONS, detail({ interest_count: null })]);

    expect(
      await screen.findByText("The number of people interested is not shown to you."),
    ).toBeVisible();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("registers and withdraws, updating the count without a reload", async () => {
    const { calls, container } = await open([
      MEMBER,
      DEFINITIONS,
      detail(),
      {
        method: "PUT",
        path: INTEREST,
        status: 200,
        body: { has_registered_interest: true, interest_count: 3 },
      },
      {
        method: "DELETE",
        path: INTEREST,
        status: 200,
        body: { has_registered_interest: false, interest_count: 2 },
      },
    ]);
    const detailReads = () =>
      calls.filter((call) => call.method === "GET" && call.path === DETAIL).length;

    await userEvent.click(
      await screen.findByRole("button", { name: "Register interest" }),
    );

    const withdraw = await screen.findByRole("button", { name: "Withdraw interest" });
    expect(await screen.findByText("3")).toBeVisible();
    await expectNoA11yViolations(container);
    const readsAfterRegister = detailReads();

    await userEvent.click(withdraw);

    await screen.findByRole("button", { name: "Register interest" });
    expect(await screen.findByText("2")).toBeVisible();
    expect(screen.queryByText("3")).toBeNull();
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
    // The answer is written into the page, so no second read of the detail.
    expect(detailReads()).toBe(readsAfterRegister);
  });

  it("re-reads the submission when the server refuses a stale page", async () => {
    const { calls } = await open([
      MEMBER,
      DEFINITIONS,
      detail(),
      {
        method: "PUT",
        path: INTEREST,
        status: 409,
        body: { detail: "This submission is closed, so interest cannot be registered." },
      },
    ]);
    const detailReads = () =>
      calls.filter((call) => call.method === "GET" && call.path === DETAIL).length;
    await userEvent.click(
      await screen.findByRole("button", { name: "Register interest" }),
    );

    await waitFor(() => expect(detailReads()).toBeGreaterThan(1));
  });
});

describe("submission detail failures", () => {
  it("shows the not-found page for an unknown id", async () => {
    await open([
      MEMBER,
      { method: "GET", path: DETAIL, status: 404, body: { detail: "not found" } },
    ]);

    expect(
      await screen.findByRole("heading", { name: "We couldn't find that page" }),
    ).toBeVisible();
  });

  it("shows the not-found page for a malformed id", async () => {
    stubApi([
      MEMBER,
      {
        method: "GET",
        path: "/api/v1/submissions/not-a-uuid",
        status: 422,
        body: { detail: [{ loc: ["path", "submission_id"], msg: "bad uuid" }] },
      },
    ]);
    await renderRoute("/submissions/not-a-uuid", SIGNED_IN);

    expect(
      await screen.findByRole("heading", { name: "We couldn't find that page" }),
    ).toBeVisible();
  });

  it("offers a retry when the submission cannot be loaded", async () => {
    await open([
      MEMBER,
      { method: "GET", path: DETAIL, status: 500, body: { detail: "boom" } },
    ]);

    expect(
      await screen.findByText(
        "This submission could not be loaded. Try again in a moment.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
  });

  it("warns that the page may be out of date when a refresh fails over data on screen", async () => {
    let failing = false;
    stubApi([MEMBER, DEFINITIONS, detail()], {
      vary: (call) =>
        failing && call.method === "GET" && call.path === DETAIL
          ? { method: "GET", path: DETAIL, status: 500, body: { detail: "boom" } }
          : null,
    });
    const { queryClient } = await renderRoute(`/submissions/${ID}`, SIGNED_IN);
    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    failing = true;

    await queryClient.refetchQueries({
      queryKey: ["api", "/api/v1/submissions/{submission_id}"],
    });

    await waitFor(() =>
      expect(
        screen.getAllByText(
          "This submission could not be refreshed just now, so it may be out of date.",
        ),
      ).toHaveLength(2),
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Ferritin level" }),
    ).toBeVisible();
  });
});
