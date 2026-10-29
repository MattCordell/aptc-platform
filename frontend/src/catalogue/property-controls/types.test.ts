import { describe, expect, it } from "vitest";

import { sameSlotValue, slotValue, submittedSlotIndexes } from "./types.ts";
import type { PropertyValueSlot } from "./types.ts";

function added(id: string, value: unknown): PropertyValueSlot {
  return { id: `fixture-${id}`, value, justification: null };
}

function draft(value: unknown): PropertyValueSlot {
  return { id: "fixture-draft", value, justification: null, draft: true };
}

describe("submittedSlotIndexes (FR-23)", () => {
  it("sends every slot that holds a value", () => {
    expect(
      submittedSlotIndexes([added("1", "a"), added("2", null), added("3", "b")]),
    ).toEqual([0, 2]);
  });

  it("sends a draft that is not already in the list", () => {
    expect(submittedSlotIndexes([added("1", "a"), draft("b")])).toEqual([0, 1]);
  });

  it("drops a draft that repeats an added value, even with stray spaces", () => {
    expect(submittedSlotIndexes([added("1", "Anaemia"), draft("Anaemia ")])).toEqual([0]);
  });

  it("drops a draft that is only spaces", () => {
    expect(submittedSlotIndexes([added("1", "a"), draft("   ")])).toEqual([0]);
  });

  it("keeps an added value that happens to match another added value", () => {
    expect(submittedSlotIndexes([added("1", "a"), added("2", "a")])).toEqual([0, 1]);
  });
});

describe("sameSlotValue (FR-23, FR-52)", () => {
  const SNOMED = "http://snomed.info/sct";
  const LOCAL = "https://aptc.example/code-system/subgroup";

  it("matches coded values on system and code, whatever term each carries", () => {
    expect(
      sameSlotValue(
        { system: SNOMED, code: "1", display: "Old term" },
        { system: SNOMED, code: "1", display: "New term" },
      ),
    ).toBe(true);
  });

  it("treats the same code under two systems as two values", () => {
    expect(
      sameSlotValue({ system: SNOMED, code: "1" }, { system: LOCAL, code: "1" }),
    ).toBe(false);
  });

  it("compares anything else with ===", () => {
    expect(sameSlotValue("a", "a")).toBe(true);
    expect(sameSlotValue("a", { system: SNOMED, code: "a" })).toBe(false);
  });
});

describe("submittedSlotIndexes with coded values (FR-23)", () => {
  const SNOMED = "http://snomed.info/sct";

  it("drops a draft that repeats an added coded value, though it is another object", () => {
    expect(
      submittedSlotIndexes([
        added("1", { system: SNOMED, code: "1", display: "A" }),
        draft({ system: SNOMED, code: "1" }),
      ]),
    ).toEqual([0]);
  });

  it("keeps a draft with the same code under another system", () => {
    expect(
      submittedSlotIndexes([
        added("1", { system: SNOMED, code: "1" }),
        draft({ system: "https://aptc.example/code-system/subgroup", code: "1" }),
      ]),
    ).toEqual([0, 1]);
  });
});

describe("slotValue", () => {
  it("trims a draft, as adding it would", () => {
    expect(slotValue(draft(" b "))).toBe("b");
  });

  it("leaves an added value as it is", () => {
    expect(slotValue(added("1", " b "))).toBe(" b ");
  });

  it("leaves a draft number as it is", () => {
    expect(slotValue(draft(3))).toBe(3);
  });
});
