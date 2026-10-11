import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../../api/query-client.ts";
import { AuthContext, type AuthContextValue } from "../../auth/session.ts";
import { INPUT_CLASSES } from "../../components/input-classes.ts";
import { expectNoA11yViolations } from "../../test/a11y.ts";
import { CONTROLS, RepeatableValues } from "./index.ts";
import type { ControlKind, PropertyValueSlot } from "./index.ts";
import type { FormError } from "../../components/error-summary.tsx";

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

function withProviders(children: ReactNode) {
  const queryClient = createQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

/**
 * The control registry (ADR-0013 SS3, issue #151) and the cardinality
 * wrapper around it.
 *
 * `CONTROLS` itself is proven exhaustive by TypeScript (a `ControlKind`
 * missing a row is a `tsc -b` error), so these tests exercise behaviour: each
 * control renders something, reports a value through `onChange`, and the
 * wrapper adds/removes slots correctly for single vs. multi cardinality.
 */

function stubFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe.each(["text", "textarea", "number", "uri"] as ControlKind[])(
  "CONTROLS.%s",
  (kind) => {
    it("renders a labelled control and reports a change", async () => {
      stubFetch(200, { items: [], total: 0 });
      const user = userEvent.setup();
      const Control = CONTROLS[kind];
      const onChange = vi.fn();

      const { container } = render(
        withProviders(
          <Control
            id="prop-0"
            label="Usage guidance"
            propertyKey="usage_guidance"
            params={{}}
            value={null}
            onChange={onChange}
          />,
        ),
      );

      const control = screen.getByLabelText("Usage guidance");
      await user.type(control, kind === "number" ? "3" : "x");
      expect(onChange).toHaveBeenCalled();
      await expectNoA11yViolations(container);
    });
  },
);

// jsdom computes no styles, so this asserts the class contract: the shared
// text-input look, so an edit screen's inputs match the landing page's.
describe.each(["text", "number", "uri"] as ControlKind[])(
  "CONTROLS.%s input style",
  (kind) => {
    it("uses the shared text-input classes", () => {
      const Control = CONTROLS[kind];

      render(
        withProviders(
          <Control
            id="prop-0"
            label="Usage guidance"
            propertyKey="usage_guidance"
            params={{}}
            value={null}
            onChange={vi.fn()}
          />,
        ),
      );

      expect(screen.getByLabelText("Usage guidance")).toHaveClass(
        ...INPUT_CLASSES.split(" "),
      );
    });
  },
);

const PLASMA = { code: "119361006", display: "Plasma specimen" };
const SERUM = { code: "119364003", display: "Serum specimen" };
const SHORT_LIST = { items: [PLASMA, SERUM], total: 2 };

/**
 * Answers every request with `respond`'s result and returns the URLs asked
 * for, so a test can tell a browser-side narrowing from a server search.
 */
function stubValues(respond: (url: URL) => { status?: number; body: unknown }): URL[] {
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      urls.push(url);
      const { status = 200, body } = respond(url);
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }),
  );
  return urls;
}

function renderPicker(props: {
  value?: unknown;
  onChange?: (value: unknown) => void;
  onCommit?: (value: unknown, label?: string) => void;
}) {
  const Control = CONTROLS.concept_picker;
  return render(
    withProviders(
      <Control
        id="specimen-0"
        label="Specimen"
        propertyKey="specimen"
        params={{}}
        value={props.value ?? null}
        onChange={props.onChange ?? vi.fn()}
        onCommit={props.onCommit}
      />,
    ),
  );
}

const box = () => screen.getByRole("combobox", { name: "Specimen" });

