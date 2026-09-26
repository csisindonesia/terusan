import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconAlertTriangle,
  IconApi,
  IconArrowLeft,
  IconDeviceDesktop,
  IconLogin,
  IconShieldLock,
} from "@tabler/icons-react";

import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { StickyHeader } from "~/components/sticky-header";
import { UserActions } from "~/components/user-actions";
import { errorMessage } from "~/components/user-dialogs";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { api, type AccessEvent, type User } from "~/lib/api";
import { formatCount, formatMoment, formatRelative } from "~/lib/format";
import { useSessionState } from "~/lib/session";
import {
  ACCESS_KINDS,
  STATE_LABELS,
  displayName,
  formatLocation,
  isPrivateAddress,
  lastSeen,
  roleLabel,
  userState,
} from "~/lib/users";

/**
 * One account: who it is, what it may do, and where it has been used from.
 *
 * The access log is the reason for the page. "Was this account used from
 * somewhere it should not have been" is a question an admin can only answer
 * with dates, addresses and places side by side — so that is the table, one
 * row per sign-in and per few minutes of use from each address.
 */

export const Route = createFileRoute("/users/$userId")({
  component: UserPage,
});

const KIND_ICONS = { login: IconLogin, web: IconDeviceDesktop, api: IconApi } as const;

const accessColumns: ColumnDef<AccessEvent>[] = [
  {
    accessorKey: "at",
    header: "Date & time",
    meta: { width: "w-48" },
    cell: ({ row }) => (
      <StackedCell
        primary={formatMoment(row.original.at)}
        secondary={formatRelative(row.original.at)}
      />
    ),
  },
  {
    accessorKey: "kind",
    header: "Activity",
    cell: ({ row }) => {
      const Icon = KIND_ICONS[row.original.kind as keyof typeof KIND_ICONS] ?? IconDeviceDesktop;
      return (
        <span className="inline-flex items-center gap-1.5 text-sm">
          <Icon className="size-4 text-muted-foreground" />
          {ACCESS_KINDS[row.original.kind] ?? row.original.kind}
        </span>
      );
    },
  },
  {
    accessorKey: "ip",
    header: "IP address",
    cell: ({ row }) => (
      <code className="font-mono text-xs">{row.original.ip || "—"}</code>
    ),
  },
  {
    id: "location",
    header: "Location",
    cell: ({ row }) => (
      <span className={isPrivateAddress(row.original.ip) ? "text-muted-foreground" : ""}>
        {formatLocation(row.original)}
      </span>
    ),
  },
  {
    accessorKey: "user_agent",
    header: "Device",
    meta: { width: "w-80" },
    cell: ({ row }) => (
      <span
        className="line-clamp-1 max-w-[20rem] text-xs text-muted-foreground"
        title={row.original.user_agent}
      >
        {row.original.user_agent || "—"}
      </span>
    ),
  },
];

function UserPage() {
  const { userId } = Route.useParams();
  const navigate = useNavigate();
  const { session, isLoading: sessionLoading } = useSessionState();
  const isAdmin = session?.user.role === "admin";

  const query = useQuery({
    queryKey: ["user", userId],
    queryFn: async () => (await api.user(userId)).data,
    enabled: isAdmin,
    retry: false,
  });

  const back = (
    <Button variant="ghost" size="sm" render={<Link to="/users" />}>
      <IconArrowLeft className="size-4" />
      All users
    </Button>
  );

  if (!sessionLoading && !isAdmin) {
    return (
      <div className="space-y-5">
        <PageHeader title="User" actions={back} />
        <Alert>
          <IconShieldLock />
          <AlertDescription>Only an administrator can see other accounts.</AlertDescription>
        </Alert>
      </div>
    );
  }

  if (query.isLoading || sessionLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-48 w-full rounded-sm" />
        <Skeleton className="h-64 w-full rounded-sm" />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="space-y-5">
        <PageHeader title="User" actions={back} />
        <Alert variant="destructive">
          <IconAlertTriangle />
          <AlertDescription>{errorMessage(query.error)}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const { user, access } = query.data;
  const addresses = new Set(access.map((event) => event.ip).filter(Boolean));
  const places = new Set(
    access.map(formatLocation).filter((place) => place !== "Unknown"),
  );

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title={displayName(user)}
            description={user.name ? user.email : undefined}
            actions={
              <div className="flex items-center gap-2">
                {back}
                <UserActions
                  user={user}
                  selfId={session?.user.id}
                  showView={false}
                  onRemoved={() => void navigate({ to: "/users" })}
                />
              </div>
            }
          />
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
          <CardDescription>
            What this account is and what it may do. Change it from the three dots above.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Name" value={user.name || "—"} />
            <Fact label="Email" value={user.email} />
            <Fact label="User role" value={<RoleValue user={user} />} />
            <Fact label="Department" value={user.department || "—"} />
            <Fact label="Status" value={<StatusValue user={user} />} />
            <Fact
              label="Access until"
              value={
                user.role === "guest" && user.access_expires_at
                  ? formatMoment(user.access_expires_at)
                  : "No end date"
              }
            />
            <Fact
              label={user.status === "pending" ? "Requested" : "Account created"}
              value={formatMoment(user.created_at)}
            />
            <Fact
              label="Last sign-in"
              value={user.last_login_at ? formatMoment(user.last_login_at) : "Never"}
            />
            <Fact
              label="Last active"
              value={
                lastSeen(user)
                  ? `${formatMoment(lastSeen(user))} (${formatRelative(lastSeen(user)) ?? ""})`
                  : "Never"
              }
            />
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Access history</CardTitle>
          <CardDescription>
            Every sign-in, and use of the portal or an API token at most once per five
            minutes from each address. Location is what Cloudflare reported for the
            address; it is absent when the API is reached directly. Kept for 180 days.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {access.length ? (
            <p className="text-xs text-muted-foreground">
              {formatCount(access.length)} event{access.length === 1 ? "" : "s"} from{" "}
              {formatCount(addresses.size)} address{addresses.size === 1 ? "" : "es"}
              {places.size ? ` in ${[...places].slice(0, 3).join("; ")}` : ""}
              {places.size > 3 ? ` and ${places.size - 3} more` : ""}.
            </p>
          ) : null}
          <DataTable
            columns={accessColumns}
            data={access}
            emptyMessage={
              user.status === "pending"
                ? "This request has not been approved, so the account has never been used."
                : "No access recorded yet."
            }
            getRowId={(row) => row.id}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function RoleValue({ user }: { user: User }) {
  return (
    <Badge variant={user.role === "admin" ? "default" : "secondary"}>
      {roleLabel(user.role)}
    </Badge>
  );
}

function StatusValue({ user }: { user: User }) {
  const state = userState(user);
  return (
    <Badge variant={state === "active" ? "secondary" : state === "pending" ? "outline" : "destructive"}>
      {STATE_LABELS[state]}
    </Badge>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
