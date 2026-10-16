import type { SubmissionField } from "../api/conflicts.ts";
import type { components } from "../api/schema.ts";
import { SUBMISSION_LIMITS } from "../catalogue/limits.ts";
import {
  newSlotId,
  slotFieldId,
  slotValue,
  submittedSlotIndexes,
} from "../catalogue/property-controls/index.ts";
import type { PropertyValueSlot } from "../catalogue/property-controls/index.ts";
import type { CodeSelection } from "../catalogue/use-code-selection.ts";
import type { FormError } from "../components/error-summary.tsx";
import { workingCopyDiff } from "./working-copy-diff.ts";
import type { WorkingContent } from "./working-copy-diff.ts";

/**
 * What the new-test form holds, and how it becomes a request (FR-23, FR-24).
 *
 * Every check here is about wording and about a field the server needs before
 * it can answer at all. The server stays the authority: it cleans the terms,
 * checks the code and fetches the reference link (ADR-0030).
 */

type CreateSubmissionBody = components["schemas"]["CreateSubmissionRequest"];
type DuplicateCheckBody = components["schemas"]["DuplicateCheckRequest"];
type PropertyDefinition = components["schemas"]["PropertyDefinitionResponse"];

/** The id of each control a server refusal can name. */
export const FIELD_IDS: Record<SubmissionField, string> = {
  preferred_term: "submission-preferred-term",
  synonyms: "submission-other-names",
  snomed_code: "submission-code-search",
  reference_url: "submission-reference-url",
  notes: "submission-notes",
  organisation: "submission-organisation",
};

export interface NameRow {
  id: string;
  term: string;
}

export interface SubmissionValues {
  preferredTerm: string;
  /** The other names the user has added, shown as a list. */
  names: NameRow[];
  /** Text typed into the other-names box and not yet added. It is sent like an added name. */
  nameDraft: string;
  /** The code the user picked. Sent only once the terminology server has named it. */
  code: string | null;
  slots: Record<string, PropertyValueSlot[]>;
  referenceUrl: string;
  notes: string;
  /** `null` until the user edits it, so the profile's value shows and is not copied. */
  organisation: string | null;
}

export function initialValues(): SubmissionValues {
  return {
    preferredTerm: "",
    names: [],
    nameDraft: "",
    code: null,
    slots: {},
    referenceUrl: "",
    notes: "",
    organisation: null,
  };
}

/** The properties the form shows, in the registry's display order. */
export function submissionRows(definitions: PropertyDefinition[]): PropertyDefinition[] {
  return [...definitions].sort((a, b) => a.display_order - b.display_order);
}

/**
 * How two names are compared when deciding whether one repeats another. The server folds
 * names further (`distinct_synonyms`) and stays the authority; this keeps the obvious
 * repeats out of the list the user sees.
 */
function nameKey(term: string): string {
  return term.trim().toLowerCase();
}

/**
 * Adds names to the list. A blank name and a name already in the list (or earlier in
 * `candidates`) is left out and the repeat reported, so the caller can say so.
 */
export function addNames(
  existing: NameRow[],
  candidates: string[],
): { names: NameRow[]; added: string[]; repeats: string[] } {
  const seen = new Set(existing.map((row) => nameKey(row.term)));
  const added: NameRow[] = [];
  const repeats: string[] = [];
  for (const candidate of candidates) {
    const term = candidate.trim();
    if (term === "") {
      continue;
    }
    if (seen.has(nameKey(term))) {
      repeats.push(term);
      continue;
    }
    seen.add(nameKey(term));
    added.push({ id: newSlotId(), term });
  }
  return {
    names: [...existing, ...added],
    added: added.map((row) => row.term),
    repeats,
  };
}

/** The names to send: those added, then the text left in the box, each once. */
export function filledNames(
  values: Pick<SubmissionValues, "names" | "nameDraft">,
): string[] {
  return addNames([], [...values.names.map((row) => row.term), values.nameDraft]).added;
}

