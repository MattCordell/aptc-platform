import { Field } from "../../components/field.tsx";
import { INPUT_CLASSES } from "../../components/input-classes.ts";
import { commitOnEnter, entryHint } from "./types.ts";
import type { ControlProps } from "./types.ts";

/**
 * `ControlKind.URI` - the `url` datatype. `params.schemes` names the schemes
 * the handler accepts (e.g. `["http", "https"]`) but is not enforced here:
 * ADR-0030 keeps the server the authority on shape, and a bad scheme comes
 * back as a field-level 422 naming this property.
 */
export function UriControl({
  id,
  label,
  hint,
  error,
  value,
  onChange,
  onCommit,
}: ControlProps) {
  return (
    <Field id={id} label={label} hint={entryHint(hint, onCommit)} error={error}>
      {(controlProps) => (
        <input
          {...controlProps}
          className={INPUT_CLASSES}
          type="url"
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={commitOnEnter(onCommit, value)}
        />
      )}
    </Field>
  );
}
