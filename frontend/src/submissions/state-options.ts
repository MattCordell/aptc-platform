import type { components } from "../api/schema.ts";
import type { StatusTone } from "../components/status-badge.tsx";

export type SubmissionState = components["schemas"]["SubmissionState"];

/**
 * What the `/submissions` state filter may hold: one state, or one of two
 * groups. The groups are named, not listed, so a pasted link stays short and
 * keeps meaning "the open states" when a new state is added.
 */
export type SubmissionStateFilter = SubmissionState | "open" | "all";

interface StateDetails {
  label: string;
  tone: StatusTone;
  /** Open submissions are the ones the server lists when no `state` is sent
   * (FR-28). */
  open: boolean;
}

/**
 * Every submission state, with its label, badge tone and whether it is open.
 * A `Record` over the generated union on purpose: a state added to the API
 * fails `pnpm typecheck` here, instead of rendering with no label.
 */
const STATE_DETAILS: Record<SubmissionState, StateDetails> = {
  Submitted: { label: "Submitted", tone: "draft", open: true },
  Triage: { label: "Triage", tone: "draft", open: true },
  "Under review": { label: "Under review", tone: "draft", open: true },
  "Ready for approval": { label: "Ready for approval", tone: "draft", open: true },
  // Approved still waits for a release (FR-29), so it stays open and keeps the default list.
  Approved: { label: "Approved", tone: "active", open: true },
  "Published in release": { label: "Published in release", tone: "active", open: false },
  Rejected: { label: "Rejected", tone: "deprecated", open: false },
  Withdrawn: { label: "Withdrawn", tone: "neutral", open: false },
};

export const SUBMISSION_STATES = Object.keys(STATE_DETAILS) as SubmissionState[];

const OPEN_STATES = SUBMISSION_STATES.filter((state) => STATE_DETAILS[state].open);

function isSubmissionState(value: string): value is SubmissionState {
  return Object.hasOwn(STATE_DETAILS, value);
}

/**
 * Whether a state the server returned is open (FR-28). An unknown value is not
 * open, so a screen that offers an action only on open submissions hides it
 * rather than offering it on a state it cannot name.
 */
export function isOpenSubmissionState(state: string): boolean {
  return isSubmissionState(state) && STATE_DETAILS[state].open;
}

export function isSubmissionStateFilter(value: string): value is SubmissionStateFilter {
  return value === "open" || value === "all" || isSubmissionState(value);
}

/**
 * The display label for a state the server returned. The wire type is `string`,
 * not the union, so an unknown value degrades to its own text rather than
 * throwing.
 */
export function submissionStateLabel(state: string): string {
  return isSubmissionState(state) ? STATE_DETAILS[state].label : state;
}

export function submissionStateTone(state: string): StatusTone {
  return isSubmissionState(state) ? STATE_DETAILS[state].tone : "neutral";
}

/**
 * What a view shows when the URL holds no `state`. "My submissions" shows
 * every state, the All view shows the open ones, as the server does.
 */
export function defaultStateFilter(mine: boolean): SubmissionStateFilter {
  return mine ? "all" : "open";
}

/**
 * The `state` values to send for a filter. `undefined` for an absent filter,
 * so the request is the bare default and the server's own default applies.
 */
export function statesForRequest(
  filter: SubmissionStateFilter | undefined,
): SubmissionState[] | undefined {
  if (filter === undefined) {
    return undefined;
  }
  if (filter === "open") {
    return OPEN_STATES;
  }
  return filter === "all" ? SUBMISSION_STATES : [filter];
}

export function stateFilterOptions(): { value: SubmissionStateFilter; label: string }[] {
  return [
    { value: "open", label: "Open" },
    { value: "all", label: "All states" },
    ...SUBMISSION_STATES.map((state) => ({
      value: state,
      label: STATE_DETAILS[state].label,
    })),
  ];
}
