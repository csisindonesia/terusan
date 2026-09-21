import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { setupRouterSsrQueryIntegration } from "@tanstack/react-router-ssr-query";

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

  const router = createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: "intent",
    scrollRestoration: true,
  });

  // What carries the server's query cache to the browser: the server streams
  // each resolved query down with the HTML, and this reads them back into the
  // client's cache so a page does not fetch again what it was just rendered
  // from.
  //
  // `@tanstack/react-router-ssr-query`, not the `react-router-with-query` this
  // used to call. That package stopped at 1.130 while the router went on to
  // 1.170, and the two no longer agreed on the shape of the stream: the client
  // failed to read it ("Cannot read properties of undefined (reading
  // 'mutations')") and every query it had streamed hydrated as a half-built
  // object. A page that then read one — `capabilities.data.run_pipelines` —
  // threw, and the route rendered as "Something went wrong".
  setupRouterSsrQueryIntegration({ router, queryClient });

  return router;
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof getRouter>;
  }
}
