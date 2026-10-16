import { useEffect, useRef, useState } from "react";

import { asReasonRefusal, asSubmissionVersionConflict } from "../api/conflicts.ts";
import {
  useEditWorkingCopy,
  useHasPermission,
  useSubmissionPropertyDefinitions,
} from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { RepeatableValues } from "../catalogue/property-controls/index.ts";
import type { PropertyValueSlot } from "../catalogue/property-controls/index.ts";
import { useCodeSelection } from "../catalogue/use-code-selection.ts";
import { Button } from "../components/button.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { FieldGrid } from "../components/field-grid.tsx";
import { Form } from "../components/form.tsx";
import type { SubmitOutcome } from "../components/form.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { reasonProblem } from "../registry/property-form.ts";
import { ReasonField } from "../registry/reason-field.tsx";
import { SubmissionCodeField } from "./code-field.tsx";
import {
  CODE_UNAVAILABLE_KEEPING_MESSAGE,
  FIELD_IDS,
  submissionRows,
  submittedIndexesByKey,
  validateWorkingCopy,
  valuesFromContent,
  workingCopyChanges,
} from "./form-state.ts";
import type { SubmissionValues, WorkingCopyChanges } from "./form-state.ts";
import { OtherNamesField } from "./other-names-field.tsx";
import { readRefusal } from "./refusals.ts";
import { NotesField } from "./support-fields.tsx";
import type { WorkingContent } from "./working-copy-diff.ts";

type PropertyCardinality = components["schemas"]["PropertyCardinality"];

/**
 * The states the server accepts a working copy edit in. The API gives no flag for
 * it, so the list is the one place the screen names them, and it holds no rule
 * beyond which button to offer: the server still refuses every other state with a
 * 409, so hiding the button is a convenience, never the check (NFR-20, FR-44).
 */
const EDITABLE_STATES: readonly string[] = [
  "Triage",
  "Under review",
  "Ready for approval",
];

function isEditableState(state: string): boolean {
  return EDITABLE_STATES.includes(state);
}

const REVIEW_PERMISSION = "submission.transition.review";

const REASON_FIELD_ID = "working-copy-reason";
const PROPERTIES_HEADING_ID = "working-copy-properties-heading";

const REASON_HINT =
  "Recorded in the audit log with this edit. It does not appear in this submission's history.";

const STALE_VERSION =
  "This submission changed since you opened the form. Your changes and reason are kept. Save again to apply them to the latest version, or cancel to read it first.";

const NOT_EDITABLE = "This submission can no longer be edited.";

const NOTHING_CHANGED = "You have not changed anything. Change a field, or cancel.";

const FALLBACK_REFUSAL =
  "The changes could not be saved. Check your connection and try again, or contact an administrator if the problem persists.";

const CHANGED_FIELD_LABELS: Record<keyof WorkingCopyChanges, string> = {
  preferred_term: "Test name",
  synonyms: "Other names",
  snomed_code: "SNOMED CT code",
  property_values: "Properties",
  notes: "Notes",
};

/** The sentence that confirms a save, naming the fields it changed. */
function savedSentence(sent: WorkingCopyChanges, changed: boolean): string {
  if (!changed) {
    return "Nothing changed: the working copy already held these values.";
  }
  const names = (Object.keys(CHANGED_FIELD_LABELS) as (keyof WorkingCopyChanges)[])
    .filter((key) => key in sent)
    .map((key) => CHANGED_FIELD_LABELS[key]);
  return `Saved. Changed: ${names.join(", ")}.`;
}

/**
 * The Edit action in the sidebar, for a caller who may review a submission in a state
 * that can be edited. The form itself is in the main column, so this section only
 * opens it and then holds the line that confirms a save. The line takes focus,
 * because the form that held it has gone (FR-23).
 */
