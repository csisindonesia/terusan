import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconAlertTriangle,
  IconDownload,
  IconShieldLock,
  IconUserPlus,
} from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { DataTable, StackedCell } from "~/components/data-table";
import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { PageHeader } from "~/components/page-header";
import { SearchInput } from "~/components/search-input";
import { StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { UserActions } from "~/components/user-actions";
import { SecretDialog, UserFormDialog, errorMessage } from "~/components/user-dialogs";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type User } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";
import { useSessionState } from "~/lib/session";
import {
  ROLES,
  STATE_LABELS,
  displayName,
  lastSeen,
  roleLabel,
  userState,
  type UserState,
} from "~/lib/users";

/**
 * Everyone who can sign in, and everyone waiting to.
 *
 * Laid out like the Datasets table, because it is used the same way: find a
 * row, open it, or act on it from its three dots. Registrations sort to the
 * top — they are the rows somebody is waiting on — and the banner above the
 * table says how many there are, so an admin arriving for another reason
 * still sees them.
 */

const searchSchema = z.object({
  role: listParam,
  state: listParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/users/")({
  validateSearch: searchSchema,
  component: Users,
});

const PAGE_SIZE = 50;

const STATE_ORDER: Record<UserState, number> = {
  pending: 0,
  active: 1,
  expired: 2,
  disabled: 3,
};

function RoleBadge({ user }: { user: User }) {
  const role = roleLabel(user.role);
  return (
    <div className="space-y-1">
      <Badge variant={user.role === "admin" ? "default" : "secondary"}>{role}</Badge>
      {user.role === "guest" && user.access_expires_at ? (
        <div className="text-xs text-muted-foreground">
          until {formatDate(user.access_expires_at)}
        </div>
      ) : null}
    </div>
  );
}

function StateBadge({ state }: { state: UserState }) {
  if (state === "active") return null;
  return (
    <Badge
      variant={state === "pending" ? "outline" : "destructive"}
      className={state === "pending" ? "border-amber-500/50 text-amber-700 dark:text-amber-400" : ""}
    >
      {STATE_LABELS[state]}
    </Badge>
  );
}

function columns(selfId?: string): ColumnDef<User>[] {
  return [
    {
      id: "name",
      header: "Name",
      meta: { width: "w-64" },
      cell: ({ row }) => (
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to="/users/$userId"
            params={{ userId: row.original.id }}
            className="font-medium underline-offset-4 hover:underline"
          >
            {row.original.name?.trim() || "—"}
          </Link>
          {row.original.id === selfId ? (
            <span className="text-xs text-muted-foreground">(you)</span>
          ) : null}
          <StateBadge state={userState(row.original)} />
        </div>
      ),
    },
    {
      accessorKey: "email",
      header: "Email",
      cell: ({ row }) => <span className="text-sm">{row.original.email}</span>,
    },
    {
      accessorKey: "role",
      header: "User role",
      cell: ({ row }) => <RoleBadge user={row.original} />,
    },
    {
      accessorKey: "department",
      header: "Department",
      cell: ({ row }) => row.original.department || "—",
    },
    {
      id: "last_active",
      header: "Last active",
      cell: ({ row }) => {
        const seen = lastSeen(row.original);
        if (!seen) {
          return (
            <span className="text-muted-foreground">
              {row.original.status === "pending"
                ? `Asked ${formatRelative(row.original.created_at) ?? formatDate(row.original.created_at)}`
                : "Never"}
            </span>
          );
        }
        return <StackedCell primary={formatRelative(seen) ?? formatDate(seen)} secondary={formatDate(seen)} />;
      },
    },
    {
      id: "actions",
      header: "",
      enableSorting: false,
      meta: { align: "right" },
      cell: ({ row }) => <UserActions user={row.original} selfId={selfId} />,
    },
  ];
}

const EXPORT_COLUMNS = [
  { key: "name" as const, header: "name" },
  { key: "email" as const, header: "email" },
  { key: "role" as const, header: "role" },
  { key: "department" as const, header: "department" },
  { key: "status" as const, header: "status" },
  { key: "access_expires_at" as const, header: "access_expires_at" },
  { key: "last_active_at" as const, header: "last_active_at" },
  { key: "last_login_at" as const, header: "last_login_at" },
  { key: "created_at" as const, header: "created_at" },
];

