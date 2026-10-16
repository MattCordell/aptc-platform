import { Link } from "@tanstack/react-router";

import type { components } from "../api/schema.ts";

type Submitter =
  components["schemas"]["PlainSubmitter"] | components["schemas"]["IdentifiedSubmitter"];

const MUTED = "text-[var(--color-text-muted)]";

const ENTRY_LINK_CLASS = "text-[var(--color-accent)] underline";

/**
 * The kind of a submission, with a link to the entry an amendment changes or
 * the entry an approved new test became. The wire type is `string`, so an
 * unknown kind degrades to its own text.
 */
export function SubmissionKind({
  kind,
  entryBusinessKey,
  createdEntryBusinessKey,
}: {
  kind: string;
  entryBusinessKey: string | null;
  createdEntryBusinessKey: string | null;
}) {
  if (kind === "new_test") {
    if (createdEntryBusinessKey === null) {
      return "New test";
    }
    return (
      <>
        New test, created as{" "}
        <Link
          to="/catalogue/$businessKey"
          params={{ businessKey: createdEntryBusinessKey }}
          className={ENTRY_LINK_CLASS}
        >
          {createdEntryBusinessKey}
        </Link>
      </>
    );
  }
  if (kind !== "amendment") {
    return kind;
  }
  if (entryBusinessKey === null) {
    return "Amendment";
  }
  return (
    <>
      Amendment to{" "}
      <Link
        to="/catalogue/$businessKey"
        params={{ businessKey: entryBusinessKey }}
        className={ENTRY_LINK_CLASS}
      >
        {entryBusinessKey}
      </Link>
    </>
  );
}

/**
 * The submitter exactly as the server returned them (FR-34): a username for a
 * caller who may not see identities, otherwise the name and the organisation
 * sent with the submission. It narrows by field, never by the caller's role.
 */
export function SubmitterView({ submitter }: { submitter: Submitter }) {
  if (!("display_name" in submitter)) {
    return submitter.username;
  }
  return (
    <span className="flex flex-col">
      <span>{submitter.display_name}</span>
      {submitter.organisation === null ? null : (
        <span className={MUTED}>{submitter.organisation}</span>
      )}
    </span>
  );
}
