import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/query-client.ts";
import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { stubApi, type Route } from "../test/stub-api.ts";
import { InterestIdentitiesPanel } from "./interest-identities-panel.tsx";

/**
 * The interest identities panel (FR-33, FR-44, NFR-20, NFR-31). It shows only for
 * a caller holding `interest.identities.view`, and for anyone else the identities
 * request is never sent.
 */

const ID = "6f1d2c3e-0000-4000-8000-000000000041";
const INTEREST = `/api/v1/submissions/${ID}/interest`;

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

const REVIEWER = session(["interest.identities.view"]);

const IDENTITIES: Route = {
  method: "GET",
  path: INTEREST,
  status: 200,
  body: {
    interest_count: 3,
    distinct_organisation_count: 1,
    distinct_organisations: [
      { organisation: "Acme Pathology", label: "Acme Pathology", count: 2 },
      { organisation: null, label: "No organisation given", count: 1 },
    ],
    records: [
      {
        username: "u-closed",
        display_name: "Closed account",
        organisation: null,
        registered_at: "2026-09-03T12:00:00Z",
      },
      {
        username: "u-sam",
        display_name: "Sam Submitter",
        organisation: "Acme Pathology",
        registered_at: "2026-09-02T12:00:00Z",
      },
      {
        username: "u-kim",
        display_name: "Kim Keen",
        organisation: "Acme Pathology",
        registered_at: "2026-09-01T12:00:00Z",
      },
    ],
  },
};

function renderPanel() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <AuthContext.Provider value={AUTH}>
        <InterestIdentitiesPanel submissionId={ID} />
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

async function settled(calls: ReturnType<typeof stubApi>) {
  await waitFor(() =>
    expect(calls.some((call) => call.path === "/api/v1/auth/me")).toBe(true),
  );
  // The panel is absent until the session answers, so wait for the answer to
  // land before asserting that nothing rendered and nothing was requested.
  await new Promise((resolve) => setTimeout(resolve, 50));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("InterestIdentitiesPanel", () => {
  it("lists each person and each organisation for a Reviewer", async () => {
    stubApi([REVIEWER, IDENTITIES]);
    const { container } = renderPanel();

    const heading = await screen.findByRole("heading", {
      name: "Who registered interest",
    });
    const section = heading.parentElement as HTMLElement;
    expect(
      await within(section).findByText("3 people from 1 organisation."),
    ).toBeVisible();

    const organisations = within(section).getAllByRole("list")[0] as HTMLElement;
    expect(within(organisations).getByText("Acme Pathology")).toBeVisible();
    expect(within(organisations).getByText("2")).toBeVisible();

    const people = within(section).getAllByRole("list")[1] as HTMLElement;
    expect(within(people).getAllByRole("listitem")).toHaveLength(3);
    expect(within(people).getByText("Sam Submitter")).toBeVisible();
    expect(within(people).getByText("(u-sam)")).toBeVisible();
    expect(within(people).getByText("2 September 2026", { exact: false })).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("shows a closed account's placeholder and the missing-organisation label as sent", async () => {
    stubApi([REVIEWER, IDENTITIES]);
    renderPanel();

    expect(await screen.findByText("Closed account")).toBeVisible();
    expect(screen.getByText("(u-closed)")).toBeVisible();
    // Once in the organisation list and once on the person with none.
    expect(screen.getAllByText("No organisation given")).toHaveLength(2);
  });

  it("says so when no one has registered interest", async () => {
    stubApi([
      REVIEWER,
      {
        method: "GET",
        path: INTEREST,
        status: 200,
        body: {
          interest_count: 0,
          distinct_organisation_count: 0,
          distinct_organisations: [],
          records: [],
        },
      },
    ]);
    const { container } = renderPanel();

    expect(await screen.findByText("No one has registered interest yet.")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  // The principal failure mode (NFR-20): a caller without the permission must
  // never cost the server a 403.
  it.each([
    ["a Member", ["interest.register", "submission.view"]],
    ["an Observer", ["catalogue.read"]],
  ])("is absent for %s, and sends no identities request", async (_who, permissions) => {
    const calls = stubApi([session(permissions), IDENTITIES]);
    const { container } = renderPanel();

    await settled(calls);
    expect(screen.queryByRole("heading")).toBeNull();
    expect(calls.some((call) => call.path === INTEREST)).toBe(false);
    await expectNoA11yViolations(container);
  });

  it("offers a retry when the list cannot be loaded", async () => {
    stubApi([
      REVIEWER,
      { method: "GET", path: INTEREST, status: 500, body: { detail: "boom" } },
    ]);
    const { container } = renderPanel();

    expect(
      await screen.findByText(
        "Who registered interest could not be loaded. Try again in a moment.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("shows the server's reason and no retry when it refuses the caller", async () => {
    stubApi([
      REVIEWER,
      {
        method: "GET",
        path: INTEREST,
        status: 403,
        body: { detail: "You may not see who registered interest." },
      },
    ]);
    renderPanel();

    expect(
      await screen.findByText("You may not see who registered interest."),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("reads the list again when the user retries", async () => {
    let failing = true;
    const calls = stubApi([REVIEWER, IDENTITIES], {
      vary: (call) =>
        failing && call.method === "GET" && call.path === INTEREST
          ? { method: "GET", path: INTEREST, status: 500, body: { detail: "boom" } }
          : null,
    });
    renderPanel();
    const retry = await screen.findByRole("button", { name: "Try again" });
    failing = false;
    await userEvent.click(retry);

    expect(await screen.findByText("3 people from 1 organisation.")).toBeVisible();
    expect(calls.filter((call) => call.path === INTEREST).length).toBeGreaterThan(1);
  });
});
