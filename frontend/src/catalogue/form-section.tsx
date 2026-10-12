import type { ReactNode } from "react";

/**
 * One titled part of the entry edit form. `divided` draws the rule that
 * separates it from the part above. `group: false` leaves the part unnamed for
 * assistive technology, for a part whose title would only repeat the label of
 * the control inside it.
 */
export function FormSection({
  id,
  title,
  divided = false,
  group = true,
  children,
}: {
  id: string;
  title: string;
  divided?: boolean;
  group?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      role={group ? "group" : undefined}
      aria-labelledby={group ? id : undefined}
      className={[
        "flex flex-col gap-3",
        divided ? "border-t border-[var(--color-border)] pt-4" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <h3 id={id} className="m-0 text-lg">
        {title}
      </h3>
      {children}
    </div>
  );
}