export function EditWorkingCopySection({
  state,
  editing,
  saved,
  onEdit,
}: {
  state: string;
  editing: boolean;
  saved: string | null;
  onEdit: () => void;
}) {
  const permitted = useHasPermission(REVIEW_PERMISSION);
  const confirmation = useRef<HTMLParagraphElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);

  useEffect(() => {
    if (saved !== null) {
      confirmation.current?.focus();
    } else if (wasEditing.current && !editing) {
      // The form was cancelled, and the button that opened it is back.
      editButton.current?.focus();
    }
    wasEditing.current = editing;
  }, [editing, saved]);

  if (!permitted || (!editing && saved === null && !isEditableState(state))) {
    return null;
  }
  return (
    <EntrySection title="Edit">
      <div className="flex flex-col items-start gap-2">
        {editing ? (
          <p className="m-0 text-sm">
            You are editing this submission. Save or cancel in the form.
          </p>
        ) : isEditableState(state) ? (
          <>
            <p className="m-0 text-sm">
              Change this submission&rsquo;s content before it is approved. What the
              submitter sent stays on the page.
            </p>
            <Button ref={editButton} type="button" variant="secondary" onClick={onEdit}>
              Edit
            </Button>
          </>
        ) : null}
        {saved === null ? null : (
          <p ref={confirmation} tabIndex={-1} className="m-0 text-sm">
            {saved}
          </p>
        )}
      </div>
    </EntrySection>
  );
}

/**
 * The form that edits a submission's working copy (FR-23), in place of the content it
 * edits. It reuses the submission form's fields and refusals, and an amendment shows
 * only its other names and its code, because the server accepts no more.
 *
 * `opened` is the content the form started from. The form sends only the fields the
 * user changed against it, so an edit never overwrites a field someone else changed
 * since. The write carries the submission's current `row_version`: a stale one is
 * refused, the page reads the submission again, and the user's changes and reason
 * stay in the form for another try. The form stays open when the state stops being
 * editable, so the user's text is not lost, and Save says why it is blocked.
 */
