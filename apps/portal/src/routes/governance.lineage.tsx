/**
 * Where a dataset's figures came from (program.md §5–8, §39).
 *
 * Every figure in Silver carries the document it was read out of, and every
 * document the source that fetched it and the content hash it was stored
 * under. Followed backwards, that is a dataset's lineage: the source that
 * fetched the files, the files as they sit in RAW, the parser versions that
 * read them into Bronze, the series normalization published, and the
 * pipelines that did each step. This page draws that path for one dataset,
 * with the health of each step on it, so "why is this figure wrong" starts at
 * the step that broke rather than at the top.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconArrowLeft, IconArrowRight, IconCopy } from "@tabler/icons-react";
import { z } from "zod";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { PipelineHealthBadge } from "~/components/pipeline-health";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { SearchInput } from "~/components/search-input";
import { Section } from "~/components/section";
import { StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { api, type Dataset, type Lineage, type Pipeline } from "~/lib/api";
import { describeSchedule } from "~/lib/cron";
import { formatCount, formatDate, formatMoment, formatRelative } from "~/lib/format";
import { datasetLabel, indicatorLabel, titleFromId } from "~/lib/labels";
import { healthOf } from "~/lib/pipelines";
import { asText, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  dataset: textParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/governance/lineage")({
  validateSearch: searchSchema,
  component: LineagePage,
});

function LineagePage() {
  const search = Route.useSearch();
  const dataset = asText(search.dataset);
  return dataset ? <DatasetLineage datasetId={dataset} /> : <DatasetPicker />;
}

// ------------------------------------------------------------------ picker

const PAGE_SIZE = 50;

const pickerColumns: ColumnDef<Dataset>[] = [
  {
    accessorKey: "dataset_id",
    header: "Dataset",
    meta: { width: "w-96" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          <Link
            to="/governance/lineage"
            search={{ dataset: row.original.dataset_id }}
            className="underline-offset-4 hover:underline"
          >
            <ClampedText className="max-w-[22rem]">
              {datasetLabel(row.original)}
            </ClampedText>
          </Link>
        }
        secondary={
          <span className="font-mono">
            {row.original.slug ?? row.original.dataset_id}
          </span>
        }
      />
    ),
  },
  {
    accessorKey: "source_id",
    header: "Source",
    cell: ({ row }) => (
      <StackedCell
        primary={row.original.source_name ?? row.original.source_id}
        secondary={<span className="font-mono">{row.original.source_id}</span>}
      />
    ),
  },
  {
    id: "series",
    header: "Series",
    meta: { align: "right" },
    cell: ({ row }) =>
      formatCount(row.original.series ?? row.original.indicators.length),
  },
  {
    accessorKey: "observations",
    header: "Figures",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.observations),
  },
  {
    id: "trace",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => (
      <Button
        variant="outline"
        size="sm"
        nativeButton={false}
        render={
          <Link
            to="/governance/lineage"
            search={{ dataset: row.original.dataset_id }}
          />
        }
      >
        Trace
        <IconArrowRight className="size-4" />
      </Button>
    ),
  },
];

function DatasetPicker() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });

  const all = datasets.data?.data ?? [];
  const needle = asText(search.q)?.toLowerCase() ?? "";
  const rows = all
    .filter(
      (dataset) =>
        !needle ||
        [dataset.dataset_id, dataset.slug, datasetLabel(dataset), dataset.source_id]
          .filter(Boolean)
          .some((text) => (text as string).toLowerCase().includes(needle)),
    )
    .sort((a, b) => datasetLabel(a).localeCompare(datasetLabel(b)));

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Lineage"
            count={rows.length}
            isLoading={datasets.isLoading}
            description="Choose a dataset to trace its figures back through Silver, Bronze and RAW to the source that fetched them, with the pipeline that did each step."
          />
        }
        filters={
          <TableToolbar
            search={
              <SearchInput
                value={asText(search.q)}
                placeholder="Search datasets and sources"
                onSearch={(q) =>
                  navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })
                }
              />
            }
          />
        }
      />
      <DataTable
        columns={pickerColumns}
        data={rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)}
        isLoading={datasets.isLoading}
        getRowId={(dataset) => dataset.dataset_id}
        emptyMessage={
          all.length ? "No datasets match." : "No datasets are in Silver yet."
        }
      />
      <TablePagination
        page={page}
        total={rows.length}
        pageSize={PAGE_SIZE}
        onPage={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
      />
    </div>
  );
}

// ----------------------------------------------------------------- lineage

function DatasetLineage({ datasetId }: { datasetId: string }) {
  const lineage = useQuery({
    queryKey: ["lineage", datasetId],
    queryFn: () => api.lineage(datasetId),
    retry: false,
  });
  const dataset = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => api.dataset(datasetId),
    retry: false,
  });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });

  const data = lineage.data?.data;
  const record = dataset.data?.data;
  const source = (sources.data?.data ?? []).find(
    (candidate) => candidate.source_id === data?.source_id,
  );
  const title = record ? datasetLabel(record) : titleFromId(datasetId);

  const back = (
    <Button
      variant="outline"
      size="sm"
      nativeButton={false}
      render={<Link to="/governance/lineage" />}
    >
      <IconArrowLeft className="size-4" />
      All datasets
    </Button>
  );

  if (lineage.isError) {
    return (
      <div className="space-y-5">
        <PageHeader title="Lineage" actions={back} />
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(lineage.error as Error).message}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title={title}
        description={
          <>
            Lineage of{" "}
            <Link
              to="/datasets/$datasetId"
              params={{ datasetId }}
              className="underline underline-offset-4"
            >
              this dataset
            </Link>
            : where its figures were fetched, stored, read and published, and the
            pipeline that did each step.
          </>
        }
        actions={back}
      />

      {data ? (
        <LineageFlow
          lineage={data}
          sourceName={source?.name}
          schedule={source?.schedule}
          title={title}
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-5">
          {Array.from({ length: 5 }, (_, n) => (
            <Skeleton key={n} className="h-36 rounded-lg" />
          ))}
        </div>
      )}

      {data ? (
        <>
          <Section
            title="Pipelines"
            description="The ingestion of its source, the extraction every source shares, and the normalizations that write its series."
          >
            <DataTable
              columns={pipelineColumns}
              data={data.pipelines}
              getRowId={(pipeline) => `${pipeline.kind}:${pipeline.pipeline}`}
              emptyMessage="The journal has recorded no run for this dataset's source."
            />
          </Section>

          <Section
            title="Series"
            description="What normalization published into this dataset, largest first."
          >
            <div className="flex flex-wrap gap-2">
              {data.series.map((series) => (
                <Link
                  key={series.indicator_id}
                  to="/indicators/$indicatorId"
                  params={{ indicatorId: series.indicator_id }}
                  className="rounded-full border px-3 py-1 text-xs hover:bg-muted"
                >
                  <span title={series.indicator_id}>{indicatorLabel(series)}</span>{" "}
                  <span className="text-muted-foreground tabular-nums">
                    {formatCount(series.figures)}
                  </span>
                </Link>
              ))}
            </div>
          </Section>

          <Section
            title="Documents"
            description={
              data.documents_total > data.documents.length
                ? `The ${formatCount(data.documents.length)} most recently retrieved of ${formatCount(data.documents_total)} documents the figures were read out of.`
                : "The documents the figures were read out of, most recently retrieved first."
            }
          >
            <DataTable
              columns={documentColumns}
              data={data.documents}
              getRowId={(document) => document.document_id}
              emptyMessage="No figure in this dataset records the document it came from."
            />
          </Section>

          <Section
            title="Extractions"
            description="Every parser version that has read this source's documents into Bronze. Extraction keeps the old reading beside the new, so a source re-read many times holds every version."
          >
            {data.extractions.length ? (
              <ul className="divide-y rounded-lg border text-sm">
                {data.extractions.map((extraction, index) => (
                  <li
                    key={extraction.parser_version}
                    className="flex items-center justify-between gap-3 px-4 py-2"
                  >
                    <span className="flex items-center gap-2">
                      <span className="font-mono">
                        parser v{extraction.parser_version}
                      </span>
                      {index === 0 ? <Badge variant="secondary">Newest</Badge> : null}
                    </span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {formatCount(extraction.documents)} document
                      {extraction.documents === 1 ? "" : "s"} ·{" "}
                      {formatCount(extraction.records)} records
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                No Bronze records are recorded for this source.
              </p>
            )}
          </Section>
        </>
      ) : null}
    </div>
  );
}

/** The five steps from source to dataset, each with its health. */
function LineageFlow({
  lineage,
  sourceName,
  schedule,
  title,
}: {
  lineage: Lineage;
  sourceName?: string;
  schedule?: string;
  title: string;
}) {
  const ingest = lineage.pipelines.find((pipeline) => pipeline.kind === "ingest");
  const extract = lineage.pipelines.find((pipeline) => pipeline.kind === "extract");
  const normalizations = lineage.pipelines.filter(
    (pipeline) => pipeline.kind === "normalize",
  );
  const failing = normalizations.filter(
    (pipeline) => healthOf(pipeline) === "failing",
  ).length;
  const figures = lineage.series.reduce((sum, series) => sum + series.figures, 0);
  const newest = lineage.extractions[0];

  const steps: {
    layer: string;
    title: React.ReactNode;
    detail: React.ReactNode;
    health?: React.ReactNode;
  }[] = [
    {
      layer: "Source",
      title: sourceName ?? lineage.source_id ?? "Not recorded",
      detail: (
        <>
          <span className="font-mono">{lineage.source_id}</span>
          <br />
          {describeSchedule(schedule) ?? "Run by hand"}
        </>
      ),
      health: ingest ? (
        <PipelineHealthBadge pipeline={ingest} />
      ) : (
        <Badge variant="outline">Never run</Badge>
      ),
    },
    {
      layer: "RAW",
      title: `${formatCount(lineage.documents_total)} document${lineage.documents_total === 1 ? "" : "s"}`,
      detail: lineage.documents[0]?.retrieved_at
        ? `newest retrieved ${formatRelative(lineage.documents[0].retrieved_at)}`
        : "retrieval not recorded",
    },
    {
      layer: "Bronze",
      title: newest ? `parser v${newest.parser_version}` : "Not extracted",
      detail: newest
        ? `${formatCount(newest.documents)} document${newest.documents === 1 ? "" : "s"} at the newest of ${formatCount(lineage.extractions.length)} version${lineage.extractions.length === 1 ? "" : "s"}`
        : "no Bronze records for this source",
      health: extract ? <PipelineHealthBadge pipeline={extract} /> : undefined,
    },
    {
      layer: "Silver",
      title: `${formatCount(lineage.series.length)} series`,
      detail: `${formatCount(figures)} figures`,
      health: normalizations.length ? (
        failing ? (
          <Badge variant="destructive">
            {formatCount(failing)} of {formatCount(normalizations.length)} failing
          </Badge>
        ) : (
          <Badge variant="secondary">
            {formatCount(normalizations.length)} normalization
            {normalizations.length === 1 ? "" : "s"} succeeded
          </Badge>
        )
      ) : (
        <Badge variant="outline">No runs recorded</Badge>
      ),
    },
    {
      layer: "Dataset",
      title: (
        <Link
          to="/datasets/$datasetId"
          params={{ datasetId: lineage.dataset_id }}
          className="underline-offset-4 hover:underline"
        >
          {title}
        </Link>
      ),
      detail: <span className="font-mono">{lineage.slug ?? lineage.dataset_id}</span>,
    },
  ];

  return (
    <ol className="grid gap-3 md:grid-cols-5">
      {steps.map((step, index) => (
        <li
          key={step.layer}
          className="relative flex flex-col gap-2 rounded-lg border p-3"
        >
          <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {step.layer}
          </div>
          <div className="text-sm font-semibold break-words">{step.title}</div>
          <div className="text-xs break-words text-muted-foreground">{step.detail}</div>
          {step.health ? <div className="mt-auto pt-1">{step.health}</div> : null}
          {index < steps.length - 1 ? (
            <IconArrowRight
              aria-hidden
              className="absolute top-1/2 -right-3 z-10 hidden size-4 -translate-y-1/2 rounded-full bg-background text-muted-foreground md:block"
            />
          ) : null}
        </li>
      ))}
    </ol>
  );
}

