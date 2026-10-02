/**
 * Every source in the registry, with when it is meant to run and how it last
 * did (program.md §16, §39).
 *
 * The registry says what should happen — a schedule, a collection method, a
 * rate limit — and the journal says what did. Neither alone answers the
 * question this page is for, which is "is every source still being fetched":
 * a source on a daily schedule whose last ingestion was a month ago looks
 * healthy in the registry and is simply absent from the journal. So the two
 * are joined, a row per source, and a source the journal has never heard of
 * says so rather than showing a blank.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconCopy,
  IconDownload,
  IconExternalLink,
  IconHistory,
  IconRefresh,
} from "@tabler/icons-react";
import { z } from "zod";

import { ChoiceFilter } from "~/components/choice-filter";
import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { PipelineHealthBadge } from "~/components/pipeline-health";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { RunSourceButton } from "~/components/run-source-button";
import { SearchInput } from "~/components/search-input";
import { StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Pipeline, type Source } from "~/lib/api";
import { describeSchedule } from "~/lib/cron";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatMoment, formatRelative } from "~/lib/format";
import { titleFromId } from "~/lib/labels";
import { HEALTH_LABELS, HEALTH_ORDER, bySource, healthOf } from "~/lib/pipelines";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  state: listParam,
  health: listParam,
  method: listParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/admin/ingestion")({
  validateSearch: searchSchema,
  component: Ingestion,
});

const PAGE_SIZE = 50;

/** A source and the newest ingestion of it, if there has been one. */
type Row = Source & { ingest?: Pipeline };

/**
 * The health filter's values. "Never run" is a state of the join rather than
 * of a pipeline, and the one an operator most needs to find.
 */
type RowHealth = keyof typeof HEALTH_LABELS | "never";

const ROW_HEALTH_LABELS: Record<RowHealth, string> = {
  ...HEALTH_LABELS,
  empty: "Nothing new",
  never: "Never run",
};

function rowHealth(row: Row): RowHealth {
  return row.ingest ? healthOf(row.ingest) : "never";
}

const ROW_HEALTH_ORDER: Record<RowHealth, number> = { ...HEALTH_ORDER, never: 1.5 };

/**
 * A registry code in words. `titleFromId` leaves `api` as "Api", deliberately —
 * in an indicator id it is as likely the Indonesian word for fire — but in a
 * collection method it is only ever the acronym.
 */
function codeLabel(value: string): string {
  return titleFromId(value).replace(/\bApi\b/g, "API");
}

const STATES = [
  { value: "active", label: "Active" },
  { value: "gated", label: "Gated" },
];

const columns: ColumnDef<Row>[] = [
  {
    accessorKey: "name",
    header: "Source",
    meta: { width: "w-72" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          <ClampedText className="max-w-[17rem]">{row.original.name}</ClampedText>
        }
        secondary={<span className="font-mono">{row.original.source_id}</span>}
      />
    ),
  },
  {
    accessorKey: "collection_method",
    header: "Collected by",
    meta: { width: "w-40" },
    cell: ({ row }) => (
      <StackedCell
        primary={codeLabel(row.original.collection_method)}
        secondary={codeLabel(row.original.source_type)}
      />
    ),
  },
  {
    accessorKey: "schedule",
    header: "Schedule",
    meta: { width: "w-48" },
    cell: ({ row }) => {
      if (!row.original.active) {
        // A gated source is registered so the gap is visible, with the
        // sentence saying what access it needs in its notes.
        return (
          <div className="space-y-1">
            <Badge variant="outline">Gated</Badge>
            {row.original.notes ? (
              <p
                className="line-clamp-2 max-w-[14rem] text-xs text-muted-foreground"
                title={row.original.notes}
              >
                {row.original.notes}
              </p>
            ) : null}
          </div>
        );
      }
      return (
        <StackedCell
          primary={describeSchedule(row.original.schedule) ?? "By hand"}
          secondary={titleFromId(row.original.update_frequency)}
        />
      );
    },
  },
  {
    id: "last",
    header: "Last ingestion",
    meta: { width: "w-56" },
    cell: ({ row }) => {
      const ingest = row.original.ingest;
      if (!ingest) {
        return <span className="text-muted-foreground">Never run</span>;
      }
      return (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <PipelineHealthBadge pipeline={ingest} />
            <span className="text-xs text-muted-foreground">
              {formatRelative(ingest.last_started_at)}
            </span>
          </div>
          {ingest.last_status === "failed" && ingest.last_error ? (
            <p
              className="line-clamp-2 max-w-[13rem] font-mono text-xs break-words text-destructive"
              title={ingest.last_error}
            >
              {ingest.last_error}
            </p>
          ) : null}
        </div>
      );
    },
  },
  {
    id: "succeeded",
    header: "Last success",
    meta: { width: "w-40" },
    cell: ({ row }) => {
      const at = row.original.ingest?.last_succeeded_at;
      if (!row.original.ingest) return <span className="text-muted-foreground">—</span>;
      return at ? (
        <StackedCell primary={formatMoment(at)} secondary={formatRelative(at)} />
      ) : (
        <span className="text-muted-foreground">Never</span>
      );
    },
  },
  {
    id: "run",
    header: "",
    enableSorting: false,
    meta: { align: "right", width: "w-28" },
    cell: ({ row }) => (
      <RunSourceButton sourceId={row.original.source_id} active={row.original.active} />
    ),
  },
  {
    id: "actions",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => <SourceActions source={row.original} />,
  },
];

