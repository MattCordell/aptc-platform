import type { Route } from "./stub-api.ts";
import { stubApi } from "./stub-api.ts";

/** The signed-in session `GET /auth/me` answers with, holding `permissions`. */
export function sessionRoute(permissions: string[]): Route {
  return {
    method: "GET",
    path: "/auth/me",
    status: 200,
    body: {
      authenticated: true,
      user: {
        username: "ada",
        display_name: "Ada Admin",
        organisation: null,
        status: "active",
      },
      roles: [],
      permissions,
      mfa_satisfied: true,
    },
  };
}

/**
 * `stubApi` for a screen that shows its edit form only to a caller who holds
 * `catalogue.edit_published`: the session answers as an editor unless a route
 * of the test's own answers `/auth/me` first.
 */
export function stubEditorApi(routes: Route[], options?: Parameters<typeof stubApi>[1]) {
  return stubApi([...routes, sessionRoute(["catalogue.edit_published"])], options);
}
