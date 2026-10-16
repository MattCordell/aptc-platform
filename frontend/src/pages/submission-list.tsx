import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { useSubmissionsList } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { formatDate } from "../catalogue/format-date.ts";
import { resultAnnouncement, useCurrentPage } from "../catalogue/list-screen.ts";
import { buttonClassName } from "../components/button-class-name.ts";
import { DataTable } from "../components/data-table.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { Pagination } from "../components/pagination.tsx";
import { Select } from "../components/select.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { useKeysetPaging } from "../components/use-keyset-paging.ts";
import { SubmissionKind, SubmitterView } from "../submissions/kind-and-submitter.tsx";
import {
  defaultStateFilter,
  isSubmissionStateFilter,
  stateFilterOptions,
  statesForRequest,
  submissionStateLabel,
  submissionStateTone,
  type SubmissionStateFilter,
} from "../submissions/state-options.ts";

const HEADING_ID = "submission-list-heading";

/**
 * `/submissions` (FR-23, FR-28, FR-34). Lists submissions newest first, in two
 * views: everyone's (`/submissions`) and the caller's own (`?mine=true`). Each
 * view is its own URL, and so is its state filter, so a filtered view is a link
 * that can be shared.
 *
 * The link to the new-test form shows for every signed-in user: the server
 * refuses a caller who may not submit (NFR-20). The submitter is shown as the
 * server returned it, and an interest count the server withheld is shown as
 * withheld, never as zero (FR-42).
 *
 * Paging is keyset (ADR-0024): no page number and no total exist, so the
 * screen never states one.
 */

// TanStack Router keys `useSearch`'s `from` off the route's internal id, which
// includes the pathless `authenticated` layout, while `useNavigate`'s `from`
// is the resolved URL path.
const ROUTE_ID = "/authenticated/submissions/" as const;
const ROUTE_PATH = "/submissions/" as const;

const PAGE_SIZE = 50;

type Row =
  | components["schemas"]["SubmissionSummary"]
  | components["schemas"]["IdentifiedSubmissionSummary"]
  | components["schemas"]["CountedSubmissionSummary"]
  | components["schemas"]["CountedIdentifiedSubmissionSummary"];

const STALE_DATA_WARNING =
  "The list could not be refreshed just now, so these submissions may be out of date.";

const LOAD_FAILURE =
  "The submissions could not be loaded. Try again in a moment, or change the state.";

const TAB_CLASSES =
  "inline-block border-b-2 border-transparent px-3 py-2 text-sm font-medium text-[var(--color-text)] hover:underline aria-[current=page]:border-[var(--color-accent)] aria-[current=page]:text-[var(--color-accent-hover)] aria-[current=page]:underline aria-[current=page]:decoration-2 aria-[current=page]:underline-offset-4";

const MUTED = "text-[var(--color-text-muted)]";

function emptyStateText(
  mine: boolean,
  filter: SubmissionStateFilter,
  hasCursor: boolean,
): string {
  if (hasCursor) {
    return "No more submissions.";
  }
  if (mine) {
    if (filter === "all") {
      return "You have no submissions yet.";
    }
    return filter === "open"
      ? "You have no open submissions."
      : `You have no ${submissionStateLabel(filter)} submissions.`;
  }
  if (filter === "all") {
    return "There are no submissions yet.";
  }
  return filter === "open"
    ? "There are no open submissions."
    : `There are no ${submissionStateLabel(filter)} submissions.`;
}

