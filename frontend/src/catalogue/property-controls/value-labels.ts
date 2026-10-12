import { usePropertyValueResolveQueries } from "../../api/queries.ts";
import { findCoded, isCodedValue } from "../coded-value.ts";
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
 * gets its name when the server knows one. The answer must name the same system
 * as the value. When it does not, the label falls back to the term recorded with
 * the value, then to the bare code.
 *
 * One query per code, not one for the whole set: a set that gains or loses a
 * member is a new query with no data, which would turn every label already
 * shown back into a bare code until it answered. A query per code keeps the
 * others cached and asks only for the code that is new.
 */
function useCodeLabels(propertyKey: string, values: readonly unknown[]) {
  const coded = values.map((value) => (isCodedValue(value) ? value : null));
  const resolved = usePropertyValueResolveQueries(
    coded.map((value) => ({
      key: propertyKey,
      codes: value === null ? [] : [value.code],
    })),
  );
  return values.map((value, index) => {
    const own = coded[index];
    if (own === null) {
      return value === null || value === undefined ? "" : String(value);
    }
    const query = resolved[index];
    if (query?.data === undefined) {
      return query?.isError ? codeLabel(own.code, own.display) : null;
    }
    return codeLabel(own.code, findCoded(query.data.items, own)?.display ?? own.display);
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
