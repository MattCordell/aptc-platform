import { Button } from "../components/button.tsx";
import { Field } from "../components/field.tsx";
import { FieldGrid } from "../components/field-grid.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { ADD_SYNONYMS_FIELD_ID, addedTerms, synonymFieldId } from "./synonym-state.ts";
import type { SynonymRow } from "./synonym-state.ts";

/**
 * The entry's RCPA Synonyms, inside the edit form (FR-04, FR-36), in two parts the
 * form places apart: the box that adds terms, beside the preferred term, and the
 * synonyms the entry already holds, under them both.
 *
 * The box takes a cell pasted straight from the legacy workbook, delimiters and
 * all. Each active synonym is a text box. Remove marks a synonym for retirement
 * and the form's Save performs it, so one Save and one changelog note cover every
 * change. A retired synonym is not deleted: the platform keeps it and its history.
 */

export function AddSynonymsField({
  addText,
  addError,
  onAddTextChange,
}: {
  addText: string;
  addError: string | undefined;
  onAddTextChange: (text: string) => void;
}) {
  const terms = addedTerms(addText);

  return (
    <div className="flex flex-col gap-1">
      <Field
        id={ADD_SYNONYMS_FIELD_ID}
        label="Add synonyms"
        hint="Separate terms with a semicolon, or paste a cell."
        error={addError}
      >
        {(controlProps) => (
          <input
            {...controlProps}
            className={INPUT_CLASSES}
            type="text"
            value={addText}
            onChange={(event) => onAddTextChange(event.target.value)}
          />
        )}
      </Field>
      {/* Not a live region: it changes on every keystroke, and a screen-reader
          editor would be interrupted once per character. The result is announced
          once, after the save. */}
      <p className="m-0 text-sm text-[var(--color-text-muted)]">
        {addText.trim().length === 0
          ? "No new synonyms yet."
          : terms.length === 0
            ? "Nothing to add. A cell of only delimiters adds no terms."
            : `Save will add ${terms.length} ${terms.length === 1 ? "term" : "terms"}: ${terms
                .map((term) => `“${term}”`)
                .join(", ")}`}
      </p>
    </div>
  );
}

const CURRENT_SYNONYMS_LABEL_ID = "entry-current-synonyms";

export function CurrentSynonyms({
  rows,
  rowErrors,
  onRowsChange,
}: {
  rows: SynonymRow[];
  /** The reason a row's change was refused or is invalid, by row id. */
  rowErrors: Record<string, string>;
  onRowsChange: (next: SynonymRow[]) => void;
}) {
  function update(id: string, change: Partial<SynonymRow>) {
    onRowsChange(rows.map((row) => (row.id === id ? { ...row, ...change } : row)));
  }

  return (
    <div
      role="group"
      aria-labelledby={CURRENT_SYNONYMS_LABEL_ID}
      className="flex flex-col gap-1"
    >
      <p
        id={CURRENT_SYNONYMS_LABEL_ID}
        className="m-0 text-sm font-medium text-[var(--color-text)]"
      >
        Current synonyms
      </p>
      {rows.length === 0 ? (
        <p className="m-0">This entry has no RCPA Synonyms.</p>
      ) : (
        <FieldGrid fill>
          {rows.map((row, index) =>
            row.removed ? (
              <div key={row.id} className="flex flex-col gap-1">
                <div className="flex min-h-10 items-center justify-between gap-2">
                  <p className="m-0 min-w-0 break-words">
                    <del>{row.original}</del> will be removed when you save.
                  </p>
                  <Button
                    type="button"
                    variant="secondary"
                    className="min-h-10 shrink-0"
                    aria-label={`Keep ${row.original}`}
                    onClick={() => update(row.id, { removed: false })}
                  >
                    Keep
                  </Button>
                </div>
                {rowErrors[row.id] !== undefined && (
                  <p className="m-0 text-sm text-[var(--color-danger)]">
                    {rowErrors[row.id]}
                  </p>
                )}
              </div>
            ) : (
              <div key={row.id} className="flex items-start gap-2">
                <Field
                  id={synonymFieldId(row.id)}
                  label={`Synonym ${index + 1}`}
                  hideLabel
                  error={rowErrors[row.id]}
                  className="min-w-0 flex-1"
                >
                  {(controlProps) => (
                    <input
                      {...controlProps}
                      className={INPUT_CLASSES}
                      type="text"
                      value={row.term}
                      onChange={(event) => update(row.id, { term: event.target.value })}
                    />
                  )}
                </Field>
                <Button
                  type="button"
                  variant="danger"
                  className="min-h-10 shrink-0"
                  aria-label={`Remove ${row.original}`}
                  onClick={() => update(row.id, { removed: true })}
                >
                  Remove
                </Button>
              </div>
            ),
          )}
        </FieldGrid>
      )}
    </div>
  );
}