/** The render index of each slot that holds a value, which is what the server's `ordinal` counts. */
export function filledSlotIndexes(slots: PropertyValueSlot[]): number[] {
  return submittedSlotIndexes(slots);
}

export function submittedIndexesByKey(
  slots: Record<string, PropertyValueSlot[]>,
): Record<string, number[]> {
  return Object.fromEntries(
    Object.entries(slots).map(([key, list]) => [key, filledSlotIndexes(list)]),
  );
}

export const CODE_UNAVAILABLE_MESSAGE =
  "The terminology server could not be reached, so the code could not be checked. Clear the code to send the form without one, or try again shortly.";

/** The same outage, worded for a form that already holds a code the user can keep. */
export const CODE_UNAVAILABLE_KEEPING_MESSAGE =
  "The terminology server could not be reached, so the new code could not be checked. Keep the current code to save the other changes, or try again shortly.";

/**
 * Why a picked code cannot be sent, and what the user can do about it. `keeping` is
 * set where the form already holds a code, so the way out is to keep it.
 */
export function unresolvedCodeMessage(
  selection: Extract<CodeSelection, { status: "unresolved" }>,
  keeping = false,
): string {
  if (selection.unavailable) {
    return keeping ? CODE_UNAVAILABLE_KEEPING_MESSAGE : CODE_UNAVAILABLE_MESSAGE;
  }
  return keeping
    ? `${selection.message} Keep the current code to save the other changes.`
    : `${selection.message} Clear the code to send the form without one.`;
}

function overLimit(count: number, limit: number): boolean {
  return count > limit;
}

function nameErrors(values: SubmissionValues): FormError[] {
  const { termLength, synonyms } = SUBMISSION_LIMITS;
  const names = filledNames(values);
  if (overLimit(names.length, synonyms)) {
    return [
      {
        fieldId: FIELD_IDS.synonyms,
        message: `You can give at most ${synonyms} other names. Remove ${names.length - synonyms}.`,
      },
    ];
  }
  if (names.some((name) => overLimit(name.length, termLength))) {
    return [
      {
        fieldId: FIELD_IDS.synonyms,
        message: `An other name is over ${termLength} characters. Shorten it.`,
      },
    ];
  }
  return [];
}

function codeErrors(selection: CodeSelection): FormError[] {
  if (selection.status === "checking") {
    return [
      {
        fieldId: FIELD_IDS.snomed_code,
        message: "Wait for the code to finish checking, or clear it.",
      },
    ];
  }
  if (selection.status === "unresolved") {
    return [
      { fieldId: FIELD_IDS.snomed_code, message: unresolvedCodeMessage(selection) },
    ];
  }
  return [];
}

function referenceLengthErrors(values: SubmissionValues): FormError[] {
  return overLimit(
    values.referenceUrl.trim().length,
    SUBMISSION_LIMITS.referenceUrlLength,
  )
    ? [
        {
          fieldId: FIELD_IDS.reference_url,
          message: `The link is over ${SUBMISSION_LIMITS.referenceUrlLength} characters. Use a shorter address.`,
        },
      ]
    : [];
}

function notesAndOrganisationErrors(values: SubmissionValues): FormError[] {
  const { notesLength, organisationLength } = SUBMISSION_LIMITS;
  const found: FormError[] = [];
  if (overLimit(values.notes.length, notesLength)) {
    found.push({
      fieldId: FIELD_IDS.notes,
      message: `The notes are over ${notesLength} characters. Shorten them.`,
    });
  }
  if (
    values.organisation !== null &&
    overLimit(values.organisation.length, organisationLength)
  ) {
    found.push({
      fieldId: FIELD_IDS.organisation,
      message: `The organisation is over ${organisationLength} characters. Shorten it.`,
    });
  }
  return found;
}

