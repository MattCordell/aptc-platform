import type { components } from "../api/schema.ts";
import { sameSlotValue } from "../catalogue/property-controls/index.ts";

type WorkingCopyView = components["schemas"]["WorkingCopyView"];
type SubmittedValue = components["schemas"]["SubmittedPropertyValue"];

/**
 * The content a reviewer edits: the fields of the working copy, which are also the
 * fields the submitter sent. A submission with no working copy has this content
 * in its original fields (FR-23).
 */
export type WorkingContent = Pick<
  WorkingCopyView,
  | "preferred_term"
  | "synonyms"
  | "snomed_code"
  | "snomed_fsn"
  | "property_values"
  | "notes"
>;

type Schemas = components["schemas"];
type ReviewDetail =
  Schemas["ReviewSubmissionDetail"] | Schemas["IdentifiedReviewSubmissionDetail"];
export type AnySubmissionDetail =
  Schemas["SubmissionDetail"] | Schemas["IdentifiedSubmissionDetail"] | ReviewDetail;

/**
 * Whether the server sent the working copy, which it does only to a holder of
 * `submission.transition.review`. Every other caller's detail has no such key.
 */
export function isReviewDetail(detail: AnySubmissionDetail): detail is ReviewDetail {
  return "working_copy" in detail;
}

/** What the submitter sent, in the shape of the working copy. */
export function originalContent(detail: AnySubmissionDetail): WorkingContent {
  return {
    preferred_term: detail.preferred_term,
    synonyms: detail.synonyms,
    snomed_code: detail.snomed_code,
    snomed_fsn: detail.snomed_fsn,
    property_values: detail.property_values,
    notes: detail.notes,
  };
}

/** What a reviewer sees as the submission's content now: the working copy, or the original while none exists. */
export function workingContentOf(detail: ReviewDetail): WorkingContent {
  return detail.working_copy ?? originalContent(detail);
}

export interface WorkingCopyDifference {
  preferredTerm: boolean;
  synonyms: boolean;
  code: boolean;
  notes: boolean;
  /** The property keys whose values differ, in the order the working copy lists them. */
  properties: string[];
}

function justificationOf(item: SubmittedValue): string | null {
  return item.justification === null || item.justification === ""
    ? null
    : item.justification;
}

function sameItem(a: SubmittedValue, b: SubmittedValue): boolean {
  return (
    justificationOf(a) === justificationOf(b) &&
    (sameSlotValue(a.value, b.value) ||
      JSON.stringify(a.value) === JSON.stringify(b.value))
  );
}

/** Whether two value lists hold the same values in the same order. */
export function sameValueItems(a: SubmittedValue[], b: SubmittedValue[]): boolean {
  return a.length === b.length && a.every((item, index) => sameItem(item, b[index]));
}

function sameNotes(a: string | null, b: string | null): boolean {
  return (a ?? "").trim() === (b ?? "").trim();
}

/** The names without regard to their order, which carries no meaning. */
function sameNames(a: string[], b: string[]): boolean {
  const sorted = (names: string[]) => names.map((name) => name.trim()).sort();
  return JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
}

/**
 * Which fields the working copy changes from what the submitter sent. A field
 * the working copy leaves as it was is not listed, so the page marks only what a
 * reviewer changed.
 */
export function workingCopyDiff(
  original: WorkingContent,
  working: WorkingContent,
): WorkingCopyDifference {
  const keys = new Set([
    ...Object.keys(working.property_values),
    ...Object.keys(original.property_values),
  ]);
  return {
    preferredTerm: original.preferred_term.trim() !== working.preferred_term.trim(),
    synonyms: !sameNames(original.synonyms, working.synonyms),
    code: original.snomed_code !== working.snomed_code,
    notes: !sameNotes(original.notes, working.notes),
    properties: [...keys].filter(
      (key) =>
        !sameValueItems(
          original.property_values[key] ?? [],
          working.property_values[key] ?? [],
        ),
    ),
  };
}

export function hasDifference(difference: WorkingCopyDifference): boolean {
  return (
    difference.preferredTerm ||
    difference.synonyms ||
    difference.code ||
    difference.notes ||
    difference.properties.length > 0
  );
}
