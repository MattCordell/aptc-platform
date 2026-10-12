import type { ReactNode } from "react";

/**
 * Fields side by side where there is room, and one column where there is not, so
 * the page still wraps at 200% zoom. Source order is the visual order, which keeps
 * the tab order the same as the reading order. `items-start` stops a list under one
 * box from stretching its neighbours.
 */
export function FieldGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(min(20rem,100%),1fr))] items-start gap-x-4 gap-y-3">
      {children}
    </div>
  );
}
