import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { useTransitionSubmission } from "../api/queries.ts";
import { Button } from "../components/button.tsx";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { AttachCodeDialog, type AttachedCode } from "./attach-code-dialog.tsx";
import { ApproveDialog, type Approvable, type ApprovedEntry } from "./approve-dialog.tsx";
import { submissionStateLabel, type SubmissionState } from "./state-options.ts";
import { TerminologyRequestDialog } from "./terminology-request-dialog.tsx";
import {
  isMadeOnTheTriagePanel,
  moveWording,
  REASON_READERS_HINT,
} from "./transition-actions.ts";
import { TransitionDialog } from "./transition-dialog.tsx";

type Triageable = Approvable;

const NO_LONGER_OFFERED = "The platform no longer offers this move for this submission.";

const FALLBACK_REFUSAL =
  "The submission could not be moved. Try again, or contact an administrator if the problem persists.";

function MoveDialog({
  submission,
  from,
  to,
  onClose,
  onMoved,
}: {
  submission: Triageable;
  /** The state the user chose the move from. The submission prop moves on while
   * the write settles, and the dialog's wording must not change under the user. */
  from: string;
  to: SubmissionState;
  onClose: () => void;
  onMoved: () => void;
}) {
  const move = useTransitionSubmission(submission.id);
  const wording = moveWording(from, to, submissionStateLabel(to));
  return (
    <TransitionDialog
      title={wording.label}
      intro={
        <p>
          Move {submission.preferred_term} from {submissionStateLabel(from)} to{" "}
          {submissionStateLabel(to)}. {wording.effect}
        </p>
      }
      submitLabel="Confirm move"
      pendingLabel="Moving"
      reasonAction="this move"
      reasonHint={REASON_READERS_HINT}
      blocked={
        submission.state !== from || !submission.available_transitions.includes(to)
      }
      blockedReason={NO_LONGER_OFFERED}
      fallbackRefusal={FALLBACK_REFUSAL}
      write={move}
      send={(reason) =>
        move.mutateAsync({
          to_state: to,
          expected_row_version: submission.row_version,
          reason,
        })
      }
      onClose={onClose}
      onDone={onMoved}
    />
  );
}

/**
 * The moves a reviewer makes on a submission (FR-28, FR-81): one button for each
 * state the server lists in `available_transitions`, apart from the moves that
 * have a screen of their own. The server filters that list by the caller's
 * permissions and the submission's state, so the panel keeps no copy of the
 * state graph. The server refuses every other move, so hiding a button is a
 * convenience, never the check (NFR-20, FR-44).
 *
 * The dialog stays open on a refusal so the user reads why. A success closes it
 * and moves focus to a confirmation, because the button that opened it may have
 * gone with the old state. The panel stays on screen while that line shows, even
 * when the new state offers no move here.
 */
export function TriageSubmission({ submission }: { submission: Triageable }) {
  // Keyed by id so the open dialog and the confirmation never carry over when
  // the route shows another submission.
  return <TriagePanel key={submission.id} submission={submission} />;
}

/** Attach code is the one move to Ready for approval that starts from Awaiting terminology. */
function isAttach(from: string, to: SubmissionState): boolean {
  return from === "Awaiting terminology" && to === "Ready for approval";
}

function TriagePanel({ submission }: { submission: Triageable }) {
  const [moving, setMoving] = useState<{ from: string; to: SubmissionState } | null>(
    null,
  );
  const [movedTo, setMovedTo] = useState<SubmissionState | null>(null);
  const [approvedEntry, setApprovedEntry] = useState<ApprovedEntry | null>(null);
  const [attachedCode, setAttachedCode] = useState<AttachedCode | null>(null);
  const confirmation = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (movedTo !== null) {
      confirmation.current?.focus();
    }
  }, [movedTo]);

  const moves = submission.available_transitions.filter((to) =>
    isMadeOnTheTriagePanel(submission.state, to),
  );
  if (moves.length === 0 && movedTo === null && moving === null) {
    return null;
  }

  return (
    <EntrySection title="Triage">
      <div className="flex flex-col items-start gap-2">
        {moves.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {moves.map((to) => (
              <Button
                key={to}
                type="button"
                variant="secondary"
                onClick={() => {
                  setMovedTo(null);
                  setApprovedEntry(null);
                  setAttachedCode(null);
                  setMoving({ from: submission.state, to });
                }}
              >
                {moveWording(submission.state, to, submissionStateLabel(to)).label}
              </Button>
            ))}
          </div>
        ) : null}
        {movedTo !== null ? (
          <p ref={confirmation} tabIndex={-1} className="m-0 text-sm">
            {attachedCode !== null ? (
              `Attached ${attachedCode.code}, ${attachedCode.fsn}. Moved to ${submissionStateLabel(movedTo)}.`
            ) : approvedEntry === null ? (
              `Moved to ${submissionStateLabel(movedTo)}.`
            ) : (
              <>
                Approved. {approvedEntry.created ? "Created" : "Amended"} entry{" "}
                <Link
                  to="/catalogue/$businessKey"
                  params={{ businessKey: approvedEntry.businessKey }}
                  className="text-[var(--color-accent)] underline"
                >
                  {approvedEntry.businessKey}
                </Link>
                .
              </>
            )}
          </p>
        ) : null}
        {moving?.to === "Awaiting terminology" ? (
          <TerminologyRequestDialog
            submission={submission}
            from={moving.from}
            onClose={() => setMoving(null)}
            onRequested={() => {
              setMovedTo("Awaiting terminology");
              setMoving(null);
            }}
          />
        ) : null}
        {moving !== null && isAttach(moving.from, moving.to) ? (
          <AttachCodeDialog
            submission={submission}
            from={moving.from}
            onClose={() => setMoving(null)}
            onAttached={(attached) => {
              setAttachedCode(attached);
              setMovedTo("Ready for approval");
              setMoving(null);
            }}
          />
        ) : null}
        {moving?.to === "Approved" ? (
          <ApproveDialog
            submission={submission}
            from={moving.from}
            onClose={() => setMoving(null)}
            onApproved={(entry) => {
              setApprovedEntry(entry);
              setMovedTo("Approved");
              setMoving(null);
            }}
          />
        ) : null}
        {moving !== null &&
        moving.to !== "Approved" &&
        moving.to !== "Awaiting terminology" &&
        !isAttach(moving.from, moving.to) ? (
          <MoveDialog
            key={moving.to}
            submission={submission}
            from={moving.from}
            to={moving.to}
            onClose={() => setMoving(null)}
            onMoved={() => {
              setMovedTo(moving.to);
              setMoving(null);
            }}
          />
        ) : null}
      </div>
    </EntrySection>
  );
}
