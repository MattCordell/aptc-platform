import { useEffect, useRef, useState } from "react";

import { useHasPermission, useWithdrawSubmission } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { REASON_READERS_HINT } from "./transition-actions.ts";
import { TransitionDialog } from "./transition-dialog.tsx";

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

const CLOSED = "This submission can no longer be withdrawn.";

const FALLBACK_REFUSAL =
  "The submission could not be withdrawn. Try again, or contact an administrator if the problem persists.";

function WithdrawDialog({
  submission,
  onClose,
  onWithdrawn,
}: {
  submission: Withdrawable;
  onClose: () => void;
  onWithdrawn: () => void;
}) {
  const withdraw = useWithdrawSubmission(submission.id);
  return (
    <TransitionDialog
      title="Withdraw submission"
      intro={
        <p>
          Withdrawing {submission.preferred_term} closes it, and you cannot reopen it. To
          propose it again, send a new submission.
        </p>
      }
      submitLabel="Confirm withdrawal"
      pendingLabel="Withdrawing"
      reasonAction="withdrawing this submission"
      reasonHint={REASON_READERS_HINT}
      blocked={!mayWithdrawNow(submission)}
      blockedReason={CLOSED}
      fallbackRefusal={FALLBACK_REFUSAL}
      write={withdraw}
      send={(reason) =>
        withdraw.mutateAsync({ expected_row_version: submission.row_version, reason })
      }
      onClose={onClose}
      onDone={onWithdrawn}
    />
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
