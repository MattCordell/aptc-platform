import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import { stubApi } from "../test/stub-api.ts";

/** The `/submissions` page (FR-23, NFR-31): the way into the new-test form. */

const SIGNED_IN = { auth: { status: "signed-in" as const } };

describe("submissions page (FR-23)", () => {
  it("shows the heading and a Create submission link to the new-test form", async () => {
    stubApi([]);
    await renderRoute("/submissions", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Submissions" });
    expect(
      within(screen.getByRole("main")).getByRole("link", { name: "Create submission" }),
    ).toHaveAttribute("href", "/submissions/new");
  });

  it("no longer shows the placeholder text", async () => {
    stubApi([]);
    await renderRoute("/submissions", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Submissions" });
    expect(
      screen.queryByText("This screen has not been built yet."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Planned")).not.toBeInTheDocument();
    expect(screen.getByText("The list of submissions is coming soon.")).toBeVisible();
  });

  it("opens the new-test form from the link", async () => {
    const user = userEvent.setup();
    stubApi([]);
    const { router } = await renderRoute("/submissions", SIGNED_IN);

    await user.click(await screen.findByRole("link", { name: "Create submission" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/submissions/new"));
  });

  it("has one h1 and no axe violations", async () => {
    stubApi([]);
    const { container } = await renderRoute("/submissions", SIGNED_IN);

    await screen.findByRole("heading", { level: 1, name: "Submissions" });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await expectNoA11yViolations(container);
  });
});
