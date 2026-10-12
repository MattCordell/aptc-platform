/**
 * What the API stores and expects for a coded value: the code, the system it belongs
 * to, and the term recorded with it. `code` stays a string end to end (FR-06).
 */
export interface CodedValue {
  system: string;
  code: string;
  display?: string | null;
}

/**
 * Looks at the shape of the value, never at the property's `datatype`: branching on
 * that is what ADR-0013 forbids.
 */
export function isCodedValue(value: unknown): value is CodedValue {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const { code, system, display } = value as Record<string, unknown>;
  return (
    typeof code === "string" &&
    typeof system === "string" &&
    (display === null || display === undefined || typeof display === "string")
  );
}

/** The code of a coded value, or `null` for anything else. */
export function codeOf(value: unknown): string | null {
  return isCodedValue(value) ? value.code : null;
}

/**
 * The value a pick stores. `display` is left out when the server named none, because
 * the server accepts a string there and refuses `null`.
 */
export function toCodedValue(item: {
  system: string;
  code: string;
  display?: string | null;
}): CodedValue {
  return item.display === null || item.display === undefined
    ? { system: item.system, code: item.code }
    : { system: item.system, code: item.code, display: item.display };
}

/** Two coded values are the same value when `system` and `code` match. */
export function sameCodedValue(a: CodedValue, b: CodedValue): boolean {
  return a.system === b.system && a.code === b.code;
}

/** The item naming the same value as `value`: both `system` and `code` must match. */
export function findCoded<T extends { system: string; code: string }>(
  items: readonly T[] | undefined,
  value: CodedValue,
): T | undefined {
  return items?.find((item) => sameCodedValue(item, value));
}
