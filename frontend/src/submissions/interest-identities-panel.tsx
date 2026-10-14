import { refusalDetail } from "../api/conflicts.ts";
import { useInterestIdentities, useSession } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { ApiError } from "../api/unwrap.ts";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { formatDate } from "../catalogue/format-date.ts";
import { Button } from "../components/button.tsx";

type Identities = components["schemas"]["InterestIdentitiesResponse"];

const VIEW_PERMISSION = "interest.identities.view";

const NO_ORGANISATION = "No organisation given";

const LOAD_FAILURE =
  "Who registered interest could not be loaded. Try again in a moment.";

const MUTED = "text-[var(--color-text-muted)]";

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function summary(identities: Identities): string {
  const people = plural(identities.interest_count, "person", "people");
  const organisations = plural(
    identities.distinct_organisation_count,
    "organisation",
    "organisations",
  );
  return `${people} from ${organisations}.`;
}

function Breakdown({ identities }: { identities: Identities }) {
  if (identities.interest_count === 0) {
    return <p className="m-0 text-sm">No one has registered interest yet.</p>;
  }
  return (
    <>
      <p className="m-0 text-sm">{summary(identities)}</p>

      <h3 className="m-0 text-base">Organisations</h3>
      <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
        {identities.distinct_organisations.map((organisation) => (
          <li key={organisation.label} className="flex justify-between gap-2">
            <span className="min-w-0">{organisation.label}</span>
            <span className="tabular-nums">{organisation.count}</span>
          </li>
        ))}
      </ul>

      <h3 className="m-0 text-base">People</h3>
      <ul className="m-0 flex list-none flex-col gap-3 p-0 text-sm">
        {identities.records.map((person, index) => (
          <li key={`${person.username}-${index}`} className="flex flex-col">
            <span>
              {person.display_name} <span className={MUTED}>({person.username})</span>
            </span>
            <span className={MUTED}>{person.organisation ?? NO_ORGANISATION}</span>
            <span className={MUTED}>
              Registered{" "}
              <time dateTime={person.registered_at}>
                {formatDate(person.registered_at)}
              </time>
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

function Failure({ error, retry }: { error: unknown; retry: () => void }) {
  // A refusal is the server saying this caller may not see the list, so a
  // retry cannot succeed and its own sentence says why.
  const refused =
    error instanceof ApiError && (error.status === 401 || error.status === 403);
  return (
    <div className="flex flex-col items-start gap-2">
      <p className="m-0 text-sm text-[var(--color-danger)]">
        {(refused ? refusalDetail(error) : null) ?? LOAD_FAILURE}
      </p>
      {refused ? null : (
        <Button type="button" variant="secondary" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}

/**
 * Who registered interest, and which organisations they represent (FR-33). It
 * shows only for a caller who holds `interest.identities.view`, and for anyone
 * else the identities request is never sent: the server would refuse it, so
 * asking would only cost a 403 (NFR-20, FR-44). Names, closed-account
 * placeholders and the organisation label render as the server sent them.
 */
export function InterestIdentitiesPanel({ submissionId }: { submissionId: string }) {
  const session = useSession();
  const mayView = session.data?.permissions?.includes(VIEW_PERMISSION) === true;
  const identities = useInterestIdentities(submissionId, mayView);

  if (!mayView) {
    return null;
  }

  let body = <p className="m-0 text-sm">Loading who registered interest…</p>;
  if (identities.data !== undefined) {
    body = <Breakdown identities={identities.data} />;
  } else if (identities.isError) {
    body = <Failure error={identities.error} retry={() => void identities.refetch()} />;
  }
  return <EntrySection title="Who registered interest">{body}</EntrySection>;
}
