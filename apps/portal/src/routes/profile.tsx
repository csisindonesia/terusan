import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  IconAlertTriangle,
  IconCheck,
  IconDeviceDesktop,
  IconLogout,
  IconShieldLock,
} from "@tabler/icons-react";
import { useState } from "react";

import { PageHeader } from "~/components/page-header";
import { StickyHeader } from "~/components/sticky-header";
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
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { ApiRequestError, api, type LoginRecord } from "~/lib/api";
import { formatMoment, formatRelative } from "~/lib/format";
import { SESSION_KEY, useSessionState, useSignOut } from "~/lib/session";

/**
 * The account, and everything that belongs to the person rather than to the
 * warehouse.
 *
 * Three things, in the order somebody needs them: who they are, the password
 * that proves it, and every browser currently claiming to be them. The last of
 * those is the one that earns the page — "am I still logged in on the machine
 * in the meeting room" is a question a portal should be able to answer, and
 * ending that session should not require an administrator.
 *
 * What is deliberately not here: roles, permissions and other people's
 * accounts. A session says who is reading and nothing is scoped to it yet
 * (program.md §35), so a page offering to change a role would be offering to
 * change nothing. Accounts are granted from the terminal, with `authctl`.
 */

export const Route = createFileRoute("/profile")({
  component: Profile,
});

function Profile() {
  const { session, hasAuth, isLoading } = useSessionState();
  const user = session?.user;

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-48 w-full rounded-sm" />
      </div>
    );
  }

  if (!hasAuth || !user) {
    return (
      <div className="space-y-5">
        <PageHeader title="Account" description="Who you are signed in as." />
        <Alert>
          <IconShieldLock />
          <AlertDescription>
            {hasAuth ? (
              <>
                Nobody is signed in on this browser.{" "}
                <Link to="/login" className="underline underline-offset-4">
                  Log in
                </Link>
                .
              </>
            ) : (
              <>
                This deployment keeps no accounts, so there is no account page. Set{" "}
                <code className="font-mono">APP_DB</code> where the API runs.
              </>
            )}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Account"
            description="Your details, your password, and every browser signed in as you."
            actions={<SignOutButton />}
          />
        }
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <ProfileCard
          name={user.name ?? ""}
          email={user.email}
          role={user.role}
          createdAt={user.created_at}
          lastLoginAt={user.last_login_at}
        />
        <PasswordCard />
      </div>

      <LoginsCard currentExpiry={session.expires_at} />
    </div>
  );
}

function ProfileCard({
  name,
  email,
  role,
  createdAt,
  lastLoginAt,
}: {
  name: string;
  email: string;
  role: string;
  createdAt: string;
  lastLoginAt?: string;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(name);
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: () => api.updateProfile(draft.trim()),
    onSuccess: async () => {
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: SESSION_KEY });
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile</CardTitle>
        <CardDescription>
          What the portal calls you. Your email address identifies the account and is
          changed by whoever administers this deployment.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            setSaved(false);
            save.mutate();
          }}
        >
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="name">Name</FieldLabel>
              <Input
                id="name"
                value={draft}
                placeholder="Your name"
                onChange={(event) => {
                  setDraft(event.target.value);
                  setSaved(false);
                }}
              />
              <FieldDescription>
                Shown in the sidebar and beside anything you save.
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel htmlFor="email">Email</FieldLabel>
              <Input id="email" value={email} readOnly disabled />
            </Field>
          </FieldGroup>

          {save.isError ? (
            <Alert variant="destructive">
              <IconAlertTriangle />
              <AlertDescription>{message(save.error)}</AlertDescription>
            </Alert>
          ) : null}

          <div className="flex items-center gap-3">
            <Button type="submit" size="sm" disabled={save.isPending || draft === name}>
              {save.isPending ? "Saving…" : "Save"}
            </Button>
            {saved ? (
              <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
                <IconCheck className="size-4" />
                Saved
              </span>
            ) : null}
          </div>
        </form>

        <dl className="grid gap-3 border-t pt-4 text-sm sm:grid-cols-3">
          <Fact label="Role" value={<Badge variant="secondary">{role}</Badge>} />
          <Fact label="Account created" value={formatMoment(createdAt)} />
          <Fact
            label="Last login"
            value={lastLoginAt ? formatMoment(lastLoginAt) : "—"}
          />
        </dl>

        {/* Roles are carried and shown, and enforce nothing yet. Saying so
            here is better than letting the badge imply a permission model. */}
        <p className="text-xs text-muted-foreground">
          Roles are recorded but not yet enforced: everyone who can sign in sees the
          same warehouse and the same collections.
        </p>
      </CardContent>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-0.5">{value}</dd>
    </div>
  );
}

/**
 * `id`s so the sidebar menu can point at a section rather than at the page.
 * Named for what they are, because they end up in a URL somebody may bookmark.
 */
