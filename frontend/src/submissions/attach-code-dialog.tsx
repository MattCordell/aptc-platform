import { useState } from "react";

import { asFieldRefusal, asReasonRefusal } from "../api/conflicts.ts";
import { useAttachTerminologyCode } from "../api/queries.ts";
import { useCodeSelection } from "../catalogue/use-code-selection.ts";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Form } from "../components/form.tsx";
import { reasonProblem } from "../registry/property-form.ts";
import { ReasonField } from "../registry/reason-field.tsx";
import { SubmissionCodeField } from "./code-field.tsx";
import { FIELD_IDS, requiredCodeMessage } from "./form-state.ts";
import type { Requestable } from "./terminology-request-dialog.tsx";
import { REASON_READERS_HINT } from "./transition-actions.ts";
import { refusalSentence } from "./transition-refusal.ts";

/** One dialog is open at a time, so the field's id can be fixed. */
const REASON_FIELD_ID = "attach-code-reason";

const NO_LONGER_OFFERED =
  "The platform no longer offers Attach code for this submission.";

const FALLBACK_REFUSAL =
  "The code could not be attached. Try again, or contact an administrator if the problem persists.";

/** The code and name the server wrote to the working copy. */
export interface AttachedCode {
  code: string;
  fsn: string;
}

/**
 * The Administrator's Attach code (FR-30): the code the terminology authority
 * issued for the request, chosen with the code picker. The server checks it on
 * the terminology server, writes it and its fully specified name to the working
 * copy and moves the submission to `Ready for approval` in one step. The name
 * shown is the one the server returned for the pick, never one the user typed
 * (FR-82), and the code is sent only once that lookup has named it (FR-06).
 *
 * The dialog stays open on every refusal so the user reads why. A 422 or an
 * outage that names the code marks the picker, and a 422 on the reason marks the
 * reason. A refusal of a code the user has since replaced no longer shows.
 */
export function AttachCodeDialog({
  submission,
  from,
  onClose,
  onAttached,
}: {
  submission: Requestable;
  /** The state the user chose the move from. The submission prop moves on while
   * the write settles, and the dialog's wording must not change under the user. */
  from: string;
  onClose: () => void;
  onAttached: (attached: AttachedCode) => void;
}) {
  const write = useAttachTerminologyCode(submission.id);
  const [code, setCode] = useState<string | null>(null);
  const [sentCode, setSentCode] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const selection = useCodeSelection(code);

  const reasonRefusal = write.isError ? asReasonRefusal(write.error) : null;
  const codeRefusal = write.isError ? asFieldRefusal(write.error) : null;
  const serverErrors: FormError[] = [
    ...(reasonRefusal === null
      ? []
      : [{ fieldId: REASON_FIELD_ID, message: reasonRefusal.detail }]),
    ...(codeRefusal?.field === "snomed_code" && sentCode === code
      ? [{ fieldId: FIELD_IDS.snomed_code, message: codeRefusal.detail }]
      : []),
  ];
  const fieldErrors = serverErrors.length > 0 ? serverErrors : errors;
  const refusal =
    write.isError &&
    reasonRefusal === null &&
    (codeRefusal === null || codeRefusal.field !== "snomed_code")
      ? write.error
      : null;

  function errorFor(fieldId: string): string | undefined {
    return fieldErrors.find((error) => error.fieldId === fieldId)?.message;
  }

  function problems(): FormError[] {
    const found: FormError[] = [];
    if (selection.status === "none") {
      found.push({
        fieldId: FIELD_IDS.snomed_code,
        message: "Choose the code the terminology authority issued.",
      });
    } else if (selection.status === "checking") {
      found.push({
        fieldId: FIELD_IDS.snomed_code,
        message: "Wait for the code to finish checking, or clear it.",
      });
    } else if (selection.status === "unresolved") {
      found.push({
        fieldId: FIELD_IDS.snomed_code,
        message: requiredCodeMessage(selection),
      });
    }
    const missingReason = reasonProblem(reason, "attaching this code");
    if (missingReason !== null) {
      found.push({ fieldId: REASON_FIELD_ID, message: missingReason });
    }
    return found;
  }

  return (
    <Dialog open onClose={onClose} title="Attach code">
      <Form
        submitLabel="Attach code"
        pendingLabel="Attaching"
        pending={write.isPending}
        submitBlocked={
          submission.state !== from ||
          !submission.available_transitions.includes("Ready for approval")
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
          if (found.length > 0 || selection.status !== "ready") {
            // An earlier server refusal no longer describes this attempt.
            write.reset();
            return;
          }
          setSentCode(selection.concept.code);
          return write
            .mutateAsync({
              snomed_code: selection.concept.code,
              reason: reason.trim(),
              expected_row_version: submission.row_version,
            })
            .then(
              (answer) => {
                onAttached({ code: answer.snomed_code, fsn: answer.snomed_fsn });
                return { ok: true };
              },
              () => ({ ok: false }),
            );
        }}
      >
        <p className="m-0">
          Attach the code the terminology authority issued for {submission.preferred_term}
          . The platform checks it on the terminology server, writes it and its fully
          specified name to the working copy, and moves the submission to Ready for
          approval.
        </p>

        <SubmissionCodeField
          required
          selection={selection}
          error={errorFor(FIELD_IDS.snomed_code)}
          onPick={(picked) => {
            setCode(picked);
            setErrors((existing) =>
              existing.filter((error) => error.fieldId !== FIELD_IDS.snomed_code),
            );
          }}
          onClear={() => setCode(null)}
        />

        <ReasonField
          id={REASON_FIELD_ID}
          value={reason}
          onChange={(value) => {
            setReason(value);
            setErrors((existing) =>
              existing.filter((error) => error.fieldId !== REASON_FIELD_ID),
            );
          }}
          hint={REASON_READERS_HINT}
          error={errorFor(REASON_FIELD_ID)}
        />
      </Form>
    </Dialog>
  );
}
