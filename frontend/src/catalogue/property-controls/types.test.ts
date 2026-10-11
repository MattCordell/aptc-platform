import { describe, expect, it } from "vitest";

import { slotValue, submittedSlotIndexes } from "./types.ts";
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
