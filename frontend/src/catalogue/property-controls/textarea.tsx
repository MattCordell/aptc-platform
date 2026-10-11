import { Field } from "../../components/field.tsx";
import { commitOnEnter, entryHint } from "./types.ts";
import type { ControlProps } from "./types.ts";

/** `ControlKind.TEXTAREA` - a longer string (`string` datatype, `maxLength > 200`). */
export function TextareaControl({
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
        <textarea
          {...controlProps}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={commitOnEnter(onCommit, value)}
        />
      )}
    </Field>
  );
}
