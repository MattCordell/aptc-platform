import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/query-client.ts";
import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { stubApi, type Route } from "../test/stub-api.ts";
import { InterestButton } from "./interest-button.tsx";

/**
 * The interest toggle (FR-32, FR-34, FR-44, NFR-20, NFR-31). It shows only for a
 * caller holding `interest.register`, on an open submission that is not their
 * own, and every refusal the server still makes reaches the screen.
 */

const ID = "6f1d2c3e-0000-4000-8000-000000000011";
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

const MEMBER = session(["interest.register"]);

function submission(overrides: Record<string, unknown> = {}) {
  return {
    id: ID,
    is_own: false,
    state: "Submitted",
    has_registered_interest: false,
    ...overrides,
  };
}

function renderButton(props: ReturnType<typeof submission>) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <AuthContext.Provider value={AUTH}>
        <InterestButton submission={props} />
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

async function settled(calls: ReturnType<typeof stubApi>) {
  await waitFor(() =>
    expect(calls.some((call) => call.path === "/api/v1/auth/me")).toBe(true),
  );
  // The button is absent until the session answers, so wait for the answer to
  // land before asserting that nothing rendered.
  await new Promise((resolve) => setTimeout(resolve, 50));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("InterestButton visibility", () => {
  it("offers Register interest to a Member on an open submission that is not theirs", async () => {
    stubApi([MEMBER]);
    const { container } = renderButton(submission());

    expect(
      await screen.findByRole("button", { name: "Register interest" }),
    ).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("offers Withdraw interest when the caller already holds interest", async () => {
    stubApi([MEMBER]);
    const { container } = renderButton(submission({ has_registered_interest: true }));

    expect(
      await screen.findByRole("button", { name: "Withdraw interest" }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Register interest" })).toBeNull();
    await expectNoA11yViolations(container);
  });

  it.each([["Triage"]])("also offers it in the %s state", async (state) => {
    stubApi([MEMBER]);
    renderButton(submission({ state }));

    expect(
      await screen.findByRole("button", { name: "Register interest" }),
    ).toBeVisible();
  });

  it("is absent for an Observer, who lacks interest.register", async () => {
    const calls = stubApi([session(["catalogue.read"])]);
    const { container } = renderButton(submission());

    await settled(calls);
    expect(screen.queryByRole("button")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("is absent for a Provisional user, who lacks interest.register", async () => {
    const calls = stubApi([session(["submission.create"])]);
    const { container } = renderButton(submission());

    await settled(calls);
    expect(screen.queryByRole("button")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("is absent for the submitter, even though they hold interest.register", async () => {
    const calls = stubApi([MEMBER]);
    const { container } = renderButton(submission({ is_own: true }));

    await settled(calls);
    expect(screen.queryByRole("button")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it.each([["Published in release"], ["Rejected"], ["Withdrawn"], ["Escalated"]])(
    "is absent in the %s state, closed or unknown",
    async (state) => {
      const calls = stubApi([MEMBER]);
      const { container } = renderButton(submission({ state }));

      await settled(calls);
      expect(screen.queryByRole("button")).toBeNull();
      await expectNoA11yViolations(container);
    },
  );
});

describe("InterestButton actions", () => {
  it("registers with a PUT and announces the new count", async () => {
    const calls = stubApi([
      MEMBER,
      {
        method: "PUT",
        path: INTEREST,
        status: 200,
        body: { has_registered_interest: true, interest_count: 3 },
      },
    ]);
    const { container } = renderButton(submission());

    await userEvent.click(
      await screen.findByRole("button", { name: "Register interest" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Interest registered. 3 people have registered interest.",
      ),
    );
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    await expectNoA11yViolations(container);
  });

  it("withdraws with a DELETE and says nothing about a count the server withholds", async () => {
    const calls = stubApi([
      MEMBER,
      {
        method: "DELETE",
        path: INTEREST,
        status: 200,
        body: { has_registered_interest: false, interest_count: null },
      },
    ]);
    const { container } = renderButton(submission({ has_registered_interest: true }));

    await userEvent.click(
      await screen.findByRole("button", { name: "Withdraw interest" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/^Interest withdrawn\.$/),
    );
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
    await expectNoA11yViolations(container);
  });

  it("shows and announces the server's own words when it refuses a stale page", async () => {
    const reason = "This submission is closed, so interest cannot be registered.";
    stubApi([
      MEMBER,
      { method: "PUT", path: INTEREST, status: 409, body: { detail: reason } },
    ]);
    const { container } = renderButton(submission());

    await userEvent.click(
      await screen.findByRole("button", { name: "Register interest" }),
    );

    await waitFor(() => expect(container.querySelector("p")).toHaveTextContent(reason));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(reason));
    await expectNoA11yViolations(container);
  });

  it("falls back to a plain message when the failure carries no detail", async () => {
    stubApi([MEMBER, { method: "PUT", path: INTEREST, status: 500, body: {} }]);
    const { container } = renderButton(submission());

    await userEvent.click(
      await screen.findByRole("button", { name: "Register interest" }),
    );

    await waitFor(() =>
      expect(container.querySelector("p")).toHaveTextContent(
        "Your interest could not be saved. Try again in a moment.",
      ),
    );
  });
});
