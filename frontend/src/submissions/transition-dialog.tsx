import { useState, type ReactNode } from "react";

import {
  asReasonRefusal,
  asSubmissionVersionConflict,
  refusalDetail,
} from "../api/conflicts.ts";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Form } from "../components/form.tsx";
import { reasonProblem } from "../registry/property-form.ts";
import { ReasonField } from "../registry/reason-field.tsx";

/** One dialog is open at a time, so the field's id can be fixed. */
const reasonFieldId = "transition-reason";

const STALE_VERSION =
  "This submission changed since you opened it. The page now shows the latest version. Check it, then confirm again.";

/** What the dialog reads from the write it drives. A `useMutation` result fits. */
interface ReasonWrite {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  reset: () => void;
}

/**
 * The dialog for a change of state that needs a reason and nothing else
 * (FR-28, FR-81): withdrawing a submission, and each move a reviewer makes.
 * The caller owns the write, so one dialog serves both routes.
 *
 * The reason is required, and the dialog stays open on every refusal so the
 * user reads why: a 422 on `reason` marks the field, a stale version says the
 * page has reloaded, and any other refusal shows the server's own sentence. The
 * caller closes it on success.
 */
export function TransitionDialog({
  title,
  intro,
  submitLabel,
  pendingLabel,
  reasonAction,
  reasonHint,
  blocked,
  blockedReason,
  fallbackRefusal,
  write,
  send,
  onClose,
  onDone,
}: {
  title: string;
  intro: ReactNode;
  submitLabel: string;
  pendingLabel: string;
  /** Completes "Enter the reason for ...", as in "moving this submission". */
  reasonAction: string;
  reasonHint: string;
  /** True once the server no longer offers this move, so confirming is pointless. */
  blocked: boolean;
  blockedReason: string;
  fallbackRefusal: string;
  write: ReasonWrite;
  send: (reason: string) => Promise<unknown>;
  onClose: () => void;
  onDone: () => void;
}) {
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);

  const reasonRefusal = write.isError ? asReasonRefusal(write.error) : null;
  const fieldErrors: FormError[] =
    reasonRefusal === null
      ? errors
      : [{ fieldId: reasonFieldId, message: reasonRefusal.detail }];
  const refusal = write.isError && reasonRefusal === null ? write.error : null;

  return (
    <Dialog open onClose={onClose} title={title}>
      <Form
        submitLabel={submitLabel}
        pendingLabel={pendingLabel}
        pending={write.isPending}
        submitBlocked={blocked}
        blockedReason={blockedReason}
        errors={fieldErrors}
        formError={
          refusal === null ? undefined : refusalSentence(refusal, fallbackRefusal)
        }
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const missing = reasonProblem(reason, reasonAction);
          setErrors(
            missing === null ? [] : [{ fieldId: reasonFieldId, message: missing }],
          );
          if (missing !== null) {
            // An earlier server refusal no longer describes this attempt.
            write.reset();
            return;
          }
          return send(reason.trim()).then(
            () => {
              onDone();
              return { ok: true };
            },
            () => ({ ok: false }),
          );
        }}
      >
        {intro}
        <ReasonField
          id={reasonFieldId}
          value={reason}
          onChange={setReason}
          hint={reasonHint}
          error={fieldErrors.find((error) => error.fieldId === reasonFieldId)?.message}
        />
      </Form>
    </Dialog>
  );
}

function refusalSentence(error: unknown, fallback: string): string {
  if (asSubmissionVersionConflict(error) !== null) {
    return STALE_VERSION;
  }
  return refusalDetail(error) ?? fallback;
}
