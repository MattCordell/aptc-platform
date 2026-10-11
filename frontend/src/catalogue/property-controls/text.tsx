import { Field } from "../../components/field.tsx";
import { INPUT_CLASSES } from "../../components/input-classes.ts";
import { commitOnEnter, entryHint } from "./types.ts";
import type { ControlProps } from "./types.ts";

/** `ControlKind.TEXT` - a short string (`string` datatype, `maxLength <= 200`). */
export function TextControl({
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
          type="text"
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={commitOnEnter(onCommit, value)}
        />
      )}
    </Field>
  );
}
