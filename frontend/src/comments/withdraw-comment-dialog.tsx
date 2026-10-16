import { useState } from "react";

import { asReasonRefusal, refusalDetail } from "../api/conflicts.ts";
import { useWithdrawInternalComment, type CommentTarget } from "../api/queries.ts";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Form } from "../components/form.tsx";
import { reasonProblem } from "../registry/property-form.ts";
import { ReasonField } from "../registry/reason-field.tsx";

/** One dialog is open at a time, so the field's id can be fixed. */
const REASON_FIELD_ID = "withdraw-comment-reason";

/** The server's limit on a withdrawal reason. The generated schema does not
 * carry it, so it is copied here. */
const MAX_LENGTH = 2000;

const FALLBACK_REFUSAL =
  "The comment could not be withdrawn. Try again, or contact an administrator if the problem persists.";

/**
 * Withdrawing an internal comment needs a reason (FR-95). The comment stays on
 * the page with its text, marked as withdrawn, so the reason tells the next
 * reader why it no longer stands. The dialog stays open on every refusal so the
 * user reads why: a 422 on `reason` marks the field, and any other refusal
 * shows the server's own sentence, such as a comment already withdrawn.
 */
export function WithdrawCommentDialog({
  target,
  commentId,
  alreadyWithdrawn = false,
  onClose,
  onDone,
}: {
  target: CommentTarget;
  commentId: string;
  /** True once the list shows the comment withdrawn, so confirming is pointless. */
  alreadyWithdrawn?: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const withdraw = useWithdrawInternalComment(target);
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);

  const reasonRefusal = withdraw.isError ? asReasonRefusal(withdraw.error) : null;
  const fieldErrors: FormError[] =
    reasonRefusal === null
      ? errors
      : [{ fieldId: REASON_FIELD_ID, message: reasonRefusal.detail }];
  const refusal = withdraw.isError && reasonRefusal === null ? withdraw.error : null;

  return (
    <Dialog open onClose={onClose} title="Withdraw comment">
      <Form
        submitLabel="Confirm withdrawal"
        pendingLabel="Withdrawing"
        pending={withdraw.isPending}
        submitBlocked={alreadyWithdrawn}
        blockedReason="This comment was already withdrawn."
        errors={fieldErrors}
        formError={
          refusal === null ? undefined : (refusalDetail(refusal) ?? FALLBACK_REFUSAL)
        }
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const missing = reasonProblem(reason, "withdrawing this comment");
          setErrors(
            missing === null ? [] : [{ fieldId: REASON_FIELD_ID, message: missing }],
          );
          if (missing !== null) {
            // An earlier server refusal no longer describes this attempt.
            withdraw.reset();
            return;
          }
          return withdraw.mutateAsync({ commentId, reason: reason.trim() }).then(
            () => {
              onDone();
              return { ok: true };
            },
            () => ({ ok: false }),
          );
        }}
      >
        <p>
          The comment stays on the page with its text, marked as withdrawn. Everyone who
          can read internal comments sees your reason.
        </p>
        <ReasonField
          id={REASON_FIELD_ID}
          value={reason}
          onChange={(value) => {
            setReason(value);
            // A refusal of the earlier text no longer describes this text.
            if (withdraw.isError) {
              withdraw.reset();
            }
          }}
          maxLength={MAX_LENGTH}
          hint={`Say why the comment no longer stands. Up to ${MAX_LENGTH.toLocaleString("en-AU")} characters.`}
          error={fieldErrors.find((error) => error.fieldId === REASON_FIELD_ID)?.message}
        />
      </Form>
    </Dialog>
  );
}
