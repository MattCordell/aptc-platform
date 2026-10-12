import { describe, expect, it } from "vitest";

import type { components } from "../api/schema.ts";
import { slotFieldId } from "../catalogue/property-controls/index.ts";
import type { CodeSelection } from "../catalogue/use-code-selection.ts";
import {
  FIELD_IDS,
  addNames,
  amendmentBody,
  createBody,
  duplicateCheckBody,
  filledNames,
  filledSlotIndexes,
  initialValues,
  validate,
  validateAmendment,
} from "./form-state.ts";

type Definition = components["schemas"]["PropertyDefinitionResponse"];

const NONE: CodeSelection = { status: "none" };
const READY: CodeSelection = {
  status: "ready",
  concept: {
    code: "0391483001",
    fsn: "Microscopy (procedure)",
    auPreferredTerm: null,
    edition: "au",
    active: true,
  },
};

function definition(key: string, required: boolean): Definition {
  return {
    key,
    label: key,
    datatype: "alpha",
    cardinality: "0..*",
    scope: "submission",
    required_for_submission: required,
    required_for_publication: false,
    binding_target: null,
    value_set_uri: null,
    strength: null,
    edition: null,
    local_code_system_key: null,
    filterable: false,
    origin: "admin",
    status: "active",
    display_order: 0,
    constraints: {},
    row_version: 1,
    form_control: { control: "text", params: {} },
  };
}

function valid() {
  return {
    ...initialValues(),
    preferredTerm: "Serum ferritin",
    referenceUrl: "https://example.org/x",
  };
}

describe("validate", () => {
  it("accepts a form with a name and a reference link", () => {
    expect(validate(valid(), [], NONE)).toEqual([]);
  });

  it("asks for a required property with no value, at its first control", () => {
    const found = validate(
      valid(),
      [definition("needed", true), definition("extra", false)],
      NONE,
    );

    expect(found).toEqual([
      { fieldId: slotFieldId("needed", 0), message: "Enter a value for needed." },
    ]);
  });

  it("counts a blank slot as no value", () => {
    const values = {
      ...valid(),
      slots: { needed: [{ id: "a", value: "  ", justification: null }] },
    };

    expect(validate(values, [definition("needed", true)], NONE)).toHaveLength(1);
  });

  it("holds the form back while a code is checking or could not be named", () => {
    const checking = validate(valid(), [], { status: "checking", code: "1" });
    const down = validate(valid(), [], {
      status: "unresolved",
      code: "1",
      message: "x",
      unavailable: true,
    });

    expect(checking[0]?.fieldId).toBe(FIELD_IDS.snomed_code);
    expect(down[0]?.fieldId).toBe(FIELD_IDS.snomed_code);
    expect(down[0]?.message).toContain("could not be reached");
  });
});

describe("request bodies", () => {
  it("sends the code the server named as a string, with its leading zero", () => {
    const body = duplicateCheckBody(valid(), READY);

    expect(body.snomed_code).toBe("0391483001");
    expect(JSON.stringify(body)).toContain('"snomed_code":"0391483001"');
  });

  it("leaves out a code that is not ready, empty notes and an untouched organisation", () => {
    const body = createBody(valid(), [], { status: "checking", code: "1" }, false);

    expect(body).not.toHaveProperty("snomed_code");
    expect(body).not.toHaveProperty("notes");
    expect(body).not.toHaveProperty("organisation");
    expect(body.confirm_not_duplicate).toBe(false);
  });

  it("sends only the slots that hold a value, in render order", () => {
    const values = {
      ...valid(),
      slots: {
        tag: [
          { id: "a", value: "first", justification: "" },
          { id: "b", value: "", justification: null },
          { id: "c", value: "third", justification: "why" },
        ],
      },
    };

    expect(filledSlotIndexes(values.slots.tag)).toEqual([0, 2]);
    expect(
      createBody(values, [definition("tag", false)], NONE, true).property_values,
    ).toEqual({
      tag: [
        { value: "first", justification: null },
        { value: "third", justification: "why" },
      ],
    });
  });

  it("sends a coded value as the object the picker stored, with its system (FR-23, FR-52)", () => {
    const specimen = {
      system: "http://snomed.info/sct",
      code: "119364003",
      display: "Serum specimen",
    };
    const values = {
      ...valid(),
      slots: { specimen: [{ id: "a", value: specimen, justification: null }] },
    };

    expect(
      createBody(values, [definition("specimen", false)], NONE, true).property_values,
    ).toEqual({ specimen: [{ value: specimen, justification: null }] });
  });
});

