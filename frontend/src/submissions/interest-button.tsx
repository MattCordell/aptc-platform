import { refusalDetail } from "../api/conflicts.ts";
import { useRegisterInterest, useRevokeInterest, useSession } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { Button } from "../components/button.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { isOpenSubmissionState } from "./state-options.ts";

type SubmissionDetail = components["schemas"]["SubmissionDetail"];

const REGISTER_PERMISSION = "interest.register";

const SAVE_FAILURE = "Your interest could not be saved. Try again in a moment.";

function countSentence(count: number | null): string {
  if (count === null) {
    return "";
  }
  return count === 1
    ? " 1 person has registered interest."
    : ` ${count} people have registered interest.`;
}

/**
 * Registers or withdraws the caller's interest in a submission (FR-32, FR-34).
 * It shows only for a caller who holds `interest.register`, on a submission
 * that is open and not their own: the server refuses every other case, so
 * hiding the button is a convenience, never the check (NFR-20, FR-44). A
 * refusal from a stale page shows the server's own words, and the detail is
 * re-read so the page corrects itself.
 */
export function InterestButton({
  submission,
}: {
  submission: Pick<
    SubmissionDetail,
    "id" | "is_own" | "state" | "has_registered_interest"
  >;
}) {
  const session = useSession();
  const register = useRegisterInterest(submission.id);
  const revoke = useRevokeInterest(submission.id);
  const { message, politeness, announce } = useAnnounce();

  const mayRegister = session.data?.permissions?.includes(REGISTER_PERMISSION) === true;
  if (!mayRegister || submission.is_own || !isOpenSubmissionState(submission.state)) {
    return null;
  }

  const registered = submission.has_registered_interest;
  const mutation = registered ? revoke : register;
  const failure = mutation.isError
    ? (refusalDetail(mutation.error) ?? SAVE_FAILURE)
    : null;

  function toggle() {
    if (mutation.isPending) {
      return;
    }
    mutation.mutate(undefined, {
      onSuccess: (answer) =>
        announce(
          `${answer.has_registered_interest ? "Interest registered." : "Interest withdrawn."}${countSentence(answer.interest_count)}`,
        ),
      onError: (error) => announce(refusalDetail(error) ?? SAVE_FAILURE),
    });
  }

  return (
    <div className="flex flex-col items-start gap-2">
      <LiveRegion message={message} politeness={politeness} />
      <Button
        type="button"
        variant={registered ? "secondary" : "primary"}
        aria-disabled={mutation.isPending ? true : undefined}
        onClick={toggle}
      >
        {registered ? "Withdraw interest" : "Register interest"}
      </Button>
      {failure === null ? null : (
        <p className="m-0 text-[var(--color-danger)]">{failure}</p>
      )}
    </div>
  );
}