function Users() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const { session, isLoading: sessionLoading } = useSessionState();
  const isAdmin = session?.user.role === "admin";

  const query = useQuery({
    queryKey: ["users"],
    queryFn: () => api.users(),
    enabled: isAdmin,
    retry: false,
  });
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<{ email: string; password: string } | null>(null);
  const [selected, setSelected] = useState<User[]>([]);

  if (!sessionLoading && !isAdmin) {
    return (
      <div className="space-y-5">
        <PageHeader title="Users" description="Everyone who can sign in to this portal." />
        <Alert>
          <IconShieldLock />
          <AlertDescription>
            Only an administrator can manage accounts. Ask one if you need access changed
            for yourself or a colleague.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const all = query.data?.data ?? [];
  const roles = asTextList(search.role);
  const states = asTextList(search.state);
  const needle = asText(search.q)?.toLowerCase() ?? "";
  const pending = all.filter((user) => user.status === "pending").length;

  const rows = all
    .filter(
      (user) =>
        (!roles.length || roles.includes(user.role)) &&
        (!states.length || states.includes(userState(user))) &&
        (!needle ||
          user.email.toLowerCase().includes(needle) ||
          (user.name ?? "").toLowerCase().includes(needle) ||
          (user.department ?? "").toLowerCase().includes(needle)),
    )
    .sort(
      (a, b) =>
        STATE_ORDER[userState(a)] - STATE_ORDER[userState(b)] ||
        (lastSeen(b) ?? "").localeCompare(lastSeen(a) ?? "") ||
        displayName(a).localeCompare(displayName(b)),
    );

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function exportRows(chosen: User[], suffix: string) {
    downloadCsv(
      `users-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  const clear = (key: "role" | "state") =>
    navigate({ search: (prev) => ({ ...prev, [key]: undefined, page: 0 }) });

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Users"
            count={rows.length}
            isLoading={query.isLoading}
            description="Everyone who can sign in, and everyone who has asked to. Admins manage accounts; researchers read; guests read until their end date."
            actions={
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!rows.length}
                  onClick={() => exportRows(rows, "all")}
                >
                  <IconDownload className="size-4" />
                  Export
                </Button>
                <Button size="sm" onClick={() => setAdding(true)}>
                  <IconUserPlus className="size-4" />
                  Add user
                </Button>
              </div>
            }
          />
        }
        filters={
          <TableToolbar
            filters={
              <>
                <FilterChip
                  label="Role"
                  value={summarise(roles.map(roleLabel))}
                  onClear={() => clear("role")}
                >
                  <ChoiceList
                    options={ROLES.map((entry) => ({ value: entry.value, label: entry.label }))}
                    selected={roles}
                    searchPlaceholder="Search roles"
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({ ...prev, role: toggle(roles, value), page: 0 }),
                      })
                    }
                    onClear={() => clear("role")}
                  />
                </FilterChip>
                <FilterChip
                  label="Status"
                  value={summarise(states.map((value) => STATE_LABELS[value as UserState] ?? value))}
                  onClear={() => clear("state")}
                >
                  <ChoiceList
                    options={(Object.keys(STATE_LABELS) as UserState[]).map((value) => ({
                      value,
                      label: STATE_LABELS[value],
                    }))}
                    selected={states}
                    searchPlaceholder="Search statuses"
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({ ...prev, state: toggle(states, value), page: 0 }),
                      })
                    }
                    onClear={() => clear("state")}
                  />
                </FilterChip>
                {roles.length || states.length || search.q ? (
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
                placeholder="Search names, emails and departments"
                onSearch={(q) => navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })}
              />
            }
          />
        }
      />

      {pending > 0 && !states.includes("pending") ? (
        <Alert>
          <IconUserPlus />
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            <span>
              {formatCount(pending)} {pending === 1 ? "person is" : "people are"} waiting for
              approval.
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate({ search: { state: ["pending"] } })}
            >
              Review requests
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {query.isError ? (
        <Alert variant="destructive">
          <IconAlertTriangle />
          <AlertDescription>{errorMessage(query.error)}</AlertDescription>
        </Alert>
      ) : null}

      <DataTable
        columns={columns(session?.user.id)}
        data={visible}
        isLoading={query.isLoading || sessionLoading}
        emptyMessage="No users match this filter."
        selectable
        getRowId={(row) => row.id}
        onSelectionChange={setSelected}
        renderSelectionActions={(chosen) => (
          <Button variant="outline" size="sm" onClick={() => exportRows(chosen, "selection")}>
            <IconDownload className="size-4" />
            Export {formatCount(chosen.length)}
          </Button>
        )}
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
              {formatCount(rows.length)} user{rows.length === 1 ? "" : "s"}
              {rows.length !== all.length ? ` (${formatCount(all.length)} total)` : null}
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />

      <UserFormDialog
        mode="create"
        open={adding}
        onOpenChange={setAdding}
        onCreated={(user, password) => {
          if (password) setCreated({ email: user.email, password });
        }}
      />
      <SecretDialog
        email={created?.email ?? ""}
        secret={created?.password ?? null}
        onClose={() => setCreated(null)}
      />
    </div>
  );
}