function termErrors(values: SubmissionValues): FormError[] {
  const { termLength } = SUBMISSION_LIMITS;
  if (values.preferredTerm.trim() === "") {
    return [{ fieldId: FIELD_IDS.preferred_term, message: "Enter the test name." }];
  }
  return overLimit(values.preferredTerm.length, termLength)
    ? [
        {
          fieldId: FIELD_IDS.preferred_term,
          message: `The test name is over ${termLength} characters. Shorten it.`,
        },
      ]
    : [];
}

function propertyErrors(
  values: SubmissionValues,
  definitions: PropertyDefinition[],
  requireValues: boolean,
): FormError[] {
  const found: FormError[] = [];
  for (const definition of definitions) {
    const list = values.slots[definition.key] ?? [];
    const filled = filledSlotIndexes(list).length;
    if (requireValues && definition.required_for_submission && filled === 0) {
      found.push({
        fieldId: slotFieldId(definition.key, 0),
        message: `Enter a value for ${definition.label}.`,
      });
    } else if (overLimit(filled, SUBMISSION_LIMITS.valuesPerProperty)) {
      found.push({
        fieldId: slotFieldId(definition.key, 0),
        message: `${definition.label} can have at most ${SUBMISSION_LIMITS.valuesPerProperty} values.`,
      });
    }
  }
  return found;
}

/**
 * The form's own mistakes, found before anything is sent. A code that is still
 * being checked, or that the server could not name, holds the form back: the
 * user clears it to send the form without one.
 */
export function validate(
  values: SubmissionValues,
  definitions: PropertyDefinition[],
  selection: CodeSelection,
): FormError[] {
  const found: FormError[] = [
    ...termErrors(values),
    ...nameErrors(values),
    ...codeErrors(selection),
    ...propertyErrors(values, definitions, true),
  ];

  if (values.referenceUrl.trim() === "") {
    found.push({
      fieldId: FIELD_IDS.reference_url,
      message: "Enter a link to a web page that supports this test.",
    });
  } else {
    found.push(...referenceLengthErrors(values));
  }

  found.push(...notesAndOrganisationErrors(values));
  return found;
}

/** The code to send, which is always the string the terminology server returned (FR-06, FR-26). */
function codeToSend(selection: CodeSelection): string | undefined {
  return selection.status === "ready" ? selection.concept.code : undefined;
}

export function duplicateCheckBody(
  values: SubmissionValues,
  selection: CodeSelection,
): DuplicateCheckBody {
  const code = codeToSend(selection);
  return {
    preferred_term: values.preferredTerm,
    synonyms: filledNames(values),
    ...(code === undefined ? {} : { snomed_code: code }),
  };
}

export function createBody(
  values: SubmissionValues,
  definitions: PropertyDefinition[],
  selection: CodeSelection,
  confirmNotDuplicate: boolean,
): CreateSubmissionBody {
  const propertyValues: NonNullable<CreateSubmissionBody["property_values"]> = {};
  for (const definition of definitions) {
    const list = values.slots[definition.key] ?? [];
    const filled = filledSlotIndexes(list).map((index) => list[index]);
    if (filled.length > 0) {
      propertyValues[definition.key] = filled.map((slot) => ({
        value: slotValue(slot),
        justification:
          slot.justification === null || slot.justification === ""
            ? null
            : slot.justification,
      }));
    }
  }
  return {
    ...duplicateCheckBody(values, selection),
    reference_url: values.referenceUrl.trim(),
    property_values: propertyValues,
    ...(values.notes.trim() === "" ? {} : { notes: values.notes }),
    ...(values.organisation === null ? {} : { organisation: values.organisation }),
    confirm_not_duplicate: confirmNotDuplicate,
  };
}

type CreateAmendmentBody = components["schemas"]["CreateAmendmentRequest"];

/**
 * The amendment form's own mistakes (FR-35). It has no test name, properties or
 * required reference link: an amendment needs at least one new name or a code
 * the terminology server has named, and nothing else. The server still refuses
 * a name or code the entry already holds.
 */
