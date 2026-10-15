import { useSubmissionTransitions } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { formatDate } from "../catalogue/format-date.ts";
import { SubmitterView } from "./kind-and-submitter.tsx";
import { SectionFailure } from "./section-failure.tsx";
import { submissionStateLabel } from "./state-options.ts";

type Item =
  | components["schemas"]["TransitionItem"]
  | components["schemas"]["IdentifiedTransitionItem"];

const LOAD_FAILURE = "The history could not be loaded. Try again in a moment.";

const MUTED = "text-[var(--color-text-muted)]";

function Move({ item }: { item: Item }) {
  return (
    <li className="flex min-w-0 flex-col gap-1">
      <span className="font-medium">
        {submissionStateLabel(item.from_state)} to {submissionStateLabel(item.to_state)}
      </span>
      <span className="[overflow-wrap:anywhere] whitespace-pre-line">{item.reason}</span>
      <span className={MUTED}>
        <time dateTime={item.created_at}>{formatDate(item.created_at)}</time>
      </span>
      <span className={`flex gap-1 ${MUTED}`}>
        By <SubmitterView submitter={item.actor} />
      </span>
    </li>
  );
}

/**
 * Every change of state a submission has made, oldest first (FR-81): the move,
 * the reason, when it happened and who made it. The reason is the working
 * group's own words to the submitter, so everyone who can open the submission
 * reads it. The actor is shown as the server sent it, a username or a name with
 * an organisation, and a closed account's placeholder is shown as it arrives.
 */
export function TransitionHistory({ submissionId }: { submissionId: string }) {
  const history = useSubmissionTransitions(submissionId);

  let body = <p className="m-0 text-sm">Loading the history…</p>;
  if (history.data !== undefined) {
    body =
      history.data.items.length === 0 ? (
        <p className="m-0 text-sm">This submission has not changed state yet.</p>
      ) : (
        <ol className="m-0 flex list-none flex-col gap-4 p-0 text-sm">
          {history.data.items.map((item, index) => (
            <Move key={`${item.created_at}-${index}`} item={item} />
          ))}
        </ol>
      );
  } else if (history.isError) {
    body = (
      <SectionFailure
        error={history.error}
        message={LOAD_FAILURE}
        retry={() => void history.refetch()}
      />
    );
  }
  return <EntrySection title="History">{body}</EntrySection>;
}
