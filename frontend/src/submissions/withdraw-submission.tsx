import { useEffect, useRef, useState } from "react";

import {
  asReasonRefusal,
  asSubmissionVersionConflict,
  refusalDetail,
} from "../api/conflicts.ts";
import { useHasPermission, useWithdrawSubmission } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { Dialog } from "../components/dialog.tsx";
import type { FormError } from "../components/error-summary.tsx";
import { Form } from "../components/form.tsx";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { reasonProblem } from "../registry/property-form.ts";
import { ReasonField } from "../registry/reason-field.tsx";

type SubmissionDetail = components["schemas"]["SubmissionDetail"];

type Withdrawable = Pick<
  SubmissionDetail,
  "id" | "is_own" | "available_transitions" | "row_version" | "preferred_term"
>;

/**
 * Whether the server offers this caller the withdrawal now. It reads the
 * server's own list, so no screen keeps a second copy of the state graph.
 */
function mayWithdrawNow(submission: Withdrawable): boolean {
  return submission.available_transitions.includes("Withdrawn");
}

const WITHDRAW_PERMISSION = "submission.withdraw.own";

const REASON_FIELD_ID = "withdraw-reason";

const STALE_VERSION =
  "This submission changed since you opened it. The page now shows the latest version. Check it, then confirm again.";

const CLOSED = "This submission can no longer be withdrawn.";

const FALLBACK_REFUSAL =
  "The submission could not be withdrawn. Try again, or contact an administrator if the problem persists.";

function refusalSentence(error: unknown): string {
  if (asSubmissionVersionConflict(error) !== null) {
    return STALE_VERSION;
  }
  return refusalDetail(error) ?? FALLBACK_REFUSAL;
}

function WithdrawDialog({
  submission,
  onClose,
  onWithdrawn,
}: {
  submission: Withdrawable;
  onClose: () => void;
  onWithdrawn: () => void;
}) {
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);
  const withdraw = useWithdrawSubmission(submission.id);

  const reasonRefusal = withdraw.isError ? asReasonRefusal(withdraw.error) : null;
  const fieldErrors: FormError[] =
    reasonRefusal === null
      ? errors
      : [{ fieldId: REASON_FIELD_ID, message: reasonRefusal.detail }];
  const refusal = withdraw.isError && reasonRefusal === null ? withdraw.error : null;

  return (
    <Dialog open onClose={onClose} title="Withdraw submission">
      <Form
        submitLabel="Confirm withdrawal"
        pendingLabel="Withdrawing"
        pending={withdraw.isPending}
        submitBlocked={!mayWithdrawNow(submission)}
        blockedReason={CLOSED}
        errors={fieldErrors}
        formError={refusal === null ? undefined : refusalSentence(refusal)}
        errorSummaryHeadingLevel={3}
        secondaryActions={
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        }
        onSubmit={() => {
          const missing = reasonProblem(reason, "withdrawing this submission");
          setErrors(
            missing === null ? [] : [{ fieldId: REASON_FIELD_ID, message: missing }],
          );
          if (missing !== null) {
            // An earlier server refusal no longer describes this attempt.
            withdraw.reset();
            return;
          }
          return withdraw
            .mutateAsync({
              expected_row_version: submission.row_version,
              reason: reason.trim(),
            })
            .then(
              () => {
                onWithdrawn();
                return { ok: true };
              },
              () => ({ ok: false }),
            );
        }}
      >
        <p>
          Withdrawing {submission.preferred_term} closes it, and you cannot reopen it. To
          propose it again, send a new submission.
        </p>
        <ReasonField
          id={REASON_FIELD_ID}
          value={reason}
          onChange={setReason}
          error={fieldErrors.find((error) => error.fieldId === REASON_FIELD_ID)?.message}
        />
      </Form>
    </Dialog>
  );
}

/**
 * The submitter's way to take back their own submission (FR-28). The button
 * shows only for a caller who holds `submission.withdraw.own`, on a submission
 * that is theirs and whose `available_transitions` include `Withdrawn`: the
 * server refuses every other case, so hiding it is a convenience, never the
 * check (NFR-20, FR-44).
 *
 * The dialog stays open on a refusal, even when the refusal means the
 * submission has closed and the button has gone, so the user reads why. A
 * success closes it and moves focus to a confirmation, because the button that
 * opened the dialog no longer exists to take focus back.
 */
export function WithdrawSubmission({ submission }: { submission: Withdrawable }) {
  // Keyed by id so the open dialog and the confirmation never carry over when
  // the route shows another submission.
  return <WithdrawPanel key={submission.id} submission={submission} />;
}

function WithdrawPanel({ submission }: { submission: Withdrawable }) {
  const mayWithdraw = useHasPermission(WITHDRAW_PERMISSION);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [withdrawn, setWithdrawn] = useState(false);
  const confirmation = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (withdrawn) {
      confirmation.current?.focus();
    }
  }, [withdrawn]);

  const canWithdraw = mayWithdrawNow(submission);
  if (!mayWithdraw || !submission.is_own || (!canWithdraw && !withdrawn && !dialogOpen)) {
    return null;
  }

  return (
    <EntrySection title="Your submission">
      <div className="flex flex-col items-start gap-2">
        {canWithdraw ? (
          <Button type="button" variant="secondary" onClick={() => setDialogOpen(true)}>
            Withdraw submission
          </Button>
        ) : null}
        {withdrawn ? (
          <p ref={confirmation} tabIndex={-1} className="m-0 text-sm">
            You withdrew this submission.
          </p>
        ) : null}
        {dialogOpen ? (
          <WithdrawDialog
            submission={submission}
            onClose={() => setDialogOpen(false)}
            onWithdrawn={() => {
              setDialogOpen(false);
              setWithdrawn(true);
            }}
          />
        ) : null}
      </div>
    </EntrySection>
  );
}
