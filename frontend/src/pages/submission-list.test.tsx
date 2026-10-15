import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The `/submissions` list (FR-23, FR-28, FR-34, FR-42, NFR-31), driven through
 * the real route, signed in. Requests are told apart by their query string,
 * never by a call count: `<StrictMode>` fetches the first page twice.
 */

const SIGNED_IN = { auth: { status: "signed-in" as const } };

const OWN_ID = "6f1d2c3e-0000-4000-8000-000000000001";
const AMENDMENT_ID = "6f1d2c3e-0000-4000-8000-000000000002";

function summary(overrides: Record<string, unknown>) {
  return {
    id: "6f1d2c3e-0000-4000-8000-0000000000ff",
    kind: "new_test",
    state: "Submitted",
    entry_business_key: null,
    preferred_term: "Placeholder",
    snomed_code: null,
    snomed_fsn: null,
    created_at: "2026-09-01T12:00:00Z",
    is_own: false,
    has_registered_interest: false,
    interest_count: 0,
    label_provenance: {},
    submitter: { username: "someone" },
    ...overrides,
  };
}

const NEW_TEST_ROW = summary({
  id: OWN_ID,
  preferred_term: "Ferritin level",
  submitter: { username: "u-submitter" },
  interest_count: 3,
});
const AMENDMENT_ROW = summary({
  id: AMENDMENT_ID,
  kind: "amendment",
  entry_business_key: "NPTC-000247",
  preferred_term: "Full blood count",
  state: "Withdrawn",
  submitter: { username: "u-other" },
  interest_count: 1,
});

function listRoute(items: unknown[], next_cursor: string | null = null): Route {
  return {
    method: "GET",
    path: "/api/v1/submissions",
    status: 200,
    body: { items, next_cursor },
  };
}

function lastListCall(calls: ReturnType<typeof stubApi>) {
  const listCalls = calls.filter((call) => call.path === "/api/v1/submissions");
  return listCalls[listCalls.length - 1];
}

