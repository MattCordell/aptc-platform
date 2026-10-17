import { useState } from "react";

import { asTerminologyFieldRefusal } from "../api/conflicts.ts";
import { useRequestTerminology } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { Select } from "../components/select.tsx";
import { reasonProblem } from "../registry/property-form.ts";
import { ReasonField } from "../registry/reason-field.tsx";
import { ExpectedReleaseField, ReferenceFields } from "./terminology-fields.tsx";
import { TERMINOLOGY_IDS, trackingProblems } from "./terminology-form.ts";
import { REASON_READERS_HINT } from "./transition-actions.ts";
import { refusalSentence } from "./transition-refusal.ts";

type SubmissionDetail = components["schemas"]["SubmissionDetail"];
type TargetEdition = components["schemas"]["TargetEdition"];

export type Requestable = Pick<
  SubmissionDetail,
  "id" | "state" | "available_transitions" | "row_version" | "preferred_term"
>;

const EDITIONS: { value: TargetEdition; label: string }[] = [
  { value: "au", label: "Australian edition" },
  { value: "int", label: "International edition" },
];

const NO_LONGER_OFFERED =
  "The platform no longer offers a request for a new code for this submission.";

const FALLBACK_REFUSAL =
  "The request could not be sent. Try again, or contact an administrator if the problem persists.";

function isEdition(value: string): value is TargetEdition {
  return EDITIONS.some((edition) => edition.value === value);
}

/**
 * The reviewer's request for a new SNOMED CT concept (FR-30, FR-31). It moves the
 * submission to `Awaiting terminology` and records what was asked for, where it
 * is tracked and when the code is expected. The server keeps the proposed name,
 * edition, justification and raised date fixed once sent, and lets the
 * reviewer change the tracking details later.
 *
 * Every field but the expected release is required. The dialog checks that
 * before it sends anything, and a 422 from the server marks the field it names.
 * The dialog stays open on every other refusal so the user reads why, and the
 * caller closes it on success.
 */
export function TerminologyRequestDialog({
  submission,
  from,
  onClose,
  onRequested,
}: {
  submission: Requestable;
  /** The state the user chose the move from. The submission prop moves on while
   * the write settles, and the dialog's wording must not change under the user. */
  from: string;
  onClose: () => void;
  onRequested: () => void;
}) {
  const write = useRequestTerminology(submission.id);
  const [proposedFsn, setProposedFsn] = useState("");
  const [edition, setEdition] = useState("");
  const [justification, setJustification] = useState("");
  const [system, setSystem] = useState("");
  const [reference, setReference] = useState("");
  const [raisedDate, setRaisedDate] = useState("");
  const [expectedRelease, setExpectedRelease] = useState("");
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);

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

  /** Drops the local error that described the text a change replaces. */
  function edited(fieldId: string) {
    setErrors((existing) => existing.filter((error) => error.fieldId !== fieldId));
  }

  function problems(): FormError[] {
    const found: FormError[] = [];
    if (proposedFsn.trim().length === 0) {
      found.push({
        fieldId: TERMINOLOGY_IDS.proposed_fsn,
        message: "Enter the proposed fully specified name.",
      });
    }
    if (!isEdition(edition)) {
      found.push({
        fieldId: TERMINOLOGY_IDS.target_edition,
        message: "Choose the edition the code is for.",
      });
    }
    if (justification.trim().length === 0) {
      found.push({
        fieldId: TERMINOLOGY_IDS.justification,
        message: "Enter why the catalogue needs a new code.",
      });
    }
    found.push(...trackingProblems({ system, reference, expectedRelease }, raisedDate));
    if (raisedDate.length === 0) {
      found.push({
        fieldId: TERMINOLOGY_IDS.raised_date,
        message: "Enter the date the request was raised.",
      });
    }
    const missingReason = reasonProblem(reason, "this request");
    if (missingReason !== null) {
      found.push({ fieldId: TERMINOLOGY_IDS.reason, message: missingReason });
    }
    // The summary lists the problems in the order the fields appear.
    const order = Object.values(TERMINOLOGY_IDS);
    return found.sort((a, b) => order.indexOf(a.fieldId) - order.indexOf(b.fieldId));
  }

  return (
    <Dialog open onClose={onClose} title="Request a new code">
      <Form
        submitLabel="Send request"
        pendingLabel="Sending"
        pending={write.isPending}
        submitBlocked={
          submission.state !== from ||
          !submission.available_transitions.includes("Awaiting terminology")
        }
        blockedReason={NO_LONGER_OFFERED}
        errors={fieldErrors}
        formError={
          refusal === null ? undefined : refusalSentence(refusal, FALLBACK_REFUSAL)
        }
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const found = problems();
          setErrors(found);
          if (found.length > 0 || !isEdition(edition)) {
            // An earlier server refusal no longer describes this attempt.
            write.reset();
            return;
          }
          return write
            .mutateAsync({
              proposed_fsn: proposedFsn.trim(),
              target_edition: edition,
              justification: justification.trim(),
              external_reference: { system: system.trim(), reference: reference.trim() },
              raised_date: raisedDate,
              ...(expectedRelease.length > 0
                ? { expected_release: expectedRelease }
                : {}),
              reason: reason.trim(),
              expected_row_version: submission.row_version,
            })
            .then(
              () => {
                onRequested();
                return { ok: true };
              },
              () => ({ ok: false }),
            );
        }}
      >
        <p className="m-0">
          Move {submission.preferred_term} to Awaiting terminology and record the request
          for a new SNOMED CT code. The submission waits there until an Administrator
          attaches the code the terminology authority issues.
        </p>

        <Field
          id={TERMINOLOGY_IDS.proposed_fsn}
          label="Proposed fully specified name (required)"
          error={errorFor(TERMINOLOGY_IDS.proposed_fsn)}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              className={INPUT_CLASSES}
              type="text"
              value={proposedFsn}
              onChange={(event) => {
                setProposedFsn(event.target.value);
                edited(TERMINOLOGY_IDS.proposed_fsn);
              }}
            />
          )}
        </Field>

        <Select
          id={TERMINOLOGY_IDS.target_edition}
          label="Target edition (required)"
          placeholder="Choose an edition"
          options={EDITIONS}
          value={edition}
          error={errorFor(TERMINOLOGY_IDS.target_edition)}
          onChange={(event) => {
            setEdition(event.target.value);
            edited(TERMINOLOGY_IDS.target_edition);
          }}
        />

        <Field
          id={TERMINOLOGY_IDS.justification}
          label="Justification (required)"
          error={errorFor(TERMINOLOGY_IDS.justification)}
        >
          {(controlProps) => (
            <textarea
              {...controlProps}
              className={INPUT_CLASSES}
              rows={3}
              value={justification}
              onChange={(event) => {
                setJustification(event.target.value);
                edited(TERMINOLOGY_IDS.justification);
              }}
            />
          )}
        </Field>

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

        <Field
          id={TERMINOLOGY_IDS.raised_date}
          label="Date raised (required)"
          hint="When the request was raised with the terminology authority."
          error={errorFor(TERMINOLOGY_IDS.raised_date)}
        >
          {(controlProps) => (
            <input
              {...controlProps}
              className={INPUT_CLASSES}
              type="date"
              value={raisedDate}
              onChange={(event) => {
                setRaisedDate(event.target.value);
                edited(TERMINOLOGY_IDS.raised_date);
                edited(TERMINOLOGY_IDS.expected_release);
              }}
            />
          )}
        </Field>

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
