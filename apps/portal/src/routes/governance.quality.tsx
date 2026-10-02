/**
 * How clean each series is (program.md §45).
 *
 * Normalization refuses to guess. A figure printed as `-` or `x` stays a
 * status rather than becoming zero, a place it cannot resolve stays unresolved
 * with the text it was printed as, and a series another source says better is
 * marked as superseded rather than dropped. Each of those is the right call at
 * the time and a question for later — is the marker meaningful, should the
 * place be an alias, should the duplicate be retired — and this page is where
 * they are counted, series by series, so the later actually comes.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconDownload } from "@tabler/icons-react";
import { z } from "zod";

import { ChoiceFilter } from "~/components/choice-filter";
import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { SearchInput } from "~/components/search-input";
import { StatRow } from "~/components/stat-row";
import { StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type SeriesQuality } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatShare, statusLabel } from "~/lib/format";
import { datasetLabel, indicatorLabel } from "~/lib/labels";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  issue: listParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/governance/quality")({
  validateSearch: searchSchema,
  component: Quality,
});

const PAGE_SIZE = 50;

type Issue = "markers" | "unplaced" | "superseded" | "clean";

const ISSUES: { value: Issue; label: string }[] = [
  { value: "markers", label: "Figures without a number" },
  { value: "unplaced", label: "Unresolved places" },
  { value: "superseded", label: "Superseded" },
  { value: "clean", label: "Nothing to look at" },
];

/** Figures that are not a number: every status but `ok`. */
function markers(series: SeriesQuality): number {
  return series.figures - (series.statuses.ok ?? 0);
}

function issuesOf(series: SeriesQuality): Issue[] {
  const out: Issue[] = [];
  if (markers(series) > 0) out.push("markers");
  if (series.unplaced > 0) out.push("unplaced");
  if (series.superseded_by) out.push("superseded");
  if (!out.length) out.push("clean");
  return out;
}

/**
 * How much there is to look at in one series, for ordering: the share of its
 * figures that are a problem, so a small series that is mostly markers sorts
 * above a large one with a handful.
 */
function weight(series: SeriesQuality): number {
  if (!series.figures) return 0;
  return (
    (markers(series) + series.unplaced) / series.figures +
    (series.superseded_by ? 1 : 0)
  );
}

type Row = SeriesQuality & { datasetTitle?: string };

const columns: ColumnDef<Row>[] = [
  {
    accessorKey: "indicator_id",
    header: "Series",
    meta: { width: "w-96" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          <Link
            to="/indicators/$indicatorId"
            params={{ indicatorId: row.original.indicator_id }}
            className="underline-offset-4 hover:underline"
          >
            <ClampedText className="max-w-[22rem]">
              {indicatorLabel(row.original)}
            </ClampedText>
          </Link>
        }
        secondary={
          <ClampedText className="max-w-[22rem]">
            {[row.original.datasetTitle, row.original.source_id]
              .filter(Boolean)
              .join(" · ")}
          </ClampedText>
        }
      />
    ),
  },
  {
    accessorKey: "figures",
    header: "Figures",
    meta: { align: "right", width: "w-24" },
    cell: ({ row }) => (
      <span className="tabular-nums">{formatCount(row.original.figures)}</span>
    ),
  },
  {
    id: "markers",
    header: "Without a number",
    meta: { width: "w-56" },
    cell: ({ row }) => {
      const count = markers(row.original);
      if (!count) return <span className="text-muted-foreground">None</span>;
      // Each marker by name: a `missing` is the agency saying "no figure",
      // an `unparseable` is the parser failing, and they are fixed in
      // different places.
      const statuses = Object.entries(row.original.statuses)
        .filter(([status]) => status !== "ok")
        .sort((a, b) => b[1] - a[1]);
      return (
        <div className="space-y-1">
          <div className="tabular-nums">
            {formatCount(count)}{" "}
            <span className="text-xs text-muted-foreground">
              ({formatShare(count, row.original.figures)})
            </span>
          </div>
          <div className="flex flex-wrap gap-1">
            {statuses.map(([status, n]) => (
              <Badge
                key={status}
                variant={status === "unparseable" ? "destructive" : "outline"}
              >
                {statusLabel(status)} {formatCount(n)}
              </Badge>
            ))}
          </div>
        </div>
      );
    },
  },
  {
    accessorKey: "unplaced",
    header: "Unresolved places",
    meta: { width: "w-40" },
    cell: ({ row }) =>
      row.original.unplaced ? (
        <span className="tabular-nums">
          {formatCount(row.original.unplaced)}{" "}
          <span className="text-xs text-muted-foreground">
            ({formatShare(row.original.unplaced, row.original.figures)})
          </span>
        </span>
      ) : (
        <span className="text-muted-foreground">None</span>
      ),
  },
  {
    id: "superseded",
    header: "Superseded by",
    meta: { width: "w-64" },
    cell: ({ row }) => {
      const { superseded_by, superseded_by_name, match_share } = row.original;
      if (!superseded_by) return <span className="text-muted-foreground">—</span>;
      return (
        <StackedCell
          primary={
            <Link
              to="/indicators/$indicatorId"
              params={{ indicatorId: superseded_by }}
              className="underline-offset-4 hover:underline"
            >
              <ClampedText className="max-w-[15rem]">
                {superseded_by_name ?? superseded_by}
              </ClampedText>
            </Link>
          }
          secondary={
            match_share !== undefined
              ? `agrees on ${formatShare(match_share, 1)} of shared periods`
              : undefined
          }
        />
      );
    },
  },
];

