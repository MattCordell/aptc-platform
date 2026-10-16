import { Button } from "../components/button.tsx";
import { CodeChip } from "../components/code-chip.tsx";
import { ProcedurePicker } from "../catalogue/procedure-picker.tsx";
import type { CodeSelection } from "../catalogue/use-code-selection.ts";
import { FIELD_IDS, unresolvedCodeMessage } from "./form-state.ts";

/**
 * The optional SNOMED CT code of a new test (FR-26, FR-54).
 *
 * The picker and the lookup are the entry form's. What differs is the wording
 * when the terminology server is down: a submitter can still send the form
 * without a code, which an editor of an existing entry has no reason to say.
 * The code is sent only once the server has named it, and never typed in
 * full by the user (FR-06).
 *
 * A reviewer editing a working copy passes the `current` code. It is shown as it
 * stands, with no lookup, and the user can pick another. A code cannot be cleared
 * once a submission holds one, so the way back from a pick is to keep the current
 * code, and the wording says so.
 */

const OUTAGE_NOTE = "You can still send the rest of this form without a code.";
const OUTAGE_NOTE_KEEPING = "You can still save the other changes.";

export function SubmissionCodeField({
  selection,
  error,
  current,
  onPick,
  onClear,
}: {
  selection: CodeSelection;
  error?: string;
  current?: { code: string; fsn: string | null };
  onPick: (code: string) => void;
  onClear: () => void;
}) {
  const keeping = current !== undefined;
  const clearLabel = keeping ? "Keep the current code" : "Clear the code";
  return (
    <div className="flex flex-col gap-3">
      {current !== undefined && selection.status === "none" && (
        <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
          <dt className="text-[var(--color-text-muted)]">Current code</dt>
          <dd className="m-0">
            <CodeChip code={current.code} />
          </dd>
          {current.fsn === null ? null : (
            <>
              <dt className="text-[var(--color-text-muted)]">Fully specified name</dt>
              <dd className="m-0">{current.fsn}</dd>
            </>
          )}
        </dl>
      )}
      <ProcedurePicker
        id={FIELD_IDS.snomed_code}
        label={keeping ? "Choose a different SNOMED CT code" : "SNOMED CT code"}
        error={error}
        outageNote={keeping ? OUTAGE_NOTE_KEEPING : OUTAGE_NOTE}
        onPick={onPick}
      />
      {selection.status === "checking" && (
        <p className="m-0">Checking {selection.code} against the terminology server…</p>
      )}
      {selection.status === "unresolved" && (
        <div className="flex flex-col gap-2">
          <p className="m-0">{unresolvedCodeMessage(selection, keeping)}</p>
          <div>
            <Button type="button" variant="secondary" onClick={onClear}>
              {clearLabel}
            </Button>
          </div>
        </div>
      )}
      {selection.status === "ready" && (
        <div className="flex flex-col gap-2">
          <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
            <dt className="text-[var(--color-text-muted)]">Chosen code</dt>
            <dd className="m-0">
              <CodeChip code={selection.concept.code} />
            </dd>
            <dt className="text-[var(--color-text-muted)]">Fully specified name</dt>
            <dd className="m-0">{selection.concept.fsn}</dd>
            <dt className="text-[var(--color-text-muted)]">AU preferred term</dt>
            <dd className="m-0">{selection.concept.auPreferredTerm ?? "not reported"}</dd>
          </dl>
          <div>
            <Button type="button" variant="secondary" onClick={onClear}>
              {clearLabel}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
