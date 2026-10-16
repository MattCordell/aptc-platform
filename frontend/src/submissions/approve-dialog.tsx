import { useRef } from "react";

import { useApproveSubmission } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { moveWording } from "./transition-actions.ts";
import { TransitionDialog } from "./transition-dialog.tsx";

type SubmissionDetail = components["schemas"]["SubmissionDetail"];

export type Approvable = Pick<
  SubmissionDetail,
  | "id"
  | "state"
  | "available_transitions"
  | "row_version"
  | "preferred_term"
  | "kind"
  | "entry_business_key"
> & {
  /** Present only for a caller who may review. The approval writes this content, not the original. */
  working_copy?: { preferred_term: string } | null;
};

/** The entry the approval wrote, as the server named it. */
export interface ApprovedEntry {
  businessKey: string;
  created: boolean;
}

const REASON_BECOMES_THE_NOTE =
  "The reason becomes the entry's changelog note and appears in the entry's History. It is also recorded in the audit log and in this submission's history.";

const NO_LONGER_OFFERED = "The platform no longer offers approval for this submission.";

const FALLBACK_REFUSAL =
  "The submission could not be approved. Try again, or contact an administrator if the problem persists.";

/** `fallback` words a kind the screen does not name, so no kind is left without a sentence. */
function effectOf(submission: Approvable, fallback: string): string {
  if (submission.kind === "new_test") {
    const term = submission.working_copy?.preferred_term ?? submission.preferred_term;
    return `Approving creates ${term} as a new active entry in the catalogue.`;
  }
  if (submission.kind === "amendment") {
    const entry = submission.entry_business_key;
    return entry === null
      ? "Approving changes the catalogue entry this amendment names."
      : `Approving changes the catalogue entry ${entry}.`;
  }
  return fallback;
}

/**
 * The Administrator's approval (FR-28, FR-35). It words the effect by the kind
 * of submission, because a new test creates an entry and an amendment changes
 * one, and it says where the reason goes, because the reason becomes the
 * entry's changelog note (FR-37). The dialog applies no rule to the reason
 * itself: the server owns the note's rules and its refusal reaches the field.
 *
 * The first sentence names the submission as the page heading does. The effect
 * names the working copy's term when a reviewer changed it, because that is the
 * term the new entry takes.
 */
export function ApproveDialog({
  submission,
  from,
  onClose,
  onApproved,
}: {
  submission: Approvable;
  /** The state the user chose approval from. The submission prop moves on while
   * the write settles, and the dialog's wording must not change under the user. */
  from: string;
  onClose: () => void;
  onApproved: (entry: ApprovedEntry) => void;
}) {
  const approve = useApproveSubmission(submission.id);
  const approved = useRef<ApprovedEntry | null>(null);
  const wording = moveWording(from, "Approved", "Approved");
  return (
    <TransitionDialog
      title={wording.label}
      intro={
        <p>
          Approve {submission.preferred_term}. {effectOf(submission, wording.effect)}
        </p>
      }
      submitLabel="Confirm approval"
      pendingLabel="Approving"
      reasonAction="approving this submission"
      reasonHint={REASON_BECOMES_THE_NOTE}
      blocked={
        submission.state !== from ||
        !submission.available_transitions.includes("Approved")
      }
      blockedReason={NO_LONGER_OFFERED}
      fallbackRefusal={FALLBACK_REFUSAL}
      write={approve}
      send={(reason) =>
        approve
          .mutateAsync({ expected_row_version: submission.row_version, reason })
          .then((answer) => {
            approved.current = {
              businessKey: answer.business_key,
              created: answer.entry_created,
            };
          })
      }
      onClose={onClose}
      onDone={() => {
        if (approved.current !== null) {
          onApproved(approved.current);
        }
      }}
    />
  );
}
