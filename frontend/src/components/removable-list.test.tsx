import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { RemovableList } from "./removable-list.tsx";
import type { RemovableListItem } from "./removable-list.tsx";

function items(...names: string[]): RemovableListItem[] {
  return names.map((name) => ({
    id: `fixture-${name}`,
    label: name,
    removeLabel: `Remove ${name}`,
  }));
}

function Harness({ initial }: { initial: RemovableListItem[] }) {
  const [current, setCurrent] = useState(initial);
  const box = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={box} aria-label="Add" />
      <RemovableList
        label="Chosen values"
        items={current}
        onRemove={(id) => setCurrent((all) => all.filter((item) => item.id !== id))}
        emptyFocus={box}
      />
    </>
  );
}

describe("RemovableList (NFR-31)", () => {
  it("renders a labelled bulleted list with one named Remove button per item", async () => {
    const { container } = render(<Harness initial={items("Plasma", "Serum")} />);

    const list = screen.getByRole("list", { name: "Chosen values" });
    expect(list.querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Remove Plasma" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Serum" })).toBeInTheDocument();
    await expectNoA11yViolations(container);
  });

  it("renders nothing when it has no items", () => {
    render(<Harness initial={[]} />);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("moves focus to the item that takes the removed item's place", async () => {
    const user = userEvent.setup();
    render(<Harness initial={items("Plasma", "Serum", "Urine")} />);

    await user.click(screen.getByRole("button", { name: "Remove Plasma" }));
    expect(screen.getByRole("button", { name: "Remove Serum" })).toHaveFocus();
  });

  it("moves focus to the item before when the last item is removed", async () => {
    const user = userEvent.setup();
    render(<Harness initial={items("Plasma", "Serum")} />);

    await user.click(screen.getByRole("button", { name: "Remove Serum" }));
    expect(screen.getByRole("button", { name: "Remove Plasma" })).toHaveFocus();
  });

  it("returns focus to the box when the list empties", async () => {
    const user = userEvent.setup();
    render(<Harness initial={items("Plasma")} />);

    await user.click(screen.getByRole("button", { name: "Remove Plasma" }));
    expect(screen.getByRole("textbox", { name: "Add" })).toHaveFocus();
  });

  it("shows an item's error and extra content, and addresses it by elementId", () => {
    render(
      <RemovableList
        label="Chosen values"
        items={[
          {
            id: "fixture-1",
            elementId: "specimen-1",
            label: "Plasma",
            removeLabel: "Remove Plasma",
            error: "That value is not offered.",
            children: <p>Extra</p>,
          },
        ]}
        onRemove={() => undefined}
      />,
    );

    expect(screen.getByText("That value is not offered.")).toBeInTheDocument();
    expect(screen.getByText("Extra")).toBeInTheDocument();
    expect(document.getElementById("specimen-1")).toHaveAttribute("tabindex", "-1");
  });
});