export function SubmissionListPage() {
  const search = useSearch({ from: ROUTE_ID });
  const navigate = useNavigate({ from: ROUTE_PATH });
  const { message, politeness, announce } = useAnnounce();

  const mine = search.mine === "true";
  const filter = search.state ?? defaultStateFilter(mine);

  const query = useSubmissionsList({
    state: statesForRequest(search.state),
    mine,
    limit: PAGE_SIZE,
    after: search.after,
    keepPreviousPage: true,
  });
  const data = useCurrentPage(query, `${mine}:${search.state ?? ""}`);

  const items: Row[] = data?.items ?? [];
  const nextCursor = data?.next_cursor ?? null;

  const paging = useKeysetPaging(search.after);

  function handleStateChange(value: string) {
    if (!isSubmissionStateFilter(value)) {
      return;
    }
    // The view's default is the absent parameter, so choosing it returns to
    // the plain URL. `after` goes with any change: the population a cursor
    // was paging over no longer exists.
    void navigate({
      search: (prev) => ({
        ...prev,
        state: value === defaultStateFilter(mine) ? undefined : value,
        after: undefined,
      }),
    });
  }

  function handleNextPage() {
    // A placeholder's `next_cursor` points at the page already loading, so
    // acting on it would push a duplicate onto the Previous stack.
    if (nextCursor !== null && !query.isPlaceholderData) {
      paging.next(nextCursor);
      void navigate({ search: (prev) => ({ ...prev, after: nextCursor }) });
    }
  }

  function handlePreviousPage() {
    const previousAfter = paging.previous();
    void navigate({ search: (prev) => ({ ...prev, after: previousAfter }) });
  }

  const staleData = query.isError && data !== undefined;
  const hardFailure = query.isError && data === undefined;
  const hardFailureMessage = refusalDetail(query.error) ?? LOAD_FAILURE;

  // `data` keeps its identity across a refetch that returns the same page
  // (structural sharing), so this speaks once per new result set. A
  // placeholder is never announced: it is the page being replaced.
  const resultMessage =
    data && !query.isError && !query.isPlaceholderData
      ? resultAnnouncement(data.items.length, data.next_cursor !== null)
      : null;
  useEffect(() => {
    if (resultMessage !== null) {
      announce(resultMessage);
    }
  }, [data, resultMessage, announce]);

  useEffect(() => {
    if (staleData) {
      announce(STALE_DATA_WARNING);
    }
  }, [staleData, announce]);

  useEffect(() => {
    if (hardFailure) {
      announce(hardFailureMessage);
    }
  }, [hardFailure, hardFailureMessage, announce]);

  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="flex flex-col gap-4 py-6">
        <PageHeader
          id={HEADING_ID}
          title="Submissions"
          actions={
            <Link to="/submissions/new" className={buttonClassName("primary")}>
              Create submission
            </Link>
          }
        />

        <nav
          aria-label="Submission views"
          className="flex gap-2 border-b border-[var(--color-border)]"
        >
          {/* `mine: undefined` with `explicitUndefined` makes All inactive in
              the My view: a bare empty search would match every URL. */}
          <Link
            to="/submissions"
            search={{ mine: undefined }}
            activeOptions={{ explicitUndefined: true }}
            className={TAB_CLASSES}
          >
            All submissions
          </Link>
          <Link
            to="/submissions"
            search={{ mine: "true" }}
            activeOptions={{ explicitUndefined: true }}
            className={TAB_CLASSES}
          >
            My submissions
          </Link>
        </nav>

        <div className="flex flex-wrap items-end gap-4">
          <Select
            id="submission-state-filter"
            label="State"
            value={filter}
            onChange={(event) => handleStateChange(event.target.value)}
            options={stateFilterOptions()}
          />
        </div>

        {(query.isPending || query.isPlaceholderData) && (
          <p className="m-0">Loading submissions…</p>
        )}

        {hardFailure && (
          <p className="m-0 text-[var(--color-danger)]">{hardFailureMessage}</p>
        )}

        {staleData && <p className="m-0">{STALE_DATA_WARNING}</p>}

        {data && (
          <>
            {/* Scrolls on its own at narrow widths rather than widening the
                page. Its row links are what a keyboard user scrolls it by. */}
            <div data-testid="results-scroll" className="overflow-x-auto">
              <DataTable
                caption={mine ? "My submissions" : "Submissions"}
                columns={[
                  {
                    key: "preferred_term",
                    header: "Requesting term",
                    isRowHeader: true,
                    render: (row: Row) => (
                      <Link
                        to="/submissions/$submissionId"
                        params={{ submissionId: row.id }}
                        className="text-[var(--color-accent)] hover:underline"
                      >
                        {row.preferred_term}
                      </Link>
                    ),
                  },
                  {
                    key: "kind",
                    header: "Kind",
                    render: (row: Row) => (
                      <SubmissionKind
                        kind={row.kind}
                        entryBusinessKey={row.entry_business_key}
                        createdEntryBusinessKey={row.created_entry_business_key}
                      />
                    ),
                  },
                  {
                    key: "state",
                    header: "State",
                    render: (row: Row) => (
                      <StatusBadge
                        tone={submissionStateTone(row.state)}
                        label={submissionStateLabel(row.state)}
                      />
                    ),
                  },
                  {
                    key: "created_at",
                    header: "Submitted",
                    render: (row: Row) => formatDate(row.created_at),
                  },
                  {
                    key: "submitter",
                    header: "Submitter",
                    render: (row: Row) => <SubmitterView submitter={row.submitter} />,
                  },
                  {
                    key: "interest_count",
                    header: "Interest",
                    align: "right",
                    render: (row: Row) =>
                      row.interest_count === null ? (
                        <span className={MUTED}>Not shown</span>
                      ) : (
                        <span className="tabular-nums">{row.interest_count}</span>
                      ),
                  },
                ]}
                rows={items}
                getRowKey={(row) => row.id}
                emptyState={
                  <p className="m-0">
                    {emptyStateText(mine, filter, search.after !== undefined)}
                  </p>
                }
              />
            </div>

            {(items.length > 0 || paging.hasPrevious) && (
              <Pagination
                hasNext={nextCursor !== null}
                onNext={handleNextPage}
                onPrevious={paging.hasPrevious ? handlePreviousPage : undefined}
                className="self-start"
              />
            )}
          </>
        )}
      </PageContainer>
    </section>
  );
}
