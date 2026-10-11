import { MAX_RESOLVE_CODES, usePropertyValueResolveQueries } from "../../api/queries.ts";
import type { ControlKind } from "./types.ts";

/** How a coded value reads wherever it is shown: the code, then its name when one is known. */
export function codeLabel(code: string, display: string | null | undefined): string {
  return display === null || display === undefined ? code : `${code} — ${display}`;
}

/**
 * Turns a property's recorded values into the text a list shows for each.
 * `null` means the label is still being fetched, so the caller can show
 * something it already knows instead of flashing the bare code.
 *
 * A hook per control, called once for each rendering of a property's whole
 * value set, so the same property always calls the same one.
 */
export type ValueLabels = (
  propertyKey: string,
  values: readonly unknown[],
) => (string | null)[];

function useTextLabels(_propertyKey: string, values: readonly unknown[]) {
  return values.map((value) =>
    value === null || value === undefined ? "" : String(value),
  );
}

/**
 * Resolves by code, so a value the property's bound list no longer offers still
 * gets its name when the server knows one, and reads as its bare code when it
 * does not.
 */
function useCodeLabels(propertyKey: string, values: readonly unknown[]) {
  const codes = values.filter((value): value is string => typeof value === "string");
  const [resolved] = usePropertyValueResolveQueries([
    { key: propertyKey, codes: codes.slice(0, MAX_RESOLVE_CODES) },
  ]);
  return values.map((value) => {
    if (typeof value !== "string") {
      return value === null || value === undefined ? "" : String(value);
    }
    if (resolved.data === undefined) {
      return resolved.isError ? value : null;
    }
    return codeLabel(
      value,
      resolved.data.items.find((item) => item.code === value)?.display,
    );
  });
}

/**
 * Every `ControlKind` mapped to its labelling hook. A `Record`, like `CONTROLS`,
 * so a new kind without a row is a `tsc -b` error rather than a silent fallback.
 */
export const VALUE_LABELS: Record<ControlKind, ValueLabels> = {
  text: useTextLabels,
  textarea: useTextLabels,
  number: useTextLabels,
  uri: useTextLabels,
  concept_picker: useCodeLabels,
};
