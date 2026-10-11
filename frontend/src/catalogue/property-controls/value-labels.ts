import { usePropertyValueResolveQueries } from "../../api/queries.ts";
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
 *
 * One query per code, not one for the whole set: a set that gains or loses a
 * member is a new query with no data, which would turn every label already
 * shown back into a bare code until it answered. A query per code keeps the
 * others cached and asks only for the code that is new.
 */
function useCodeLabels(propertyKey: string, values: readonly unknown[]) {
  const codes = values.filter((value): value is string => typeof value === "string");
  const resolved = usePropertyValueResolveQueries(
    codes.map((code) => ({ key: propertyKey, codes: [code] })),
  );
  const byCode = new Map(codes.map((code, index) => [code, resolved[index]]));
  return values.map((value) => {
    if (typeof value !== "string") {
      return value === null || value === undefined ? "" : String(value);
    }
    const query = byCode.get(value);
    if (query?.data === undefined) {
      return query?.isError ? value : null;
    }
    return codeLabel(
      value,
      query.data.items.find((item) => item.code === value)?.display,
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
