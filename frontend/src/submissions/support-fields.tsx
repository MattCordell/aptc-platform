import { Field } from "../components/field.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { FIELD_IDS } from "./form-state.ts";
import type { SubmissionValues } from "./form-state.ts";

/** The example in the reference-link hint: a guideline or a method suits a new test, a supplier's directory a new name. */
const REFERENCE_EXAMPLE = {
  test: "a guideline or a method",
  change: "a supplier's test directory",
} as const;

/**
 * The reference link, notes and organisation that a new test and an amendment
 * both carry (FR-23, FR-35). A new test needs a reference link and an
 * amendment does not, which is the only difference in what they ask. Each is its
 * own component so a form can place it beside others (FR-23).
 */

type Support = {
  errorFor: (fieldId: string) => string | undefined;
  onEdit: (patch: Partial<SubmissionValues>, fieldId: string) => void;
};

export function ReferenceLinkField({
  value,
  errorFor,
  onEdit,
  referenceRequired,
  noun,
}: Support & {
  value: string;
  referenceRequired: boolean;
  noun: "test" | "change";
}) {
  return (
    <Field
      id={FIELD_IDS.reference_url}
      label={referenceRequired ? "Reference link (required)" : "Reference link"}
      hint={`${referenceRequired ? "" : "Optional. "}A supporting page, such as ${REFERENCE_EXAMPLE[noun]}. The platform checks that it answers.`}
      error={errorFor(FIELD_IDS.reference_url)}
    >
      {(controlProps) => (
        <input
          {...controlProps}
          className={INPUT_CLASSES}
          type="text"
          inputMode="url"
          autoComplete="off"
          value={value}
          onChange={(event) =>
            onEdit({ referenceUrl: event.target.value }, FIELD_IDS.reference_url)
          }
        />
      )}
    </Field>
  );
}

export function NotesField({ value, errorFor, onEdit }: Support & { value: string }) {
  return (
    <Field id={FIELD_IDS.notes} label="Notes" error={errorFor(FIELD_IDS.notes)}>
      {(controlProps) => (
        <textarea
          {...controlProps}
          className={INPUT_CLASSES}
          rows={3}
          value={value}
          onChange={(event) => onEdit({ notes: event.target.value }, FIELD_IDS.notes)}
        />
      )}
    </Field>
  );
}

/** Shows the profile's organisation until the user edits it, so it is sent only when changed. */
export function OrganisationField({
  value,
  defaultValue,
  errorFor,
  onEdit,
}: Support & { value: string | null; defaultValue: string }) {
  return (
    <Field
      id={FIELD_IDS.organisation}
      label="Organisation"
      hint="Filled in from your profile. Change it if needed."
      error={errorFor(FIELD_IDS.organisation)}
    >
      {(controlProps) => (
        <input
          {...controlProps}
          className={INPUT_CLASSES}
          type="text"
          value={value ?? defaultValue}
          onChange={(event) =>
            onEdit({ organisation: event.target.value }, FIELD_IDS.organisation)
          }
        />
      )}
    </Field>
  );
}
