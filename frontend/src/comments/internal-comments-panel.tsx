import { useEffect, useRef, useState } from "react";

import {
  useHasPermission,
  useInternalComments,
  type CommentTarget,
} from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { useAuthStatus } from "../auth/auth-status.ts";
import { EntrySection } from "../catalogue/entry-section.tsx";
import { formatDate } from "../catalogue/format-date.ts";
import { Button } from "../components/button.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { SectionFailure } from "../submissions/section-failure.tsx";
import { PostCommentForm } from "./post-comment-form.tsx";
import { WithdrawCommentDialog } from "./withdraw-comment-dialog.tsx";

type Comment = components["schemas"]["CommentResponse"];
type Person = components["schemas"]["CommentPerson"];

const READ_PERMISSION = "comment.internal.read";
const WRITE_PERMISSION = "comment.internal.write";

const LOAD_FAILURE = "The internal comments could not be loaded. Try again in a moment.";

const MUTED = "text-[var(--color-text-muted)]";

/** Comment text and reasons are plain text: React renders them as text, never
 * as markup (NFR-23), and the line breaks the writer typed are kept. */
const PLAIN_TEXT = "m-0 whitespace-pre-line [overflow-wrap:anywhere]";

function PersonName({ person }: { person: Person }) {
  return (
    <>
      {person.display_name} <span className={MUTED}>({person.username})</span>
    </>
  );
}

function CommentItem({
  comment,
  canWithdraw,
  focusWhenWithdrawn,
  onWithdraw,
}: {
  comment: Comment;
  canWithdraw: boolean;
  focusWhenWithdrawn: boolean;
  onWithdraw: () => void;
}) {
  const item = useRef<HTMLLIElement>(null);
  const withdrawn = comment.withdrawn_at !== null;

  // The Withdraw button is gone once the comment is withdrawn, so focus moves
  // to the comment rather than falling back to the page.
  useEffect(() => {
    if (withdrawn && focusWhenWithdrawn) {
      item.current?.focus();
    }
  }, [withdrawn, focusWhenWithdrawn]);

  return (
    <li ref={item} tabIndex={-1} className="flex min-w-0 flex-col gap-1">
      <span className="flex flex-wrap items-center gap-2">
        <span className="font-medium">
          <PersonName person={comment.author} />
        </span>
        <span className={MUTED}>
          <time dateTime={comment.created_at}>{formatDate(comment.created_at)}</time>
        </span>
        {withdrawn ? <StatusBadge tone="deprecated" label="Withdrawn" /> : null}
      </span>
      <p className={PLAIN_TEXT}>{comment.body}</p>
      {comment.withdrawn_at !== null && comment.withdrawn_by !== null ? (
        <>
          <span className={MUTED}>
            Withdrawn by <PersonName person={comment.withdrawn_by} /> on{" "}
            <time dateTime={comment.withdrawn_at}>
              {formatDate(comment.withdrawn_at)}
            </time>
          </span>
          <p className={`${PLAIN_TEXT} ${MUTED}`}>Reason: {comment.withdrawn_reason}</p>
        </>
      ) : null}
      {!withdrawn && canWithdraw ? (
        <Button
          type="button"
          variant="secondary"
          className="self-start"
          aria-label={`Withdraw the comment by ${comment.author.display_name} on ${formatDate(comment.created_at)}`}
          onClick={onWithdraw}
        >
          Withdraw
        </Button>
      ) : null}
    </li>
  );
}

/**
 * The internal comments on a submission or a catalogue entry (FR-95). It shows
 * only for a caller who holds `comment.internal.read`, and for anyone else it
 * renders nothing and the comments request is never sent: the server would
 * refuse it, so asking would only cost a 403 (NFR-20, FR-44). Posting and
 * withdrawing need `comment.internal.write`. The server checks every request
 * again, so hiding a control here is a convenience, never the check.
 *
 * A visitor who is not signed in holds no permission, so the session is never
 * read for them. The public entry page would otherwise send every anonymous
 * visitor a request it did not send before.
 */
export function InternalCommentsPanel({ target }: { target: CommentTarget }) {
  const signedIn = useAuthStatus() === "signed-in";
  // Keyed by target so a draft, an open dialog and the focus request never
  // carry over when the route shows another submission or entry.
  const targetKey = target.kind === "submission" ? target.id : target.businessKey;
  return signedIn ? (
    <SignedInPanel key={`${target.kind}:${targetKey}`} target={target} />
  ) : null;
}

function SignedInPanel({ target }: { target: CommentTarget }) {
  const mayRead = useHasPermission(READ_PERMISSION);
  const mayWrite = useHasPermission(WRITE_PERMISSION);
  const comments = useInternalComments(target, mayRead);
  const { message, politeness, announce } = useAnnounce();
  const [withdrawing, setWithdrawing] = useState<string | null>(null);
  const [justWithdrawn, setJustWithdrawn] = useState<string | null>(null);

  if (!mayRead) {
    return null;
  }

  let body = <p className="m-0 text-sm">Loading the internal comments…</p>;
  if (comments.data !== undefined) {
    body =
      comments.data.comments.length === 0 ? (
        <p className="m-0 text-sm">There are no internal comments yet.</p>
      ) : (
        <ol className="m-0 flex list-none flex-col gap-4 p-0 text-sm">
          {comments.data.comments.map((comment) => (
            <CommentItem
              key={comment.id}
              comment={comment}
              canWithdraw={mayWrite}
              focusWhenWithdrawn={justWithdrawn === comment.id}
              onWithdraw={() => setWithdrawing(comment.id)}
            />
          ))}
        </ol>
      );
  } else if (comments.isError) {
    body = (
      <SectionFailure
        error={comments.error}
        message={LOAD_FAILURE}
        retry={() => void comments.refetch()}
      />
    );
  }

  return (
    <EntrySection title="Internal comments">
      <LiveRegion message={message} politeness={politeness} />
      <p className={`m-0 text-sm ${MUTED}`}>
        Only Reviewers and Administrators can read these comments. The submitter and the
        public cannot.
      </p>
      {body}
      {mayWrite ? (
        <PostCommentForm target={target} onPosted={() => announce("Comment posted.")} />
      ) : null}
      {withdrawing === null ? null : (
        <WithdrawCommentDialog
          target={target}
          commentId={withdrawing}
          onClose={() => setWithdrawing(null)}
          onDone={() => {
            setJustWithdrawn(withdrawing);
            setWithdrawing(null);
            announce("Comment withdrawn.");
          }}
        />
      )}
    </EntrySection>
  );
}
