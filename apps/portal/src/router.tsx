import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { routerWithQueryClient } from "@tanstack/react-router-with-query";

import { routeTree } from "./routeTree.gen";

// TanStack Start imports this entry as `#tanstack-router-entry` and calls
// `getRouter()` on both the server and the client, so the name is fixed.
export function getRouter() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        // The lake changes when a pipeline runs, not between clicks. A minute
        // of staleness costs nothing and saves a request per navigation.
        staleTime: 60_000,
        retry: 1,
      },
    },
  });

  return routerWithQueryClient(
    createRouter({
      routeTree,
      context: { queryClient },
      defaultPreload: "intent",
      scrollRestoration: true,
    }),
    queryClient,
  );
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof getRouter>;
  }
}
