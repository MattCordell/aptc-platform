import type { ComponentPropsWithoutRef } from "react";

type CardProps = ComponentPropsWithoutRef<"div"> & {
  /** 16px padding in place of 24px, for a form that has to fit one screen. */
  compact?: boolean;
};

/**
 * A white surface with a hairline border and the card radius
 * (docs/architecture/design-system.md). It has one padding, 24px, with `compact`
 * as the one exception, and no shadow: separation comes from the border alone. A
 * plain `div` - callers add `role` or `aria-labelledby` when a card is a region.
 */
export function Card({ compact = false, className, ...rest }: CardProps) {
  return (
    <div
      {...rest}
      className={[
        "rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface)]",
        compact ? "p-4" : "p-6",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    />
  );
}