export function validateAmendment(
  values: SubmissionValues,
  selection: CodeSelection,
): FormError[] {
  const found = [...nameErrors(values), ...codeErrors(selection)];
  if (
    filledNames(values).length === 0 &&
    selection.status !== "ready" &&
    found.length === 0
  ) {
    found.push({
      fieldId: FIELD_IDS.synonyms,
      message: "Add a new other name or a SNOMED CT code to propose a change.",
    });
  }
  if (values.referenceUrl.trim() !== "") {
    found.push(...referenceLengthErrors(values));
  }
  found.push(...notesAndOrganisationErrors(values));
  return found;
}

export function amendmentBody(
  entryBusinessKey: string,
  values: SubmissionValues,
  selection: CodeSelection,
): CreateAmendmentBody {
  const code = codeToSend(selection);
  const names = filledNames(values);
  return {
    entry_business_key: entryBusinessKey,
    ...(names.length === 0 ? {} : { synonyms: names }),
    ...(code === undefined ? {} : { snomed_code: code }),
    ...(values.referenceUrl.trim() === ""
      ? {}
      : { reference_url: values.referenceUrl.trim() }),
    ...(values.notes.trim() === "" ? {} : { notes: values.notes }),
    ...(values.organisation === null ? {} : { organisation: values.organisation }),
  };
}

type WorkingCopyBody = components["schemas"]["WorkingCopyRequest"];

/** The content fields of an edit. The reason and the version are the caller's. */
export type WorkingCopyChanges = Omit<WorkingCopyBody, "reason" | "expected_row_version">;

/**
 * What the edit form starts with, taken from the content the reviewer opened it on.
 * `code` stays `null` because it holds only a code the reviewer picks: the code the
 * submission already has is shown from `opened`, and needs no lookup to be kept.
 */
export function valuesFromContent(opened: WorkingContent): SubmissionValues {
  return {
    ...initialValues(),
    preferredTerm: opened.preferred_term,
    names: opened.synonyms.map((term) => ({ id: newSlotId(), term })),
    slots: Object.fromEntries(
      Object.entries(opened.property_values).map(([key, items]) => [
        key,
        items.map((item) => ({
          id: newSlotId(),
          value: item.value,
          justification: item.justification,
        })),
      ]),
    ),
    notes: opened.notes ?? "",
  };
}

function slotItems(list: PropertyValueSlot[]): WorkingContent["property_values"][string] {
  return filledSlotIndexes(list).map((index) => ({
    value: slotValue(list[index]),
    justification:
      list[index].justification === null || list[index].justification === ""
        ? null
        : list[index].justification,
  }));
}

/** The content the form now holds, in the shape of the content it was opened on. */
function contentOfValues(
  values: SubmissionValues,
  opened: WorkingContent,
  selection: CodeSelection,
): WorkingContent {
  const propertyValues: WorkingContent["property_values"] = {};
  for (const [key, list] of Object.entries(values.slots)) {
    const items = slotItems(list);
    if (items.length > 0) {
      propertyValues[key] = items;
    }
  }
  return {
    preferred_term: values.preferredTerm,
    synonyms: filledNames(values),
    snomed_code: codeToSend(selection) ?? opened.snomed_code,
    snomed_fsn: null,
    property_values: propertyValues,
    notes: values.notes.trim() === "" ? null : values.notes,
  };
}

/**
 * The other names after someone else changed the list. The reviewer's own additions and
 * removals, measured against the content they started from, are applied to the latest
 * list, so neither person's change to the names is lost.
 */
