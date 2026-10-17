import type { TerminologyField } from "../api/conflicts.ts";
import type { FormError } from "../components/error-summary.tsx";

/** One dialog is open at a time, so the ids can be fixed. */
export const TERMINOLOGY_IDS: Record<
  TerminologyField | "target_edition" | "raised_date",
  string
> = {
  proposed_fsn: "terminology-proposed-fsn",
  target_edition: "terminology-target-edition",
  justification: "terminology-justification",
  external_reference_system: "terminology-reference-system",
  external_reference_ref: "terminology-reference",
  raised_date: "terminology-raised-date",
  expected_release: "terminology-expected-release",
  reason: "terminology-reason",
};

/** The tracking fields a reviewer can change while the submission waits. */
export interface TrackingValues {
  system: string;
  reference: string;
  /** `YYYY-MM-DD`, or empty when no release is expected yet. */
  expectedRelease: string;
}

function problem(field: TerminologyField, message: string): FormError {
  return { fieldId: TERMINOLOGY_IDS[field], message };
}

/**
 * What is wrong with the tracking fields before anything is sent. The server
 * still decides: this only spares a round trip for a blank part or a release
 * that falls before the date the request was raised. Both strings are ISO
 * dates, so they compare as text.
 */
export function trackingProblems(
  values: TrackingValues,
  raisedDate: string,
): FormError[] {
  const found: FormError[] = [];
  if (values.system.trim().length === 0) {
    found.push(
      problem("external_reference_system", "Enter the system that holds the request."),
    );
  }
  if (values.reference.trim().length === 0) {
    found.push(
      problem("external_reference_ref", "Enter the reference the request has there."),
    );
  }
  if (
    values.expectedRelease.length > 0 &&
    raisedDate.length > 0 &&
    values.expectedRelease < raisedDate
  ) {
    found.push(
      problem(
        "expected_release",
        "The expected release cannot be earlier than the date the request was raised.",
      ),
    );
  }
  return found;
}
