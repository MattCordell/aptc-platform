import { refusalDetail } from "../api/conflicts.ts";
import { ApiError } from "../api/unwrap.ts";
import { Button } from "../components/button.tsx";

/**
 * What a sidebar section shows when its read fails. A 401 or 403 is the server
 * saying this caller may not see it, so a retry cannot succeed and the server's
 * own sentence says why. Any other failure gets `message` and a retry.
 */
export function SectionFailure({
  error,
  message,
  retry,
}: {
  error: unknown;
  message: string;
  retry: () => void;
}) {
  const refused =
    error instanceof ApiError && (error.status === 401 || error.status === 403);
  return (
    <div className="flex flex-col items-start gap-2">
      <p className="m-0 text-sm text-[var(--color-danger)]">
        {(refused ? refusalDetail(error) : null) ?? message}
      </p>
      {refused ? null : (
        <Button type="button" variant="secondary" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}
