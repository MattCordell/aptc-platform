import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/session.ts";
import { asCollisionError, asVersionConflict, refusalDetail } from "./conflicts.ts";
import { stubApi } from "../test/stub-api.ts";
import { createQueryClient } from "./query-client.ts";
import {
  type CatalogueSearchParams,
  useAcknowledgeCollision,
  useAddDesignations,
  useAdminEntriesList,
  useAdminEntryDetail,
  useAdminSearch,
  useAmendDesignation,
  useAmendProperty,
  useApproveSubmission,
  useCatalogueFacets,
  useCatalogueSearch,
  useCreateProperty,
  useDatatypes,
  useDeprecateProperty,
  useEditWorkingCopy,
  useEntriesList,
  useEntryBindings,
  useEntryByCode,
  useEntryBySystemCode,
  useEntryDesignations,
  useEntryDetail,
  useEntryHistory,
  useEntryProperties,
  usePropertyDefinition,
  usePropertyDefinitions,
  usePropertyValueOptions,
  useReinstateDesignation,
  useRetireDesignation,
  useSavePropertyValues,
  useSession,
  useHasPermission,
  usePostInternalComment,
  useWithdrawInternalComment,
  useInterestIdentities,
  useInternalComments,
  useRegisterInterest,
  useRevokeInterest,
  useSubmissionDetail,
  useSubmissionTransitions,
  useSubmissionsList,
  useTransitionSubmission,
  useWithdrawSubmission,
} from "./queries.ts";
import type { ApiError } from "./unwrap.ts";

/**
 * TanStack Query hooks over the generated client (issue #147).
 *
 * Infrastructure-only wiring - these tests prove the hooks call the right
 * path with the right params and surface a failed response as a thrown
 * error for `useQuery` to catch, not that any page renders the result yet.
 */

