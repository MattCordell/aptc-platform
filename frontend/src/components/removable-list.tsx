import { useLayoutEffect, useRef } from "react";
import type { ReactNode, RefObject } from "react";

import { Button } from "./button.tsx";

export type RemovableListItem = {
  /** Stable for the life of the item, so React keeps its state through a Remove. */
  id: string;
  /** The id an `ErrorSummary` link addresses to move focus to this item. */
  elementId?: string;
  label: ReactNode;
  /** The accessible name of this item's Remove button, such as "Remove Plasma". */
  removeLabel: string;
  error?: string;
  /** Extra content shown under the label, such as a justification input. */
  children?: ReactNode;
};

type RemovableListProps = {
  /** Names the list for assistive technology. */
  label: string;
  items: readonly RemovableListItem[];
  onRemove: (id: string) => void;
  /** Where focus goes when a Remove empties the list, usually the box that adds to it. */
  emptyFocus?: RefObject<HTMLElement | null>;
};

/**
 * A plain bulleted list of values the user has added, each with a Remove
 * button (NFR-31). It holds no state of its own: the caller owns `items` and
 * is told each removal through `onRemove`.
 *
 * Focus never falls to the page when its Remove button leaves the DOM. It moves
 * to the Remove button of the item that takes its place, else the item before,
 * else `emptyFocus`.
 */
export function RemovableList({
  label,
  items,
  onRemove,
  emptyFocus,
}: RemovableListProps) {
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const pending = useRef<{ id: string | null } | null>(null);

  useLayoutEffect(() => {
    const target = pending.current;
    if (target === null) {
      return;
    }
    pending.current = null;
    const next = target.id === null ? undefined : buttons.current.get(target.id);
    (next ?? emptyFocus?.current)?.focus();
  }, [items, emptyFocus]);

  if (items.length === 0) {
    return null;
  }

  return (
    <ul aria-label={label} className="m-0 flex list-disc flex-col gap-2 pl-5">
      {items.map((item, index) => (
        <li key={item.id} id={item.elementId} tabIndex={item.elementId ? -1 : undefined}>
          <div className="flex items-center justify-between gap-2">
            <span className="min-w-0 break-words">{item.label}</span>
            <Button
              ref={(button) => {
                if (button === null) {
                  buttons.current.delete(item.id);
                } else {
                  buttons.current.set(item.id, button);
                }
              }}
              type="button"
              variant="danger"
              aria-label={item.removeLabel}
              onClick={() => {
                const neighbour = items[index + 1] ?? items[index - 1];
                pending.current = { id: neighbour?.id ?? null };
                onRemove(item.id);
              }}
            >
              Remove
            </Button>
          </div>
          {item.children}
          {item.error ? (
            <p className="m-0 text-sm text-[var(--color-danger)]">{item.error}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