describe("addNames (FR-23)", () => {
  const rows = (...terms: string[]) => terms.map((term) => ({ id: term, term }));

  it("trims each name and adds it after the ones already there", () => {
    const result = addNames(rows("Ferritin"), [" Fe store ", "Serum Fe"]);

    expect(result.names.map((row) => row.term)).toEqual([
      "Ferritin",
      "Fe store",
      "Serum Fe",
    ]);
    expect(result.added).toEqual(["Fe store", "Serum Fe"]);
    expect(result.repeats).toEqual([]);
  });

  it("leaves out a blank name without calling it a repeat", () => {
    const result = addNames([], ["", "   "]);

    expect(result.names).toEqual([]);
    expect(result.repeats).toEqual([]);
  });

  it("leaves out a repeat of a listed name, ignoring case and outer space", () => {
    const result = addNames(rows("Ferritin"), [" ferritin ", "FERRITIN"]);

    expect(result.names).toHaveLength(1);
    expect(result.repeats).toEqual(["ferritin", "FERRITIN"]);
  });

  it("adds a name given twice in one call once", () => {
    const result = addNames([], ["Fe store", "fe store"]);

    expect(result.added).toEqual(["Fe store"]);
    expect(result.repeats).toEqual(["fe store"]);
  });

  it("gives each added name its own id", () => {
    const result = addNames([], ["a", "b"]);

    expect(new Set(result.names.map((row) => row.id)).size).toBe(2);
  });
});

describe("filledNames (FR-23)", () => {
  it("returns the added names and then the text left in the box", () => {
    const values = { names: [{ id: "a", term: "Ferritin" }], nameDraft: " Fe store " };

    expect(filledNames(values)).toEqual(["Ferritin", "Fe store"]);
  });

  it("returns a name once when the box repeats an added one", () => {
    const values = { names: [{ id: "a", term: "Ferritin" }], nameDraft: "ferritin" };

    expect(filledNames(values)).toEqual(["Ferritin"]);
  });

  it("returns nothing for a blank box and no names", () => {
    expect(filledNames({ names: [], nameDraft: "  " })).toEqual([]);
  });
});

describe("validateAmendment (FR-35)", () => {
  const named = (term: string) => ({ ...initialValues(), names: [{ id: "n", term }] });

  it("accepts one new name, with no reference link", () => {
    expect(validateAmendment(named("Ferritin level"), NONE)).toEqual([]);
  });

  it("accepts a code the server named, with no name", () => {
    expect(validateAmendment(initialValues(), READY)).toEqual([]);
  });

  it("counts text left in the box as a name", () => {
    expect(
      validateAmendment({ ...initialValues(), nameDraft: "Ferritin level" }, NONE),
    ).toEqual([]);
  });

  it("says what to add when there is neither a name nor a code", () => {
    const found = validateAmendment(named("   "), NONE);

    expect(found).toHaveLength(1);
    expect(found[0]?.fieldId).toBe(FIELD_IDS.synonyms);
    expect(found[0]?.message).toContain("Add a new other name or a SNOMED CT code");
  });

  it("names the code, not the empty form, while a code is checking or unresolved", () => {
    const found = validateAmendment(initialValues(), { status: "checking", code: "1" });

    expect(found.map((error) => error.fieldId)).toEqual([FIELD_IDS.snomed_code]);
  });

  it("does not need a reference link, and length-checks one that is given", () => {
    const tooLong = {
      ...named("x"),
      referenceUrl: `https://example.org/${"a".repeat(2100)}`,
    };

    expect(validateAmendment({ ...named("x"), referenceUrl: "  " }, NONE)).toEqual([]);
    expect(validateAmendment(tooLong, NONE).map((error) => error.fieldId)).toEqual([
      FIELD_IDS.reference_url,
    ]);
  });

  it("refuses more names, or a longer name, than a submission may carry", () => {
    const many = {
      ...initialValues(),
      names: Array.from({ length: 101 }, (_, index) => ({
        id: `${index}`,
        term: `name ${index}`,
      })),
    };

    expect(validateAmendment(many, NONE).map((error) => error.fieldId)).toEqual([
      FIELD_IDS.synonyms,
    ]);
    expect(validateAmendment(named("a".repeat(501)), NONE)).toHaveLength(1);
  });
});

describe("amendmentBody (FR-35)", () => {
  it("sends the entry key and the new names, and nothing that is empty", () => {
    const values = {
      ...initialValues(),
      names: [
        { id: "a", term: " Ferritin " },
        { id: "b", term: "" },
      ],
    };

    expect(amendmentBody("NPTC-000006", values, NONE)).toEqual({
      entry_business_key: "NPTC-000006",
      synonyms: ["Ferritin"],
    });
  });

  it("sends the code as the string the server named, with its leading zero", () => {
    const body = amendmentBody("NPTC-000006", initialValues(), READY);

    expect(body).not.toHaveProperty("synonyms");
    expect(JSON.stringify(body)).toContain('"snomed_code":"0391483001"');
  });

  it("leaves out a code that is not ready", () => {
    const body = amendmentBody("NPTC-000006", initialValues(), {
      status: "checking",
      code: "1",
    });

    expect(body).not.toHaveProperty("snomed_code");
  });

  it("sends a reference link, notes and a changed organisation when given", () => {
    const values = {
      ...initialValues(),
      names: [{ id: "a", term: "x" }],
      referenceUrl: " https://example.org/x ",
      notes: "Seen in the RCPA manual.",
      organisation: "Example Lab",
    };

    expect(amendmentBody("NPTC-000006", values, NONE)).toEqual({
      entry_business_key: "NPTC-000006",
      synonyms: ["x"],
      reference_url: "https://example.org/x",
      notes: "Seen in the RCPA manual.",
      organisation: "Example Lab",
    });
  });
});