const AUTH: AuthContextValue = {
  status: "signed-in",
  getAccessToken: () => Promise.resolve("test-token"),
  signIn: () => Promise.resolve(),
  stepUp: () => Promise.resolve("interaction-required"),
  signOut: () => Promise.resolve(),
  register: () => Promise.resolve(),
  restore: () => Promise.resolve(),
  completeCallback: () => Promise.resolve(null),
};

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = createQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn<(request: Request) => Promise<Response>>((request) => {
    void request;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// A response with no body at all - distinct from stubFetch's JSON body.
// openapi-fetch parses `error: undefined` for this shape (dist/index.mjs),
// which is exactly the case unwrap() (frontend/src/api/unwrap.ts) exists to
// still treat as a failure.
function stubFetchEmptyBody(status: number) {
  const fetchMock = vi.fn<(request: Request) => Promise<Response>>((request) => {
    void request;
    return Promise.resolve(new Response(null, { status }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useEntriesList", () => {
  it("fetches the entries page and exposes the parsed body", async () => {
    const page = { items: [], next_cursor: null };
    const fetchMock = stubFetch(200, page);

    const { result } = renderHook(() => useEntriesList({ limit: 20 }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(page);
    const requestUrl = new URL((fetchMock.mock.calls[0]?.[0] as Request).url);
    expect(requestUrl.pathname).toBe("/api/v1/catalogue/entries");
    expect(requestUrl.searchParams.get("limit")).toBe("20");
  });

  it("surfaces a non-2xx response as a query error", async () => {
    stubFetch(401, { detail: "not authenticated" });

    const { result } = renderHook(() => useEntriesList(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  // Principal failure mode (issue #147 review): a failed response with an
  // empty body parses as `error: undefined` in openapi-fetch, so a hook
  // that branched on the parsed error alone would resolve this as a
  // *successful* empty result instead of an error.
  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useEntriesList(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });
});

describe("useEntryDetail", () => {
  it("fetches the entry by business key", async () => {
    const entry = { business_key: "NPTC-000247", preferred_term: "x", length: 1 };
    const fetchMock = stubFetch(200, entry);

    const { result } = renderHook(() => useEntryDetail("NPTC-000247"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(entry);
    const requestUrl = new URL((fetchMock.mock.calls[0]?.[0] as Request).url);
    expect(requestUrl.pathname).toBe("/api/v1/catalogue/entries/NPTC-000247");
  });

  it("does not fetch for a blank business key", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useEntryDetail(""), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useSession", () => {
  it("exposes mfa_satisfied from /auth/me for the pre-emptive step-up banner", async () => {
    const session = {
      authenticated: true,
      user: {
        username: "a.curator",
        display_name: "A Curator",
        organisation: null,
        status: "active",
      },
      roles: ["Administrator"],
      permissions: ["role.grant_member"],
      mfa_satisfied: false,
    };
    const fetchMock = stubFetch(200, session);

    const { result } = renderHook(() => useSession(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(session);
    const requestUrl = new URL((fetchMock.mock.calls[0]?.[0] as Request).url);
    expect(requestUrl.pathname).toBe("/api/v1/auth/me");
  });

  // The route's own docstring: never 401s for an anonymous caller. This
  // hook must not gate the request on any prior knowledge of auth status.
  it("fetches even for an anonymous caller", async () => {
    const fetchMock = stubFetch(200, {
      authenticated: false,
      user: null,
      roles: [],
      permissions: [],
      mfa_satisfied: false,
    });

    const { result } = renderHook(() => useSession(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(result.current.data?.authenticated).toBe(false);
  });
});

/**
 * The write hooks (issue #149) - the first mutations in this app.
 *
 * What these prove is the wiring: the right path, the body passed through
 * unaltered, a refusal thrown rather than swallowed, and the admin-detail
 * query invalidated so the screen re-reads instead of guessing at the new
 * state. What the screen does with any of that is the page's own tests.
 */

function requestFor(fetchMock: ReturnType<typeof stubFetch>, call = 0) {
  return fetchMock.mock.calls[call]?.[0] as Request;
}

async function bodyOf(request: Request) {
  return (await request.json()) as Record<string, unknown>;
}

describe("useAdminEntryDetail", () => {
  it("reads the admin route, which serves an entry of any status", async () => {
    // The whole reason this hook exists rather than useEntryDetail: the
    // public route 404s a draft entry, so an edit screen cannot load its own
    // subject through it (issue #228).
    const entry = { business_key: "NPTC-000247", status: "draft", row_version: 3 };
    const fetchMock = stubFetch(200, entry);

    const { result } = renderHook(() => useAdminEntryDetail("NPTC-000247"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(entry);
    expect(new URL(requestFor(fetchMock).url).pathname).toBe(
      "/api/v1/catalogue/admin/entries/NPTC-000247",
    );
  });

  it("does not fetch for a blank business key", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminEntryDetail(""), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useAdminEntriesList", () => {
  it("fetches the admin entries page and exposes the parsed body", async () => {
    const page = { items: [], next_cursor: null };
    const fetchMock = stubFetch(200, page);

    const { result } = renderHook(() => useAdminEntriesList({ limit: 20 }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(page);
    const requestUrl = new URL(requestFor(fetchMock).url);
    expect(requestUrl.pathname).toBe("/api/v1/catalogue/admin/entries");
    expect(requestUrl.searchParams.get("limit")).toBe("20");
  });

  // Issue #276/ADR-0032: the one place a `filter.<key>` parameter name is
  // built by hand for the generated client - this proves it actually reaches
  // the wire as a repeated parameter, not a single comma-joined one.
  it("sends each selected filter value as its own repeated filter.<key> parameter", async () => {
    const fetchMock = stubFetch(200, { items: [], next_cursor: null });

    renderHook(() => useAdminEntriesList({ filters: { status: ["draft", "active"] } }), {
      wrapper,
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const requestUrl = new URL(requestFor(fetchMock).url);
    expect(requestUrl.searchParams.getAll("filter.status")).toEqual(["draft", "active"]);
  });

  it("does not fetch when disabled", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminEntriesList({ enabled: false }), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useSubmissionsList (FR-28, FR-34)", () => {
  it("sends a bare request by default, so the server's own default applies", async () => {
    const fetchMock = stubFetch(200, { items: [], next_cursor: null });

    renderHook(() => useSubmissionsList(), { wrapper });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const request = requestFor(fetchMock);
    expect(new URL(request.url).pathname).toBe("/api/v1/submissions");
    expect([...new URL(request.url).searchParams.keys()]).toEqual([]);
  });

  it("sends each state as its own repeated parameter, mine only when true, and the cursor", async () => {
    const fetchMock = stubFetch(200, { items: [], next_cursor: null });

    renderHook(
      () =>
        useSubmissionsList({
          state: ["Submitted", "Published in release"],
          mine: true,
          after: "cursor-1",
        }),
      { wrapper },
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const params = new URL(requestFor(fetchMock).url).searchParams;
    expect(params.getAll("state")).toEqual(["Submitted", "Published in release"]);
    expect(params.get("mine")).toBe("true");
    expect(params.get("after")).toBe("cursor-1");
  });

  it("leaves mine out when it is false", async () => {
    const fetchMock = stubFetch(200, { items: [], next_cursor: null });

    renderHook(() => useSubmissionsList({ mine: false }), { wrapper });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(new URL(requestFor(fetchMock).url).searchParams.has("mine")).toBe(false);
  });

  it("surfaces a refusal as a query error", async () => {
    stubFetch(403, { detail: "no" });

    const { result } = renderHook(() => useSubmissionsList(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe("useSubmissionDetail (FR-28, FR-34)", () => {
  const ID = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c11";

  it("reads the submission at its own path", async () => {
    const calls = stubApi([
      { method: "GET", path: `/api/v1/submissions/${ID}`, status: 200, body: { id: ID } },
    ]);

    const { result } = renderHook(() => useSubmissionDetail(ID), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls[0]?.path).toBe(`/api/v1/submissions/${ID}`);
  });

  it("sends nothing for a blank id", () => {
    const calls = stubApi([]);

    renderHook(() => useSubmissionDetail(""), { wrapper });

    expect(calls).toEqual([]);
  });

  it("surfaces an unknown id as a query error with its status", async () => {
    stubApi([
      {
        method: "GET",
        path: `/api/v1/submissions/${ID}`,
        status: 404,
        body: { detail: "no such submission" },
      },
    ]);

    const { result } = renderHook(() => useSubmissionDetail(ID), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(404);
  });
});

describe("useRegisterInterest and useRevokeInterest (FR-32, FR-34)", () => {
  const ID = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c11";
  const DETAIL = `/api/v1/submissions/${ID}`;
  const INTEREST = `${DETAIL}/interest`;
  const detail = {
    id: ID,
    preferred_term: "Ferritin",
    has_registered_interest: false,
    interest_count: 2,
  };

  function sharedClientWrapper() {
    const queryClient = createQueryClient();
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
    return { Wrapper, queryClient };
  }

  it("registers with a bodiless PUT and writes the answer into the cached detail", async () => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      {
        method: "PUT",
        path: INTEREST,
        status: 200,
        body: { has_registered_interest: true, interest_count: 3 },
      },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useRegisterInterest(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate();

    await waitFor(() => expect(result.current.read.data?.interest_count).toBe(3));
    expect(result.current.read.data).toMatchObject({
      preferred_term: "Ferritin",
      has_registered_interest: true,
    });
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
  });

  it("revokes with a DELETE and keeps a withheld count as null", async () => {
    stubApi([
      {
        method: "GET",
        path: DETAIL,
        status: 200,
        body: { ...detail, has_registered_interest: true, interest_count: null },
      },
      {
        method: "DELETE",
        path: INTEREST,
        status: 200,
        body: { has_registered_interest: false, interest_count: null },
      },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useRevokeInterest(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate();

    await waitFor(() =>
      expect(result.current.read.data?.has_registered_interest).toBe(false),
    );
    expect(result.current.read.data?.interest_count).toBeNull();
  });

  it("marks the list stale so its Interest column re-reads", async () => {
    stubApi([
      {
        method: "PUT",
        path: INTEREST,
        status: 200,
        body: { has_registered_interest: true, interest_count: 1 },
      },
    ]);
    const { Wrapper, queryClient } = sharedClientWrapper();
    queryClient.setQueryData(["api", "/api/v1/submissions", {}], { items: [] });
    const { result } = renderHook(() => useRegisterInterest(ID), { wrapper: Wrapper });

    result.current.mutate();

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(
      queryClient.getQueryState(["api", "/api/v1/submissions", {}])?.isInvalidated,
    ).toBe(true);
  });

  it("throws a refusal with its detail and leaves the cached detail alone", async () => {
    stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      {
        method: "PUT",
        path: INTEREST,
        status: 409,
        body: { detail: "You cannot register interest in your own submission." },
      },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useRegisterInterest(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate();

    await waitFor(() => expect(result.current.write.isError).toBe(true));
    expect(refusalDetail(result.current.write.error)).toBe(
      "You cannot register interest in your own submission.",
    );
    expect(result.current.read.data?.has_registered_interest).toBe(false);
  });

  it("marks the identities stale so a reviewer's own click reaches the panel", async () => {
    stubApi([
      {
        method: "PUT",
        path: INTEREST,
        status: 200,
        body: { has_registered_interest: true, interest_count: 1 },
      },
    ]);
    const { Wrapper, queryClient } = sharedClientWrapper();
    const identitiesKey = ["api", "/api/v1/submissions/{submission_id}/interest", ID];
    queryClient.setQueryData(identitiesKey, { records: [] });
    const { result } = renderHook(() => useRegisterInterest(ID), { wrapper: Wrapper });

    result.current.mutate();

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(queryClient.getQueryState(identitiesKey)?.isInvalidated).toBe(true);
  });
});

describe("useInterestIdentities (FR-33)", () => {
  const ID = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c12";
  const INTEREST = `/api/v1/submissions/${ID}/interest`;
  const IDENTITIES = {
    interest_count: 1,
    distinct_organisation_count: 1,
    distinct_organisations: [{ organisation: "Acme", label: "Acme", count: 1 }],
    records: [
      {
        username: "u-a",
        display_name: "A Person",
        organisation: "Acme",
        registered_at: "2026-09-01T12:00:00Z",
      },
    ],
  };

  it("reads the identities when enabled", async () => {
    stubApi([{ method: "GET", path: INTEREST, status: 200, body: IDENTITIES }]);

    const { result } = renderHook(() => useInterestIdentities(ID, true), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.records[0]?.display_name).toBe("A Person");
  });

  // The principal failure mode (NFR-20): a caller without the permission must
  // never cost the server a 403.
  it("sends no request when disabled", async () => {
    const calls = stubApi([
      { method: "GET", path: INTEREST, status: 200, body: IDENTITIES },
    ]);

    const { result } = renderHook(() => useInterestIdentities(ID, false), { wrapper });

    await waitFor(() => expect(result.current.fetchStatus).toBe("idle"));
    expect(calls).toHaveLength(0);
  });
});

describe("internal comment hooks (FR-95)", () => {
  const SUBMISSION = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c20";
  const KEY = "NPTC-000247";
  const COMMENT_ID = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c21";
  const TARGETS = [
    {
      name: "a submission",
      target: { kind: "submission", id: SUBMISSION } as const,
      path: `/api/v1/submissions/${SUBMISSION}/comments`,
    },
    {
      name: "an entry",
      target: { kind: "entry", businessKey: KEY } as const,
      path: `/api/v1/catalogue/entries/${KEY}/comments`,
    },
  ];
  const PERSON = { username: "rev", display_name: "Rae Reviewer" };
  const COMMENT = {
    id: COMMENT_ID,
    body: "Check the specimen.",
    created_at: "2026-10-01T09:00:00Z",
    author: PERSON,
    withdrawn_at: null,
    withdrawn_by: null,
    withdrawn_reason: null,
  };

  function sharedClientWrapper() {
    const queryClient = createQueryClient();
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
    return { Wrapper };
  }

  describe.each(TARGETS)("on $name", ({ target, path }) => {
    it("reads the comments when enabled", async () => {
      stubApi([{ method: "GET", path, status: 200, body: { comments: [COMMENT] } }]);

      const { result } = renderHook(() => useInternalComments(target, true), { wrapper });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.data?.comments[0]?.body).toBe("Check the specimen.");
    });

    // The principal failure mode (NFR-20): a caller without the permission must
    // never cost the server a 403.
    it("sends no request when disabled", async () => {
      const calls = stubApi([
        { method: "GET", path, status: 200, body: { comments: [] } },
      ]);

      const { result } = renderHook(() => useInternalComments(target, false), {
        wrapper,
      });

      await waitFor(() => expect(result.current.fetchStatus).toBe("idle"));
      expect(calls).toHaveLength(0);
    });

    it("posts the body, then reads the list again", async () => {
      const calls = stubApi([
        { method: "GET", path, status: 200, body: { comments: [] } },
        { method: "POST", path, status: 201, body: COMMENT },
      ]);
      const { Wrapper } = sharedClientWrapper();

      const { result } = renderHook(
        () => ({
          list: useInternalComments(target, true),
          post: usePostInternalComment(target),
        }),
        { wrapper: Wrapper },
      );
      await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
      await act(async () => {
        await result.current.post.mutateAsync({ body: "Check the specimen." });
      });

      const post = calls.find((call) => call.method === "POST");
      expect(post?.path).toBe(path);
      expect(post?.body).toEqual({ body: "Check the specimen." });
      await waitFor(() =>
        expect(calls.filter((call) => call.method === "GET")).toHaveLength(2),
      );
    });

    it("withdraws one comment with its reason, then reads the list again", async () => {
      const calls = stubApi([
        { method: "GET", path, status: 200, body: { comments: [COMMENT] } },
        {
          method: "POST",
          path: `${path}/${COMMENT_ID}/withdraw`,
          status: 200,
          body: COMMENT,
        },
      ]);
      const { Wrapper } = sharedClientWrapper();

      const { result } = renderHook(
        () => ({
          list: useInternalComments(target, true),
          withdraw: useWithdrawInternalComment(target),
        }),
        { wrapper: Wrapper },
      );
      await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
      await act(async () => {
        await result.current.withdraw.mutateAsync({
          commentId: COMMENT_ID,
          reason: "Wrong submission.",
        });
      });

      const withdraw = calls.find((call) => call.method === "POST");
      expect(withdraw?.path).toBe(`${path}/${COMMENT_ID}/withdraw`);
      expect(withdraw?.body).toEqual({ reason: "Wrong submission." });
      await waitFor(() =>
        expect(calls.filter((call) => call.method === "GET")).toHaveLength(2),
      );
    });

    it("reads the list again after a second withdrawal is refused, and throws the refusal", async () => {
      const calls = stubApi([
        { method: "GET", path, status: 200, body: { comments: [COMMENT] } },
        {
          method: "POST",
          path: `${path}/${COMMENT_ID}/withdraw`,
          status: 409,
          body: { detail: "This comment was already withdrawn." },
        },
      ]);
      const { Wrapper } = sharedClientWrapper();

      const { result } = renderHook(
        () => ({
          list: useInternalComments(target, true),
          withdraw: useWithdrawInternalComment(target),
        }),
        { wrapper: Wrapper },
      );
      await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
      await act(async () => {
        await result.current.withdraw
          .mutateAsync({ commentId: COMMENT_ID, reason: "Again." })
          .catch(() => undefined);
      });

      await waitFor(() =>
        expect(refusalDetail(result.current.withdraw.error)).toBe(
          "This comment was already withdrawn.",
        ),
      );
      await waitFor(() =>
        expect(calls.filter((call) => call.method === "GET")).toHaveLength(2),
      );
    });
  });
});

describe("useWithdrawSubmission (FR-28)", () => {
  const ID = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c13";
  const DETAIL = `/api/v1/submissions/${ID}`;
  const WITHDRAW = `${DETAIL}/withdraw`;
  const detail = {
    id: ID,
    preferred_term: "Ferritin",
    state: "Submitted",
    row_version: 1,
    interest_count: 2,
  };
  const BODY = { reason: "Sent in error.", expected_row_version: 1 };

  function sharedClientWrapper() {
    const queryClient = createQueryClient();
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
    return { Wrapper, queryClient };
  }

  it("posts the reason and version, and writes the new state into the cached detail", async () => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      {
        method: "POST",
        path: WITHDRAW,
        status: 200,
        body: { id: ID, state: "Withdrawn", row_version: 2 },
      },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useWithdrawSubmission(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.read.data?.state).toBe("Withdrawn"));
    expect(result.current.read.data).toMatchObject({
      preferred_term: "Ferritin",
      row_version: 2,
    });
    expect(calls.find((call) => call.method === "POST")?.body).toEqual(BODY);
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
  });

  it("marks the list stale so its State column re-reads", async () => {
    stubApi([
      {
        method: "POST",
        path: WITHDRAW,
        status: 200,
        body: { id: ID, state: "Withdrawn", row_version: 2 },
      },
    ]);
    const { Wrapper, queryClient } = sharedClientWrapper();
    queryClient.setQueryData(["api", "/api/v1/submissions", {}], { items: [] });
    const { result } = renderHook(() => useWithdrawSubmission(ID), { wrapper: Wrapper });

    result.current.mutate(BODY);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(
      queryClient.getQueryState(["api", "/api/v1/submissions", {}])?.isInvalidated,
    ).toBe(true);
  });

  it.each([
    [
      "a stale version",
      { detail: "Reload.", expected_row_version: 1, current_row_version: 2 },
    ],
    ["a closed state", { detail: "This submission is closed." }],
  ])("re-reads the detail on a 409 for %s", async (_name, refusal) => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      { method: "POST", path: WITHDRAW, status: 409, body: refusal },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useWithdrawSubmission(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.write.isError).toBe(true));
    await waitFor(() =>
      expect(calls.filter((call) => call.method === "GET").length).toBeGreaterThan(1),
    );
    expect(result.current.read.data?.state).toBe("Submitted");
  });

  it("leaves the detail alone on a 422 for the reason", async () => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      {
        method: "POST",
        path: WITHDRAW,
        status: 422,
        body: { detail: "Enter a reason.", field: "reason" },
      },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useWithdrawSubmission(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.write.isError).toBe(true));
    expect(refusalDetail(result.current.write.error)).toBe("Enter a reason.");
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
  });
});

describe("useHasPermission (FR-44, NFR-20)", () => {
  function session(permissions: string[]) {
    return {
      method: "GET" as const,
      path: "/auth/me",
      status: 200,
      body: {
        authenticated: true,
        user: {
          username: "jo",
          display_name: "Jo",
          organisation: null,
          status: "active",
        },
        roles: [],
        permissions,
        mfa_satisfied: false,
      },
    };
  }

  it("is false while the session loads, then true for a permission the caller holds", async () => {
    stubApi([session(["interest.register"])]);
    const { result } = renderHook(() => useHasPermission("interest.register"), {
      wrapper,
    });

    expect(result.current).toBe(false);
    await waitFor(() => expect(result.current).toBe(true));
  });

  it("stays false for a permission the caller does not hold", async () => {
    stubApi([session(["interest.register"])]);
    const { result } = renderHook(
      () => ({ held: useHasPermission("submission.transition"), session: useSession() }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.session.isSuccess).toBe(true));
    expect(result.current.held).toBe(false);
  });
});

describe("useSubmissionTransitions (FR-81)", () => {
  const ID = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c14";
  const HISTORY = `/api/v1/submissions/${ID}/transitions`;

  it("reads the history of the submission it names", async () => {
    const history = {
      items: [
        {
          from_state: "Submitted",
          to_state: "Triage",
          reason: "Looks complete.",
          created_at: "2026-10-01T02:00:00Z",
          actor: { username: "rev" },
        },
      ],
    };
    const calls = stubApi([{ method: "GET", path: HISTORY, status: 200, body: history }]);

    const { result } = renderHook(() => useSubmissionTransitions(ID), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(history);
    expect(calls).toHaveLength(1);
  });

  it("sends nothing for a blank id", () => {
    const calls = stubApi([]);

    renderHook(() => useSubmissionTransitions(""), { wrapper });

    expect(calls).toHaveLength(0);
  });

  it("surfaces a refusal as an error", async () => {
    stubApi([
      { method: "GET", path: HISTORY, status: 404, body: { detail: "No such." } },
    ]);

    const { result } = renderHook(() => useSubmissionTransitions(ID), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(refusalDetail(result.current.error)).toBe("No such.");
  });
});

describe("useTransitionSubmission (FR-28, FR-81)", () => {
  const ID = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c15";
  const DETAIL = `/api/v1/submissions/${ID}`;
  const HISTORY = `${DETAIL}/transitions`;
  const detail = {
    id: ID,
    preferred_term: "Ferritin",
    state: "Triage",
    row_version: 3,
    available_transitions: ["Under review", "Rejected"],
  };
  const BODY = {
    to_state: "Under review" as const,
    reason: "Looks complete.",
    expected_row_version: 3,
  };

  function sharedClientWrapper() {
    const queryClient = createQueryClient();
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
    return { Wrapper, queryClient };
  }

  it("posts the target, reason and version, and writes the answer into the cached detail", async () => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      {
        method: "POST",
        path: HISTORY,
        status: 200,
        body: {
          id: ID,
          state: "Under review",
          row_version: 4,
          available_transitions: ["Triage", "Rejected"],
        },
      },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useTransitionSubmission(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.read.data?.state).toBe("Under review"));
    expect(result.current.read.data).toMatchObject({
      preferred_term: "Ferritin",
      row_version: 4,
      available_transitions: ["Triage", "Rejected"],
    });
    expect(calls.find((call) => call.method === "POST")?.body).toEqual(BODY);
    expect(calls.filter((call) => call.path === DETAIL)).toHaveLength(1);
  });

  it.each([
    ["a transition", useTransitionSubmission, HISTORY, BODY],
    [
      "a withdrawal",
      useWithdrawSubmission,
      `${DETAIL}/withdraw`,
      { reason: "Sent in error.", expected_row_version: 3 },
    ],
  ])(
    "marks the history and the list stale after %s",
    async (_name, useWrite, path, body) => {
      stubApi([
        {
          method: "POST",
          path,
          status: 200,
          body: { id: ID, state: "Withdrawn", row_version: 4, available_transitions: [] },
        },
      ]);
      const { Wrapper, queryClient } = sharedClientWrapper();
      const historyKey = ["api", "/api/v1/submissions/{submission_id}/transitions", ID];
      queryClient.setQueryData(historyKey, { items: [] });
      queryClient.setQueryData(["api", "/api/v1/submissions", {}], { items: [] });
      const { result } = renderHook(() => useWrite(ID), { wrapper: Wrapper });

      result.current.mutate(body as never);

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(queryClient.getQueryState(historyKey)?.isInvalidated).toBe(true);
      expect(
        queryClient.getQueryState(["api", "/api/v1/submissions", {}])?.isInvalidated,
      ).toBe(true);
    },
  );

  it("leaves the catalogue reads alone after a move that writes no entry", async () => {
    stubApi([
      {
        method: "POST",
        path: HISTORY,
        status: 200,
        body: { id: ID, state: "Rejected", row_version: 4, available_transitions: [] },
      },
    ]);
    const { Wrapper, queryClient } = sharedClientWrapper();
    const entryKey = ["api", "/api/v1/catalogue/entries/{business_key}", "NPTC-000123"];
    queryClient.setQueryData(entryKey, {});
    const { result } = renderHook(() => useTransitionSubmission(ID), {
      wrapper: Wrapper,
    });

    result.current.mutate({ ...BODY, to_state: "Rejected" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(queryClient.getQueryState(entryKey)?.isInvalidated).toBe(false);
  });

  it.each([
    [
      "a stale version",
      { detail: "Reload.", expected_row_version: 3, current_row_version: 4 },
    ],
    ["a move the state no longer allows", { detail: "Not a permitted move." }],
  ])("re-reads the detail and the history on a 409 for %s", async (_name, refusal) => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      { method: "GET", path: HISTORY, status: 200, body: { items: [] } },
      { method: "POST", path: HISTORY, status: 409, body: refusal },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({
        read: useSubmissionDetail(ID),
        history: useSubmissionTransitions(ID),
        write: useTransitionSubmission(ID),
      }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.history.isSuccess).toBe(true));
    const reads = (path: string) =>
      calls.filter((call) => call.method === "GET" && call.path === path).length;

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.write.isError).toBe(true));
    await waitFor(() => expect(reads(DETAIL)).toBeGreaterThan(1));
    await waitFor(() => expect(reads(HISTORY)).toBeGreaterThan(1));
    expect(result.current.read.data?.state).toBe("Triage");
  });

  it.each([
    [403, { detail: "You may not move this submission." }],
    [
      422,
      { detail: "The reason has a character that cannot be shown.", field: "reason" },
    ],
  ])(
    "leaves the detail alone on a %s and passes the refusal on",
    async (status, refusal) => {
      const calls = stubApi([
        { method: "GET", path: DETAIL, status: 200, body: detail },
        { method: "POST", path: HISTORY, status, body: refusal },
      ]);
      const { Wrapper } = sharedClientWrapper();
      const { result } = renderHook(
        () => ({ read: useSubmissionDetail(ID), write: useTransitionSubmission(ID) }),
        { wrapper: Wrapper },
      );
      await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

      result.current.write.mutate(BODY);

      await waitFor(() => expect(result.current.write.isError).toBe(true));
      expect(refusalDetail(result.current.write.error)).toBe(refusal.detail);
      expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
    },
  );
});

describe("useApproveSubmission (FR-28, FR-35)", () => {
  const ID = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c16";
  const DETAIL = `/api/v1/submissions/${ID}`;
  const APPROVE = `${DETAIL}/approve`;
  const HISTORY = `${DETAIL}/transitions`;
  const detail = {
    id: ID,
    preferred_term: "Ferritin",
    state: "Ready for approval",
    row_version: 5,
    available_transitions: ["Approved", "Rejected"],
    entry_business_key: null,
    created_entry_business_key: null,
  };
  const BODY = { reason: "Checked against the source.", expected_row_version: 5 };
  const answer = {
    id: ID,
    state: "Approved",
    row_version: 6,
    available_transitions: [],
    entry_id: "6f1d2c3b-4a5e-4f60-8a71-92b3c4d5e6f7",
    business_key: "NPTC-000123",
    entry_row_version: 1,
  };

  function sharedClientWrapper() {
    const queryClient = createQueryClient();
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
    return { Wrapper, queryClient };
  }

  it("posts the reason and version, and caches the state with the entry a new test became", async () => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      {
        method: "POST",
        path: APPROVE,
        status: 200,
        body: { ...answer, entry_created: true },
      },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useApproveSubmission(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.read.data?.state).toBe("Approved"));
    expect(result.current.read.data).toMatchObject({
      preferred_term: "Ferritin",
      row_version: 6,
      available_transitions: [],
      entry_business_key: null,
      created_entry_business_key: "NPTC-000123",
    });
    expect(calls.find((call) => call.method === "POST")?.body).toEqual(BODY);
    expect(calls.filter((call) => call.path === DETAIL)).toHaveLength(1);
  });

  it("leaves the entry fields of an amendment as they were", async () => {
    stubApi([
      {
        method: "GET",
        path: DETAIL,
        status: 200,
        body: { ...detail, entry_business_key: "NPTC-000123" },
      },
      {
        method: "POST",
        path: APPROVE,
        status: 200,
        body: { ...answer, entry_created: false },
      },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useApproveSubmission(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.read.data?.state).toBe("Approved"));
    expect(result.current.read.data).toMatchObject({
      entry_business_key: "NPTC-000123",
      created_entry_business_key: null,
    });
  });

  it("marks the history and the list stale after an approval", async () => {
    stubApi([
      {
        method: "POST",
        path: APPROVE,
        status: 200,
        body: { ...answer, entry_created: true },
      },
    ]);
    const { Wrapper, queryClient } = sharedClientWrapper();
    const historyKey = ["api", "/api/v1/submissions/{submission_id}/transitions", ID];
    queryClient.setQueryData(historyKey, { items: [] });
    queryClient.setQueryData(["api", "/api/v1/submissions", {}], { items: [] });
    const { result } = renderHook(() => useApproveSubmission(ID), { wrapper: Wrapper });

    result.current.mutate(BODY);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(queryClient.getQueryState(historyKey)?.isInvalidated).toBe(true);
    expect(
      queryClient.getQueryState(["api", "/api/v1/submissions", {}])?.isInvalidated,
    ).toBe(true);
  });

  it("marks every cached catalogue read stale, and leaves other reads alone", async () => {
    stubApi([
      {
        method: "POST",
        path: APPROVE,
        status: 200,
        body: { ...answer, entry_created: false },
      },
    ]);
    const { Wrapper, queryClient } = sharedClientWrapper();
    const catalogueKeys = [
      ["api", "/api/v1/catalogue/entries/{business_key}", "NPTC-000123"],
      ["api", "/api/v1/catalogue/entries/{business_key}/designations", "NPTC-000123"],
      ["api", "/api/v1/catalogue/entries", {}],
      ["api", "/api/v1/catalogue/search", { q: "ferritin" }],
      ["api", "/api/v1/catalogue/admin/entries", {}],
    ];
    const otherKey = ["api", "/api/v1/registry/properties", {}];
    for (const key of [...catalogueKeys, otherKey]) {
      queryClient.setQueryData(key, {});
    }
    const { result } = renderHook(() => useApproveSubmission(ID), { wrapper: Wrapper });

    result.current.mutate(BODY);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    for (const key of catalogueKeys) {
      expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true);
    }
    expect(queryClient.getQueryState(otherKey)?.isInvalidated).toBe(false);
  });

  it("re-reads the detail and the history on a 409 and passes the sentence on", async () => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      { method: "GET", path: HISTORY, status: 200, body: { items: [] } },
      {
        method: "POST",
        path: APPROVE,
        status: 409,
        body: { detail: "The entry is no longer active." },
      },
    ]);
    const { Wrapper } = sharedClientWrapper();
    const { result } = renderHook(
      () => ({
        read: useSubmissionDetail(ID),
        history: useSubmissionTransitions(ID),
        write: useApproveSubmission(ID),
      }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.history.isSuccess).toBe(true));
    const reads = (path: string) =>
      calls.filter((call) => call.method === "GET" && call.path === path).length;

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.write.isError).toBe(true));
    expect(refusalDetail(result.current.write.error)).toBe(
      "The entry is no longer active.",
    );
    await waitFor(() => expect(reads(DETAIL)).toBeGreaterThan(1));
    await waitFor(() => expect(reads(HISTORY)).toBeGreaterThan(1));
    expect(result.current.read.data?.state).toBe("Ready for approval");
  });

  it.each([
    [403, { detail: "You may not approve this submission." }],
    [422, { detail: "The reason is too short to be a changelog note." }],
  ])(
    "leaves the detail alone on a %s and passes the refusal on",
    async (status, refusal) => {
      const calls = stubApi([
        { method: "GET", path: DETAIL, status: 200, body: detail },
        { method: "POST", path: APPROVE, status, body: refusal },
      ]);
      const { Wrapper } = sharedClientWrapper();
      const { result } = renderHook(
        () => ({ read: useSubmissionDetail(ID), write: useApproveSubmission(ID) }),
        { wrapper: Wrapper },
      );
      await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

      result.current.write.mutate(BODY);

      await waitFor(() => expect(result.current.write.isError).toBe(true));
      expect(refusalDetail(result.current.write.error)).toBe(refusal.detail);
      expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
      expect(result.current.read.data?.state).toBe("Ready for approval");
    },
  );
});

describe("useEditWorkingCopy (FR-23)", () => {
  const ID = "0b8a5c7e-6a43-4f56-9d1b-2f4f5a4e1c16";
  const DETAIL = `/api/v1/submissions/${ID}`;
  const PATCH = `${DETAIL}/working-copy`;
  const detail = {
    id: ID,
    preferred_term: "Ferritin",
    state: "Triage",
    row_version: 3,
    working_copy: null,
  };
  const BODY = {
    preferred_term: "Serum ferritin",
    reason: "Matches the pathology provider's wording.",
    expected_row_version: 3,
  };
  const answer = {
    preferred_term: "Serum ferritin",
    synonyms: [],
    snomed_code: null,
    snomed_fsn: null,
    property_values: {},
    notes: null,
    label_provenance: {},
    submission_id: ID,
    row_version: 4,
  };

  function sharedClientWrapper() {
    const queryClient = createQueryClient();
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    );
    return { Wrapper, queryClient };
  }

  it("sends the partial body, then reads the detail and marks the list stale", async () => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      { method: "PATCH", path: PATCH, status: 200, body: { ...answer, changed: true } },
    ]);
    const { Wrapper, queryClient } = sharedClientWrapper();
    const listKey = ["api", "/api/v1/submissions", {}];
    queryClient.setQueryData(listKey, { items: [] });
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useEditWorkingCopy(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.write.isSuccess).toBe(true));
    expect(calls.find((call) => call.method === "PATCH")?.body).toEqual(BODY);
    await waitFor(() =>
      expect(calls.filter((call) => call.method === "GET")).toHaveLength(2),
    );
    expect(queryClient.getQueryState(listKey)?.isInvalidated).toBe(true);
  });

  it("reads nothing again when the edit changed nothing", async () => {
    const calls = stubApi([
      { method: "GET", path: DETAIL, status: 200, body: detail },
      { method: "PATCH", path: PATCH, status: 200, body: { ...answer, changed: false } },
    ]);
    const { Wrapper, queryClient } = sharedClientWrapper();
    const listKey = ["api", "/api/v1/submissions", {}];
    queryClient.setQueryData(listKey, { items: [] });
    const { result } = renderHook(
      () => ({ read: useSubmissionDetail(ID), write: useEditWorkingCopy(ID) }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

    result.current.write.mutate(BODY);

    await waitFor(() => expect(result.current.write.isSuccess).toBe(true));
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
    expect(queryClient.getQueryState(listKey)?.isInvalidated).toBe(false);
  });

  it.each([
    [409, { detail: "This submission changed.", current_row_version: 5 }],
    [409, { detail: "The submission is not in a state that can be edited." }],
  ])(
    "re-reads the detail on a 409 and passes the refusal on",
    async (status, refusal) => {
      const calls = stubApi([
        { method: "GET", path: DETAIL, status: 200, body: detail },
        { method: "PATCH", path: PATCH, status, body: refusal },
      ]);
      const { Wrapper } = sharedClientWrapper();
      const { result } = renderHook(
        () => ({ read: useSubmissionDetail(ID), write: useEditWorkingCopy(ID) }),
        { wrapper: Wrapper },
      );
      await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

      result.current.write.mutate(BODY);

      await waitFor(() => expect(result.current.write.isError).toBe(true));
      expect(refusalDetail(result.current.write.error)).toBe(refusal.detail);
      await waitFor(() =>
        expect(calls.filter((call) => call.method === "GET").length).toBeGreaterThan(1),
      );
    },
  );

  it.each([
    [403, { detail: "You may not edit this submission." }],
    [422, { detail: "The reason is blank.", field: "reason" }],
  ])(
    "leaves the detail alone on a %s and passes the refusal on",
    async (status, refusal) => {
      const calls = stubApi([
        { method: "GET", path: DETAIL, status: 200, body: detail },
        { method: "PATCH", path: PATCH, status, body: refusal },
      ]);
      const { Wrapper } = sharedClientWrapper();
      const { result } = renderHook(
        () => ({ read: useSubmissionDetail(ID), write: useEditWorkingCopy(ID) }),
        { wrapper: Wrapper },
      );
      await waitFor(() => expect(result.current.read.isSuccess).toBe(true));

      result.current.write.mutate(BODY);

      await waitFor(() => expect(result.current.write.isError).toBe(true));
      expect(refusalDetail(result.current.write.error)).toBe(refusal.detail);
      expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
    },
  );
});

describe("useAdminSearch", () => {
  it("fetches the admin search page and exposes the parsed body", async () => {
    const page = { items: [], next_cursor: null, facets: [] };
    const fetchMock = stubFetch(200, page);

    const { result } = renderHook(() => useAdminSearch({ q: "glucose" }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(page);
    const requestUrl = new URL(requestFor(fetchMock).url);
    expect(requestUrl.pathname).toBe("/api/v1/catalogue/admin/search");
    expect(requestUrl.searchParams.get("q")).toBe("glucose");
  });

  it("does not fetch for a blank query, even when enabled", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminSearch({ q: "" }), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  // PR #285 review finding 5: trimmed, not merely non-empty, so this can
  // never fire in a state the page's own `mode` considers browse (it
  // computes `mode` with the identical `.trim()`).
  it("does not fetch for a whitespace-only query, even when enabled", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminSearch({ q: "   " }), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not fetch when disabled, even with a non-blank query", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => useAdminSearch({ q: "glucose", enabled: false }), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useAddDesignations", () => {
  it("posts the split terms as a batch and invalidates the entry", async () => {
    const fetchMock = stubFetch(201, { designations: [], warnings: [], row_version: 2 });
    const { result } = renderHook(
      () => ({
        add: useAddDesignations("NPTC-000247"),
        entry: useAdminEntryDetail("NPTC-000247"),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.entry.isSuccess).toBe(true));

    result.current.add.mutate({
      terms: ["Zovirax", "Cyclir"],
      reason: "Split the pasted synonym cell",
      expected_row_version: 1,
    });
    await waitFor(() => expect(result.current.add.isSuccess).toBe(true));

    const request = requestFor(fetchMock, 1);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations",
    );
    expect(request.method).toBe("POST");
    expect(await bodyOf(request)).toEqual({
      terms: ["Zovirax", "Cyclir"],
      reason: "Split the pasted synonym cell",
      expected_row_version: 1,
    });
    // The invalidation: a third call, re-reading the entry.
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3));
    expect(new URL(requestFor(fetchMock, 2).url).pathname).toBe(
      "/api/v1/catalogue/admin/entries/NPTC-000247",
    );
  });

  // The principal failure mode: an FR-05 error-severity collision. It must
  // reach the caller as an error with its body intact - a mutation that
  // resolved successfully here would let the screen report a save that never
  // happened.
  it("throws a 409 collision with its body intact", async () => {
    stubFetch(409, {
      detail: "This term matches another entry's preferred term or synonym.",
      collisions: [
        { severity: "error", business_key: "NPTC-000111", preferred_term: "Adrenal Ab" },
      ],
    });
    const { result } = renderHook(() => useAddDesignations("NPTC-000247"), { wrapper });

    result.current.mutate({
      terms: ["Adrenal Ab"],
      reason: "Add a colliding synonym",
      expected_row_version: 1,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asCollisionError(result.current.error)?.collisions[0]?.business_key).toBe(
      "NPTC-000111",
    );
  });

  // FR-38 (issue #300): the second axis this route can now refuse on. The
  // cached entry must be refetched, matching useAmendDesignation/
  // useSavePropertyValues - otherwise a retry from the same open form fails
  // identically against the same stale row_version.
  it("refetches the entry on a version conflict", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 1,
      current_row_version: 2,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(() => useAddDesignations("NPTC-000247"), { wrapper });

    result.current.mutate({
      terms: ["Zovirax"],
      reason: "Add under a stale version",
      expected_row_version: 1,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(2);
  });
});

describe("useAmendDesignation", () => {
  it("sends target and expected_row_version so the entry's own term is addressable", async () => {
    // ADR-0022 keeps the catalogue's own preferred term off `designation`
    // entirely. `target: "preferred_term"` is what reaches past a synonym shadowing it,
    // and `expected_row_version` is FR-38's lock, required on that branch.
    const fetchMock = stubFetch(200, {
      designation: { term: "Serum ferritin" },
      warnings: [],
      row_version: 4,
    });
    const { result } = renderHook(() => useAmendDesignation("NPTC-000247"), { wrapper });

    result.current.mutate({
      term: "Ferritin",
      new_term: "Serum ferritin",
      target: "preferred_term",
      expected_row_version: 3,
      reason: "Disambiguate against the plasma assay",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations/amendment",
    );
    expect(await bodyOf(request)).toMatchObject({
      term: "Ferritin",
      new_term: "Serum ferritin",
      target: "preferred_term",
      expected_row_version: 3,
    });
  });

  it("throws a 409 version conflict with its body intact", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 3,
      current_row_version: 4,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(() => useAmendDesignation("NPTC-000247"), { wrapper });

    result.current.mutate({
      term: "Ferritin",
      new_term: "Serum ferritin",
      expected_row_version: 3,
      reason: "Amend under a stale version",
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(4);
  });
});

describe("useRetireDesignation", () => {
  it("posts the term and its mandatory reason to the retirement route", async () => {
    const fetchMock = stubFetch(200, {
      designation: { term: "Cyclir", status: "withdrawn" },
      row_version: 2,
    });
    const { result } = renderHook(() => useRetireDesignation("NPTC-000247"), { wrapper });

    result.current.mutate({
      term: "Cyclir",
      reason: "Withdrawn brand name",
      expected_row_version: 1,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations/retirement",
    );
    expect(await bodyOf(request)).toEqual({
      term: "Cyclir",
      reason: "Withdrawn brand name",
      expected_row_version: 1,
    });
  });

  // FR-38 (issue #300): this route now takes a lock token and can refuse on
  // it - see useAddDesignations' identical test for why the cache must be
  // refetched, not left stale.
  it("refetches the entry on a version conflict", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 1,
      current_row_version: 2,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(() => useRetireDesignation("NPTC-000247"), { wrapper });

    result.current.mutate({
      term: "Cyclir",
      reason: "Retire under a stale version",
      expected_row_version: 1,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(2);
  });
});

describe("useReinstateDesignation", () => {
  it("posts the term and its mandatory reason to the reinstatement route", async () => {
    const fetchMock = stubFetch(200, {
      designation: { term: "Cyclir", status: "active" },
      warnings: [],
      row_version: 2,
    });
    const { result } = renderHook(() => useReinstateDesignation("NPTC-000247"), {
      wrapper,
    });

    result.current.mutate({
      term: "Cyclir",
      reason: "Retired by mistake",
      expected_row_version: 1,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations/reinstatement",
    );
    expect(await bodyOf(request)).toEqual({
      term: "Cyclir",
      reason: "Retired by mistake",
      expected_row_version: 1,
    });
  });

  // FR-38 (issue #300): this route takes a lock token too, and can refuse on
  // it - see useRetireDesignation's identical test for why the cache must be
  // refetched, not left stale.
  it("refetches the entry on a version conflict", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 1,
      current_row_version: 2,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(() => useReinstateDesignation("NPTC-000247"), {
      wrapper,
    });

    result.current.mutate({
      term: "Cyclir",
      reason: "Reinstate under a stale version",
      expected_row_version: 1,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(2);
  });
});

describe("useAcknowledgeCollision", () => {
  it("posts to the acknowledgement route, which is gated on another permission", async () => {
    // `validation.acknowledge`, held by Reviewer as well as Administrator and
    // not MFA-gated - so a caller who can edit is not guaranteed to be able to
    // acknowledge, and this route's 403 is a different sentence.
    const fetchMock = stubFetch(200, {
      reason: "Both are valid",
      created: true,
    });
    const { result } = renderHook(() => useAcknowledgeCollision("NPTC-000247"), {
      wrapper,
    });

    result.current.mutate({
      term: "Ferritin",
      reason: "Both are valid",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(new URL(requestFor(fetchMock).url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/designations/acknowledgement",
    );
    expect(result.current.data).toMatchObject({ created: true });
  });
});

/**
 * The registry-properties hooks (issue #151) - generated-form data plus the
 * two writes the panel needs (a property's values, and the entry's own core
 * columns). Same infrastructure-only scope as the write hooks above.
 */

describe("usePropertyDefinitions", () => {
  it("always asks for deprecated properties too, so FR-11 values still render", async () => {
    const list = { items: [] };
    const fetchMock = stubFetch(200, list);

    const { result } = renderHook(() => usePropertyDefinitions(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(list);
    const requestUrl = new URL(requestFor(fetchMock).url);
    expect(requestUrl.pathname).toBe("/api/v1/registry/properties");
    expect(requestUrl.searchParams.get("include_deprecated")).toBe("true");
  });
});

describe("usePropertyDefinition", () => {
  it("fetches one definition by key", async () => {
    const definition = { key: "specimen", label: "Specimen", status: "deprecated" };
    const fetchMock = stubFetch(200, definition);

    const { result } = renderHook(() => usePropertyDefinition("specimen"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(definition);
    expect(new URL(requestFor(fetchMock).url).pathname).toBe(
      "/api/v1/registry/properties/specimen",
    );
  });

  it("surfaces a 404 as an error carrying its status", async () => {
    stubFetch(404, { detail: "No property definition matches the given key." });

    const { result } = renderHook(() => usePropertyDefinition("missing"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));

    expect((result.current.error as ApiError).status).toBe(404);
  });

  it("does not fetch for a blank key", () => {
    const fetchMock = stubFetch(200, {});

    renderHook(() => usePropertyDefinition(""), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useDatatypes", () => {
  it("fetches the registered datatypes", async () => {
    const list = {
      items: [
        { name: "string", constraints_schema: { type: "object" }, uses_binding: false },
      ],
    };
    const fetchMock = stubFetch(200, list);

    const { result } = renderHook(() => useDatatypes(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(list);
    expect(new URL(requestFor(fetchMock).url).pathname).toBe(
      "/api/v1/registry/datatypes",
    );
  });

  it("surfaces a 403 as an error carrying its status", async () => {
    stubFetch(403, { detail: "You do not have permission to do that." });

    const { result } = renderHook(() => useDatatypes(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));

    expect((result.current.error as ApiError).status).toBe(403);
  });
});

const PROPERTY_LIST_KEY = [
  "api",
  "/api/v1/registry/properties",
  { include_deprecated: true },
];
const PROPERTY_KEY = ["api", "/api/v1/registry/properties/{key}", "assay_method"];

function invalidatedKeys(spy: { mock: { calls: unknown[][] } }) {
  return spy.mock.calls.map((call) => (call[0] as { queryKey: unknown }).queryKey);
}

// The generated body type marks the four defaulted fields as required, so a
// caller always sends them.
function newProperty(reason: string) {
  return {
    key: "assay_method",
    label: "Assay method",
    datatype: "string",
    cardinality: "0..1" as const,
    scope: "both" as const,
    required_for_submission: false,
    required_for_publication: false,
    filterable: false,
    display_order: 0,
    reason,
  };
}

describe("useCreateProperty", () => {
  it("posts the body and refreshes the list and the new key's detail", async () => {
    const created = { key: "assay_method", row_version: 1 };
    const fetchMock = stubFetch(201, created);
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCreateProperty(), {
      wrapper: wrapperWithStatus("signed-in", queryClient),
    });

    result.current.mutate(newProperty("Needed for the immunoassay entries"));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(request.method).toBe("POST");
    expect(new URL(request.url).pathname).toBe("/api/v1/registry/properties");
    expect(await bodyOf(request)).toMatchObject({
      key: "assay_method",
      datatype: "string",
    });
    expect(invalidatedKeys(invalidate)).toEqual([PROPERTY_LIST_KEY, PROPERTY_KEY]);
  });

  it("throws a duplicate-key 409 with its plain detail and refreshes nothing", async () => {
    stubFetch(409, { detail: "A property definition with this key already exists." });
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCreateProperty(), {
      wrapper: wrapperWithStatus("signed-in", queryClient),
    });

    result.current.mutate(newProperty("Duplicate"));
    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(refusalDetail(result.current.error)).toBe(
      "A property definition with this key already exists.",
    );
    expect(asVersionConflict(result.current.error)).toBeNull();
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("throws a 403 for a caller without registry.manage", async () => {
    stubFetch(403, { detail: "You do not have permission to do that." });
    const { result } = renderHook(() => useCreateProperty(), { wrapper });

    result.current.mutate(newProperty("Not permitted"));
    await waitFor(() => expect(result.current.isError).toBe(true));

    expect((result.current.error as ApiError).status).toBe(403);
  });
});

describe("useAmendProperty", () => {
  it("patches the named key with only the fields given", async () => {
    const fetchMock = stubFetch(200, { key: "assay_method", row_version: 2 });
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAmendProperty("assay_method"), {
      wrapper: wrapperWithStatus("signed-in", queryClient),
    });

    result.current.mutate({
      label: "Assay method (immunoassay)",
      expected_row_version: 1,
      reason: "Clarify the label",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(request.method).toBe("PATCH");
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/registry/properties/assay_method",
    );
    expect(await bodyOf(request)).toEqual({
      label: "Assay method (immunoassay)",
      expected_row_version: 1,
      reason: "Clarify the label",
    });
    expect(invalidatedKeys(invalidate)).toEqual([PROPERTY_LIST_KEY, PROPERTY_KEY]);
  });

  it("refetches the definition after a stale-version 409 and keeps the conflict body", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "assay_method",
      expected_row_version: 1,
      current_row_version: 2,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-10-07T00:00:00Z",
    });
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAmendProperty("assay_method"), {
      wrapper: wrapperWithStatus("signed-in", queryClient),
    });

    result.current.mutate({ label: "Stale", expected_row_version: 1, reason: "Stale" });
    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(2);
    expect(invalidatedKeys(invalidate)).toEqual([PROPERTY_KEY]);
  });

  it("does not refetch after a refusal that is not a version conflict", async () => {
    stubFetch(403, { detail: "You do not have permission to do that." });
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAmendProperty("assay_method"), {
      wrapper: wrapperWithStatus("signed-in", queryClient),
    });

    result.current.mutate({ label: "Nope", expected_row_version: 1, reason: "Nope" });
    await waitFor(() => expect(result.current.isError).toBe(true));

    expect((result.current.error as ApiError).status).toBe(403);
    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("useDeprecateProperty", () => {
  it("posts the deprecation and refreshes the list and the definition", async () => {
    const fetchMock = stubFetch(200, { key: "assay_method", status: "deprecated" });
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useDeprecateProperty("assay_method"), {
      wrapper: wrapperWithStatus("signed-in", queryClient),
    });

    result.current.mutate({
      expected_row_version: 2,
      reason: "Replaced by a coded property",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const request = requestFor(fetchMock);
    expect(request.method).toBe("POST");
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/registry/properties/assay_method/deprecation",
    );
    expect(await bodyOf(request)).toEqual({
      expected_row_version: 2,
      reason: "Replaced by a coded property",
    });
    expect(invalidatedKeys(invalidate)).toEqual([PROPERTY_LIST_KEY, PROPERTY_KEY]);
  });

  it("throws the system-property refusal with its plain detail", async () => {
    stubFetch(409, { detail: "A built-in system property cannot be deprecated." });
    const { result } = renderHook(() => useDeprecateProperty("specimen"), { wrapper });

    result.current.mutate({ expected_row_version: 1, reason: "Try" });
    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(refusalDetail(result.current.error)).toBe(
      "A built-in system property cannot be deprecated.",
    );
  });

  it("refreshes the list and the definition after any 409, such as already deprecated", async () => {
    stubFetch(409, { detail: "This property is already deprecated." });
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useDeprecateProperty("assay_method"), {
      wrapper: wrapperWithStatus("signed-in", queryClient),
    });

    result.current.mutate({ expected_row_version: 2, reason: "Again" });
    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(asVersionConflict(result.current.error)).toBeNull();
    expect(invalidatedKeys(invalidate)).toEqual([PROPERTY_LIST_KEY, PROPERTY_KEY]);
  });

  it("does not refresh after a 403", async () => {
    stubFetch(403, { detail: "You do not have permission to do that." });
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useDeprecateProperty("assay_method"), {
      wrapper: wrapperWithStatus("signed-in", queryClient),
    });

    result.current.mutate({ expected_row_version: 2, reason: "Nope" });
    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("usePropertyValueOptions", () => {
  it("fetches a coded property's offerable values by key", async () => {
    const page = { items: [{ code: "119361006", display: "Plasma specimen" }], total: 1 };
    const fetchMock = stubFetch(200, page);

    const { result } = renderHook(() => usePropertyValueOptions("specimen", ""), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(page);
    expect(new URL(requestFor(fetchMock).url).pathname).toBe(
      "/api/v1/registry/properties/specimen/values",
    );
  });

  it("passes a non-empty filter through as a query param", async () => {
    const fetchMock = stubFetch(200, { items: [], total: 0 });

    renderHook(() => usePropertyValueOptions("specimen", "plasma"), { wrapper });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(new URL(requestFor(fetchMock).url).searchParams.get("filter")).toBe("plasma");
  });

  it("does not fetch for a blank property key", () => {
    const fetchMock = stubFetch(200, { items: [], total: 0 });

    renderHook(() => usePropertyValueOptions("", ""), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useSavePropertyValues", () => {
  it("PUTs the whole value set to the property's own route and invalidates the entry", async () => {
    const fetchMock = stubFetch(200, {
      values: [{ key: "discipline", label: "Discipline", ordinal: 0, value: "HAEM" }],
      row_version: 4,
    });
    const { result } = renderHook(
      () => ({
        save: useSavePropertyValues("NPTC-000247", "discipline"),
        entry: useAdminEntryDetail("NPTC-000247"),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.entry.isSuccess).toBe(true));

    result.current.save.mutate({
      values: [{ value: "HAEM" }],
      reason: "Record the discipline",
      expected_row_version: 3,
    });
    await waitFor(() => expect(result.current.save.isSuccess).toBe(true));

    const request = requestFor(fetchMock, 1);
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/catalogue/entries/NPTC-000247/properties/discipline",
    );
    expect(request.method).toBe("PUT");
    expect(await bodyOf(request)).toEqual({
      values: [{ value: "HAEM" }],
      reason: "Record the discipline",
      expected_row_version: 3,
    });
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(3));
    expect(new URL(requestFor(fetchMock, 2).url).pathname).toBe(
      "/api/v1/catalogue/admin/entries/NPTC-000247",
    );
  });

  // Principal failure mode: FR-38's optimistic lock. The cached entry must be
  // refetched, matching useAmendDesignation - otherwise a retry from the same
  // open dialog fails identically against the same stale row_version.
  it("refetches the entry on a version conflict", async () => {
    stubFetch(409, {
      detail: "This entry was changed by someone else since you loaded it.",
      business_key: "NPTC-000247",
      expected_row_version: 3,
      current_row_version: 4,
      conflicts: [],
      changed_by: "A Curator",
      changed_at: "2026-09-02T00:00:00Z",
    });
    const { result } = renderHook(
      () => useSavePropertyValues("NPTC-000247", "discipline"),
      { wrapper },
    );

    result.current.mutate({
      values: [{ value: "HAEM" }],
      reason: "Record the discipline",
      expected_row_version: 3,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(asVersionConflict(result.current.error)?.current_row_version).toBe(4);
  });
});

/**
 * The public catalogue read hooks (FR-14 to FR-19). These use the shared
 * `stubApi` so each test can assert the query string the request carried,
 * not only its path.
 */

const ENTRY_KEY = "NPTC-000247";
const ENTRIES = "/api/v1/catalogue/entries";

function wrapperWithStatus(
  status: AuthContextValue["status"],
  queryClient = createQueryClient(),
) {
  return function StatusWrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={{ ...AUTH, status }}>
          {children}
        </AuthContext.Provider>
      </QueryClientProvider>
    );
  };
}

describe("useEntriesList filters and gating", () => {
  it("sends each facet selection as a repeated filter.<key> pair", async () => {
    const calls = stubApi([
      { method: "GET", path: ENTRIES, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(
      () =>
        useEntriesList({
          filters: { specimen: ["119297000", "122575003"], discipline: ["chem"] },
        }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const { searchParams } = calls[0]!;
    expect(searchParams.getAll("filter.specimen")).toEqual(["119297000", "122575003"]);
    expect(searchParams.getAll("filter.discipline")).toEqual(["chem"]);
  });

  it("passes the after cursor through unchanged and sends no offset, page or total", async () => {
    const calls = stubApi([
      { method: "GET", path: ENTRIES, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(
      () => useEntriesList({ limit: 10, after: "opaque+/=cursor" }),
      {
        wrapper,
      },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const { searchParams } = calls[0]!;
    expect(searchParams.get("after")).toBe("opaque+/=cursor");
    expect([...searchParams.keys()].sort()).toEqual(["after", "limit"]);
  });

  it("sends no after parameter for a null cursor", async () => {
    const calls = stubApi([
      { method: "GET", path: ENTRIES, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(() => useEntriesList({ after: null }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls[0]!.searchParams.has("after")).toBe(false);
  });

  it("does not fetch while enabled is false", () => {
    const calls = stubApi([
      { method: "GET", path: ENTRIES, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(() => useEntriesList({ enabled: false }), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });
});

describe("useCatalogueSearch", () => {
  const SEARCH = "/api/v1/catalogue/search";

  it("sends q, the cursor and repeated filters, and exposes the parsed page", async () => {
    const page = { items: [], facets: [], next_cursor: null };
    const calls = stubApi([{ method: "GET", path: SEARCH, status: 200, body: page }]);

    const { result } = renderHook(
      () =>
        useCatalogueSearch({
          q: "glucose",
          limit: 25,
          after: "cursor-1",
          filters: { specimen: ["119297000", "122575003"] },
        }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(page);
    const call = calls[0]!;
    expect(call.path).toBe(SEARCH);
    expect(call.searchParams.get("q")).toBe("glucose");
    expect(call.searchParams.get("limit")).toBe("25");
    expect(call.searchParams.get("after")).toBe("cursor-1");
    expect(call.searchParams.getAll("filter.specimen")).toEqual([
      "119297000",
      "122575003",
    ]);
    expect(
      [...call.searchParams.keys()].some((key) => /^(offset|page|total)/.test(key)),
    ).toBe(false);
  });

  it("keeps a different q, cursor or filter set in a separate cache entry", async () => {
    const calls = stubApi([
      { method: "GET", path: SEARCH, status: 200, body: { items: [], facets: [] } },
    ]);
    const queryClient = createQueryClient();
    const { rerender } = renderHook(
      (props: CatalogueSearchParams) => useCatalogueSearch(props),
      {
        wrapper: wrapperWithStatus("signed-in", queryClient),
        initialProps: { q: "glucose" } as CatalogueSearchParams,
      },
    );
    await waitFor(() => expect(calls).toHaveLength(1));

    rerender({ q: "glucose", after: "cursor-1" });
    await waitFor(() => expect(calls).toHaveLength(2));

    rerender({ q: "glucose", after: "cursor-1", filters: { specimen: ["119297000"] } });
    await waitFor(() => expect(calls).toHaveLength(3));

    rerender({ q: "glucose", after: "cursor-1", filters: { specimen: ["122575003"] } });
    await waitFor(() => expect(calls).toHaveLength(4));

    rerender({ q: "sodium", after: "cursor-1", filters: { specimen: ["122575003"] } });
    await waitFor(() => expect(calls).toHaveLength(5));
  });

  // A page's next_cursor is `string | null`, so a screen passes it straight
  // back; a null on the last page must mean "no cursor", not the text "null".
  it("sends no after parameter for a null cursor", async () => {
    const calls = stubApi([
      { method: "GET", path: SEARCH, status: 200, body: { items: [], facets: [] } },
    ]);

    const { result } = renderHook(
      () => useCatalogueSearch({ q: "glucose", after: null }),
      {
        wrapper,
      },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls[0]!.searchParams.has("after")).toBe(false);
  });

  it.each(["", "   "])("does not fetch for a blank q (%j)", (q) => {
    const calls = stubApi([{ method: "GET", path: SEARCH, status: 200, body: {} }]);

    const { result } = renderHook(() => useCatalogueSearch({ q }), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });

  it("surfaces a non-2xx response as a query error", async () => {
    stubApi([
      { method: "GET", path: SEARCH, status: 422, body: { detail: "bad cursor" } },
    ]);

    const { result } = renderHook(() => useCatalogueSearch({ q: "glucose" }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(422);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useCatalogueSearch({ q: "glucose" }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });
});

describe("useCatalogueFacets", () => {
  const SEARCH = "/api/v1/catalogue/search";
  const FACETS = [
    {
      key: "discipline",
      label: "Discipline",
      facetable: true,
      truncated: false,
      buckets: [],
    },
  ];

  it("browses with facets=true and one row, and returns just the facet list", async () => {
    const calls = stubApi([
      {
        method: "GET",
        path: ENTRIES,
        status: 200,
        body: { items: [], next_cursor: null, facets: FACETS },
      },
    ]);

    const { result } = renderHook(
      () => useCatalogueFacets({ q: "", filters: { specimen: ["serum", "urine"] } }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(FACETS);
    const { searchParams } = calls[0]!;
    expect(searchParams.get("facets")).toBe("true");
    expect(searchParams.get("limit")).toBe("1");
    expect(searchParams.getAll("filter.specimen")).toEqual(["serum", "urine"]);
    expect(searchParams.has("after")).toBe(false);
  });

  it("searches, instead of browsing, once q is not blank", async () => {
    const calls = stubApi([
      {
        method: "GET",
        path: SEARCH,
        status: 200,
        body: { items: [], next_cursor: null, facets: FACETS },
      },
    ]);

    const { result } = renderHook(() => useCatalogueFacets({ q: "glucose" }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls[0]!.path).toBe(SEARCH);
    expect(calls[0]!.searchParams.get("q")).toBe("glucose");
    expect(calls[0]!.searchParams.get("limit")).toBe("1");
  });

  it("answers an entries response with no facets as an empty list", async () => {
    stubApi([
      {
        method: "GET",
        path: ENTRIES,
        status: 200,
        body: { items: [], next_cursor: null },
      },
    ]);

    const { result } = renderHook(() => useCatalogueFacets({ q: "" }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it("surfaces a refusal as a query error", async () => {
    stubApi([{ method: "GET", path: ENTRIES, status: 422, body: { detail: "no" } }]);

    const { result } = renderHook(() => useCatalogueFacets({ q: "" }), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(422);
  });
});

describe.each([
  { name: "useEntryDesignations", hook: useEntryDesignations, suffix: "designations" },
  { name: "useEntryBindings", hook: useEntryBindings, suffix: "bindings" },
  { name: "useEntryProperties", hook: useEntryProperties, suffix: "properties" },
])("$name", ({ hook, suffix }) => {
  const path = `${ENTRIES}/${ENTRY_KEY}/${suffix}`;

  it("fetches the entry's records and exposes the parsed body", async () => {
    const body = { items: [{ marker: suffix }] };
    const calls = stubApi([{ method: "GET", path, status: 200, body }]);

    const { result } = renderHook(() => hook(ENTRY_KEY), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(body);
    expect(calls[0]!.path).toBe(path);
    expect([...calls[0]!.searchParams.keys()]).toEqual([]);
  });

  it("surfaces a 404 as a query error that keeps its status", async () => {
    stubApi([{ method: "GET", path, status: 404, body: { detail: "no such entry" } }]);

    const { result } = renderHook(() => hook(ENTRY_KEY), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(404);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => hook(ENTRY_KEY), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });

  it("does not fetch for a blank business key", () => {
    const calls = stubApi([{ method: "GET", path, status: 200, body: {} }]);

    const { result } = renderHook(() => hook(""), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });
});

describe("useEntryHistory", () => {
  const HISTORY = `${ENTRIES}/${ENTRY_KEY}/history`;

  it("sends limit and the before cursor, and exposes the parsed page", async () => {
    const page = { items: [], next_cursor: null };
    const calls = stubApi([{ method: "GET", path: HISTORY, status: 200, body: page }]);

    const { result } = renderHook(
      () => useEntryHistory(ENTRY_KEY, { limit: 5, before: "cursor-9" }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(page);
    const { searchParams } = calls[0]!;
    expect(searchParams.get("limit")).toBe("5");
    expect(searchParams.get("before")).toBe("cursor-9");
    expect([...searchParams.keys()].sort()).toEqual(["before", "limit"]);
  });

  it("sends no before parameter for a null cursor", async () => {
    const calls = stubApi([
      { method: "GET", path: HISTORY, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(() => useEntryHistory(ENTRY_KEY, { before: null }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls[0]!.searchParams.has("before")).toBe(false);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useEntryHistory(ENTRY_KEY), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });

  it("does not fetch for a blank business key", () => {
    const calls = stubApi([{ method: "GET", path: HISTORY, status: 200, body: {} }]);

    const { result } = renderHook(() => useEntryHistory(""), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });

  // Principal failure mode (NFR-26): the route answers `changed_by: null` to
  // an anonymous caller. A page cached before the session restored must not
  // be served to the signed-in reader afterwards.
  it("waits for the session to restore before asking", () => {
    const calls = stubApi([
      { method: "GET", path: HISTORY, status: 200, body: { items: [] } },
    ]);

    const { result } = renderHook(() => useEntryHistory(ENTRY_KEY), {
      wrapper: wrapperWithStatus("restoring"),
    });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });

  it("asks again, rather than reusing the cached page, when the caller signs in", async () => {
    const calls = stubApi([
      { method: "GET", path: HISTORY, status: 200, body: { items: [] } },
    ]);
    const queryClient = createQueryClient();
    let status: AuthContextValue["status"] = "signed-out";
    function Wrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          <AuthContext.Provider value={{ ...AUTH, status }}>
            {children}
          </AuthContext.Provider>
        </QueryClientProvider>
      );
    }

    const { result, rerender } = renderHook(() => useEntryHistory(ENTRY_KEY), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls).toHaveLength(1);

    status = "signed-in";
    rerender();

    await waitFor(() => expect(calls).toHaveLength(2));
  });

  describe("keepPreviousPage", () => {
    const FIRST = { items: [{ note: "page one" }], next_cursor: "c2" };
    const OTHER_KEY = "NPTC-000999";

    /**
     * Page one of `ENTRY_KEY` answers at once. Every other request waits, so a
     * test sees what the hook shows while the next page is still loading.
     */
    function renderPaged(initial: { signedIn: boolean }) {
      stubApi([], {
        vary: ({ path, searchParams }) => {
          if (!path.endsWith("/history")) {
            return null;
          }
          const first = path.includes(ENTRY_KEY) && !searchParams.has("before");
          return first
            ? { method: "GET", path, status: 200, body: FIRST }
            : { method: "GET", path, status: 200, body: null, neverSettles: true };
        },
      });
      const queryClient = createQueryClient();
      let status: AuthContextValue["status"] = initial.signedIn
        ? "signed-in"
        : "signed-out";
      function Wrapper({ children }: { children: ReactNode }) {
        return (
          <QueryClientProvider client={queryClient}>
            <AuthContext.Provider value={{ ...AUTH, status }}>
              {children}
            </AuthContext.Provider>
          </QueryClientProvider>
        );
      }
      const view = renderHook(
        (props: { key: string; before?: string; keep: boolean }) =>
          useEntryHistory(props.key, {
            before: props.before,
            keepPreviousPage: props.keep,
          }),
        {
          wrapper: Wrapper,
          initialProps: { key: ENTRY_KEY, keep: true } as {
            key: string;
            before?: string;
            keep: boolean;
          },
        },
      );
      return {
        ...view,
        signInAs: (signedIn: boolean) => {
          status = signedIn ? "signed-in" : "signed-out";
        },
      };
    }

    it("shows the earlier page while the next page of the same entry loads", async () => {
      const view = renderPaged({ signedIn: false });
      await waitFor(() => expect(view.result.current.data).toEqual(FIRST));

      view.rerender({ key: ENTRY_KEY, before: "c2", keep: true });

      await waitFor(() => expect(view.result.current.isPlaceholderData).toBe(true));
      expect(view.result.current.data).toEqual(FIRST);
    });

    it("shows nothing while the next page loads unless asked to keep the earlier one", async () => {
      const view = renderPaged({ signedIn: false });
      await waitFor(() => expect(view.result.current.data).toEqual(FIRST));

      view.rerender({ key: ENTRY_KEY, before: "c2", keep: false });

      await waitFor(() => expect(view.result.current.isPending).toBe(true));
      expect(view.result.current.data).toBeUndefined();
    });

    // Principal failure mode (NFR-26): a signed-in page carries author names,
    // and must not stand in for the page of a reader who is no longer signed in.
    it.each([
      { from: true, to: false },
      { from: false, to: true },
    ])(
      "shows no earlier page when the reader changes from signed in $from to $to",
      async ({ from, to }) => {
        const view = renderPaged({ signedIn: from });
        await waitFor(() => expect(view.result.current.data).toEqual(FIRST));

        view.signInAs(to);
        view.rerender({ key: ENTRY_KEY, before: "c2", keep: true });

        await waitFor(() => expect(view.result.current.isPending).toBe(true));
        expect(view.result.current.data).toBeUndefined();
      },
    );

    it("shows no earlier page under another entry", async () => {
      const view = renderPaged({ signedIn: false });
      await waitFor(() => expect(view.result.current.data).toEqual(FIRST));

      view.rerender({ key: OTHER_KEY, keep: true });

      await waitFor(() => expect(view.result.current.isPending).toBe(true));
      expect(view.result.current.data).toBeUndefined();
    });
  });
});

describe("useEntryByCode", () => {
  it("fetches the entry for a code under a system token", async () => {
    const entry = { business_key: ENTRY_KEY };
    const calls = stubApi([
      {
        method: "GET",
        path: "/api/v1/catalogue/code/sct/26604007",
        status: 200,
        body: entry,
      },
    ]);

    const { result } = renderHook(() => useEntryByCode("sct", "26604007"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(entry);
    expect(calls[0]!.path).toBe("/api/v1/catalogue/code/sct/26604007");
  });

  // FR-06: a number-typed code would lose the leading zeros. The assertion is
  // on the raw request URL, which a coerced value could not fake.
  it("keeps a code with leading zeros as written", async () => {
    const calls = stubApi([
      {
        method: "GET",
        path: "/code/sct/0012345",
        status: 200,
        body: { business_key: ENTRY_KEY },
      },
    ]);

    const { result } = renderHook(() => useEntryByCode("sct", "0012345"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(new URL(calls[0]!.url).pathname.endsWith("/code/sct/0012345")).toBe(true);
  });

  it("surfaces a 404 as a query error that keeps its status", async () => {
    stubApi([
      {
        method: "GET",
        path: "/code/sct/999",
        status: 404,
        body: { detail: "No such entry." },
      },
    ]);

    const { result } = renderHook(() => useEntryByCode("sct", "999"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(404);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useEntryByCode("sct", "26604007"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });

  it.each([
    ["a blank token", "", "26604007"],
    ["a blank code", "sct", ""],
  ])("does not fetch for %s", (_label, token, code) => {
    const calls = stubApi([{ method: "GET", path: "/code/", status: 200, body: {} }]);

    const { result } = renderHook(() => useEntryByCode(token, code), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });
});

describe("useEntryBySystemCode", () => {
  const LOOKUP = "/api/v1/catalogue/lookup";
  const SNOMED = "http://snomed.info/sct";

  it("sends the system URI and code as query parameters", async () => {
    const entry = { business_key: ENTRY_KEY };
    const calls = stubApi([{ method: "GET", path: LOOKUP, status: 200, body: entry }]);

    const { result } = renderHook(() => useEntryBySystemCode(SNOMED, "26604007"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(entry);
    expect(calls[0]!.path).toBe(LOOKUP);
    expect(calls[0]!.searchParams.get("system")).toBe(SNOMED);
    expect(calls[0]!.searchParams.get("code")).toBe("26604007");
  });

  it("keeps a code with leading zeros as written", async () => {
    const calls = stubApi([
      { method: "GET", path: LOOKUP, status: 200, body: { business_key: ENTRY_KEY } },
    ]);

    const { result } = renderHook(() => useEntryBySystemCode(SNOMED, "0012345"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(new URL(calls[0]!.url).search).toContain("code=0012345");
  });

  it("surfaces a 404 as a query error that keeps its status", async () => {
    stubApi([
      { method: "GET", path: LOOKUP, status: 404, body: { detail: "No such entry." } },
    ]);

    const { result } = renderHook(() => useEntryBySystemCode(SNOMED, "999"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).status).toBe(404);
  });

  it("surfaces a non-2xx response with an empty body as a query error", async () => {
    stubFetchEmptyBody(500);

    const { result } = renderHook(() => useEntryBySystemCode(SNOMED, "26604007"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
  });

  it.each([
    ["a blank system", "", "26604007"],
    ["a blank code", SNOMED, ""],
  ])("does not fetch for %s", (_label, system, code) => {
    const calls = stubApi([{ method: "GET", path: LOOKUP, status: 200, body: {} }]);

    const { result } = renderHook(() => useEntryBySystemCode(system, code), { wrapper });

    // A hook that fired would be fetching by now; the stub's call log alone
    // lags behind it.
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });
});