export function EditWorkingCopyForm({
  submission,
  opened,
  onSaved,
  onCancel,
}: {
  submission: { id: string; kind: string; state: string; row_version: number };
  opened: WorkingContent;
  onSaved: (sentence: string) => void;
  onCancel: () => void;
}) {
  const amendment = submission.kind === "amendment";
  const definitions = useSubmissionPropertyDefinitions(!amendment);
  const write = useEditWorkingCopy(submission.id);
  const [values, setValues] = useState<SubmissionValues>(() => valuesFromContent(opened));
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const selection = useCodeSelection(values.code);

  useEffect(() => {
    // The Edit button that opened the form has gone, so focus moves to the first field.
    document
      .getElementById(amendment ? FIELD_IDS.synonyms : FIELD_IDS.preferred_term)
      ?.focus();
  }, [amendment]);

  const rows = submissionRows(definitions.data?.items ?? []);
  const current =
    opened.snomed_code === null
      ? undefined
      : { code: opened.snomed_code, fsn: opened.snomed_fsn };

  function errorFor(fieldId: string): string | undefined {
    return errors.find((error) => error.fieldId === fieldId)?.message;
  }

  /** Applies an edit and drops the errors that described the text it replaced. */
  function edit(
    patch:
      | Partial<SubmissionValues>
      | ((previous: SubmissionValues) => Partial<SubmissionValues>),
    clears: (fieldId: string) => boolean,
  ) {
    setValues((previous) => ({
      ...previous,
      ...(typeof patch === "function" ? patch(previous) : patch),
    }));
    setErrors((existing) =>
      existing.some((error) => clears(error.fieldId))
        ? existing.filter((error) => !clears(error.fieldId))
        : existing,
    );
    setFormError(null);
  }

  function onSupportEdit(patch: Partial<SubmissionValues>, fieldId: string) {
    edit(patch, (id) => id === fieldId);
  }

  function refuse(error: unknown): SubmitOutcome {
    const reasonRefusal = asReasonRefusal(error);
    if (reasonRefusal !== null) {
      setErrors([{ fieldId: REASON_FIELD_ID, message: reasonRefusal.detail }]);
      setFormError(null);
      return { ok: false };
    }
    if (asSubmissionVersionConflict(error) !== null) {
      setErrors([]);
      setFormError(STALE_VERSION);
      return { ok: false };
    }
    const outcome = readRefusal(
      error,
      submittedIndexesByKey(values.slots),
      FALLBACK_REFUSAL,
      current === undefined ? undefined : CODE_UNAVAILABLE_KEEPING_MESSAGE,
    );
    if (outcome.kind === "refused") {
      setErrors(outcome.fieldErrors);
      setFormError(outcome.message);
    } else {
      setErrors([]);
      setFormError(FALLBACK_REFUSAL);
    }
    return { ok: false };
  }

  async function submit(): Promise<SubmitOutcome> {
    setFormError(null);
    const missingReason = reasonProblem(reason, "this edit");
    const found = [
      ...validateWorkingCopy(values, opened, rows, selection, submission.kind),
      ...(missingReason === null
        ? []
        : [{ fieldId: REASON_FIELD_ID, message: missingReason }]),
    ];
    setErrors(found);
    if (found.length > 0) {
      return { ok: false };
    }
    const changes = workingCopyChanges(values, opened, selection, submission.kind);
    if (Object.keys(changes).length === 0) {
      setFormError(NOTHING_CHANGED);
      return { ok: false };
    }
    try {
      const answer = await write.mutateAsync({
        ...changes,
        reason: reason.trim(),
        expected_row_version: submission.row_version,
      });
      onSaved(savedSentence(changes, answer.changed));
      return { ok: true };
    } catch (error) {
      return refuse(error);
    }
  }

  return (
    <EntrySection title="Edit this submission">
      <Form
        submitLabel="Save"
        pendingLabel="Saving"
        pending={write.isPending}
        submitBlocked={!isEditableState(submission.state)}
        blockedReason={NOT_EDITABLE}
        errors={errors}
        formError={formError ?? undefined}
        errorSummaryHeadingLevel={3}
        onSubmit={submit}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        }
      >
        <p className="m-0">
          {amendment
            ? "Change the other names or the code this amendment proposes. "
            : "Change what this submission proposes. "}
          After you save, the page marks each change and shows what the submitter sent.
        </p>

        <FieldGrid>
          {amendment ? null : (
            <Field
              id={FIELD_IDS.preferred_term}
              label="Test name (required)"
              error={errorFor(FIELD_IDS.preferred_term)}
            >
              {(controlProps) => (
                <input
                  {...controlProps}
                  className={INPUT_CLASSES}
                  type="text"
                  value={values.preferredTerm}
                  onChange={(event) =>
                    edit(
                      { preferredTerm: event.target.value },
                      (id) => id === FIELD_IDS.preferred_term,
                    )
                  }
                />
              )}
            </Field>
          )}

          <OtherNamesField
            names={values.names}
            draft={values.nameDraft}
            error={errorFor(FIELD_IDS.synonyms)}
            onChange={(change) => edit(change, (id) => id === FIELD_IDS.synonyms)}
          />
        </FieldGrid>

        <SubmissionCodeField
          selection={selection}
          current={current}
          error={errorFor(FIELD_IDS.snomed_code)}
          onPick={(code) => edit({ code }, (id) => id === FIELD_IDS.snomed_code)}
          onClear={() => edit({ code: null }, (id) => id === FIELD_IDS.snomed_code)}
        />

        {amendment ? null : (
          <>
            {definitions.isPending && <p className="m-0">Loading the properties…</p>}
            {definitions.isError && (
              <p className="m-0">
                The properties could not be loaded, so they are not shown and stay as they
                are. Reload the page to try again.
              </p>
            )}
            {rows.length > 0 && (
              <div
                role="group"
                aria-labelledby={PROPERTIES_HEADING_ID}
                className="flex flex-col gap-2"
              >
                <h3 id={PROPERTIES_HEADING_ID} className="m-0">
                  Properties
                </h3>
                <FieldGrid>
                  {rows.map((definition) => (
                    <RepeatableValues
                      key={definition.key}
                      propertyKey={definition.key}
                      label={
                        definition.required_for_submission
                          ? `${definition.label} (required)`
                          : definition.label
                      }
                      cardinality={definition.cardinality as PropertyCardinality}
                      control={definition.form_control.control}
                      params={definition.form_control.params}
                      slots={values.slots[definition.key] ?? []}
                      onChange={(next: PropertyValueSlot[]) =>
                        edit(
                          (previous) => ({
                            slots: { ...previous.slots, [definition.key]: next },
                          }),
                          (id) => id.startsWith(`${definition.key}-`),
                        )
                      }
                      errors={errors}
                      compact
                    />
                  ))}
                </FieldGrid>
              </div>
            )}
            <NotesField value={values.notes} errorFor={errorFor} onEdit={onSupportEdit} />
          </>
        )}

        <ReasonField
          id={REASON_FIELD_ID}
          value={reason}
          onChange={(next) => {
            setReason(next);
            setErrors((existing) =>
              existing.filter((error) => error.fieldId !== REASON_FIELD_ID),
            );
          }}
          hint={REASON_HINT}
          error={errorFor(REASON_FIELD_ID)}
        />
      </Form>
    </EntrySection>
  );
}
