import type { components } from "../api/schema.ts";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { formatDate } from "../catalogue/format-date.ts";
import { StatusBadge } from "../components/status-badge.tsx";
import { InterestButton } from "./interest-button.tsx";
import { SubmissionKind, SubmitterView } from "./kind-and-submitter.tsx";
import { submissionStateLabel, submissionStateTone } from "./state-options.ts";

type SubmissionDetail = components["schemas"]["SubmissionDetail"];

/** The submission's own facts: kind, state, date and submitter. */
export function SubmissionDetails({ submission }: { submission: SubmissionDetail }) {
  return (
    <EntrySection title="Details">
      <dl className="m-0 grid grid-cols-1 gap-y-1 text-sm">
        <dt className="font-medium">Kind</dt>
        <dd className="m-0 mb-3 min-w-0">
          <SubmissionKind
            kind={submission.kind}
            entryBusinessKey={submission.entry_business_key}
            createdEntryBusinessKey={submission.created_entry_business_key}
          />
        </dd>

        <dt className="font-medium">State</dt>
        <dd className="m-0 mb-3 min-w-0">
          <StatusBadge
            tone={submissionStateTone(submission.state)}
            label={submissionStateLabel(submission.state)}
          />
        </dd>

        <dt className="font-medium">Submitted</dt>
        <dd className="m-0 mb-3 min-w-0">
          <time dateTime={submission.created_at}>
            {formatDate(submission.created_at)}
          </time>
        </dd>

        <dt className="font-medium">Submitter</dt>
        <dd className="m-0 min-w-0">
          <SubmitterView submitter={submission.submitter} />
        </dd>
      </dl>
    </EntrySection>
  );
}

/**
 * The interest count and the action to register or withdraw it (FR-32, FR-34,
 * FR-42). A count the server withholds reads as withheld, never as zero.
 */
export function SubmissionInterest({ submission }: { submission: SubmissionDetail }) {
  return (
    <EntrySection title="Interest">
      <p className="m-0 text-sm">
        {submission.interest_count === null ? (
          <span className="text-[var(--color-text-muted)]">
            The number of people interested is not shown to you.
          </span>
        ) : (
          <>
            People who registered interest:{" "}
            <strong className="tabular-nums">{submission.interest_count}</strong>
          </>
        )}
      </p>
      <p className="m-0 text-sm">
        Interest helps reviewers set priority. It does not decide whether a submission is
        approved.
      </p>
      <InterestButton submission={submission} />
    </EntrySection>
  );
}
