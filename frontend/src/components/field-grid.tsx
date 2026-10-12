import type { ReactNode } from "react";

/**
 * Fields side by side where there is room, and one column where there is not, so
 * the page still wraps at 200% zoom. Source order is the visual order, which keeps
 * the tab order the same as the reading order. `items-start` stops a list under one
 * box from stretching its neighbours.
 *
 * `fill` keeps every column the same width however few fields there are, so a
 * short list lines up under the row above it instead of stretching across.
 */
export function FieldGrid({
  children,
  fill = false,
}: {
  children: ReactNode;
  fill?: boolean;
}) {
  return (
    <div
      className={`grid ${
        fill
          ? "grid-cols-[repeat(auto-fill,minmax(min(20rem,100%),1fr))]"
          : "grid-cols-[repeat(auto-fit,minmax(min(20rem,100%),1fr))]"
      } items-start gap-x-4 gap-y-3`}
    >
      {children}
    </div>
  );
}
