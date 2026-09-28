import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconArrowLeft, IconDatabase, IconExternalLink } from "@tabler/icons-react";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { BELOW_STICKY_HEADER, StickyHeader } from "~/components/sticky-header";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Dataset } from "~/lib/api";
import { describeSchedule } from "~/lib/cron";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { datasetLabel } from "~/lib/labels";
import { organizationsFrom } from "~/lib/organizations";

export const Route = createFileRoute("/organizations/$name")({
  component: OrganizationPage,
});

const datasetColumns: ColumnDef<Dataset>[] = [
  {
    accessorKey: "dataset_id",
    header: "Dataset",
    meta: { width: "w-96" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          <Link
            to="/datasets/$datasetId"
            params={{ datasetId: row.original.dataset_id }}
            className="underline-offset-4 hover:underline"
          >
            <ClampedText className="max-w-[24rem]">
              {datasetLabel(row.original)}
            </ClampedText>
          </Link>
        }
        secondary={row.original.source_id}
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
    id: "covers",
    header: "Covers",
    cell: ({ row }) => `${row.original.period_start} – ${row.original.period_end}`,
  },
  {
    accessorKey: "last_updated",
    header: "Updated",
    cell: ({ row }) =>
      row.original.last_updated ? (
        <StackedCell
          primary={formatRelative(row.original.last_updated)}
          secondary={formatDate(row.original.last_updated)}
        />
      ) : (
        "—"
      ),
  },
];

/**
 * One organization: the sources we collect from it, how each is collected, and
 * every dataset those sources have published.
 */
function OrganizationPage() {
  const { name } = Route.useParams();
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });

  const isLoading = sources.isLoading || datasets.isLoading;
  const organization = organizationsFrom(
    sources.data?.data ?? [],
    datasets.data?.data ?? [],
  ).find((entry) => entry.name === name);
  const websites = [
    ...new Set(
      (organization?.sources ?? [])
        .map((source) => source.base_url)
        .filter((url): url is string => Boolean(url)),
    ),
  ];
  const error = sources.error ?? datasets.error;

  return (
    <div className="space-y-5">
      <Link
        to="/organizations"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <IconArrowLeft className="size-4" />
        All organizations
      </Link>

      <StickyHeader
        heading={
          <PageHeader
            title={name}
            count={organization?.datasets.length}
            isLoading={isLoading}
            description={
              organization
                ? `${formatCount(organization.observations)} figures in ${formatCount(organization.series)} series, collected through ${formatCount(organization.sources.length)} ${organization.sources.length === 1 ? "source" : "sources"}${organization.period_start ? `, covering ${organization.period_start} to ${organization.period_end}` : ""}.`
                : undefined
            }
            actions={
              <div className="flex items-center gap-2">
                {websites[0] ? (
                  <a
                    href={websites[0]}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-sm underline underline-offset-4"
                  >
                    <IconExternalLink className="size-4" />
                    Website
                  </a>
                ) : null}
                <Button
                  variant="outline"
                  size="sm"
                  render={
                    <Link to="/datasets" search={{ organization: [name] }} />
                  }
                >
                  <IconDatabase className="size-4" />
                  Datasets
                </Button>
              </div>
            }
          />
        }
      />

      {error ? (
        <p className="rounded-lg bg-destructive/5 px-4 py-3 text-sm">
          {(error as Error).message}
        </p>
      ) : null}
      {!isLoading && !error && !organization ? (
        <p className="rounded-lg bg-muted/60 px-4 py-3 text-sm">
          No source in the registry belongs to an organization named “{name}”.
        </p>
      ) : null}

      <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className={`lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
          <h2 className="font-heading text-sm font-semibold tracking-tight">About</h2>
          <dl className="mt-3 space-y-3">
            <Fact label="Country" value={organization?.countries.join(", ") || undefined} />
            <Fact label="Licence" value={organization?.licenses.join("; ") || undefined} />
            <Fact
              label="Sources"
              value={organization ? formatCount(organization.sources.length) : undefined}
            />
            <Fact
              label="Datasets"
              value={organization ? formatCount(organization.datasets.length) : undefined}
            />
            <Fact
              label="Series"
              value={organization ? formatCount(organization.series) : undefined}
            />
            <Fact
              label="Figures"
              value={organization ? formatCount(organization.observations) : undefined}
            />
            <Fact
              label="Coverage"
              value={
                organization?.period_start
                  ? `${organization.period_start} – ${organization.period_end}`
                  : undefined
              }
            />
            <Fact
              label="Last updated"
              value={organization?.last_updated ? formatDate(organization.last_updated) : undefined}
              hint={formatRelative(organization?.last_updated)}
            />
          </dl>
        </aside>

        <div className="min-w-0 space-y-8">
          <section className="space-y-3">
            <h2 className="font-heading text-sm font-semibold tracking-tight">
              Sources
            </h2>
            <ul className="divide-y">
              {(organization?.sources ?? []).map((source) => (
                <li key={source.source_id} className="space-y-1 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{source.name}</span>
                    <code className="text-xs text-muted-foreground">{source.source_id}</code>
                    <Badge variant={source.active ? "secondary" : "outline"}>
                      {source.active ? "Scheduled" : "Paused"}
                    </Badge>
                    {source.base_url ? (
                      <a
                        href={source.base_url}
                        target="_blank"
                        rel="noreferrer"
                        className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                      >
                        <IconExternalLink className="size-3.5" />
                        {hostOf(source.base_url)}
                      </a>
                    ) : null}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {[
                      source.category,
                      source.source_type,
                      source.collection_method,
                      source.update_frequency,
                      describeSchedule(source.schedule) ?? "Manual only",
                      source.license,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                  {source.notes ? <p className="text-sm">{source.notes}</p> : null}
                </li>
              ))}
            </ul>
          </section>

          <section className="space-y-3">
            <h2 className="font-heading text-sm font-semibold tracking-tight">
              Datasets
            </h2>
            <DataTable
              columns={datasetColumns}
              data={organization?.datasets ?? []}
              isLoading={isLoading}
              loadingRows={4}
              emptyMessage="Nothing collected from this organization has been published yet."
              getRowId={(row) => row.dataset_id}
            />
          </section>
        </div>
      </div>
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function Fact({ label, value, hint }: { label: string; value?: string; hint?: string }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm">
        {value ?? <span className="text-muted-foreground">—</span>}
        {hint ? <span className="ml-1.5 text-xs text-muted-foreground">{hint}</span> : null}
      </dd>
    </div>
  );
}
