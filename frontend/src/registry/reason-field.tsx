import { Field } from "../components/field.tsx";

const AUDIT_ONLY_HINT =
  "Recorded in the audit log with this change. It is not published.";

/**
 * The reason every registry write requires. By default it goes to the audit log
 * only, so unlike an entry's changelog note it is never published as History
 * text and needs no wording rules beyond being present. A screen whose reason
 * is shown to other people passes its own `hint`, so the field does not claim
 * otherwise.
 */
export function ReasonField({
  id,
  value,
  onChange,
  error,
  hint = AUDIT_ONLY_HINT,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  error: string | undefined;
  hint?: string;
}) {
  return (
    <Field id={id} label="Reason" hint={hint} error={error}>
      {(controlProps) => (
        <input
          {...controlProps}
          type="text"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  );
}
