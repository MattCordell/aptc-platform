import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { CodeChip } from "../components/code-chip.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { BindingRetirement } from "./binding-retirement.tsx";
import { EntrySection } from "./entry-section.tsx";
import { statusLabelFor, statusToneFor } from "./status-options.ts";

type EntryDetail = components["schemas"]["EntryDetail"];

/**
 * Beside the entry edit form: the entry's own facts, and the synonyms and codes
 * it has retired (FR-04, FR-08). None of it is part of the form's Save.
 * Reinstate opens its own dialog, which saves on its own.
 */
export function EntryEditSidebar({
  entry,
  onReinstate,
}: {
  entry: EntryDetail;
  onReinstate: (term: string) => void;
}) {
  const retiredTerms = entry.designations
    .filter((designation) => designation.status !== "active")
    .map((designation) => designation.term);
  const retiredCodes = entry.bindings.filter((binding) => binding.status === "retired");

  return (
    <>
      <EntrySection title="Details">
        <dl className="m-0 grid grid-cols-1 gap-y-1 text-sm">
          <dt className="font-medium">Identifier</dt>
          <dd className="m-0 mb-3 min-w-0 font-mono">{entry.business_key}</dd>

          <dt className="font-medium">Entry status</dt>
          <dd className="m-0 mb-3 min-w-0">
            <StatusBadge
              tone={statusToneFor(entry.status)}
              label={statusLabelFor(entry.status)}
            />
          </dd>

          <dt className="font-medium">Last changed</dt>
          <dd className="m-0 min-w-0 tabular-nums">
            <time dateTime={entry.updated_at}>
              {new Date(entry.updated_at).toLocaleString()}
            </time>
          </dd>
        </dl>
      </EntrySection>

      {retiredTerms.length > 0 && (
        <EntrySection title="Retired synonyms">
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {retiredTerms.map((term) => (
              <li key={term} className="flex items-center justify-between gap-2">
                <span className="min-w-0 break-words">{term}</span>
                <Button
                  type="button"
                  variant="secondary"
                  className="shrink-0"
                  aria-label={`Reinstate ${term}`}
                  onClick={() => onReinstate(term)}
                >
                  Reinstate
                </Button>
              </li>
            ))}
          </ul>
        </EntrySection>
      )}

      {retiredCodes.length > 0 && (
        <EntrySection title="Retired SNOMED CT codes">
          <ul className="m-0 flex list-none flex-col gap-3 p-0 text-sm">
            {retiredCodes.map((binding, index) => (
              // A code can be bound and retired more than once, so the code
              // alone does not identify an item.
              <li key={`${binding.code}:${index}`} className="flex flex-col gap-1">
                <CodeChip code={binding.code} />
                <span className="min-w-0 break-words">{binding.fsn}</span>
                <BindingRetirement binding={binding} />
              </li>
            ))}
          </ul>
        </EntrySection>
      )}
    </>
  );
}