const pipelineColumns: ColumnDef<Pipeline>[] = [
  {
    accessorKey: "pipeline",
    header: "Pipeline",
    cell: ({ row }) => (
      <StackedCell
        primary={
          <Link
            to="/jobs"
            search={{ pipeline: row.original.pipeline }}
            className="font-mono text-xs underline-offset-4 hover:underline"
          >
            {row.original.pipeline}
          </Link>
        }
        secondary={row.original.kind}
      />
    ),
  },
  {
    id: "health",
    header: "Last run",
    cell: ({ row }) => <PipelineHealthBadge pipeline={row.original} />,
  },
  {
    accessorKey: "last_started_at",
    header: "Ran",
    cell: ({ row }) => (
      <StackedCell
        primary={formatMoment(row.original.last_started_at)}
        secondary={formatRelative(row.original.last_started_at)}
      />
    ),
  },
  {
    accessorKey: "last_records_out",
    header: "Wrote",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.last_records_out),
  },
];

type LineageDocument = Lineage["documents"][number];

const documentColumns: ColumnDef<LineageDocument>[] = [
  {
    accessorKey: "document_id",
    header: "Document",
    meta: { width: "w-96" },
    cell: ({ row }) => (
      <StackedCell
        // Not linked to the document page: that catalogues the documents
        // people read, and the files a statistical series is read out of are
        // not among them. The address it was fetched from is what there is.
        primary={<span className="font-mono text-xs">{row.original.document_id}</span>}
        secondary={
          row.original.source_url ? (
            <a
              href={row.original.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline-offset-4 hover:underline"
            >
              <ClampedText className="max-w-[22rem]">
                {row.original.source_url}
              </ClampedText>
            </a>
          ) : undefined
        }
      />
    ),
  },
  {
    accessorKey: "retrieved_at",
    header: "Retrieved",
    cell: ({ row }) => (
      <StackedCell
        primary={formatDate(row.original.retrieved_at)}
        secondary={formatRelative(row.original.retrieved_at)}
      />
    ),
  },
  {
    accessorKey: "figures",
    header: "Figures",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.figures),
  },
  {
    id: "series",
    header: "Feeds",
    cell: ({ row }) => (
      <span className="text-xs text-muted-foreground">
        {formatCount(row.original.series.length)} series
      </span>
    ),
  },
  {
    id: "actions",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => (
      <RowActions
        bare
        actions={[
          {
            label: "Copy RAW path",
            icon: IconCopy,
            onSelect: row.original.raw_path
              ? () => void copyToClipboard(row.original.raw_path as string)
              : undefined,
            hint: row.original.raw_path ? undefined : "No path is recorded",
          },
          {
            label: "Copy content hash",
            icon: IconCopy,
            onSelect: row.original.content_hash
              ? () => void copyToClipboard(row.original.content_hash as string)
              : undefined,
            hint: row.original.content_hash ? undefined : "No hash is recorded",
          },
        ]}
      />
    ),
  },
];
