import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { expect } from "vitest";

import { SESSION_QUERY_KEY } from "../api/queries.ts";

/**
 * Resolves once the session query has answered. A test that asserts something is
 * absent for a caller's permissions calls this first, so the assertion cannot
 * pass merely because the session was still loading.
 */
export async function waitForSession(queryClient: QueryClient): Promise<void> {
  await waitFor(() =>
    expect(queryClient.getQueryState(SESSION_QUERY_KEY)?.status).toBe("success"),
  );
}
