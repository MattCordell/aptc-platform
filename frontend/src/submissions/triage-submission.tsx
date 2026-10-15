import { useEffect, useRef, useState } from "react";

import { useTransitionSubmission } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { submissionStateLabel, type SubmissionState } from "./state-options.ts";
import {
  isMadeOnTheTriagePanel,
  moveWording,
  REASON_READERS_HINT,
} from "./transition-actions.ts";
import { TransitionDialog } from "./transition-dialog.tsx";

type SubmissionDetail = components["schemas"]["SubmissionDetail"];

type Triageable = Pick<
  SubmissionDetail,
  "id" | "state" | "available_transitions" | "row_version" | "preferred_term"
>;

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
      blocked={!submission.available_transitions.includes(to)}
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

function TriagePanel({ submission }: { submission: Triageable }) {
  const [moving, setMoving] = useState<{ from: string; to: SubmissionState } | null>(
    null,
  );
  const [movedTo, setMovedTo] = useState<SubmissionState | null>(null);
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
            Moved to {submissionStateLabel(movedTo)}.
          </p>
        ) : null}
        {moving !== null ? (
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
