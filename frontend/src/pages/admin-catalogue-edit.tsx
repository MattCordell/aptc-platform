import { useParams } from "@tanstack/react-router";
import { useEffect } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { useAdminEntryDetail, useSession } from "../api/queries.ts";
import { ApiError } from "../api/unwrap.ts";
import { EntryEditForm } from "../catalogue/entry-edit-form.tsx";
import { InternalCommentsPanel } from "../comments/internal-comments-panel.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { useAnnounce } from "../components/use-announce.ts";

/**
 * The catalogue entry editing screen (issue #149; FR-36).
 *
 * Loads through the *admin* detail route, not the public one: an entry being
 * edited is very often `draft`, and the public route 404s a draft entry
 * identically to a key that was never minted (#142/#228).
 *
 * This page is the shell. `EntryEditForm` edits the preferred term, the SNOMED
 * CT code, the RCPA Synonyms and the registry properties in one form, with one
 * Save and one changelog note.
 *
 * The internal comments panel (FR-95) sits below the form, so a Reviewer or an
 * Administrator can comment on a draft entry, which has no public page. A
 * Reviewer holds `comment.internal.read` but not `catalogue.edit_published`: the
 * server lets that caller read the entry, and this page then shows the comments
 * without the form. Hiding the form is a convenience; every write is checked
 * again by the server (FR-44). The form waits for the session, so an editor never
 * sees it flash in or out, and a session that fails says so.
 */

/**
 * A refresh that failed over an entry already on screen. One string, because
 * it is both shown and announced and the two must not drift.
 */
function staleWarning(businessKey: string): string {
  return (
    `${businessKey} could not be refreshed just now, so what follows may be out of ` +
    "date. Sign in again, or reload the page, before making further changes."
  );
}

/**
 * An initial load that failed with no prior data to fall back on. One
 * string, shown and announced, for the same reason as `staleWarning` (#288 -
 * mirrors the list screen's `hardFailureMessage`, PR #285 review finding 3).
 *
 * No 401/403 special case any more (issue #184): `catalogue.edit_published`
 * requiring multi-factor authentication is now answered by `RootLayout`'s
 * `StepUpController`, which reacts to `WWW-Authenticate`'s RFC 9470
 * challenge before this component's own `entry.isError` ever has a chance to
 * render - a silent step-up retries the read in place, and an interactive
 * one navigates away entirely. What lands here for a 403 is a step-up the
 * user abandoned, which is an ordinary refusal like any other.
 */
function loadFailureMessage(businessKey: string, error: unknown): string {
  const status = error instanceof ApiError ? error.status : null;
  if (status === 404) {
    return `No catalogue entry was found for ${businessKey}. Check the identifier.`;
  }
  return (
    refusalDetail(error) ??
    `${businessKey} could not be loaded. Try again, or contact an administrator if the problem persists.`
  );
}

const EDIT_PERMISSION = "catalogue.edit_published";

/** Shown and announced when the session cannot be read, because without it the
 * page cannot tell an editor from a Reviewer and leaves the form out. */
const SESSION_FAILURE =
  "Your permissions could not be checked, so the edit form is not shown. Reload the page to try again.";

export function AdminCatalogueEditPage() {
  const { businessKey } = useParams({
    from: "/authenticated/admin/catalogue/$businessKey/edit",
  });
  const entry = useAdminEntryDetail(businessKey);
  const session = useSession();
  const mayEdit = session.data?.permissions?.includes(EDIT_PERMISSION) === true;
  const sessionFailed = session.isError && session.data === undefined;
  const { message, politeness, announce } = useAnnounce();

  // `isError` with data is a *refresh* that failed, which is announced rather
  // than rendered into a live region as it appears: `LiveRegion` is mounted
  // and empty from the first render for the reason its own docstring gives -
  // a screen reader reliably reports a text change inside a region that was
  // already there, and not an element created and filled in the same tick
  // (#148). This banner's whole job is telling an editor that what they are
  // reading may be stale, so the reader who cannot see it appear is the one
  // who most needs it (PR #238 review).
  const staleData = entry.isError && entry.data !== undefined;
  useEffect(() => {
    if (staleData) {
      announce(staleWarning(businessKey));
    }
  }, [staleData, businessKey, announce]);

  // The initial-load failure (no prior data) was rendered but never
  // announced (#288, mirroring PR #285 review finding 3 on the list screen)
  // - silence for a screen-reader user whose first load 404s or 4xxs.
  //
  // The message, not `entry.error`, is the effect's dependency (matching
  // `admin-catalogue-list.tsx`'s own `hardFailureMessage`): `retry` is off
  // but `refetchOnWindowFocus` is on, so a hard failure that refetches on
  // focus and fails again the same way yields a *new* `ApiError` instance
  // with identical wording - keying on the instance would re-announce an
  // unchanged sentence (review finding 1).
  const hardFailure = entry.isError && entry.data === undefined;
  const hardFailureMessage = hardFailure
    ? loadFailureMessage(businessKey, entry.error)
    : null;
  useEffect(() => {
    if (hardFailureMessage !== null) {
      announce(hardFailureMessage);
    }
  }, [hardFailureMessage, announce]);

  useEffect(() => {
    if (sessionFailed) {
      announce(SESSION_FAILURE);
    }
  }, [sessionFailed, announce]);

  return (
    <section aria-labelledby="edit-entry-heading">
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="py-6">
        <PageHeader
          id="edit-entry-heading"
          title={entry.data ? entry.data.preferred_term : `Edit ${businessKey}`}
        />

        {entry.isPending && <p>Loading {businessKey}…</p>}

        {/* `isError` and `data` are not exclusive: `retry` is off and
            `refetchOnWindowFocus` is on, so an entry that loaded and then failed
            a *re*fetch has both. Rendering the failure paragraph unconditionally
            put "you cannot edit this entry" directly above a working editor -
            and the amend mutation's conflict refetch makes that a designed-in
            path, since a long-open session is exactly when one expires (PR #238
            review). A first load that fails blocks; a failed refresh is a
            banner over the terms the screen already has. */}
        {hardFailureMessage !== null && <p>{hardFailureMessage}</p>}

        {staleData && <p>{staleWarning(businessKey)}</p>}

        {entry.data && sessionFailed && <p>{SESSION_FAILURE}</p>}

        {entry.data && mayEdit && (
          <EntryEditForm key={entry.data.business_key} entry={entry.data} />
        )}

        {entry.data && (
          <InternalCommentsPanel
            target={{ kind: "entry", businessKey: entry.data.business_key }}
          />
        )}
      </PageContainer>
    </section>
  );
}