function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);

  const mismatch = confirm !== "" && next !== confirm;
  const tooShort = next !== "" && next.length < 12;

  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => {
      setDone(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    },
  });

  return (
    <Card id="password" className="scroll-mt-24">
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>
          Changing it ends every other session you have open — which is usually the
          reason for changing it. This browser stays signed in.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            setDone(false);
            if (mismatch || tooShort || !current || !next) return;
            change.mutate();
          }}
        >
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="current-password">Current password</FieldLabel>
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            </Field>
            <Field data-invalid={tooShort || undefined}>
              <FieldLabel htmlFor="new-password">New password</FieldLabel>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                value={next}
                aria-invalid={tooShort || undefined}
                onChange={(event) => setNext(event.target.value)}
              />
              {/* Length, not a composition rule: "one symbol" produces
                  Password1! and nothing else, where a long passphrase is both
                  easier to remember and harder to guess. */}
              <FieldDescription>
                At least 12 characters. A passphrase beats a short password with a
                symbol in it.
              </FieldDescription>
            </Field>
            <Field data-invalid={mismatch || undefined}>
              <FieldLabel htmlFor="confirm-password">Repeat new password</FieldLabel>
              <Input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirm}
                aria-invalid={mismatch || undefined}
                onChange={(event) => setConfirm(event.target.value)}
              />
              {mismatch ? (
                <FieldDescription className="text-destructive">
                  The two do not match.
                </FieldDescription>
              ) : null}
            </Field>
          </FieldGroup>

          {change.isError ? (
            <Alert variant="destructive">
              <IconAlertTriangle />
              <AlertDescription>{message(change.error)}</AlertDescription>
            </Alert>
          ) : null}
          {done ? (
            <Alert>
              <IconCheck />
              <AlertDescription>
                Password changed. Every other session has been ended.
              </AlertDescription>
            </Alert>
          ) : null}

          <Button
            type="submit"
            size="sm"
            disabled={change.isPending || !current || !next || mismatch || tooShort}
          >
            {change.isPending ? "Changing…" : "Change password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function LoginsCard({ currentExpiry }: { currentExpiry: string }) {
  const queryClient = useQueryClient();
  const logins = useQuery({
    queryKey: ["logins"],
    queryFn: async () => (await api.logins()).data,
    retry: false,
  });

  const end = useMutation({
    mutationFn: (id: string) => api.endLogin(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["logins"] }),
  });
  const endOthers = useMutation({
    mutationFn: () => api.endOtherLogins(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["logins"] }),
  });

  const rows = logins.data ?? [];
  const others = rows.filter((row) => !row.current).length;

  return (
    <Card id="sessions" className="scroll-mt-24">
      <CardHeader>
        <CardTitle>Where you are signed in</CardTitle>
        <CardDescription>
          Every browser currently holding a session as you. Ending one takes effect
          immediately.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {logins.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : logins.isError ? (
          <Alert variant="destructive">
            <IconAlertTriangle />
            <AlertDescription>{message(logins.error)}</AlertDescription>
          </Alert>
        ) : (
          <div className="divide-y rounded-lg border">
            {rows.map((row) => (
              <LoginRow
                key={row.id}
                login={row}
                expiry={row.current ? currentExpiry : row.expires_at}
                onEnd={row.current ? undefined : () => end.mutate(row.id)}
                ending={end.isPending && end.variables === row.id}
              />
            ))}
          </div>
        )}

        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {/* The count is the useful part: "one other session" is a fact
                somebody acts on, "you have sessions" is not. */}
            {others === 0
              ? "This is your only session."
              : `${others} other session${others === 1 ? "" : "s"}.`}
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={others === 0 || endOthers.isPending}
            onClick={() => endOthers.mutate()}
          >
            <IconLogout className="size-4" />
            {endOthers.isPending ? "Ending…" : "Sign out everywhere else"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function LoginRow({
  login,
  expiry,
  onEnd,
  ending,
}: {
  login: LoginRecord;
  expiry: string;
  /** Absent for the session doing the reading — that one is the sign-out button. */
  onEnd?: () => void;
  ending: boolean;
}) {
  return (
    <div className="flex items-center gap-3 px-3 py-2.5">
      <IconDeviceDesktop className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 leading-tight">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium" title={login.user_agent}>
            {login.user_agent || "Unknown browser"}
          </span>
          {login.current ? (
            <Badge variant="secondary" className="shrink-0">
              This browser
            </Badge>
          ) : null}
        </div>
        <div className="truncate text-xs text-muted-foreground">
          Signed in {formatRelative(login.created_at) ?? formatMoment(login.created_at)}{" "}
          · expires {formatMoment(expiry)}
        </div>
      </div>
      {onEnd ? (
        <Button variant="ghost" size="sm" disabled={ending} onClick={onEnd}>
          {ending ? "Ending…" : "End"}
        </Button>
      ) : null}
    </div>
  );
}

function SignOutButton() {
  const signOut = useSignOut();
  const navigate = useNavigate();
  const [leaving, setLeaving] = useState(false);

  return (
    <Button
      variant="outline"
      size="sm"
      disabled={leaving}
      onClick={() => {
        setLeaving(true);
        void signOut().then(() => navigate({ to: "/login", replace: true }));
      }}
    >
      <IconLogout className="size-4" />
      {leaving ? "Signing out…" : "Sign out"}
    </Button>
  );
}

/** What the API said, where it said anything worth reading. */
function message(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  return "The API could not be reached.";
}
