import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { IconLayoutDashboard, IconPencil, IconPlus } from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { PageHeader } from "~/components/page-header";
import { SearchInput } from "~/components/search-input";
import { ShowcaseCard } from "~/components/showcase-card";
import { ShowcaseForm } from "~/components/showcase-form";
import { StickyHeader } from "~/components/sticky-header";
import { TableToolbar } from "~/components/table-toolbar";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { api, type ShowcaseEntry } from "~/lib/api";
import { formatCount } from "~/lib/format";
import { asText, textParam } from "~/lib/search-params";
import { capabilitiesQuery, useSessionState } from "~/lib/session";
import { article as builtArticle } from "~/articles";
import { SHOWCASE_KINDS, isInternal, kindPlural } from "~/lib/showcase";
import { formatRelative } from "~/lib/format";

const searchSchema = z.object({ q: textParam, kind: textParam });

export const Route = createFileRoute("/portal/")({
  validateSearch: searchSchema,
  component: PortalPage,
});

/**
 * Portal: the dashboards, articles and reports built on this warehouse's data.
 *
 * The rest of Discover says what the warehouse holds. This says what has been
 * made from it, which is the question a funder, a new researcher or the agency
 * that published the figures asks first — and the one no dataset page can
 * answer, because the work lives on other people's sites.
 *
 * Filtered in place: tens of entries, not thousands.
 */
function PortalPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const queryClient = useQueryClient();
  const capabilities = useQuery(capabilitiesQuery);
  const { session } = useSessionState();
  const isAdmin = session?.user.role === "admin";
  const available = capabilities.data?.showcase ?? false;

  const entries = useQuery({
    queryKey: ["showcase"],
    queryFn: () => api.showcase(),
    enabled: available,
  });
  const [editing, setEditing] = useState<ShowcaseEntry | undefined>();
  const [formOpen, setFormOpen] = useState(false);

  // Drafts are not on the Portal yet; an administrator sees them here.
  const written = useQuery({
    queryKey: ["articles"],
    queryFn: () => api.articles(),
    enabled: isAdmin && (capabilities.data?.showcase ?? false),
  });
  const drafts = (written.data?.data ?? []).filter((a) => a.status === "draft");

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteShowcase(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["showcase"] }),
  });

  const all = entries.data?.data ?? [];
  const kind = asText(search.kind);
  const needle = asText(search.q)?.toLowerCase() ?? "";
  const matching = needle
    ? all.filter((entry) =>
        [
          entry.title,
          entry.publisher,
          entry.description,
          entry.url,
          ...entry.datasets.map((d) => d.title ?? d.dataset_id),
        ].some((text) => text?.toLowerCase().includes(needle)),
      )
    : all;
  const rows = kind ? matching.filter((entry) => entry.kind === kind) : matching;
  const isLoading = capabilities.isLoading || entries.isLoading;

  const openForm = (entry?: ShowcaseEntry) => {
    // An article written in the portal is edited as an article: its entry is
    // kept in step with it, and changing the entry alone would be undone.
    const slug =
      entry && isInternal(entry.url) ? entry.url.replace(/^\/portal\//, "") : "";
    if (slug && !builtArticle(slug)) {
      void navigate({ to: "/portal/edit/$slug", params: { slug } });
      return;
    }
    setEditing(entry);
    setFormOpen(true);
  };

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Portal"
            count={available ? rows.length : undefined}
            isLoading={isLoading}
            description="Dashboards, articles and reports built on the data in this warehouse. Each links to the work itself and to the datasets it draws on."
            actions={
              isAdmin && available ? (
                <>
                  <Button size="sm" variant="outline" onClick={() => openForm()}>
                    <IconPlus className="size-4" />
                    Add a link
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    render={
                      <Link
                        to="/portal/edit/$slug"
                        params={{ slug: "new" }}
                        search={{ layout: "dashboard" }}
                      />
                    }
                  >
                    <IconLayoutDashboard className="size-4" />
                    Build a dashboard
                  </Button>
                  <Button
                    size="sm"
                    render={<Link to="/portal/edit/$slug" params={{ slug: "new" }} />}
                  >
                    <IconPencil className="size-4" />
                    Write an article
                  </Button>
                </>
              ) : null
            }
          />
        }
        filters={
          available ? (
            <TableToolbar
              filters={
                <div className="flex flex-wrap items-center gap-1">
                  {/* Counted over what the search left, so a number is what
                      clicking it shows. */}
                  <KindButton
                    label="All"
                    count={matching.length}
                    active={!kind}
                    onClick={() =>
                      navigate({ search: (prev) => ({ ...prev, kind: undefined }) })
                    }
                  />
                  {SHOWCASE_KINDS.map((value) => (
                    <KindButton
                      key={value}
                      label={kindPlural(value)}
                      count={matching.filter((entry) => entry.kind === value).length}
                      active={kind === value}
                      onClick={() =>
                        navigate({ search: (prev) => ({ ...prev, kind: value }) })
                      }
                    />
                  ))}
                </div>
              }
              search={
                <SearchInput
                  value={asText(search.q)}
                  placeholder="Search titles, publishers and datasets"
                  onSearch={(q) => navigate({ search: (prev) => ({ ...prev, q }) })}
                />
              }
            />
          ) : null
        }
      />

      {entries.error || remove.error ? (
        <p className="rounded-lg bg-destructive/5 px-4 py-3 text-sm">
          {((entries.error ?? remove.error) as Error).message}
        </p>
      ) : null}

      {drafts.length ? (
        <section className="space-y-2 rounded-lg border border-dashed p-4">
          <h2 className="text-sm font-medium">Drafts</h2>
          <ul className="space-y-1 text-sm">
            {drafts.map((d) => (
              <li
                key={d.id}
                className="flex flex-wrap items-baseline justify-between gap-2"
              >
                <Link
                  to="/portal/edit/$slug"
                  params={{ slug: d.slug }}
                  className="font-medium underline-offset-4 hover:underline"
                >
                  {d.title}
                </Link>
                <span className="text-xs text-muted-foreground">
                  changed {formatRelative(d.updated_at)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!capabilities.isLoading && !available ? (
        <Empty>
          This deployment keeps no list of work built on its data. It needs an
          application database (APP_DB) where the API runs.
        </Empty>
      ) : isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }, (_, n) => (
            <Skeleton key={n} className="h-48 rounded-lg" />
          ))}
        </div>
      ) : rows.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((entry) => (
            <ShowcaseCard
              key={entry.id}
              entry={entry}
              onEdit={isAdmin ? () => openForm(entry) : undefined}
              onDelete={
                isAdmin
                  ? () => {
                      if (window.confirm(`Remove “${entry.title}” from the Portal?`)) {
                        remove.mutate(entry.id);
                      }
                    }
                  : undefined
              }
            />
          ))}
        </div>
      ) : all.length ? (
        <Empty>
          Nothing listed matches these filters.{" "}
          <button
            type="button"
            className="underline underline-offset-4"
            onClick={() => navigate({ search: {} })}
          >
            Clear them
          </button>
          .
        </Empty>
      ) : (
        <Empty>
          Nothing is listed yet.
          {isAdmin
            ? " Add the first dashboard or article that uses this data."
            : " Know of a dashboard or article that uses this data? Tell us through the contact page."}
        </Empty>
      )}

      <ShowcaseForm open={formOpen} onOpenChange={setFormOpen} entry={editing} />
    </div>
  );
}

function KindButton({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant={active ? "secondary" : "ghost"}
      size="sm"
      className="h-8"
      aria-pressed={active}
      onClick={onClick}
    >
      {label}
      <span className="text-muted-foreground tabular-nums">{formatCount(count)}</span>
    </Button>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-sm border border-dashed px-4 py-12 text-center text-sm text-muted-foreground">
      {children}
    </p>
  );
}
