import { useEffect, useRef, useState } from "react";

import { asTerminologyFieldRefusal } from "../api/conflicts.ts";
import { useUpdateTerminologyRequest } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Form } from "../components/form.tsx";
import { reasonProblem } from "../registry/property-form.ts";
import { ReasonField } from "../registry/reason-field.tsx";
import { ExpectedReleaseField, ReferenceFields } from "./terminology-fields.tsx";
import {
  formatCalendarDate,
  TERMINOLOGY_IDS,
  trackingProblems,
} from "./terminology-form.ts";
import { REASON_READERS_HINT } from "./transition-actions.ts";
import { refusalSentence } from "./transition-refusal.ts";
import { isReviewDetail } from "./working-copy-diff.ts";
import type { AnySubmissionDetail } from "./working-copy-diff.ts";

type TerminologyRequestView = components["schemas"]["TerminologyRequestView"];

const WAITING_STATE = "Awaiting terminology";

const EDITION_LABELS: Record<TerminologyRequestView["target_edition"], string> = {
  au: "Australian edition",
  int: "International edition",
};

const NO_LONGER_WAITING =
  "This submission is no longer waiting for a code, so its request can no longer change.";

const NOTHING_CHANGED = "You have not changed anything. Change a field, or cancel.";

const FALLBACK_REFUSAL =
  "The changes could not be saved. Try again, or contact an administrator if the problem persists.";

function EditTrackingDialog({
  submission,
  request,
  onClose,
  onSaved,
}: {
  submission: { id: string; state: string; row_version: number };
  request: TerminologyRequestView;
  onClose: () => void;
  onSaved: (sentence: string) => void;
}) {
  const write = useUpdateTerminologyRequest(submission.id);
  const [system, setSystem] = useState(request.external_reference.system);
  const [reference, setReference] = useState(request.external_reference.reference);
  const [expectedRelease, setExpectedRelease] = useState(request.expected_release ?? "");
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const [nothingChanged, setNothingChanged] = useState(false);

  const serverRefusal = write.isError ? asTerminologyFieldRefusal(write.error) : null;
  const fieldErrors: FormError[] =
    serverRefusal === null
      ? errors
      : [
          {
            fieldId: TERMINOLOGY_IDS[serverRefusal.field],
            message: serverRefusal.detail,
          },
        ];
  const refusal = write.isError && serverRefusal === null ? write.error : null;

  function errorFor(fieldId: string): string | undefined {
    return fieldErrors.find((error) => error.fieldId === fieldId)?.message;
  }

  function edited(...fieldIds: string[]) {
    setNothingChanged(false);
    setErrors((existing) =>
      existing.filter((error) => !fieldIds.includes(error.fieldId)),
    );
  }

  return (
    <Dialog open onClose={onClose} title="Edit tracking details">
      <Form
        submitLabel="Save"
        pendingLabel="Saving"
        pending={write.isPending}
        submitBlocked={submission.state !== WAITING_STATE}
        blockedReason={NO_LONGER_WAITING}
        errors={fieldErrors}
        formError={
          nothingChanged
            ? NOTHING_CHANGED
            : refusal === null
              ? undefined
              : refusalSentence(refusal, FALLBACK_REFUSAL)
        }
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const missingReason = reasonProblem(reason, "this change");
          const found = [
            ...trackingProblems(
              { system, reference, expectedRelease },
              request.raised_date,
            ),
            ...(missingReason === null
              ? []
              : [{ fieldId: TERMINOLOGY_IDS.reason, message: missingReason }]),
          ];
          const referenceChanged =
            system.trim() !== request.external_reference.system ||
            reference.trim() !== request.external_reference.reference;
          const releaseChanged = expectedRelease !== (request.expected_release ?? "");
          setErrors(found);
          setNothingChanged(found.length === 0 && !referenceChanged && !releaseChanged);
          if (found.length > 0 || (!referenceChanged && !releaseChanged)) {
            // An earlier server refusal no longer describes this attempt.
            write.reset();
            return;
          }
          return write
            .mutateAsync({
              ...(referenceChanged
                ? {
                    external_reference: {
                      system: system.trim(),
                      reference: reference.trim(),
                    },
                  }
                : {}),
              ...(releaseChanged
                ? { expected_release: expectedRelease === "" ? null : expectedRelease }
                : {}),
              reason: reason.trim(),
              expected_row_version: submission.row_version,
            })
            .then(
              (answer) => {
                onSaved(
                  answer.changed
                    ? "Saved. The tracking details are updated."
                    : "Nothing changed: the request already held these details.",
                );
                return { ok: true };
              },
              () => ({ ok: false }),
            );
        }}
      >
        <p className="m-0">
          Change where the request is tracked or when its code is expected. The proposed
          name, edition, justification and date raised stay as they were sent.
        </p>
        <ReferenceFields
          system={system}
          reference={reference}
          systemError={errorFor(TERMINOLOGY_IDS.external_reference_system)}
          referenceError={errorFor(TERMINOLOGY_IDS.external_reference_ref)}
          onSystem={(value) => {
            setSystem(value);
            edited(TERMINOLOGY_IDS.external_reference_system);
          }}
          onReference={(value) => {
            setReference(value);
            edited(TERMINOLOGY_IDS.external_reference_ref);
          }}
        />
        <ExpectedReleaseField
          value={expectedRelease}
          error={errorFor(TERMINOLOGY_IDS.expected_release)}
          onChange={(value) => {
            setExpectedRelease(value);
            edited(TERMINOLOGY_IDS.expected_release);
          }}
        />
        <ReasonField
          id={TERMINOLOGY_IDS.reason}
          value={reason}
          onChange={(value) => {
            setReason(value);
            edited(TERMINOLOGY_IDS.reason);
          }}
          hint={REASON_READERS_HINT}
          error={errorFor(TERMINOLOGY_IDS.reason)}
        />
      </Form>
    </Dialog>
  );
}

