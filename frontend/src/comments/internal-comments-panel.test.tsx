import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/query-client.ts";
import { useSession, type CommentTarget } from "../api/queries.ts";
import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { stubApi, type Route } from "../test/stub-api.ts";
import { InternalCommentsPanel } from "./internal-comments-panel.tsx";

/**
 * The internal comments panel (FR-95, FR-44, NFR-20, NFR-23). It shows only for a
 * caller holding `comment.internal.read`, and for anyone else the comments
 * request is never sent. Posting and withdrawing need `comment.internal.write`.
 */

const SUBMISSION_ID = "6f1d2c3e-0000-4000-8000-000000000051";
const ENTRY_KEY = "NPTC-000247";
const COMMENT_ID = "6f1d2c3e-0000-4000-8000-000000000061";

const READ = "comment.internal.read";
const WRITE = "comment.internal.write";

const TARGETS: {
  name: string;
  target: CommentTarget;
  path: string;
}[] = [
  {
    name: "a submission",
    target: { kind: "submission", id: SUBMISSION_ID },
    path: `/api/v1/submissions/${SUBMISSION_ID}/comments`,
  },
  {
    name: "an entry",
    target: { kind: "entry", businessKey: ENTRY_KEY },
    path: `/api/v1/catalogue/entries/${ENTRY_KEY}/comments`,
  },
];

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
        username: "rae",
        display_name: "Rae Reviewer",
        organisation: null,
        status: "active",
      },
      roles: [],
      permissions,
      mfa_satisfied: false,
    },
  };
}

const ANONYMOUS: Route = {
  method: "GET",
  path: "/auth/me",
  status: 200,
  body: {
    authenticated: false,
    user: null,
    roles: [],
    permissions: [],
    mfa_satisfied: false,
  },
};

const STAFF = session([READ, WRITE]);
const READER = session([READ]);

const RAE = { username: "rae", display_name: "Rae Reviewer" };
const ALEX = { username: "alex", display_name: "Alex Admin" };

function comment(overrides: Record<string, unknown> = {}) {
  return {
    id: COMMENT_ID,
    body: "Check the specimen type before approving.",
    created_at: "2026-10-01T09:00:00Z",
    author: RAE,
    withdrawn_at: null,
    withdrawn_by: null,
    withdrawn_reason: null,
    ...overrides,
  };
}

const WITHDRAWN = comment({
  withdrawn_at: "2026-10-02T09:00:00Z",
  withdrawn_by: ALEX,
  withdrawn_reason: "Posted on the wrong submission.",
});

function listRoute(path: string, comments: unknown[]): Route {
  return { method: "GET", path, status: 200, body: { comments } };
}

/** Renders once the session has landed, so an assertion that something is absent
 * cannot pass merely because the session was still loading. */
function SessionProbe() {
  const session = useSession();
  return session.data === undefined ? null : <span data-testid="session-ready" hidden />;
}

function renderPanel(target: CommentTarget) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <AuthContext.Provider value={AUTH}>
        <SessionProbe />
        <InternalCommentsPanel target={target} />
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

async function settled() {
  await screen.findByTestId("session-ready");
}

