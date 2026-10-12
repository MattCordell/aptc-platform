import type { KeyboardEvent, ReactNode } from "react";

import type { components } from "../../api/schema.ts";
import { isCodedValue, sameCodedValue } from "./coded-value.ts";

export type ControlKind = components["schemas"]["ControlKind"];

/**
 * What every `ControlKind` component receives (ADR-0013 SS3, issue #151).
 *
 * Each control owns its **own** `Field`/`Select` composition rather than
 * being wrapped in one by its caller - `Select` already wraps `Field`
 * internally (`select.tsx`), so a caller-provided `Field` around a control
 * that renders `Select` would nest two, producing two `<label>`s for one
 * `id`. `label`/`hint`/`error` are passed through here precisely so a
 * control can hand them straight to whichever baseline primitive it uses,
 * with exactly one `Field` in the tree.
 *
 * `params` is `FormControlDescriptor.params` verbatim - JSON-serialisable
 * only, per that type's own contract - so a control reads `params.step`,
 * `params.minimum`, `params.schemes`, `params.valueSetUri` etc. rather than
 * being handed a typed struct per kind; the union that would type it
 * precisely is exactly the per-datatype branching FR-77 forbids here.
 *
 * `propertyKey` is unused by every control except `concept_picker` (it is
 * `usePropertyValueOptions`'s own parameter) but is part of the common shape
 * so `CONTROLS` can stay one `Record<ControlKind, ComponentType<ControlProps>>`
 * rather than a per-kind prop type that would need a cast at the call site.
 */
export interface ControlProps {
  id: string;
  label: string;
  hint?: ReactNode;
  error?: string;
  propertyKey: string;
  params: Record<string, unknown>;
  value: unknown;
  onChange: (value: unknown) => void;
  /**
   * Present only when the property takes several values. The control is then
   * an entry box: `value`/`onChange` carry the text typed so far, and the
   * control calls `onCommit` once the user adds it - Enter in a text box, a
   * pick from a list. The caller clears the box by dropping the value. `label`
   * is the text to announce for the added value, when the control knows a
   * better one than the value itself.
   */
  onCommit?: (value: unknown, label?: string) => void;
}

/**
 * One recorded (or being-edited) value in a property's whole value set.
 *
 * `id` is a client-only key with no server meaning - stable for the life of
 * this slot in the editor (survives re-renders, does *not* survive a Remove
 * re-numbering the slots after it). It exists so `RepeatableValues` can key
 * each rendered slot on slot identity rather than array position: keying on
 * `index` made a control that owns internal state (`ConceptPickerControl`'s
 * filter text) inherit whatever the slot that used to be at that position
 * had typed, the moment a Remove shifted later slots up.
 */
export interface PropertyValueSlot {
  id: string;
  value: unknown;
  justification: string | null;
  /**
   * Set on the one trailing slot that holds text typed into a several-valued
   * property's entry box and not yet added. It is submitted like any other
   * value, so a user who forgets to press Enter does not lose what they typed.
   */
  draft?: boolean;
}

let nextSlotId = 0;

/**
 * A fresh, unique `PropertyValueSlot.id` for a newly created slot.
 *
 * A module-level counter, not `crypto.randomUUID()`: these ids carry no
 * server meaning and need no randomness, only uniqueness for the life of
 * the page - and `randomUUID` is undefined outside a secure context
 * (HTTPS or `localhost`), which `docs/operations/configuration.md`
 * explicitly anticipates this app being deployed outside of (a plain-HTTP
 * on-prem host, per `NPTC_FRONTEND_BASE_URL`'s own note). A counter has no
 * such dependency.
 *
 * The `slot-` prefix is reserved for ids this function generates - test
 * fixtures should use a different prefix (e.g. `fixture-`) so a fixture id
 * can never collide with one minted at runtime.
 */
export function newSlotId(): string {
  nextSlotId += 1;
  return `slot-${nextSlotId}`;
}

/**
 * A slot with nothing meaningful in it - null/undefined, or a blank string.
 * `RepeatableValues` uses this to drop untouched slots before a save rather
 * than submitting `{value: ""}` as a real value, and to decide whether a
 * `0..1`/`0..*` property's array should serialise as empty.
 */
export function isEmptySlotValue(value: unknown): boolean {
  return (
    value === null ||
    value === undefined ||
    (typeof value === "string" && value.trim().length === 0)
  );
}

/**
 * What a slot sends. A draft is text the user typed and never added, so it is
 * trimmed the way `commitOnEnter` trims an added value.
 */
export function slotValue(slot: PropertyValueSlot): unknown {
  return slot.draft === true && typeof slot.value === "string"
    ? slot.value.trim()
    : slot.value;
}

/**
 * Whether two slot values are the same value. Coded values match on `system` and
 * `code` alone, so a recorded term that has since been reworded does not make the
 * same code count as new.
 */
export function sameSlotValue(a: unknown, b: unknown): boolean {
  return isCodedValue(a) && isCodedValue(b) ? sameCodedValue(a, b) : a === b;
}

/**
 * The render index of each slot a save sends, which is what the server's
 * `ordinal` counts: every slot with a value, except a draft that repeats a
 * value already added. Dropping the repeat sends what `commit` would have
 * accepted, so leaving "Anaemia " in the box beside "Anaemia" adds nothing.
 */
export function submittedSlotIndexes(slots: readonly PropertyValueSlot[]): number[] {
  return slots.reduce<number[]>((indexes, slot, index) => {
    const value = slotValue(slot);
    const repeat =
      slot.draft === true &&
      slots.some((other) => other.draft !== true && sameSlotValue(other.value, value));
    if (!isEmptySlotValue(value) && !repeat) {
      indexes.push(index);
    }
    return indexes;
  }, []);
}

/** The field id a `PropertyIssueItem` with this `ordinal` maps to (`properties-panel.tsx`). */
export function slotFieldId(propertyKey: string, ordinal: number): string {
  return `${propertyKey}-${ordinal}`;
}

/** The field id a cardinality-level issue (`ordinal: null`) maps to. */
export function groupFieldId(propertyKey: string): string {
  return `${propertyKey}-group`;
}

/**
 * The `onKeyDown` for a text-style control in entry mode, or `undefined`
 * outside it. Enter adds the value and never submits the form, whether the box
 * holds one or not. Shift+Enter adds nothing: a textarea keeps it to break a
 * line, and a single-line box swallows it because a browser would otherwise
 * submit the form. The Enter that confirms an input-method composition is left
 * alone.
 */
export function commitOnEnter(
  onCommit: ControlProps["onCommit"],
  current: unknown,
): ((event: KeyboardEvent<HTMLElement>) => void) | undefined {
  if (onCommit === undefined) {
    return undefined;
  }
  return (event) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) {
      return;
    }
    if (event.shiftKey) {
      if (event.currentTarget.tagName !== "TEXTAREA") {
        event.preventDefault();
      }
      return;
    }
    event.preventDefault();
    if (!isEmptySlotValue(current)) {
      onCommit(typeof current === "string" ? current.trim() : current);
    }
  };
}

/** The hint for a text-style control: the caller's, else how to add in entry mode. */
export function entryHint(
  hint: ReactNode,
  onCommit: ControlProps["onCommit"],
  howToAdd = "Press Enter to add this value.",
): ReactNode {
  return hint ?? (onCommit === undefined ? undefined : howToAdd);
}
