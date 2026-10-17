import type { SubmissionState } from "./state-options.ts";

/**
 * What the reason field says about who reads the reason. Every change of state
 * keeps it with the move, and the history shows it to everyone who can open the
 * submission (FR-81), so the field must not call it unpublished.
 */
export const REASON_READERS_HINT =
  "Recorded in the audit log and shown in this submission's history to everyone who can open it.";

/**
 * A move the server lists in `available_transitions` but that
 * `POST /submissions/{id}/transitions` refuses with a 409, because it has a route
 * of its own. The Triage panel skips these. The list is the only place the screen
 * names a move, and it holds no rule about which moves exist: the server still
 * decides what is offered.
 */
const MOVES_ON_OTHER_ROUTES: readonly { from?: SubmissionState; to: SubmissionState }[] =
  [
    // Withdrawal has its own route and permission, and its own panel.
    { to: "Withdrawn" },
  ];

export function isMadeOnTheTriagePanel(from: string, to: SubmissionState): boolean {
  return !MOVES_ON_OTHER_ROUTES.some(
    (move) => move.to === to && (move.from === undefined || move.from === from),
  );
}

interface MoveWording {
  /** The button, and the dialog's title. */
  label: string;
  /** One sentence on what the move means, shown above the reason field. */
  effect: string;
}

const REJECT_WORDING: MoveWording = {
  label: "Reject",
  effect: "The submitter reads your reason. You can reopen a rejected submission later.",
};

const MOVE_WORDING: Readonly<Record<string, MoveWording>> = {
  "Submitted>Triage": {
    label: "Start triage",
    effect: "Triage means a reviewer has picked up the submission.",
  },
  "Triage>Under review": {
    label: "Start review",
    effect: "Under review means the working group is assessing the proposal in detail.",
  },
  "Triage>Rejected": REJECT_WORDING,
  "Under review>Rejected": REJECT_WORDING,
  "Ready for approval>Rejected": REJECT_WORDING,
  "Rejected>Triage": {
    label: "Reopen",
    effect: "The submission goes back to Triage for another look.",
  },
  "Under review>Ready for approval": {
    label: "Mark ready for approval",
    effect: "An Administrator then decides whether to approve it.",
  },
  "Ready for approval>Approved": {
    label: "Approve",
    effect: "Approving writes the submission to the catalogue.",
  },
  "Under review>Awaiting terminology": {
    label: "Request a new code",
    effect:
      "The submission waits until the terminology authority issues a code for it. An Administrator then attaches the code.",
  },
  "Awaiting terminology>Ready for approval": {
    label: "Attach code",
    effect: "An Administrator then decides whether to approve it.",
  },
  "Awaiting terminology>Under review": {
    label: "Resume review",
    effect:
      "Use this when the request for a code is declined or nobody is pursuing it. The record of the request stays.",
  },
};

/**
 * The words for one move. A move the table does not name still gets a button,
 * labelled with its target, so a state the server adds is never hidden.
 */
export function moveWording(
  from: string,
  to: SubmissionState,
  toLabel: string,
): MoveWording {
  return (
    MOVE_WORDING[`${from}>${to}`] ?? {
      label: `Move to ${toLabel}`,
      effect: `The submission moves to ${toLabel}.`,
    }
  );
}