describe("submissions page (FR-23)", () => {
  it("shows the heading and a Create submission link to the new-test form", async () => {
    stubApi([listRoute([])]);
    await renderRoute("/submissions", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Submissions" });
    expect(
      within(screen.getByRole("main")).getByRole("link", { name: "Create submission" }),
    ).toHaveAttribute("href", "/submissions/new");
  });

  it("opens the new-test form from the link", async () => {
    const user = userEvent.setup();
    stubApi([listRoute([])]);
    const { router } = await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("link", { name: "Create submission" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/submissions/new"));
  });
});

describe("All submissions view (FR-28, FR-34)", () => {
  it("shows each submission's term, kind, state, date, submitter and interest", async () => {
    stubApi([listRoute([NEW_TEST_ROW, AMENDMENT_ROW])]);
    await renderRoute("/submissions", SIGNED_IN);

    const table = await screen.findByRole("table", { name: "Submissions" });
    const newTest = within(table).getByRole("row", { name: /Ferritin level/ });
    expect(within(newTest).getByRole("link", { name: "Ferritin level" })).toHaveAttribute(
      "href",
      `/submissions/${OWN_ID}`,
    );
    expect(within(newTest).getByText("New test")).toBeVisible();
    expect(within(newTest).getByText("Submitted")).toBeVisible();
    expect(within(newTest).getByText("1 September 2026")).toBeVisible();
    expect(within(newTest).getByText("u-submitter")).toBeVisible();
    expect(within(newTest).getByText("3")).toBeVisible();
  });

  it("shows an amendment with the entry it amends, linked to that entry", async () => {
    stubApi([listRoute([NEW_TEST_ROW, AMENDMENT_ROW])]);
    await renderRoute("/submissions", SIGNED_IN);

    const row = await screen.findByRole("row", { name: /Full blood count/ });
    expect(within(row).getByText(/Amendment to/)).toBeVisible();
    expect(within(row).getByRole("link", { name: "NPTC-000247" })).toHaveAttribute(
      "href",
      "/catalogue/NPTC-000247",
    );
    expect(within(row).getByText("Withdrawn")).toBeVisible();
  });

  it("shows a kind it does not know as the server sent it", async () => {
    stubApi([
      listRoute([summary({ id: OWN_ID, preferred_term: "Odd one", kind: "merge" })]),
    ]);
    await renderRoute("/submissions", SIGNED_IN);

    const row = await screen.findByRole("row", { name: /Odd one/ });
    expect(within(row).getByText("merge")).toBeVisible();
  });

  it("sends a bare request: no state, no mine", async () => {
    const calls = stubApi([listRoute([NEW_TEST_ROW])]);
    await renderRoute("/submissions", SIGNED_IN);

    await screen.findByRole("table", { name: "Submissions" });
    const params = lastListCall(calls)?.searchParams;
    expect(params?.has("state")).toBe(false);
    expect(params?.has("mine")).toBe(false);
  });

  it("marks All submissions as the current view", async () => {
    stubApi([listRoute([])]);
    await renderRoute("/submissions", SIGNED_IN);

    await screen.findByRole("table", { name: "Submissions" });
    expect(screen.getByRole("link", { name: "All submissions" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "My submissions" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it.each([
    ["/submissions?state=Withdrawn", "All submissions"],
    ["/submissions?after=cursor-two", "All submissions"],
    ["/submissions?mine=true&state=open", "My submissions"],
    ["/submissions?mine=true&after=cursor-two", "My submissions"],
  ])("marks only the right tab as current at %s", async (url, current) => {
    stubApi([listRoute([])]);
    await renderRoute(url, SIGNED_IN);

    await screen.findByRole("table");
    const other = current === "All submissions" ? "My submissions" : "All submissions";
    expect(screen.getByRole("link", { name: current })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: other })).not.toHaveAttribute("aria-current");
  });

  it("says there are no open submissions when the list is empty", async () => {
    stubApi([listRoute([])]);
    await renderRoute("/submissions", SIGNED_IN);

    expect(await screen.findByText("There are no open submissions.")).toBeVisible();
  });

  it("has one h1 and no axe violations", async () => {
    stubApi([listRoute([NEW_TEST_ROW, AMENDMENT_ROW])]);
    const { container } = await renderRoute("/submissions", SIGNED_IN);

    await screen.findByRole("table", { name: "Submissions" });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await expectNoA11yViolations(container);
  });
});

describe("My submissions view (FR-28)", () => {
  it("asks for the caller's own submissions and marks the tab as current", async () => {
    const calls = stubApi([listRoute([NEW_TEST_ROW])]);
    await renderRoute("/submissions?mine=true", SIGNED_IN);

    await screen.findByRole("table", { name: "My submissions" });
    const params = lastListCall(calls)?.searchParams;
    expect(params?.get("mine")).toBe("true");
    expect(params?.has("state")).toBe(false);
    expect(screen.getByRole("link", { name: "My submissions" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "All submissions" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("switches views through the tab links, each its own URL", async () => {
    const user = userEvent.setup();
    stubApi([listRoute([])]);
    const { router } = await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("link", { name: "My submissions" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ mine: "true" }));

    await user.click(screen.getByRole("link", { name: "All submissions" }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it("says the caller has no submissions yet when the view is empty", async () => {
    stubApi([listRoute([])]);
    await renderRoute("/submissions?mine=true", SIGNED_IN);

    expect(await screen.findByText("You have no submissions yet.")).toBeVisible();
  });

  it("shows every state by default, so a withdrawn submission is not hidden", async () => {
    stubApi([listRoute([AMENDMENT_ROW])]);
    await renderRoute("/submissions?mine=true", SIGNED_IN);

    await screen.findByRole("table", { name: "My submissions" });
    expect(screen.getByRole("combobox", { name: "State" })).toHaveValue("all");
    expect(screen.getByText("Withdrawn", { selector: "span" })).toBeVisible();
  });

  it("treats mine=false as the All view, not as a mine request", async () => {
    const calls = stubApi([listRoute([])]);
    await renderRoute("/submissions?mine=false", SIGNED_IN);

    await screen.findByRole("table", { name: "Submissions" });
    expect(lastListCall(calls)?.searchParams.has("mine")).toBe(false);
    expect(screen.getByRole("link", { name: "All submissions" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("has one h1 and no axe violations", async () => {
    stubApi([listRoute([NEW_TEST_ROW])]);
    const { container } = await renderRoute("/submissions?mine=true", SIGNED_IN);

    await screen.findByRole("table", { name: "My submissions" });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await expectNoA11yViolations(container);
  });
});

describe("state filter (FR-28)", () => {
  it("defaults to Open in the All view", async () => {
    stubApi([listRoute([])]);
    await renderRoute("/submissions", SIGNED_IN);

    expect(await screen.findByRole("combobox", { name: "State" })).toHaveValue("open");
  });

  it("offers Open, All states and each of the five states", async () => {
    stubApi([listRoute([])]);
    await renderRoute("/submissions", SIGNED_IN);

    const select = await screen.findByRole("combobox", { name: "State" });
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([
      "Open",
      "All states",
      "Submitted",
      "Triage",
      "Under review",
      "Awaiting terminology",
      "Ready for approval",
      "Approved",
      "Published in release",
      "Rejected",
      "Withdrawn",
    ]);
  });

  it("puts a chosen state in the URL and the request", async () => {
    const user = userEvent.setup();
    const calls = stubApi([listRoute([AMENDMENT_ROW])]);
    const { router } = await renderRoute("/submissions", SIGNED_IN);

    await user.selectOptions(
      await screen.findByRole("combobox", { name: "State" }),
      "Withdrawn",
    );

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ state: "Withdrawn" }),
    );
    await waitFor(() =>
      expect(lastListCall(calls)?.searchParams.getAll("state")).toEqual(["Withdrawn"]),
    );
  });

  it("restores a filtered view from a pasted link", async () => {
    const calls = stubApi([listRoute([AMENDMENT_ROW])]);
    await renderRoute("/submissions?state=Withdrawn", SIGNED_IN);

    expect(await screen.findByRole("combobox", { name: "State" })).toHaveValue(
      "Withdrawn",
    );
    await screen.findByRole("table", { name: "Submissions" });
    expect(lastListCall(calls)?.searchParams.getAll("state")).toEqual(["Withdrawn"]);
  });

  it("sends the open states when Open is chosen in My submissions", async () => {
    const user = userEvent.setup();
    const calls = stubApi([listRoute([])]);
    const { router } = await renderRoute("/submissions?mine=true", SIGNED_IN);

    await user.selectOptions(
      await screen.findByRole("combobox", { name: "State" }),
      "open",
    );

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ mine: "true", state: "open" }),
    );
    await waitFor(() =>
      expect(lastListCall(calls)?.searchParams.getAll("state")).toEqual([
        "Submitted",
        "Triage",
        "Under review",
        "Awaiting terminology",
        "Ready for approval",
        "Approved",
      ]),
    );
  });

  it("returns to the plain URL when the view's default is chosen again", async () => {
    const user = userEvent.setup();
    stubApi([listRoute([])]);
    const { router } = await renderRoute("/submissions?state=Withdrawn", SIGNED_IN);

    await user.selectOptions(
      await screen.findByRole("combobox", { name: "State" }),
      "open",
    );

    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it("falls back to the default for a state it does not know", async () => {
    const calls = stubApi([listRoute([])]);
    await renderRoute("/submissions?state=Escalated", SIGNED_IN);

    expect(await screen.findByRole("combobox", { name: "State" })).toHaveValue("open");
    await screen.findByRole("table", { name: "Submissions" });
    expect(lastListCall(calls)?.searchParams.has("state")).toBe(false);
  });

  it("names the state in the empty message of a filtered view", async () => {
    stubApi([listRoute([])]);
    await renderRoute("/submissions?state=Rejected", SIGNED_IN);

    expect(await screen.findByText("There are no Rejected submissions.")).toBeVisible();
  });
});

describe("the submitter and the interest count (FR-42, NFR-20)", () => {
  it("shows only the username when the server returned no identity", async () => {
    stubApi([listRoute([NEW_TEST_ROW])]);
    await renderRoute("/submissions", SIGNED_IN);

    const row = await screen.findByRole("row", { name: /Ferritin level/ });
    expect(within(row).getByText("u-submitter")).toBeVisible();
    expect(within(row).queryByText(/Example Pathology/)).not.toBeInTheDocument();
  });

  it("shows the name and organisation when the server returned them", async () => {
    const identified = summary({
      id: OWN_ID,
      preferred_term: "Ferritin level",
      submitter: {
        username: "u-submitter",
        display_name: "Sam Submitter",
        organisation: "Example Pathology",
      },
    });
    stubApi([listRoute([identified])]);
    await renderRoute("/submissions", SIGNED_IN);

    const row = await screen.findByRole("row", { name: /Ferritin level/ });
    expect(within(row).getByText("Sam Submitter")).toBeVisible();
    expect(within(row).getByText("Example Pathology")).toBeVisible();
  });

  it("shows no organisation line when the submitter sent none", async () => {
    const identified = summary({
      id: OWN_ID,
      preferred_term: "Ferritin level",
      submitter: {
        username: "u-submitter",
        display_name: "Sam Submitter",
        organisation: null,
      },
    });
    stubApi([listRoute([identified])]);
    await renderRoute("/submissions", SIGNED_IN);

    const row = await screen.findByRole("row", { name: /Ferritin level/ });
    expect(within(row).getByText("Sam Submitter")).toBeVisible();
    expect(within(row).getByText("Sam Submitter").parentElement?.children).toHaveLength(
      1,
    );
  });

  it("shows a withheld interest count as withheld, never as zero", async () => {
    const withheld = summary({
      id: OWN_ID,
      preferred_term: "Ferritin level",
      interest_count: null,
    });
    stubApi([listRoute([withheld])]);
    await renderRoute("/submissions", SIGNED_IN);

    const row = await screen.findByRole("row", { name: /Ferritin level/ });
    expect(within(row).getByText("Not shown")).toBeVisible();
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
  });

  it("shows a count of zero as zero", async () => {
    const none = summary({
      id: OWN_ID,
      preferred_term: "Ferritin level",
      interest_count: 0,
    });
    stubApi([listRoute([none])]);
    await renderRoute("/submissions", SIGNED_IN);

    const row = await screen.findByRole("row", { name: /Ferritin level/ });
    expect(within(row).getByText("0")).toBeVisible();
  });
});

describe("failures", () => {
  it("shows a message and no table when the list cannot be loaded", async () => {
    stubApi([{ method: "GET", path: "/api/v1/submissions", status: 500, body: {} }]);
    await renderRoute("/submissions", SIGNED_IN);

    expect(await screen.findByText(/could not be loaded/i)).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says why when the caller may not view submissions", async () => {
    stubApi([
      {
        method: "GET",
        path: "/api/v1/submissions",
        status: 403,
        body: { detail: "You may not view submissions." },
      },
    ]);
    await renderRoute("/submissions", SIGNED_IN);

    expect(await screen.findByText("You may not view submissions.")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("paging (ADR-0024)", () => {
  const FIRST_PAGE = listRoute([NEW_TEST_ROW], "cursor-two");
  const SECOND_PAGE = listRoute([AMENDMENT_ROW]);

  function pagedApi() {
    return stubApi([], {
      vary: ({ path, searchParams }) =>
        path === "/api/v1/submissions"
          ? searchParams.get("after") === "cursor-two"
            ? SECOND_PAGE
            : FIRST_PAGE
          : null,
    });
  }

  it("sends the next_cursor as after, and goes back to the first page", async () => {
    const user = userEvent.setup();
    const calls = pagedApi();
    const { router } = await renderRoute("/submissions", SIGNED_IN);

    await screen.findByRole("row", { name: /Ferritin level/ });
    await user.click(screen.getByRole("button", { name: "Next page" }));

    await screen.findByRole("row", { name: /Full blood count/ });
    expect(router.state.location.search).toEqual({ after: "cursor-two" });
    expect(lastListCall(calls)?.searchParams.get("after")).toBe("cursor-two");

    await user.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByRole("row", { name: /Ferritin level/ });
    expect(router.state.location.search).toEqual({});
  });

  it("drops the cursor when the state changes", async () => {
    const user = userEvent.setup();
    const calls = pagedApi();
    const { router } = await renderRoute("/submissions?after=cursor-two", SIGNED_IN);

    await screen.findByRole("row", { name: /Full blood count/ });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "State" }),
      "Withdrawn",
    );

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ state: "Withdrawn" }),
    );
    await waitFor(() =>
      expect(lastListCall(calls)?.searchParams.has("after")).toBe(false),
    );
  });

  it("drops the cursor when the view changes", async () => {
    const user = userEvent.setup();
    const calls = pagedApi();
    const { router } = await renderRoute("/submissions?after=cursor-two", SIGNED_IN);

    await screen.findByRole("row", { name: /Full blood count/ });
    await user.click(screen.getByRole("link", { name: "My submissions" }));

    await waitFor(() => expect(router.state.location.search).toEqual({ mine: "true" }));
    await waitFor(() =>
      expect(lastListCall(calls)?.searchParams.get("mine")).toBe("true"),
    );
    expect(lastListCall(calls)?.searchParams.has("after")).toBe(false);
  });
});
