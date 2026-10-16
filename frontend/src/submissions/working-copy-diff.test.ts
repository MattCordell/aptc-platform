import { describe, expect, it } from "vitest";

import {
  hasDifference,
  isReviewDetail,
  originalContent,
  workingContentOf,
  workingCopyDiff,
  type WorkingContent,
} from "./working-copy-diff.ts";

const ORIGINAL: WorkingContent = {
  preferred_term: "Ferritin",
  synonyms: ["Serum ferritin", "Ferritin level"],
  snomed_code: "0391483001",
  snomed_fsn: "Ferritin measurement (procedure)",
  property_values: {
    specimen: [{ value: "Serum", justification: null }],
    method: [{ value: "Immunoassay", justification: "Common method" }],
  },
  notes: "Seen in primary care.",
};

function edited(patch: Partial<WorkingContent>): WorkingContent {
  return { ...ORIGINAL, ...patch };
}

describe("workingCopyDiff (FR-23)", () => {
  it("lists nothing for a working copy that equals the original", () => {
    const difference = workingCopyDiff(ORIGINAL, structuredClone(ORIGINAL));

    expect(hasDifference(difference)).toBe(false);
    expect(difference.properties).toEqual([]);
  });

  it("marks a reworded test name and not a name that differs only in surrounding space", () => {
    expect(
      workingCopyDiff(ORIGINAL, edited({ preferred_term: "Serum ferritin" }))
        .preferredTerm,
    ).toBe(true);
    expect(
      workingCopyDiff(ORIGINAL, edited({ preferred_term: " Ferritin " })).preferredTerm,
    ).toBe(false);
  });

  it("marks changed other names, whatever their order", () => {
    expect(
      workingCopyDiff(
        ORIGINAL,
        edited({ synonyms: ["Ferritin level", "Serum ferritin"] }),
      ).synonyms,
    ).toBe(false);
    expect(
      workingCopyDiff(ORIGINAL, edited({ synonyms: ["Serum ferritin"] })).synonyms,
    ).toBe(true);
    expect(
      workingCopyDiff(
        ORIGINAL,
        edited({ synonyms: ["serum ferritin", "Ferritin level"] }),
      ).synonyms,
    ).toBe(true);
  });

  it("marks a different code, and a code added or removed", () => {
    expect(workingCopyDiff(ORIGINAL, edited({ snomed_code: "0391483002" })).code).toBe(
      true,
    );
    expect(workingCopyDiff(edited({ snomed_code: null }), ORIGINAL).code).toBe(true);
    expect(workingCopyDiff(ORIGINAL, ORIGINAL).code).toBe(false);
  });

  it("treats an absent note and a blank one as the same", () => {
    expect(workingCopyDiff(edited({ notes: null }), edited({ notes: "  " })).notes).toBe(
      false,
    );
    expect(workingCopyDiff(ORIGINAL, edited({ notes: null })).notes).toBe(true);
  });

  it("names each property whose values differ, including one added or removed", () => {
    const difference = workingCopyDiff(
      ORIGINAL,
      edited({
        property_values: {
          specimen: [{ value: "Plasma", justification: null }],
          method: [{ value: "Immunoassay", justification: "Common method" }],
          units: [{ value: "ug/L", justification: null }],
        },
      }),
    );

    expect(difference.properties.sort()).toEqual(["specimen", "units"]);
    const removed = workingCopyDiff(ORIGINAL, edited({ property_values: {} }));
    expect(removed.properties.sort()).toEqual(["method", "specimen"]);
  });

  it("compares a justification by its words, and a blank one as none", () => {
    const withBlank = edited({
      property_values: {
        ...ORIGINAL.property_values,
        specimen: [{ value: "Serum", justification: "" }],
      },
    });
    expect(workingCopyDiff(ORIGINAL, withBlank).properties).toEqual([]);

    const reworded = edited({
      property_values: {
        ...ORIGINAL.property_values,
        method: [{ value: "Immunoassay", justification: "Used by most laboratories" }],
      },
    });
    expect(workingCopyDiff(ORIGINAL, reworded).properties).toEqual(["method"]);
  });

  it("compares a coded value by its system and code, not its wording", () => {
    const coded = (display: string) => ({
      value: { system: "http://snomed.info/sct", code: "119297000", display },
      justification: null,
    });
    const before = edited({ property_values: { specimen: [coded("Blood specimen")] } });
    const after = edited({ property_values: { specimen: [coded("Blood sample")] } });

    expect(workingCopyDiff(before, after).properties).toEqual([]);
  });

  it("compares a value that is an object by its content", () => {
    const quantity = (value: number) => ({
      property_values: {
        amount: [{ value: { value, unit: "ug/L" }, justification: null }],
      },
    });

    expect(
      workingCopyDiff(edited(quantity(10)), edited(quantity(10))).properties,
    ).toEqual([]);
    expect(
      workingCopyDiff(edited(quantity(10)), edited(quantity(12))).properties,
    ).toEqual(["amount"]);
  });
});

describe("the detail a caller reads", () => {
  const plain = {
    preferred_term: "Ferritin",
    synonyms: ["Serum ferritin"],
    snomed_code: null,
    snomed_fsn: null,
    property_values: {},
    notes: null,
  };

  it("tells a detail that carries a working copy key from one that does not", () => {
    // The cast is the point: the server sends the key only to a reviewer.
    expect(isReviewDetail(plain as never)).toBe(false);
    expect(isReviewDetail({ ...plain, working_copy: null } as never)).toBe(true);
  });

  it("reads the original fields as content", () => {
    expect(originalContent(plain as never)).toEqual({ ...plain });
  });

  it("shows the working copy once there is one, and the original until then", () => {
    const working = {
      ...plain,
      preferred_term: "Serum ferritin",
      updated_at: "2026-10-16T00:00:00Z",
      label_provenance: {},
    };

    expect(workingContentOf({ ...plain, working_copy: working } as never)).toBe(working);
    expect(workingContentOf({ ...plain, working_copy: null } as never)).toEqual(plain);
  });
});