const EXPORT_COLUMNS = [
  { key: "indicator_id" as const, header: "indicator_id" },
  { key: "name" as const, header: "name" },
  { key: "dataset_id" as const, header: "dataset_id" },
  { key: "source_id" as const, header: "source_id" },
  { key: "figures" as const, header: "figures" },
  { key: "without_number" as const, header: "without_number" },
  { key: "unplaced" as const, header: "unplaced" },
  { key: "superseded_by" as const, header: "superseded_by" },
];

function Quality() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const quality = useQuery({ queryKey: ["quality"], queryFn: () => api.quality() });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });

  const titles = new Map(
    (datasets.data?.data ?? []).map((dataset) => [
      dataset.dataset_id,
      datasetLabel(dataset),
    ]),
  );
  const all: Row[] = (quality.data?.data ?? []).map((series) => ({
    ...series,
    datasetTitle: series.dataset_id ? titles.get(series.dataset_id) : undefined,
  }));

  const issues = asTextList(search.issue);
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const rows = all
    .filter(
      (series) =>
        (!issues.length || issuesOf(series).some((issue) => issues.includes(issue))) &&
        (!needle ||
          [
            series.indicator_id,
            series.name,
            series.dataset_id,
            series.datasetTitle,
            series.source_id,
          ]
            .filter(Boolean)
            .some((text) => (text as string).toLowerCase().includes(needle))),
    )
    .sort((a, b) => weight(b) - weight(a) || b.figures - a.figures);

  const figures = all.reduce((sum, series) => sum + series.figures, 0);
  const withoutNumber = all.reduce((sum, series) => sum + markers(series), 0);
  const unplaced = all.reduce((sum, series) => sum + series.unplaced, 0);
  const superseded = all.filter((series) => series.superseded_by).length;

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function set(next: Partial<z.infer<typeof searchSchema>>) {
    navigate({ search: (prev) => ({ ...prev, ...next, page: 0 }) });
  }

  function exportRows() {
    const flat = rows.map((series) => ({ ...series, without_number: markers(series) }));
    downloadCsv(
      "data-quality.csv",
      toCsv(flat as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Data Quality"
            count={rows.length}
            isLoading={quality.isLoading}
            description="Every series, counted by what normalization would not guess at: figures printed as a marker instead of a number, places that did not resolve, and series another source says better."
            actions={
              <Button
                variant="outline"
                size="sm"
                disabled={!rows.length}
                onClick={exportRows}
              >
                <IconDownload className="size-4" />
                Export
              </Button>
            }
          />
        }
        filters={
          <TableToolbar
            filters={
              <>
                <ChoiceFilter
                  label="Issue"
                  options={ISSUES}
                  selected={issues}
                  onChange={(issue) => set({ issue })}
                />
                {issues.length || search.q ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8"
                    onClick={() => navigate({ search: {} })}
                  >
                    Clear all
                  </Button>
                ) : null}
              </>
            }
            search={
              <SearchInput
                value={asText(search.q)}
                placeholder="Search series, datasets and sources"
                onSearch={(q) => set({ q })}
              />
            }
          />
        }
      />

      <StatRow
        isLoading={quality.isLoading}
        stats={[
          {
            label: "Figures",
            value: formatCount(figures),
            note: `in ${formatCount(all.length)} series`,
          },
          {
            label: "Without a number",
            value: formatCount(withoutNumber),
            note: `${formatShare(withoutNumber, figures)} of figures`,
            attention: withoutNumber > 0,
            onSelect: () => set({ issue: "markers" }),
          },
          {
            label: "Unresolved places",
            value: formatCount(unplaced),
            note: `${formatShare(unplaced, figures)} of figures`,
            attention: unplaced > 0,
            onSelect: () => set({ issue: "unplaced" }),
          },
          {
            label: "Superseded series",
            value: formatCount(superseded),
            note: "another source says the same",
            onSelect: () => set({ issue: "superseded" }),
          },
        ]}
      />

      {quality.isError ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(quality.error as Error).message}
        </p>
      ) : null}

      <DataTable
        columns={columns}
        data={visible}
        isLoading={quality.isLoading}
        loadingRows={10}
        getRowId={(series) => series.indicator_id}
        emptyMessage={
          all.length === 0
            ? "No series are in Silver yet."
            : "No series match these filters."
        }
      />

      <TablePagination
        page={page}
        total={rows.length}
        pageSize={PAGE_SIZE}
        onPage={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        summary={
          rows.length ? (
            <>
              {formatCount(page * PAGE_SIZE + 1)}–
              {formatCount(Math.min((page + 1) * PAGE_SIZE, rows.length))} of{" "}
              {formatCount(rows.length)}
              {rows.length !== all.length
                ? ` (${formatCount(all.length)} total)`
                : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