describe("CONTROLS.concept_picker (FR-23, FR-52)", () => {
  describe("a list that fits in one page", () => {
    it("opens to every option when the box is clicked", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      renderPicker({});

      await user.click(box());

      expect(await screen.findAllByRole("option")).toHaveLength(2);
    });

    it("opens to every option on the down arrow", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      renderPicker({});

      await user.tab();
      await user.keyboard("{ArrowDown}");

      expect(await screen.findAllByRole("option")).toHaveLength(2);
    });

    it("narrows in the browser as the user types, without asking the server", async () => {
      const urls = stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      renderPicker({});

      await user.click(box());
      await screen.findAllByRole("option");
      await user.type(box(), "ser");

      await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
      expect(screen.getByRole("option", { name: /Serum specimen/ })).toBeInTheDocument();
      expect(urls.some((url) => url.searchParams.has("filter"))).toBe(false);
      expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
    });

    it("says when nothing matches what was typed", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      renderPicker({});

      await user.click(box());
      await screen.findAllByRole("option");
      await user.type(box(), "zzz");

      expect(await screen.findByText("No value matches what you typed.")).toBeVisible();
    });
  });

  describe("a list longer than one page", () => {
    function stubLongList() {
      return stubValues((url) =>
        url.searchParams.get("filter") === "pla"
          ? { body: { items: [PLASMA], total: 3 } }
          : { body: { items: [PLASMA, SERUM], total: 340 } },
      );
    }

    it("says how many matched when the page is cut short", async () => {
      stubLongList();
      const user = userEvent.setup();
      renderPicker({});

      await user.click(box());

      expect(
        await screen.findByText("Showing 2 of 340. Type more to narrow the list."),
      ).toBeVisible();
    });

    it("sends the typed text to the server and shows what comes back", async () => {
      const urls = stubLongList();
      const user = userEvent.setup();
      renderPicker({});

      await user.click(box());
      await screen.findByText(/Showing 2 of 340/);
      await user.type(box(), "pla");

      expect(
        await screen.findByText("Showing 1 of 3. Type more to narrow the list."),
      ).toBeVisible();
      expect(urls.some((url) => url.searchParams.get("filter") === "pla")).toBe(true);
      expect(screen.getAllByRole("option")).toHaveLength(1);
    });
  });

  describe("holding one value", () => {
    it("reports the chosen code through onChange", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderPicker({ onChange });

      await user.click(box());
      await user.click(await screen.findByRole("option", { name: /Plasma specimen/ }));

      expect(onChange).toHaveBeenCalledWith("119361006");
    });

    it("shows the chosen value with its name, and clears it on request", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderPicker({ value: "119361006", onChange });

      await waitFor(() => expect(box()).toHaveValue("119361006 — Plasma specimen"));
      await user.click(screen.getByRole("button", { name: "Clear Specimen" }));

      expect(onChange).toHaveBeenCalledWith(null);
    });

    it("keeps a recorded code the server no longer offers, and lets the user clear it", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderPicker({ value: "122554006", onChange });

      await waitFor(() => expect(box()).toHaveValue("122554006"));
      await user.click(screen.getByRole("button", { name: "Clear Specimen" }));

      expect(onChange).toHaveBeenCalledWith(null);
    });

    it("offers no Clear button when nothing is chosen", () => {
      stubValues(() => ({ body: SHORT_LIST }));
      renderPicker({});

      expect(screen.queryByRole("button", { name: /Clear/ })).not.toBeInTheDocument();
    });
  });

  describe("adding to a list", () => {
    it("hands the pick and its label to onCommit, and empties the box", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      const onCommit = vi.fn();
      renderPicker({ onCommit });

      await user.click(box());
      await user.click(await screen.findByRole("option", { name: /Plasma specimen/ }));

      expect(onCommit).toHaveBeenCalledWith("119361006", "119361006 — Plasma specimen");
      expect(box()).toHaveValue("");
    });

    it("chooses the first match on Enter instead of submitting the form", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      const onCommit = vi.fn();
      const onSubmit = vi.fn((event: { preventDefault: () => void }) =>
        event.preventDefault(),
      );
      const Control = CONTROLS.concept_picker;
      render(
        withProviders(
          <form onSubmit={onSubmit}>
            <Control
              id="specimen-0"
              label="Specimen"
              propertyKey="specimen"
              params={{}}
              value={null}
              onChange={vi.fn()}
              onCommit={onCommit}
            />
          </form>,
        ),
      );

      await user.type(box(), "plas");
      await screen.findByRole("option", { name: /Plasma specimen/ });
      await user.keyboard("{Enter}");

      expect(onCommit).toHaveBeenCalledWith("119361006", "119361006 — Plasma specimen");
      expect(onSubmit).not.toHaveBeenCalled();
    });
  });

  it("says why when the list of values cannot be loaded", async () => {
    stubValues(() => ({ status: 500, body: { detail: "The server said no." } }));
    renderPicker({});

    expect(await screen.findByText(/Reload the page to try again\./)).toBeVisible();
    expect(box()).toHaveAttribute("aria-invalid", "true");
  });

  it("has no accessibility violations, open or closed", async () => {
    stubValues(() => ({ body: SHORT_LIST }));
    const user = userEvent.setup();
    const { container } = renderPicker({ onCommit: vi.fn() });

    await expectNoA11yViolations(container);
    await user.click(box());
    await expectNoA11yViolations(await screen.findByRole("listbox"));
  });
});

