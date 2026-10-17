import { asSubmissionVersionConflict, refusalDetail } from "../api/conflicts.ts";

const STALE_VERSION =
  "This submission changed since you opened it. The page now shows the latest version. Check it, then confirm again.";

/**
 * The sentence for a refusal that belongs to no field of a dialog: the stale
 * version has its own wording, any other refusal shows the server's sentence,
 * and `fallback` covers a failure that carries none.
 */
export function refusalSentence(error: unknown, fallback: string): string {
  if (asSubmissionVersionConflict(error) !== null) {
    return STALE_VERSION;
  }
  return refusalDetail(error) ?? fallback;
}
