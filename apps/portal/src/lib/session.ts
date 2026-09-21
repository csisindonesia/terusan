/**
 * Who is signed in, as the rest of the portal asks it.
 *
 * The session is a cookie the browser holds and this code never reads: it is
 * `HttpOnly`, so the only way to find out whose it is, is to ask the API. That
 * answer is cached like any other query rather than mirrored into a store —
 * there is exactly one source of truth for "am I signed in", and it is the
 * server that would refuse the next request.
 *
 * Two questions, deliberately separate. Whether the deployment *has* accounts
 * comes from `/v1/capabilities`, and a deployment without an application
 * database has none — the portal then shows no login page, because there would
 * be nothing to log in to. Whether *this browser* holds a session comes from
 * `/v1/auth/me`.
 */

import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { ApiRequestError, api, type Session } from "~/lib/api";

export const SESSION_KEY = ["session"] as const;
export const CAPABILITIES_KEY = ["capabilities"] as const;

/**
 * The session, or null.
 *
 * A 401 is the expected answer for a signed-out browser, not a failure: it is
 * turned into `null` here so every caller does not have to read status codes
 * to tell "nobody is signed in" from "the API is down". A deployment with no
 * accounts answers 404, which is also null — there is nobody to be signed in
 * as — and anything else is a real error worth surfacing.
 */
async function fetchSession(): Promise<Session | null> {
  try {
    return (await api.me()).data;
  } catch (error) {
    if (
      error instanceof ApiRequestError &&
      (error.status === 401 || error.status === 404)
    ) {
      return null;
    }
    throw error;
  }
}

export const sessionQuery = {
  queryKey: SESSION_KEY,
  queryFn: fetchSession,
  // A session that has expired should be noticed on the next navigation, not
  // five minutes later; it is one cheap request.
  staleTime: 30_000,
  retry: false,
};

export const capabilitiesQuery = {
  queryKey: CAPABILITIES_KEY,
  queryFn: async () => (await api.capabilities()).data,
  // What a deployment can do changes when it restarts, not between clicks.
  staleTime: 5 * 60_000,
  retry: false,
};

export type SessionState = {
  session: Session | null;
  /** Whether this deployment has accounts at all. */
  hasAuth: boolean;
  /** Whether reading anything at all needs a session here. */
  authRequired: boolean;
  /** True until both questions have been answered once. */
  isLoading: boolean;
  /** The API could not be reached, which is not the same as being signed out. */
  isUnreachable: boolean;
};

export function useSessionState(): SessionState {
  const capabilities = useQuery(capabilitiesQuery);
  const session = useQuery({
    ...sessionQuery,
    // Nothing to ask until the deployment says it has accounts.
    enabled: capabilities.data?.auth === true,
  });

  const hasAuth = capabilities.data?.auth ?? false;

  return {
    session: session.data ?? null,
    hasAuth,
    authRequired: capabilities.data?.auth_required ?? false,
    isLoading: capabilities.isLoading || (hasAuth && session.isLoading),
    isUnreachable: capabilities.isError,
  };
}

/** The signed-in user, where a page only needs to name them. */
export function useUser() {
  return useSessionState().session?.user ?? null;
}

/**
 * Forget everything the signed-out person was shown.
 *
 * Not only the session: a shelf and a set of saved queries were fetched as
 * them, and leaving those in the cache would show the next person at this
 * browser what the last one had open.
 */
export async function clearSession(queryClient: QueryClient): Promise<void> {
  queryClient.setQueryData(SESSION_KEY, null);
  await queryClient.resetQueries();
}

export function useSignOut(): () => Promise<void> {
  const queryClient = useQueryClient();
  return async () => {
    try {
      await api.logout();
    } finally {
      // Cleared even if the request failed: the intent was to sign out, and a
      // page that still shows the last person's name because the network
      // hiccuped is the wrong failure mode.
      await clearSession(queryClient);
    }
  };
}