describe.each(["text", "textarea", "number", "uri"] as ControlKind[])(
  "CONTROLS.%s as an entry box (FR-23)",
  (kind) => {
    const sample = kind === "number" ? 3 : kind === "uri" ? "https://a.example" : "x";

    function renderEntry(current: unknown) {
      const Control = CONTROLS[kind];
      const onCommit = vi.fn();
      render(
        withProviders(
          <form onSubmit={(event) => event.preventDefault()}>
            <Control
              id="prop-0"
              label="Usage guidance"
              propertyKey="usage_guidance"
              params={{}}
              value={current}
              onChange={vi.fn()}
              onCommit={onCommit}
            />
          </form>,
        ),
      );
      return onCommit;
    }

    it("commits the value on Enter", async () => {
      const user = userEvent.setup();
      const onCommit = renderEntry(sample);

      await user.type(screen.getByLabelText("Usage guidance"), "{Enter}");

      expect(onCommit).toHaveBeenCalledWith(sample);
    });

    it("ignores Enter on a blank box", async () => {
      const user = userEvent.setup();
      const onCommit = renderEntry(null);

      await user.type(screen.getByLabelText("Usage guidance"), "{Enter}");

      expect(onCommit).not.toHaveBeenCalled();
    });

    it("tells the user how to add", () => {
      renderEntry(null);

      expect(screen.getByText("Press Enter to add this value.")).toBeInTheDocument();
    });
  },
);

describe("CONTROLS.textarea as an entry box", () => {
  it("leaves Shift+Enter to start a new line", async () => {
    const Control = CONTROLS.textarea;
    const onCommit = vi.fn();
    render(
      withProviders(
        <Control
          id="prop-0"
          label="Usage guidance"
          propertyKey="usage_guidance"
          params={{}}
          value="x"
          onChange={vi.fn()}
          onCommit={onCommit}
        />,
      ),
    );

    await userEvent
      .setup()
      .type(screen.getByLabelText("Usage guidance"), "{Shift>}{Enter}{/Shift}");

    expect(onCommit).not.toHaveBeenCalled();
  });
});

function Harness({
  control,
  propertyKey = "specimen",
  label = "Specimen",
  cardinality = "0..*",
  initial = [],
  params = {},
  errors = [],
  onSlots,
}: {
  control: ControlKind;
  propertyKey?: string;
  label?: string;
  cardinality?: "0..1" | "1..1" | "0..*" | "1..*";
  initial?: PropertyValueSlot[];
  params?: Record<string, unknown>;
  errors?: FormError[];
  onSlots?: (slots: PropertyValueSlot[]) => void;
}) {
  const [slots, setSlots] = useState<PropertyValueSlot[]>(initial);
  return (
    <form onSubmit={(event) => event.preventDefault()}>
      <RepeatableValues
        propertyKey={propertyKey}
        label={label}
        cardinality={cardinality}
        control={control}
        params={params}
        slots={slots}
        onChange={(next) => {
          onSlots?.(next);
          setSlots(next);
        }}
        errors={errors}
      />
    </form>
  );
}

function slot(id: string, value: unknown): PropertyValueSlot {
  return { id: `fixture-${id}`, value, justification: null };
}

const list = () => screen.getByRole("list", { name: "Specimen values" });
const items = () => within(list()).getAllByRole("listitem");