/**
 * The request for a new code, shown while the submission waits for it (FR-30,
 * FR-31). The server sends `terminology_request` only to a holder of
 * `submission.transition.review`, so the section needs no permission check of
 * its own: no key, no section. A reviewer may change the tracking details, and
 * the fixed fields are shown and never offered for editing.
 *
 * The line that confirms a save takes focus, because the dialog that held the
 * button has gone. It stays on screen after the state moves on.
 */
export function TerminologyRequestSection({
  submission,
}: {
  submission: AnySubmissionDetail;
}) {
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const confirmation = useRef<HTMLParagraphElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);

  useEffect(() => {
    if (saved !== null) {
      confirmation.current?.focus();
    } else if (wasEditing.current && !editing) {
      editButton.current?.focus();
    }
    wasEditing.current = editing;
  }, [editing, saved]);

  const request = isReviewDetail(submission) ? submission.terminology_request : null;
  const waiting = submission.state === WAITING_STATE;
  if (request === null || (!waiting && !editing && saved === null)) {
    return null;
  }

  return (
    <EntrySection title="Terminology request">
      <dl className="m-0 grid grid-cols-1 gap-y-1 text-sm">
        <dt className="font-medium">Proposed fully specified name</dt>
        <dd className="m-0 mb-3 min-w-0">{request.proposed_fsn}</dd>

        <dt className="font-medium">Target edition</dt>
        <dd className="m-0 mb-3 min-w-0">{EDITION_LABELS[request.target_edition]}</dd>

        <dt className="font-medium">Justification</dt>
        <dd className="m-0 mb-3 min-w-0">{request.justification}</dd>

        <dt className="font-medium">External reference</dt>
        <dd className="m-0 mb-3 min-w-0">
          {request.external_reference.system}: {request.external_reference.reference}
        </dd>

        <dt className="font-medium">Date raised</dt>
        <dd className="m-0 mb-3 min-w-0">
          <time dateTime={request.raised_date}>
            {formatCalendarDate(request.raised_date)}
          </time>
        </dd>

        <dt className="font-medium">Expected release</dt>
        <dd className="m-0 min-w-0">
          {request.expected_release === null ? (
            <span className="text-[var(--color-text-muted)]">No date given</span>
          ) : (
            <time dateTime={request.expected_release}>
              {formatCalendarDate(request.expected_release)}
            </time>
          )}
        </dd>
      </dl>
      {saved === null ? null : (
        <p ref={confirmation} tabIndex={-1} className="m-0 text-sm">
          {saved}
        </p>
      )}
      {waiting ? (
        <div>
          <Button
            ref={editButton}
            type="button"
            variant="secondary"
            onClick={() => {
              setSaved(null);
              setEditing(true);
            }}
          >
            Edit tracking details
          </Button>
        </div>
      ) : null}
      {editing ? (
        <EditTrackingDialog
          submission={submission}
          request={request}
          onClose={() => setEditing(false)}
          onSaved={(sentence) => {
            setSaved(sentence);
            setEditing(false);
          }}
        />
      ) : null}
    </EntrySection>
  );
}