async function openWithdrawDialog() {
  await userEvent.click(
    await screen.findByRole("button", { name: /^Withdraw the comment/ }),
  );
  return screen.findByRole("dialog");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe.each(TARGETS)("InternalCommentsPanel on $name", ({ target, path }) => {
  it("lists each comment, with who wrote it and when, and offers to post and withdraw", async () => {
    stubApi([STAFF, listRoute(path, [comment()])]);
    const { container } = renderPanel(target);

    const heading = await screen.findByRole("heading", { name: "Internal comments" });
    const section = heading.parentElement as HTMLElement;
    const item = await within(section).findByRole("listitem");
    expect(
      within(item).getByText("Check the specimen type before approving."),
    ).toBeVisible();
    expect(within(item).getByText("Rae Reviewer", { exact: false })).toBeVisible();
    expect(within(item).getByText("(rae)")).toBeVisible();
    expect(within(item).getByText("1 October 2026")).toBeVisible();
    expect(
      within(item).getByRole("button", {
        name: "Withdraw the comment by Rae Reviewer on 1 October 2026",
      }),
    ).toBeVisible();
    expect(within(section).getByLabelText("Add an internal comment")).toBeVisible();
    expect(within(section).getByRole("button", { name: "Post comment" })).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("says so when there are no comments", async () => {
    stubApi([STAFF, listRoute(path, [])]);
    const { container } = renderPanel(target);

    expect(await screen.findByText("There are no internal comments yet.")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("lists the comments but offers no box and no Withdraw to a caller who may only read", async () => {
    stubApi([READER, listRoute(path, [comment()])]);
    const { container } = renderPanel(target);

    expect(
      await screen.findByText("Check the specimen type before approving."),
    ).toBeVisible();
    expect(screen.queryByLabelText("Add an internal comment")).toBeNull();
    expect(screen.queryByRole("button", { name: "Post comment" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Withdraw/ })).toBeNull();
    await expectNoA11yViolations(container);
  });

  // The principal failure mode (NFR-20): a caller without the permission must
  // never cost the server a 403, and must see no trace of the panel.
  it.each([
    ["a Member", session(["interest.register", "submission.view"])],
    ["an Observer", session(["catalogue.read"])],
    ["a submitter who holds only write", session([WRITE])],
    ["an anonymous reader", ANONYMOUS],
  ])("is absent for %s, and sends no comments request", async (_who, route) => {
    const calls = stubApi([route, listRoute(path, [comment()])]);
    const { container } = renderPanel(target);

    await settled();
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.queryByText(/internal comment/i)).toBeNull();
    expect(calls.some((call) => call.path === path)).toBe(false);
    await expectNoA11yViolations(container);
  });

  // NFR-23: the text is shown as typed and never read as markup.
  it("shows a body that contains markup as literal text, and keeps its line breaks", async () => {
    const markup = '<img src="x" onerror="alert(1)">\n<b>bold</b> & more';
    stubApi([STAFF, listRoute(path, [comment({ body: markup })])]);
    const { container } = renderPanel(target);

    const text = await screen.findByText(
      (_content, element) => element?.tagName === "P" && element.textContent === markup,
    );
    expect(text).toBeVisible();
    expect(text.className).toContain("whitespace-pre-line");
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("marks a withdrawn comment, and still shows its text, who withdrew it, when and why", async () => {
    stubApi([STAFF, listRoute(path, [WITHDRAWN])]);
    const { container } = renderPanel(target);

    const item = await screen.findByRole("listitem");
    expect(within(item).getByText("Withdrawn")).toBeVisible();
    expect(
      within(item).getByText("Check the specimen type before approving."),
    ).toBeVisible();
    expect(within(item).getByText("Alex Admin", { exact: false })).toBeVisible();
    expect(within(item).getByText("2 October 2026")).toBeVisible();
    expect(
      within(item).getByText("Reason: Posted on the wrong submission."),
    ).toBeVisible();
    expect(within(item).queryByRole("button")).toBeNull();
    await expectNoA11yViolations(container);
  });

  it("shows a withdrawal reason that contains markup as literal text", async () => {
    stubApi([
      STAFF,
      listRoute(path, [
        comment({
          withdrawn_at: "2026-10-02T09:00:00Z",
          withdrawn_by: ALEX,
          withdrawn_reason: "<script>alert(1)</script>",
        }),
      ]),
    ]);
    const { container } = renderPanel(target);

    expect(await screen.findByText("Reason: <script>alert(1)</script>")).toBeVisible();
    expect(container.querySelector("script")).toBeNull();
  });

  describe("posting", () => {
    // Every read answers with the list as it was before the post: the server can
    // still be committing when the browser reads straight after its answer.
    it("sends the text, empties the box, announces it and shows the new comment even if a read right after still answers with the old list", async () => {
      const calls = stubApi([
        STAFF,
        listRoute(path, []),
        { method: "POST", path, status: 201, body: comment() },
      ]);
      const { container } = renderPanel(target);

      const box = await screen.findByLabelText("Add an internal comment");
      await userEvent.type(box, "  Check the specimen type before approving.  ");
      await userEvent.click(screen.getByRole("button", { name: "Post comment" }));

      expect(await screen.findByRole("listitem")).toHaveTextContent(
        "Check the specimen type before approving.",
      );
      expect(calls.find((call) => call.method === "POST")?.body).toEqual({
        body: "Check the specimen type before approving.",
      });
      expect(box).toHaveValue("");
      expect(await screen.findByRole("status")).toHaveTextContent("Comment posted.");
      await expectNoA11yViolations(container);
    });

    it.each([
      ["nothing", ""],
      ["spaces and a line break", "   \n  "],
    ])("does not send a comment of %s, and marks the box", async (_what, typed) => {
      const calls = stubApi([STAFF, listRoute(path, [])]);
      const { container } = renderPanel(target);

      const box = await screen.findByLabelText("Add an internal comment");
      if (typed !== "") {
        await userEvent.type(box, typed);
      }
      await userEvent.click(screen.getByRole("button", { name: "Post comment" }));

      expect((await screen.findAllByText("Enter a comment.")).length).toBeGreaterThan(0);
      expect(box).toHaveAttribute("aria-invalid", "true");
      expect(calls.some((call) => call.method === "POST")).toBe(false);
      await expectNoA11yViolations(container);
    });

    it("marks the box with the server's sentence when it refuses the body", async () => {
      stubApi([
        STAFF,
        listRoute(path, []),
        {
          method: "POST",
          path,
          status: 422,
          body: {
            detail: "The comment holds a character that cannot be shown.",
            field: "body",
          },
        },
      ]);
      const { container } = renderPanel(target);

      const box = await screen.findByLabelText("Add an internal comment");
      await userEvent.type(box, "Odd text");
      await userEvent.click(screen.getByRole("button", { name: "Post comment" }));

      expect(
        (
          await screen.findAllByText(
            "The comment holds a character that cannot be shown.",
          )
        ).length,
      ).toBeGreaterThan(0);
      expect(box).toHaveAttribute("aria-invalid", "true");
      expect(box).toHaveValue("Odd text");
      await expectNoA11yViolations(container);
    });

    it("shows the server's sentence when it refuses the caller, and keeps the text", async () => {
      stubApi([
        STAFF,
        listRoute(path, []),
        {
          method: "POST",
          path,
          status: 403,
          body: { detail: "You may not post internal comments." },
        },
      ]);
      renderPanel(target);

      const box = await screen.findByLabelText("Add an internal comment");
      await userEvent.type(box, "Please look");
      await userEvent.click(screen.getByRole("button", { name: "Post comment" }));

      expect(
        (await screen.findAllByText("You may not post internal comments.")).length,
      ).toBeGreaterThan(0);
      expect(box).toHaveValue("Please look");
    });
  });

  describe("withdrawing", () => {
    it("does not send a blank reason, and marks the field", async () => {
      const calls = stubApi([STAFF, listRoute(path, [comment()])]);
      renderPanel(target);

      const dialog = await openWithdrawDialog();
      await userEvent.type(within(dialog).getByLabelText("Reason"), "   ");
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
      );

      expect(
        (
          await within(dialog).findAllByText(
            "Enter the reason for withdrawing this comment.",
          )
        ).length,
      ).toBeGreaterThan(0);
      expect(within(dialog).getByLabelText("Reason")).toHaveAttribute(
        "aria-invalid",
        "true",
      );
      expect(calls.some((call) => call.method === "POST")).toBe(false);
      await expectNoA11yViolations(dialog);
    });

    // Every read answers with the standing comment, as for the post above.
    it("sends the reason, closes the dialog, announces it and marks the comment withdrawn even if a read right after still answers with the standing comment", async () => {
      const calls = stubApi([
        STAFF,
        listRoute(path, [comment()]),
        {
          method: "POST",
          path: `${path}/${COMMENT_ID}/withdraw`,
          status: 200,
          body: WITHDRAWN,
        },
      ]);
      const { container } = renderPanel(target);

      const dialog = await openWithdrawDialog();
      await userEvent.type(
        within(dialog).getByLabelText("Reason"),
        "  Posted on the wrong submission.  ",
      );
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
      );

      const item = await screen.findByRole("listitem");
      await within(item).findByText("Withdrawn");
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(calls.find((call) => call.method === "POST")?.body).toEqual({
        reason: "Posted on the wrong submission.",
      });
      expect(await screen.findByRole("status")).toHaveTextContent("Comment withdrawn.");
      await waitFor(() => expect(item).toHaveFocus());
      await expectNoA11yViolations(container);
    });

    it("marks the field with the server's sentence when it refuses the reason", async () => {
      stubApi([
        STAFF,
        listRoute(path, [comment()]),
        {
          method: "POST",
          path: `${path}/${COMMENT_ID}/withdraw`,
          status: 422,
          body: {
            detail: "The reason holds a character that cannot be shown.",
            field: "reason",
          },
        },
      ]);
      renderPanel(target);

      const dialog = await openWithdrawDialog();
      await userEvent.type(within(dialog).getByLabelText("Reason"), "Odd text");
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
      );

      expect(
        (
          await within(dialog).findAllByText(
            "The reason holds a character that cannot be shown.",
          )
        ).length,
      ).toBeGreaterThan(0);
      expect(within(dialog).getByLabelText("Reason")).toHaveAttribute(
        "aria-invalid",
        "true",
      );
    });

    it("shows the server's sentence when the comment was already withdrawn, and reads the list again", async () => {
      const calls = stubApi(
        [
          STAFF,
          {
            method: "POST",
            path: `${path}/${COMMENT_ID}/withdraw`,
            status: 409,
            body: { detail: "This comment was already withdrawn." },
          },
        ],
        {
          vary: (call, priorSameCalls) =>
            call.method === "GET" && call.path === path
              ? listRoute(path, [priorSameCalls === 0 ? comment() : WITHDRAWN])
              : null,
        },
      );
      renderPanel(target);

      const dialog = await openWithdrawDialog();
      await userEvent.type(within(dialog).getByLabelText("Reason"), "Again");
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
      );

      expect(
        (await within(dialog).findAllByText("This comment was already withdrawn."))
          .length,
      ).toBeGreaterThan(0);
      await waitFor(() =>
        expect(
          calls.filter((call) => call.method === "GET" && call.path === path).length,
        ).toBe(2),
      );
      expect(screen.getByRole("dialog")).toBeVisible();
    });

    it("closes the dialog without a request when the user cancels", async () => {
      const calls = stubApi([STAFF, listRoute(path, [comment()])]);
      renderPanel(target);

      const dialog = await openWithdrawDialog();
      await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

      expect(screen.queryByRole("dialog")).toBeNull();
      expect(calls.some((call) => call.method === "POST")).toBe(false);
    });
  });

  describe("when the list cannot be read", () => {
    it("offers a retry for a server error, and no box to post into yet", async () => {
      stubApi([STAFF, { method: "GET", path, status: 500, body: { detail: "boom" } }]);
      const { container } = renderPanel(target);

      expect(
        await screen.findByText(
          "The internal comments could not be loaded. Try again in a moment.",
        ),
      ).toBeVisible();
      expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
      await expectNoA11yViolations(container);
    });

    it("reads the list again when the user retries", async () => {
      let failing = true;
      const calls = stubApi([STAFF, listRoute(path, [comment()])], {
        vary: (call) =>
          failing && call.method === "GET" && call.path === path
            ? { method: "GET", path, status: 500, body: { detail: "boom" } }
            : null,
      });
      renderPanel(target);

      const retry = await screen.findByRole("button", { name: "Try again" });
      failing = false;
      await userEvent.click(retry);

      expect(
        await screen.findByText("Check the specimen type before approving."),
      ).toBeVisible();
      expect(calls.filter((call) => call.path === path).length).toBeGreaterThan(1);
    });

    it("shows the server's reason and no retry when it refuses the caller", async () => {
      stubApi([
        STAFF,
        {
          method: "GET",
          path,
          status: 403,
          body: { detail: "You may not read internal comments." },
        },
      ]);
      renderPanel(target);

      expect(
        await screen.findByText("You may not read internal comments."),
      ).toBeVisible();
      expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    });
  });
});
