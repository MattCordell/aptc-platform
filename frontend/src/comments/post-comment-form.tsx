import { useState } from "react";

import { asBodyRefusal, refusalDetail } from "../api/conflicts.ts";
import { usePostInternalComment, type CommentTarget } from "../api/queries.ts";
import type { FormError } from "../components/error-summary.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";

const BODY_FIELD_ID = "internal-comment-body";

/** The server's limit on one comment's text. */
const MAX_LENGTH = 2000;

const FALLBACK_REFUSAL =
  "The comment could not be posted. Try again, or contact an administrator if the problem persists.";

/**
 * The box a Reviewer or Administrator posts an internal comment from (FR-95).
 * A blank comment is refused here before any request is sent, and a 422 from
 * the server marks the same field.
 */
export function PostCommentForm({
  target,
  onPosted,
}: {
  target: CommentTarget;
  onPosted: () => void;
}) {
  const post = usePostInternalComment(target);
  const [body, setBody] = useState("");
  const [errors, setErrors] = useState<FormError[]>([]);

  const bodyRefusal = post.isError ? asBodyRefusal(post.error) : null;
  const fieldErrors: FormError[] =
    bodyRefusal === null
      ? errors
      : [{ fieldId: BODY_FIELD_ID, message: bodyRefusal.detail }];
  const refusal = post.isError && bodyRefusal === null ? post.error : null;

  return (
    <Form
      submitLabel="Post comment"
      pendingLabel="Posting"
      pending={post.isPending}
      errors={fieldErrors}
      formError={
        refusal === null ? undefined : (refusalDetail(refusal) ?? FALLBACK_REFUSAL)
      }
      errorSummaryHeadingLevel={3}
      onSubmit={() => {
        const missing = body.trim().length === 0 ? "Enter a comment." : null;
        setErrors(missing === null ? [] : [{ fieldId: BODY_FIELD_ID, message: missing }]);
        if (missing !== null) {
          // An earlier server refusal no longer describes this attempt.
          post.reset();
          return;
        }
        return post.mutateAsync({ body: body.trim() }).then(
          () => {
            setBody("");
            onPosted();
            return { ok: true };
          },
          () => ({ ok: false }),
        );
      }}
    >
      <Field
        id={BODY_FIELD_ID}
        label="Add an internal comment"
        hint={`Plain text, up to ${MAX_LENGTH.toLocaleString("en-AU")} characters. Line breaks are kept.`}
        error={fieldErrors.find((error) => error.fieldId === BODY_FIELD_ID)?.message}
      >
        {(controlProps) => (
          <textarea
            {...controlProps}
            className={INPUT_CLASSES}
            rows={4}
            maxLength={MAX_LENGTH}
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
        )}
      </Field>
    </Form>
  );
}
