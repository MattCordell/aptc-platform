import { Link } from "@tanstack/react-router";

import { buttonClassName } from "../components/button-class-name.ts";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";

const HEADING_ID = "submission-list-heading";

/**
 * `/submissions` (FR-23). Offers the way into the new-test form. The link shows
 * for every signed-in user: the server refuses a caller who may not submit
 * (NFR-20).
 */
export function SubmissionListPage() {
  return (
    <section aria-labelledby={HEADING_ID}>
      <PageContainer className="py-6">
        <PageHeader
          id={HEADING_ID}
          title="Submissions"
          actions={
            <Link to="/submissions/new" className={buttonClassName("primary")}>
              Create submission
            </Link>
          }
        />
        <p className="m-0">The list of submissions is coming soon.</p>
      </PageContainer>
    </section>
  );
}
