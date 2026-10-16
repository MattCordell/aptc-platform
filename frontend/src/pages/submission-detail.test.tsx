import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";
import { waitForSession } from "../test/wait-for-session.ts";

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
      created_entry_business_key: null,
      preferred_term: "Ferritin level",
      snomed_code: "0123456789",
      snomed_fsn: "Ferritin measurement (procedure)",
      created_at: "2026-09-01T12:00:00Z",
      is_own: false,
      has_registered_interest: false,
      interest_count: 2,
      label_provenance: {},
      row_version: 1,
      available_transitions: [],
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

describe("submission detail withdraw (FR-28)", () => {
  const WITHDRAW = `${DETAIL}/withdraw`;
  const SUBMITTER = me(["submission.withdraw.own", "interest.register"]);
  const detailReads = (calls: { method: string; path: string }[]) =>
    calls.filter((call) => call.method === "GET" && call.path === DETAIL).length;
  const WITHDRAWABLE = { is_own: true, available_transitions: ["Withdrawn"] };

  it("withdraws the submission and shows it as Withdrawn without a reload", async () => {
    const { calls, container } = await open([
      SUBMITTER,
      DEFINITIONS,
      detail({ ...WITHDRAWABLE, row_version: 5 }),
      {
        method: "POST",
        path: WITHDRAW,
        status: 200,
        body: { id: ID, state: "Withdrawn", row_version: 6, available_transitions: [] },
      },
    ]);

    await userEvent.click(
      await screen.findByRole("button", { name: "Withdraw submission" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Sent in error.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    expect(await screen.findByText("You withdrew this submission.")).toBeVisible();
    const details = screen.getByRole("complementary", { name: "Submission details" });
    expect(within(details).getByText("Withdrawn", { selector: "span" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Withdraw submission" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      reason: "Sent in error.",
      expected_row_version: 5,
    });
    await expectNoA11yViolations(container);
  });

  it("re-reads the submission and keeps the dialog open when the version is stale", async () => {
    const { calls } = await open([
      SUBMITTER,
      DEFINITIONS,
      detail(WITHDRAWABLE),
      {
        method: "POST",
        path: WITHDRAW,
        status: 409,
        body: { detail: "Changed.", expected_row_version: 1, current_row_version: 2 },
      },
    ]);

    await userEvent.click(
      await screen.findByRole("button", { name: "Withdraw submission" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Sent in error.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    await waitFor(() => expect(detailReads(calls)).toBeGreaterThan(1));
    expect(
      await within(dialog).findAllByText(/The page now shows the latest version/),
    ).not.toHaveLength(0);
    expect(screen.getByRole("dialog")).toBeVisible();
  });

  it("sends the new version when the user tries again after a stale refusal", async () => {
    let refused = false;
    const calls = stubApi(
      [
        SUBMITTER,
        DEFINITIONS,
        detail({ ...WITHDRAWABLE, row_version: 1 }),
        {
          method: "POST",
          path: WITHDRAW,
          status: 409,
          body: { detail: "Changed.", expected_row_version: 1, current_row_version: 2 },
        },
      ],
      {
        vary: (call, priorSameCalls) => {
          if (call.method === "POST" && call.path === WITHDRAW) {
            refused = true;
            return priorSameCalls === 0
              ? null
              : {
                  method: "POST",
                  path: WITHDRAW,
                  status: 200,
                  body: {
                    id: ID,
                    state: "Withdrawn",
                    row_version: 3,
                    available_transitions: [],
                  },
                };
          }
          return refused && call.method === "GET" && call.path === DETAIL
            ? detail({ ...WITHDRAWABLE, row_version: 2 })
            : null;
        },
      },
    );
    await renderRoute(`/submissions/${ID}`, SIGNED_IN);

    await userEvent.click(
      await screen.findByRole("button", { name: "Withdraw submission" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Sent in error.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );
    await within(dialog).findAllByText(/The page now shows the latest version/);
    await waitFor(() => expect(detailReads(calls)).toBeGreaterThan(1));

    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    expect(await screen.findByText("You withdrew this submission.")).toBeVisible();
    const posts = calls.filter((call) => call.method === "POST");
    expect(
      posts.map(
        (call) => (call.body as { expected_row_version: number }).expected_row_version,
      ),
    ).toEqual([1, 2]);
  });

  it("offers no second attempt once the re-read shows the submission closed", async () => {
    let refused = false;
    const calls = stubApi(
      [
        SUBMITTER,
        DEFINITIONS,
        detail(WITHDRAWABLE),
        {
          method: "POST",
          path: WITHDRAW,
          status: 409,
          body: { detail: "This submission is closed." },
        },
      ],
      {
        vary: (call) => {
          if (call.method === "POST" && call.path === WITHDRAW) {
            refused = true;
            return null;
          }
          return refused && call.method === "GET" && call.path === DETAIL
            ? detail({
                is_own: true,
                state: "Rejected",
                row_version: 2,
                available_transitions: [],
              })
            : null;
        },
      },
    );
    await renderRoute(`/submissions/${ID}`, SIGNED_IN);

    await userEvent.click(
      await screen.findByRole("button", { name: "Withdraw submission" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Sent in error.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    await waitFor(() => expect(detailReads(calls)).toBeGreaterThan(1));
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
      ).toHaveAttribute("aria-disabled", "true"),
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );

    expect(
      await within(dialog).findAllByText(/This submission can no longer be withdrawn./),
    ).not.toHaveLength(0);
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
    expect(screen.getByRole("dialog")).toBeVisible();
  });

  it("offers no button to another Member", async () => {
    await open([SUBMITTER, DEFINITIONS, detail({ is_own: false })]);
    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    expect(screen.queryByRole("button", { name: "Withdraw submission" })).toBeNull();
  });
});

describe("submission detail triage (FR-28, FR-81)", () => {
  const MOVES = `${DETAIL}/transitions`;
  const REVIEWER = me(["submission.transition.review", "interest.register"]);

  it("moves the submission and shows the new state and the next move without a reload", async () => {
    const { calls, container } = await open([
      REVIEWER,
      DEFINITIONS,
      detail({
        state: "Triage",
        row_version: 5,
        available_transitions: ["Under review", "Rejected", "Withdrawn"],
      }),
      {
        method: "POST",
        path: MOVES,
        status: 200,
        body: {
          id: ID,
          state: "Under review",
          row_version: 6,
          available_transitions: ["Ready for approval", "Awaiting terminology"],
        },
      },
    ]);

    await userEvent.click(await screen.findByRole("button", { name: "Start review" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Complete enough to assess.",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm move" }));

    expect(await screen.findByText("Moved to Under review.")).toBeVisible();
    const details = screen.getByRole("complementary", { name: "Submission details" });
    expect(within(details).getByText("Under review", { selector: "span" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Mark ready for approval" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Start review" })).toBeNull();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      to_state: "Under review",
      reason: "Complete enough to assess.",
      expected_row_version: 5,
    });
    await expectNoA11yViolations(container);
  });

  it("adds the move to History without a reload", async () => {
    const moved = {
      from_state: "Triage",
      to_state: "Rejected",
      reason: "Out of scope.",
      created_at: "2026-10-02T02:00:00Z",
      actor: { username: "jo" },
    };
    const history = (items: unknown[]): Route => ({
      method: "GET",
      path: MOVES,
      status: 200,
      body: { items },
    });
    let calls: ReturnType<typeof stubApi> = [];
    calls = stubApi(
      [
        REVIEWER,
        DEFINITIONS,
        detail({ state: "Triage", available_transitions: ["Under review", "Rejected"] }),
        history([]),
        {
          method: "POST",
          path: MOVES,
          status: 200,
          body: {
            id: ID,
            state: "Rejected",
            row_version: 2,
            available_transitions: ["Triage"],
          },
        },
      ],
      {
        // Only a read after the write sees the move, so a read that reuses the
        // cache would never show it.
        vary: (call) =>
          call.method === "GET" &&
          call.path === MOVES &&
          calls.some((sent) => sent.method === "POST")
            ? history([moved])
            : null,
      },
    );
    await renderRoute(`/submissions/${ID}`, SIGNED_IN);

    expect(
      await screen.findByText("This submission has not changed state yet."),
    ).toBeVisible();
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: /Reason/ }),
      "Out of scope.",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm move" }));

    const historySection = screen.getByRole("heading", {
      name: "History",
    }).parentElement!;
    expect(await within(historySection).findByText("Out of scope.")).toBeVisible();
    expect(within(historySection).getByText("Triage to Rejected")).toBeVisible();
  });

  it("offers a Member no Triage panel", async () => {
    await open([MEMBER, DEFINITIONS, detail({ available_transitions: ["Withdrawn"] })]);

    await screen.findByRole("heading", { name: "Ferritin level" });
    expect(screen.queryByRole("heading", { name: "Triage" })).toBeNull();
  });
});

describe("submission detail approval (FR-28, FR-35)", () => {
  const APPROVE = `${DETAIL}/approve`;
  const ADMINISTRATOR = me([
    "submission.transition.approve",
    "submission.transition.review",
    "interest.register",
  ]);
  const REVIEWER = me(["submission.transition.review", "interest.register"]);

  function approved(entryCreated: boolean, businessKey: string): Route {
    return {
      method: "POST",
      path: APPROVE,
      status: 200,
      body: {
        id: ID,
        state: "Approved",
        row_version: 8,
        available_transitions: [],
        entry_id: "9a1b2c3d-0000-4000-8000-000000000099",
        business_key: businessKey,
        entry_created: entryCreated,
        entry_row_version: 1,
      },
    };
  }

  async function approve(reason: string) {
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), reason);
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Confirm approval" }),
    );
  }

  it("approves a new test and links the entry it created, in the confirmation and in Details", async () => {
    const { calls, container } = await open([
      ADMINISTRATOR,
      DEFINITIONS,
      detail({
        state: "Ready for approval",
        row_version: 7,
        available_transitions: ["Approved", "Rejected"],
      }),
      approved(true, "NPTC-000123"),
    ]);

    await approve("  Checked against the source.  ");

    const confirmation = await screen.findByText(/^Approved\. Created entry/);
    expect(confirmation).toHaveFocus();
    const details = screen.getByRole("complementary", { name: "Submission details" });
    expect(within(details).getByText("Approved", { selector: "span" })).toBeVisible();
    const links = screen.getAllByRole("link", { name: "NPTC-000123" });
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/catalogue/NPTC-000123");
      expect(link).toHaveClass("underline");
    }
    expect(within(details).getByText(/New test, created as/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      reason: "Checked against the source.",
      expected_row_version: 7,
    });
    await expectNoA11yViolations(container);
  });

  it("approves an amendment and links the entry it changed", async () => {
    const { container } = await open([
      ADMINISTRATOR,
      DEFINITIONS,
      detail({
        kind: "amendment",
        entry_business_key: "NPTC-000247",
        state: "Ready for approval",
        row_version: 7,
        available_transitions: ["Approved", "Rejected"],
        property_values: {},
      }),
      approved(false, "NPTC-000247"),
    ]);

    await approve("Checked against the source.");

    expect(await screen.findByText(/^Approved\. Amended entry/)).toBeVisible();
    const links = screen.getAllByRole("link", { name: "NPTC-000247" });
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/catalogue/NPTC-000247");
    }
    await expectNoA11yViolations(container);
  });

  it("shows the created entry's link on a reload, when nothing remains to approve", async () => {
    const { container } = await open([
      ADMINISTRATOR,
      DEFINITIONS,
      detail({
        state: "Approved",
        created_entry_business_key: "NPTC-000123",
        available_transitions: [],
      }),
    ]);

    const link = await screen.findByRole("link", { name: "NPTC-000123" });
    expect(link).toHaveAttribute("href", "/catalogue/NPTC-000123");
    expect(screen.queryByRole("heading", { name: "Triage" })).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("offers a Reviewer no Approve button, because the server leaves Approved out of the list", async () => {
    await open([
      REVIEWER,
      DEFINITIONS,
      detail({ state: "Ready for approval", available_transitions: ["Rejected"] }),
    ]);

    expect(await screen.findByRole("button", { name: "Reject" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
});

describe("submission detail interest identities (FR-33, FR-44, NFR-20)", () => {
  const IDENTITIES: Route = {
    method: "GET",
    path: INTEREST,
    status: 200,
    body: {
      interest_count: 1,
      distinct_organisation_count: 1,
      distinct_organisations: [
        { organisation: "Acme Pathology", label: "Acme Pathology", count: 1 },
      ],
      records: [
        {
          username: "u-sam",
          display_name: "Sam Submitter",
          organisation: "Acme Pathology",
          registered_at: "2026-09-02T12:00:00Z",
        },
      ],
    },
  };

  it("shows who registered interest to a caller who may see identities", async () => {
    const { container } = await open([
      me(["interest.identities.view", "interest.register"]),
      DEFINITIONS,
      detail(),
      IDENTITIES,
    ]);

    const heading = await screen.findByRole("heading", {
      name: "Who registered interest",
    });
    const section = heading.parentElement as HTMLElement;
    expect(await within(section).findByText("Sam Submitter")).toBeVisible();
    expect(within(section).getByText("1 person from 1 organisation.")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("neither shows the panel nor asks the server for it for a Member", async () => {
    const { calls } = await open([MEMBER, DEFINITIONS, detail(), IDENTITIES]);

    await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
    await screen.findByRole("button", { name: "Register interest" });
    expect(screen.queryByRole("heading", { name: "Who registered interest" })).toBeNull();
    expect(calls.some((call) => call.method === "GET" && call.path === INTEREST)).toBe(
      false,
    );
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

  it("shows the server's reason and no retry when it refuses the caller", async () => {
    await open([
      MEMBER,
      {
        method: "GET",
        path: DETAIL,
        status: 403,
        body: {
          detail: "You must accept the current terms before you can view submissions.",
        },
      },
    ]);

    // Shown on the page and announced in the live region.
    await waitFor(() =>
      expect(
        screen.getAllByText(
          "You must accept the current terms before you can view submissions.",
        ),
      ).toHaveLength(2),
    );
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.queryByText(/Try again in a moment/)).toBeNull();
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

describe("the internal comments panel (FR-95, FR-44, NFR-20)", () => {
  const COMMENTS = `${DETAIL}/comments`;
  const STAFF = ["submission.view", "comment.internal.read", "comment.internal.write"];
  const COMMENT_LIST: Route = {
    method: "GET",
    path: COMMENTS,
    status: 200,
    body: {
      comments: [
        {
          id: "6f1d2c3e-0000-4000-8000-000000000072",
          body: "Check the specimen type.",
          created_at: "2026-10-01T09:00:00Z",
          author: { username: "rae", display_name: "Rae Reviewer" },
          withdrawn_at: null,
          withdrawn_by: null,
          withdrawn_reason: null,
        },
      ],
    },
  };

  it.each([
    ["a Reviewer", STAFF],
    ["an Administrator", [...STAFF, "interest.identities.view", "audit.read"]],
  ])("shows the panel below the content to %s", async (_who, permissions) => {
    const { container } = await open([
      me(permissions),
      DEFINITIONS,
      detail(),
      COMMENT_LIST,
    ]);

    const main = screen.getByRole("main");
    expect(
      await within(main).findByRole("heading", { name: "Internal comments" }),
    ).toBeVisible();
    expect(await within(main).findByText("Check the specimen type.")).toBeVisible();
    expect(within(main).getByRole("button", { name: "Post comment" })).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it.each([
    ["a Member", ["interest.register", "submission.view"]],
    [
      "the submitter",
      ["interest.register", "submission.view", "submission.withdraw.own"],
    ],
    ["an Observer", ["catalogue.read"]],
  ])(
    "leaves no trace of it for %s, and sends no comments request",
    async (_who, permissions) => {
      const { calls, container, queryClient } = await open([
        me(permissions),
        DEFINITIONS,
        detail({ is_own: _who === "the submitter" }),
        COMMENT_LIST,
      ]);

      await screen.findByRole("heading", { level: 1, name: "Ferritin level" });
      await waitForSession(queryClient);
      expect(screen.queryByRole("heading", { name: "Internal comments" })).toBeNull();
      expect(screen.queryByText("Check the specimen type.")).toBeNull();
      expect(calls.some((call) => call.path === COMMENTS)).toBe(false);
      await expectNoA11yViolations(container);
    },
  );
});
