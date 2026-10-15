import { Link, useParams } from "@tanstack/react-router";
import { useEffect } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { useSubmissionDetail } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { ApiError } from "../api/unwrap.ts";
import { Breadcrumb } from "../components/breadcrumb.tsx";
import { Button } from "../components/button.tsx";
import { DetailLayout } from "../components/detail-layout.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { NotFoundPage } from "../shell/not-found-page.tsx";
import { useDocumentTitle } from "../shell/use-document-title.ts";
import { InterestIdentitiesPanel } from "../submissions/interest-identities-panel.tsx";
import { SubmissionContent } from "../submissions/submission-content.tsx";
import {
  SubmissionDetails,
  SubmissionInterest,
} from "../submissions/submission-sidebar.tsx";
import { TriageSubmission } from "../submissions/triage-submission.tsx";
import { WithdrawSubmission } from "../submissions/withdraw-submission.tsx";

/**
 * One submission in any state (FR-28, FR-34): what the submitter sent, who they
 * are as the server returns them, and the interest count with its action
 * (FR-32, FR-42).
 *
 * The API answers an unknown id with a 404 and a malformed one with a 422 whose
 * body is not a declared shape, so the page never reads it. Both show the
 * not-found page. The sidebar and the content are separate components, so the
 * identities panel and the triage actions can join the sidebar without
 * reshaping the page.
 */

type SubmissionDetail = components["schemas"]["SubmissionDetail"];

const HEADING_ID = "submission-detail-heading";

const STALE_DATA_WARNING =
  "This submission could not be refreshed just now, so it may be out of date.";

const LOAD_FAILURE = "This submission could not be loaded. Try again in a moment.";

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 422);
}

/** A 401 or 403 is the server refusing this caller, so a retry cannot succeed
 * and its own sentence says why (the route declares it for a caller without
 * `submission.view` or who has not accepted the terms). */
function isRefusal(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

function SubmissionBreadcrumb({ current }: { current: string }) {
  return (
    <Breadcrumb
      ancestors={[
        <Link key="home" to="/">
          Home
        </Link>,
        <Link key="submissions" to="/submissions">
          Submissions
        </Link>,
      ]}
      current={current}
    />
  );
}

function SubmissionView({
  submission,
  refreshFailed,
}: {
  submission: SubmissionDetail;
  refreshFailed: boolean;
}) {
  useDocumentTitle(`${submission.preferred_term} — NPTC Catalogue`);
  const { message, politeness, announce } = useAnnounce();

  useEffect(() => {
    if (refreshFailed) {
      announce(STALE_DATA_WARNING);
    }
  }, [refreshFailed, announce]);

  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />
      <PageContainer className="py-6">
        <SubmissionBreadcrumb current={submission.preferred_term} />

        <PageHeader id={HEADING_ID} title={submission.preferred_term} />

        {refreshFailed ? <p className="m-0">{STALE_DATA_WARNING}</p> : null}

        <DetailLayout
          sidebarLabel="Submission details"
          sidebar={
            <>
              <SubmissionDetails submission={submission} />
              <SubmissionInterest submission={submission} />
              <InterestIdentitiesPanel submissionId={submission.id} />
              <TriageSubmission submission={submission} />
              <WithdrawSubmission submission={submission} />
            </>
          }
        >
          <SubmissionContent submission={submission} />
        </DetailLayout>
      </PageContainer>
    </section>
  );
}

export function SubmissionDetailPage() {
  const { submissionId } = useParams({
    from: "/authenticated/submissions/$submissionId",
  });
  const submission = useSubmissionDetail(submissionId);
  const { message, politeness, announce } = useAnnounce();

  const hardFailure =
    submission.isError && submission.data === undefined && !isNotFound(submission.error);
  const refused = hardFailure && isRefusal(submission.error);
  const failureMessage = refused
    ? (refusalDetail(submission.error) ?? LOAD_FAILURE)
    : LOAD_FAILURE;
  useEffect(() => {
    if (hardFailure) {
      announce(failureMessage);
    }
  }, [hardFailure, failureMessage, announce]);

  if (isNotFound(submission.error)) {
    return <NotFoundPage />;
  }

  if (submission.data !== undefined) {
    return (
      <SubmissionView submission={submission.data} refreshFailed={submission.isError} />
    );
  }

  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />
      <PageContainer className="py-6">
        <SubmissionBreadcrumb current="Submission" />
        <PageHeader id={HEADING_ID} title="Submission" />
        {hardFailure ? (
          <div className="flex flex-col items-start gap-3">
            <p className="m-0 text-[var(--color-danger)]">{failureMessage}</p>
            {refused ? null : (
              <Button
                type="button"
                variant="secondary"
                onClick={() => void submission.refetch()}
              >
                Try again
              </Button>
            )}
          </div>
        ) : (
          <p className="m-0">Loading submission…</p>
        )}
      </PageContainer>
    </section>
  );
}