function mergeNames(base: string[], mine: NameRow[], latest: string[]): NameRow[] {
  const baseKeys = new Set(base.map(nameKey));
  const mineKeys = new Set(mine.map((row) => nameKey(row.term)));
  const removed = new Set([...baseKeys].filter((key) => !mineKeys.has(key)));
  const kept = latest
    .filter((term) => !removed.has(nameKey(term)))
    .map((term) => ({ id: newSlotId(), term }));
  const keptKeys = new Set(kept.map((row) => nameKey(row.term)));
  const added = mine.filter(
    (row) => !baseKeys.has(nameKey(row.term)) && !keptKeys.has(nameKey(row.term)),
  );
  return [...kept, ...added];
}

/**
 * The form's values after the content it was opened on changed under it, which is what
 * a stale version means. A field the reviewer left alone takes the latest content, so
 * saving never sends an old value over a newer one, and the whole-field properties and
 * other names of an untouched field are not rebuilt from old text. A field the reviewer
 * changed keeps their change, and where both people changed the same text field or the
 * same property, the reviewer's version stays. The other names are merged by name.
 */
export function rebaseValues(
  values: SubmissionValues,
  base: WorkingContent,
  latest: WorkingContent,
): SubmissionValues {
  const mine = workingCopyDiff(base, contentOfValues(values, base, { status: "none" }));
  const fresh = valuesFromContent(latest);
  const keys = new Set([...Object.keys(values.slots), ...Object.keys(fresh.slots)]);
  return {
    ...values,
    preferredTerm: mine.preferredTerm ? values.preferredTerm : fresh.preferredTerm,
    notes: mine.notes ? values.notes : fresh.notes,
    names: mine.synonyms
      ? mergeNames(base.synonyms, values.names, latest.synonyms)
      : fresh.names,
    slots: Object.fromEntries(
      [...keys].map((key) => [
        key,
        mine.properties.includes(key)
          ? (values.slots[key] ?? [])
          : (fresh.slots[key] ?? []),
      ]),
    ),
  };
}

/**
 * The fields the reviewer changed since the form opened. A field they left alone is
 * not sent, so an edit never overwrites a change someone else made to it in the
 * meantime. `synonyms` and `property_values` go whole, because the server replaces
 * the whole field. An amendment proposes only names and a code, so it sends no more.
 * An empty result means there is nothing to send.
 */
export function workingCopyChanges(
  values: SubmissionValues,
  opened: WorkingContent,
  selection: CodeSelection,
  kind: string,
): WorkingCopyChanges {
  const edited = contentOfValues(values, opened, selection);
  const difference = workingCopyDiff(opened, edited);
  const whole = kind !== "amendment";
  return {
    ...(difference.synonyms ? { synonyms: edited.synonyms } : {}),
    ...(difference.code && edited.snomed_code !== null
      ? { snomed_code: edited.snomed_code }
      : {}),
    ...(whole && difference.preferredTerm
      ? { preferred_term: values.preferredTerm }
      : {}),
    ...(whole && difference.notes ? { notes: values.notes } : {}),
    ...(whole && difference.properties.length > 0
      ? { property_values: edited.property_values }
      : {}),
  };
}

/**
 * The edit form's own mistakes, found before anything is sent. It asks for no
 * property value that the registry marks required: a submission can predate that
 * rule, and the server names the property it refuses.
 */
export function validateWorkingCopy(
  values: SubmissionValues,
  opened: WorkingContent,
  definitions: PropertyDefinition[],
  selection: CodeSelection,
  kind: string,
): FormError[] {
  if (kind === "amendment") {
    const found = [...nameErrors(values), ...codeErrors(selection)];
    if (
      found.length === 0 &&
      filledNames(values).length === 0 &&
      selection.status !== "ready" &&
      opened.snomed_code === null
    ) {
      found.push({
        fieldId: FIELD_IDS.synonyms,
        message: "Keep at least one other name or a SNOMED CT code.",
      });
    }
    return found;
  }
  return [
    ...termErrors(values),
    ...nameErrors(values),
    ...codeErrors(selection),
    ...propertyErrors(values, definitions, false),
    ...notesAndOrganisationErrors(values),
  ];
}
