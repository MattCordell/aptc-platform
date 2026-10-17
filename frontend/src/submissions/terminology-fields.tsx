import { Field } from "../components/field.tsx";
import { FieldGrid } from "../components/field-grid.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { TERMINOLOGY_IDS } from "./terminology-form.ts";

/**
 * The two parts of an external reference (FR-31), as one group so a screen
 * reader names them together. The ids are the form's, so an error summary link
 * lands on the part it names.
 */
export function ReferenceFields({
  system,
  reference,
  systemError,
  referenceError,
  onSystem,
  onReference,
}: {
  system: string;
  reference: string;
  systemError: string | undefined;
  referenceError: string | undefined;
  onSystem: (value: string) => void;
  onReference: (value: string) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-2 border-0 p-0">
      <legend className="text-sm font-medium text-[var(--color-text)]">
        External reference (required)
      </legend>
      <FieldGrid>
        <Field
          id={TERMINOLOGY_IDS.external_reference_system}
          label="System"
          hint="Where the request is tracked, for example a service desk."
          error={systemError}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              className={INPUT_CLASSES}
              type="text"
              value={system}
              onChange={(event) => onSystem(event.target.value)}
            />
          )}
        </Field>
        <Field
          id={TERMINOLOGY_IDS.external_reference_ref}
          label="Reference"
          hint="The identifier the request has in that system."
          error={referenceError}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              className={INPUT_CLASSES}
              type="text"
              value={reference}
              onChange={(event) => onReference(event.target.value)}
            />
          )}
        </Field>
      </FieldGrid>
    </fieldset>
  );
}

/** The date the code is expected, if the authority has said. Optional. */
export function ExpectedReleaseField({
  value,
  error,
  onChange,
}: {
  value: string;
  error: string | undefined;
  onChange: (value: string) => void;
}) {
  return (
    <Field
      id={TERMINOLOGY_IDS.expected_release}
      label="Expected release"
      hint="Optional. Leave empty if no date is known."
      error={error}
    >
      {(controlProps) => (
        <input
          {...controlProps}
          className={INPUT_CLASSES}
          type="date"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  );
}
