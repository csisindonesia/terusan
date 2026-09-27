import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api, type Indicator } from "~/lib/api";

/**
 * How many identifiers go in one request. Enough for any page of rows in one
 * go, few enough that the URL stays well inside what servers and proxies take.
 */
const CHUNK = 100;

/**
 * The series behind a set of identifiers, keyed by identifier.
 *
 * For pages that hold identifiers and want to show names: asking for these
 * few by `id` replaces downloading every series in the catalogue — tens of
 * thousands of rows — to look up a handful. An identifier the API does not
 * know is simply absent from the map, and callers fall back to showing it raw.
 */
export function useIndicatorsById(ids: readonly string[]): {
  byId: Map<string, Indicator>;
  isLoading: boolean;
} {
  // Sorted and de-duplicated so the same set of rows in another order is the
  // same cache entry rather than a second request.
  const wanted = useMemo(() => [...new Set(ids)].sort(), [ids]);

  const query = useQuery({
    queryKey: ["indicators", { id: wanted }],
    queryFn: async () => {
      const chunks: string[][] = [];
      for (let start = 0; start < wanted.length; start += CHUNK) {
        chunks.push(wanted.slice(start, start + CHUNK));
      }
      const pages = await Promise.all(
        chunks.map((chunk) => api.indicators({ id: chunk, limit: chunk.length })),
      );
      return pages.flatMap((page) => page.data);
    },
    enabled: wanted.length > 0,
  });

  const byId = useMemo(
    () =>
      new Map(
        (query.data ?? []).map((indicator) => [indicator.indicator_id, indicator]),
      ),
    [query.data],
  );

  return { byId, isLoading: query.isLoading && wanted.length > 0 };
}