describe("RepeatableValues (FR-23, FR-52)", () => {
  describe("a property with one value", () => {
    it("shows one control and no list, Remove or Add another", () => {
      render(
        withProviders(<Harness control="text" label="Discipline" cardinality="0..1" />),
      );

      expect(screen.getAllByRole("textbox")).toHaveLength(1);
      expect(screen.queryByRole("list")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /remove/i })).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /add another/i }),
      ).not.toBeInTheDocument();
    });

    it("renders no justification field when the property does not allow one", () => {
      render(
        withProviders(
          <Harness
            control="text"
            label="Discipline"
            cardinality="0..1"
            initial={[slot("1", "HAEM")]}
          />,
        ),
      );

      expect(screen.queryByLabelText("Justification")).not.toBeInTheDocument();
    });

    it("renders and reports a justification field when the property allows one (FR-10)", async () => {
      const user = userEvent.setup();
      let latest: PropertyValueSlot[] = [];
      render(
        withProviders(
          <Harness
            control="text"
            label="Discipline"
            cardinality="0..1"
            initial={[slot("1", "HAEM")]}
            params={{ allowJustification: true }}
            onSlots={(next) => {
              latest = next;
            }}
          />,
        ),
      );

      await user.type(
        screen.getByLabelText("Justification"),
        "Locally agreed substitute",
      );

      expect(latest[0].justification).toBe("Locally agreed substitute");
    });
  });

  describe("a coded property with several values", () => {
    it("adds a pick to the list and empties the box", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      render(withProviders(<Harness control="concept_picker" />));

      await user.click(box());
      await user.click(await screen.findByRole("option", { name: /Plasma specimen/ }));

      expect(items()).toHaveLength(1);
      expect(items()[0]).toHaveTextContent("119361006 — Plasma specimen");
      expect(box()).toHaveValue("");
      expect(
        screen.queryByRole("button", { name: /add another/i }),
      ).not.toBeInTheDocument();
    });

    it("ignores a value already in the list, and says so", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      render(withProviders(<Harness control="concept_picker" />));

      for (let pick = 0; pick < 2; pick += 1) {
        await user.click(box());
        await user.click(await screen.findByRole("option", { name: /Plasma specimen/ }));
      }

      expect(items()).toHaveLength(1);
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent(
          "119361006 — Plasma specimen is already in the Specimen list.",
        ),
      );
    });

    it("removes a value with a Remove button named for it, and announces it", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      render(
        withProviders(
          <Harness
            control="concept_picker"
            initial={[slot("1", "119361006"), slot("2", "119364003")]}
          />,
        ),
      );

      await user.click(
        await screen.findByRole("button", {
          name: "Remove 119361006 — Plasma specimen",
        }),
      );

      expect(items()).toHaveLength(1);
      expect(items()[0]).toHaveTextContent("Serum specimen");
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent(
          "Removed 119361006 — Plasma specimen. 1 left in the Specimen list.",
        ),
      );
    });

    it("keeps a recorded value the server does not offer, shows its code, and lets the user remove it", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      render(
        withProviders(
          <Harness control="concept_picker" initial={[slot("1", "122554006")]} />,
        ),
      );

      expect(items()[0]).toHaveTextContent("122554006");
      await user.click(screen.getByRole("button", { name: "Remove 122554006" }));

      expect(screen.queryByRole("list")).not.toBeInTheDocument();
    });

    it("keeps the values already chosen when the options cannot be loaded", async () => {
      stubValues(() => ({ status: 500, body: { detail: "The server said no." } }));
      const user = userEvent.setup();
      render(
        withProviders(
          <Harness control="concept_picker" initial={[slot("1", "119361006")]} />,
        ),
      );

      expect(await screen.findByText(/Reload the page to try again\./)).toBeVisible();
      expect(items()[0]).toHaveTextContent("119361006");
      await user.click(screen.getByRole("button", { name: "Remove 119361006" }));
      expect(screen.queryByRole("list")).not.toBeInTheDocument();
    });

    it("shows a server refusal against the value it names", () => {
      stubValues(() => ({ body: SHORT_LIST }));
      render(
        withProviders(
          <Harness
            control="concept_picker"
            initial={[slot("1", "119361006"), slot("2", "119364003")]}
            errors={[{ fieldId: "specimen-1", message: "Not a recognised code." }]}
          />,
        ),
      );

      expect(items()[0]).not.toHaveTextContent("Not a recognised code.");
      expect(items()[1]).toHaveTextContent("Not a recognised code.");
      expect(items()[1]).toHaveAttribute("id", "specimen-1");
    });

    it("shows a refusal of the whole set under the group", () => {
      stubValues(() => ({ body: SHORT_LIST }));
      render(
        withProviders(
          <Harness
            control="concept_picker"
            errors={[{ fieldId: "specimen-group", message: "Choose at least one." }]}
          />,
        ),
      );

      expect(screen.getByText("Choose at least one.")).toBeInTheDocument();
    });

    it("gives each value its own justification field (FR-10)", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      let latest: PropertyValueSlot[] = [];
      render(
        withProviders(
          <Harness
            control="concept_picker"
            initial={[slot("1", "119361006"), slot("2", "119364003")]}
            params={{ allowJustification: true }}
            onSlots={(next) => {
              latest = next;
            }}
          />,
        ),
      );

      await user.type(
        await screen.findByLabelText(/^Justification for 119364003/),
        "Locally agreed",
      );

      expect(latest.map((entry) => entry.justification)).toEqual([
        null,
        "Locally agreed",
      ]);
    });

    it("moves focus to the next value after a Remove, and to the box once the list is empty", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const user = userEvent.setup();
      render(
        withProviders(
          <Harness
            control="concept_picker"
            initial={[slot("1", "119361006"), slot("2", "119364003")]}
          />,
        ),
      );

      await user.click(await screen.findByRole("button", { name: /^Remove 119361006/ }));
      const last = await screen.findByRole("button", { name: /^Remove 119364003/ });
      expect(last).toHaveFocus();

      await user.click(last);
      expect(box()).toHaveFocus();
    });

    it("has no accessibility violations with values in the list", async () => {
      stubValues(() => ({ body: SHORT_LIST }));
      const { container } = render(
        withProviders(
          <Harness
            control="concept_picker"
            initial={[slot("1", "119361006"), slot("2", "119364003")]}
            params={{ allowJustification: true }}
          />,
        ),
      );
      await waitFor(() => expect(items()[0]).toHaveTextContent("Plasma specimen"));

      await expectNoA11yViolations(container);
    });
  });

  describe("a text property with several values", () => {
    it("adds a value on Enter, empties the box and never submits the form", async () => {
      const user = userEvent.setup();
      render(withProviders(<Harness control="text" propertyKey="note" label="Note" />));

      await user.type(screen.getByLabelText("Note"), "first{Enter}");

      expect(screen.getByLabelText("Note")).toHaveValue("");
      expect(
        within(screen.getByRole("list", { name: "Note values" })).getAllByRole(
          "listitem",
        ),
      ).toHaveLength(1);
      expect(screen.getByRole("button", { name: "Remove first" })).toBeInTheDocument();
    });

    it("ignores a blank value", async () => {
      const user = userEvent.setup();
      render(withProviders(<Harness control="text" propertyKey="note" label="Note" />));

      await user.type(screen.getByLabelText("Note"), "   {Enter}");

      expect(screen.queryByRole("list")).not.toBeInTheDocument();
    });

    it("keeps text typed but not yet added as a trailing value, so a submit does not lose it", async () => {
      const user = userEvent.setup();
      let latest: PropertyValueSlot[] = [];
      render(
        withProviders(
          <Harness
            control="text"
            propertyKey="note"
            label="Note"
            initial={[slot("1", "first")]}
            onSlots={(next) => {
              latest = next;
            }}
          />,
        ),
      );

      await user.type(screen.getByLabelText("Note"), "second");

      expect(latest.map((entry) => [entry.value, entry.draft])).toEqual([
        ["first", undefined],
        ["second", true],
      ]);
      expect(screen.getByLabelText("Note")).toHaveValue("second");

      await user.type(screen.getByLabelText("Note"), "{Enter}");
      expect(latest.map((entry) => [entry.value, entry.draft])).toEqual([
        ["first", undefined],
        ["second", undefined],
      ]);
    });
  });
});
