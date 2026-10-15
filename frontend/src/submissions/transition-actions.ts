/**
 * What the reason field says about who reads the reason. Every change of state
 * keeps it with the move, and the history shows it to everyone who can open the
 * submission (FR-81), so the field must not call it unpublished.
 */
export const REASON_READERS_HINT =
  "Recorded in the audit log and shown in this submission's history to everyone who can open it.";