function SourceActions({ source }: { source: Row }) {
  const navigate = useNavigate();
  return (
    <RowActions
      bare
      actions={[
        {
          label: "Run history",
          icon: IconHistory,
          onSelect: () =>
            void navigate({ to: "/jobs", search: { source: source.source_id } }),
        },
        {
          label: "Open the provider",
          icon: IconExternalLink,
          onSelect: source.base_url
            ? () => void window.open(source.base_url, "_blank", "noopener")
            : undefined,
          hint: source.base_url ? undefined : "No address is recorded",
        },
        {
          label: "Copy ingest command",
          icon: IconCopy,
          onSelect: () =>
            void copyToClipboard(
              `cd pipelines && uv run terusan sources run ${source.source_id}`,
            ),
        },
      ]}
    />
  );
}

const EXPORT_COLUMNS = [
  { key: "source_id" as const, header: "source_id" },
  { key: "name" as const, header: "name" },
  { key: "organization" as const, header: "organization" },
  { key: "collection_method" as const, header: "collection_method" },
  { key: "source_type" as const, header: "source_type" },
  { key: "schedule" as const, header: "schedule" },
  { key: "active" as const, header: "active" },
  { key: "last_status" as const, header: "last_status" },
  { key: "last_started_at" as const, header: "last_started_at" },
  { key: "last_succeeded_at" as const, header: "last_succeeded_at" },
  { key: "last_error" as const, header: "last_error" },
];

function Ingestion() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });
  const ingests = useQuery({
    queryKey: ["pipelines", "ingest"],
    queryFn: () => api.pipelines({ kind: ["ingest"] }),
  });

  const latest = bySource(ingests.data?.data ?? []);
  const all: Row[] = (sources.data?.data ?? []).map((source) => ({
    ...source,
    ingest: latest.get(source.source_id),
  }));

  const states = asTextList(search.state);
  const healths = asTextList(search.health);
  const methods = asTextList(search.method);
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const methodOptions = [...new Set(all.map((row) => row.collection_method))]
    .sort()
    .map((value) => ({ value, label: codeLabel(value) }));
  const healthOptions = (Object.keys(ROW_HEALTH_LABELS) as RowHealth[]).map(
    (value) => ({
      value,
      label: ROW_HEALTH_LABELS[value],
    }),
  );

  const rows = all
    .filter(
      (row) =>
        (!states.length || states.includes(row.active ? "active" : "gated")) &&
        (!healths.length || healths.includes(rowHealth(row))) &&
        (!methods.length || methods.includes(row.collection_method)) &&
        (!needle ||
          [row.source_id, row.name, row.organization, ...(row.tags ?? [])]
            .filter(Boolean)
            .some((text) => (text as string).toLowerCase().includes(needle))),
    )
    // Broken first, then never run, then by name. Gated sources sink: they
    // cannot run, so there is nothing to do about them from here.
    .sort(
      (a, b) =>
        Number(!a.active) - Number(!b.active) ||
        ROW_HEALTH_ORDER[rowHealth(a)] - ROW_HEALTH_ORDER[rowHealth(b)] ||
        a.name.localeCompare(b.name),
    );

  const failing = all.filter((row) => rowHealth(row) === "failing").length;
  const never = all.filter((row) => row.active && !row.ingest).length;

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const isLoading = sources.isLoading || ingests.isLoading;
  const error = sources.error ?? ingests.error;

  function set(next: Partial<z.infer<typeof searchSchema>>) {
    navigate({ search: (prev) => ({ ...prev, ...next, page: 0 }) });
  }

  function exportRows() {
    const flat = rows.map((row) => ({
      ...row,
      last_status: row.ingest?.last_status,
      last_started_at: row.ingest?.last_started_at,
      last_succeeded_at: row.ingest?.last_succeeded_at,
      last_error: row.ingest?.last_error,
    }));
    downloadCsv(
      "ingestion.csv",
      toCsv(flat as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Ingestion"
            count={rows.length}
            isLoading={isLoading}
            description={
              <>
                Every source in the registry, when it is meant to be fetched, and how
                the last fetch went.
                {isLoading ? null : (
                  <>
                    {" "}
                    {formatCount(failing)} failing, {formatCount(never)} active never
                    run. Runs started here appear on{" "}
                    <Link to="/jobs" className="underline underline-offset-4">
                      Jobs
                    </Link>
                    .
                  </>
                )}
              </>
            }
            actions={
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    void sources.refetch();
                    void ingests.refetch();
                  }}
                  disabled={sources.isFetching || ingests.isFetching}
                >
                  <IconRefresh className="size-4" />
                  {sources.isFetching || ingests.isFetching ? "Refreshing" : "Refresh"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!rows.length}
                  onClick={exportRows}
                >
                  <IconDownload className="size-4" />
                  Export
                </Button>
              </>
            }
          />
        }
        filters={
          <TableToolbar
            filters={
              <>
                <ChoiceFilter
                  label="State"
                  options={STATES}
                  selected={states}
                  onChange={(state) => set({ state })}
                />
                <ChoiceFilter
                  label="Last ingestion"
                  options={healthOptions}
                  selected={healths}
                  onChange={(health) => set({ health })}
                />
                <ChoiceFilter
                  label="Collected by"
                  options={methodOptions}
                  selected={methods}
                  onChange={(method) => set({ method })}
                />
                {states.length || healths.length || methods.length || search.q ? (
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
                placeholder="Search sources, publishers and tags"
                onSearch={(q) => set({ q })}
              />
            }
          />
        }
      />

      {error ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(error as Error).message}
        </p>
      ) : null}

      <DataTable
        columns={columns}
        data={visible}
        isLoading={isLoading}
        loadingRows={10}
        getRowId={(row) => row.source_id}
        emptyMessage={
          all.length === 0
            ? "No source registry is published yet. Run `terusan silver dimensions` to publish it."
            : "No sources match these filters."
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
