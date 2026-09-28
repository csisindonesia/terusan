import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconArrowRight,
  IconCopy,
  IconDatabase,
  IconDownload,
  IconExternalLink,
} from "@tabler/icons-react";
import { z } from "zod";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { SearchInput } from "~/components/search-input";
import { StickyHeader } from "~/components/sticky-header";
import { TableToolbar } from "~/components/table-toolbar";
import { Button } from "~/components/ui/button";
import { api } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { type Organization, organizationsFrom } from "~/lib/organizations";
import { asText, textParam } from "~/lib/search-params";

const searchSchema = z.object({ q: textParam });

export const Route = createFileRoute("/organizations/")({
  validateSearch: searchSchema,
  component: OrganizationsPage,
});

const columns: ColumnDef<Organization>[] = [
  {
    accessorKey: "name",
    header: "Organization",
    // Wrapped rather than clamped: the name is what the row is, and names here
    // run to "Kementerian Keuangan Direktorat Jenderal Perimbangan Keuangan".
    meta: { width: "w-80" },
    cell: ({ row }) => (
      <Link
        to="/organizations/$name"
        params={{ name: row.original.name }}
        className="block max-w-[20rem] font-medium whitespace-normal text-balance underline-offset-4 hover:underline"
      >
        {row.original.name}
      </Link>
    ),
  },
  {
    id: "id",
    header: "ID",
    // The registry's own keys — `big-inageoportal` — one per source. Clamped,
    // as is the country: an organization with five sources would otherwise
    // push the counts off the right edge.
    meta: { width: "w-48" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          <ClampedText className="max-w-[12rem] font-mono text-xs font-normal">
            {row.original.sources.map((source) => source.source_id).join(", ")}
          </ClampedText>
        }
        secondary={
          row.original.sources.length > 1
            ? `${formatCount(row.original.sources.length)} sources`
            : undefined
        }
      />
    ),
  },
  {
    id: "country",
    header: "Country",
    meta: { width: "w-32" },
    cell: ({ row }) =>
      row.original.countries.length ? (
        <ClampedText className="max-w-[8rem]">
          {row.original.countries.join(", ")}
        </ClampedText>
      ) : (
        "—"
      ),
  },
  {
    id: "datasets",
    header: "Datasets",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.datasets.length),
  },
  {
    accessorKey: "series",
    header: "Series",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.series),
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
    cell: ({ row }) =>
      row.original.period_start
        ? `${row.original.period_start} – ${row.original.period_end}`
        : "—",
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
  {
    id: "actions",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => {
      const organization = row.original;
      const website = organization.sources.find((source) => source.base_url)?.base_url;
      return (
        <RowActions
          bare
          actions={[
            {
              label: "Open",
              icon: IconArrowRight,
              onSelect: () => {
                window.location.href = `/organizations/${encodeURIComponent(organization.name)}`;
              },
            },
            {
              label: "View datasets",
              icon: IconDatabase,
              onSelect: organization.datasets.length
                ? () => {
                    window.location.href = `/datasets?organization=${encodeURIComponent(organization.name)}`;
                  }
                : undefined,
              hint: organization.datasets.length ? undefined : "Nothing published yet",
            },
            {
              label: "Visit website",
              icon: IconExternalLink,
              onSelect: website
                ? () => window.open(website, "_blank", "noopener")
                : undefined,
              hint: website ? undefined : "No public URL is recorded",
            },
            {
              label: "Copy name",
              icon: IconCopy,
              onSelect: () => void copyToClipboard(organization.name),
            },
            {
              label: "Copy ID",
              icon: IconCopy,
              onSelect: () =>
                void copyToClipboard(
                  organization.sources.map((source) => source.source_id).join(", "),
                ),
            },
          ]}
        />
      );
    },
  },
];

function OrganizationsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });

  const all = organizationsFrom(sources.data?.data ?? [], datasets.data?.data ?? []);
  const needle = asText(search.q)?.toLowerCase() ?? "";
  // Filtered in place: tens of organizations, not thousands.
  const rows = needle
    ? all.filter(
        (organization) =>
          organization.name.toLowerCase().includes(needle) ||
          organization.sources.some(
            (source) =>
              source.source_id.toLowerCase().includes(needle) ||
              source.name.toLowerCase().includes(needle),
          ),
      )
    : all;
  const isLoading = sources.isLoading || datasets.isLoading;
  const error = sources.error ?? datasets.error;

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Organizations"
            count={rows.length}
            isLoading={isLoading}
            description="Who the figures come from. Each organization is the agency, bank or ministry behind one or more of the sources we collect, with everything we hold from it."
            actions={
              <Button
                variant="outline"
                size="sm"
                disabled={!rows.length}
                onClick={() =>
                  downloadCsv(
                    "organizations.csv",
                    toCsv(
                      rows.map((organization) => ({
                        name: organization.name,
                        country: organization.countries,
                        sources: organization.sources.map((s) => s.source_id),
                        datasets: organization.datasets.length,
                        series: organization.series,
                        observations: organization.observations,
                        period_start: organization.period_start,
                        period_end: organization.period_end,
                        last_updated: organization.last_updated,
                      })),
                      [
                        { key: "name", header: "name" },
                        { key: "country", header: "country" },
                        { key: "sources", header: "sources" },
                        { key: "datasets", header: "datasets" },
                        { key: "series", header: "series" },
                        { key: "observations", header: "observations" },
                        { key: "period_start", header: "period_start" },
                        { key: "period_end", header: "period_end" },
                        { key: "last_updated", header: "last_updated" },
                      ],
                    ),
                  )
                }
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
              search.q ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8"
                  onClick={() => navigate({ search: {} })}
                >
                  Clear all
                </Button>
              ) : null
            }
            search={
              <SearchInput
                value={asText(search.q)}
                placeholder="Search organizations and sources"
                onSearch={(q) => navigate({ search: { q } })}
              />
            }
          />
        }
      />

      {error ? (
        <p className="rounded-lg bg-destructive/5 px-4 py-3 text-sm">
          {(error as Error).message}
        </p>
      ) : null}

      <DataTable
        columns={columns}
        data={rows}
        isLoading={isLoading}
        loadingRows={8}
        emptyMessage="No source registry has been published yet."
        getRowId={(row) => row.name}
      />
    </div>
  );
}
