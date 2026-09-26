import { useMutation, useQueryClient } from "@tanstack/react-query";
import { IconAlertTriangle, IconCheck, IconCopy, IconKey } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { copyToClipboard } from "~/components/row-actions";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "~/components/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { ApiRequestError, api, type User } from "~/lib/api";
import { ROLES, defaultGuestEnd, endOfDay, toDateInput } from "~/lib/users";

/**
 * The three things an admin does to an account through a form: make one, change
 * one, and let a registration in. One dialog, because the fields are the same
 * three — role, department, and a guest's end date — with the address and a
 * password added when the account is new.
 */
export type UserFormMode = "create" | "edit" | "approve";

export function UserFormDialog({
  mode,
  user,
  open,
  onOpenChange,
  onCreated,
}: {
  mode: UserFormMode;
  user?: User;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** A new account, with the generated password when one was made. */
  onCreated?: (user: User, password?: string) => void;
}) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [department, setDepartment] = useState("");
  const [role, setRole] = useState("researcher");
  const [until, setUntil] = useState("");
  const [password, setPassword] = useState("");

  // Reset from the account each time the dialog opens, so editing one row
  // and then another does not carry the first one's draft across.
  useEffect(() => {
    if (!open) return;
    setEmail(user?.email ?? "");
    setName(user?.name ?? "");
    setDepartment(user?.department ?? "");
    setRole(user && mode !== "approve" ? user.role : "researcher");
    setUntil(toDateInput(user?.access_expires_at) || defaultGuestEnd());
    setPassword("");
  }, [open, user, mode]);

  const guest = role === "guest";
  const expires = guest ? endOfDay(until) : undefined;

  const save = useMutation({
    mutationFn: async () => {
      if (mode === "create") {
        const created = await api.createUser({
          email: email.trim(),
          name: name.trim(),
          department: department.trim(),
          role,
          password: password || undefined,
          access_expires_at: expires,
        });
        return { user: created.data as User, password: created.data.password };
      }
      if (mode === "approve" && user) {
        // Name and department are the requester's own words; the admin may
        // tidy them, so they are saved before the approval.
        await api.updateUser(user.id, {
          name: name.trim(),
          department: department.trim(),
        });
        const approved = await api.approveUser(user.id, role, expires);
        return { user: approved.data };
      }
      if (!user) throw new Error("no account to change");
      const updated = await api.updateUser(user.id, {
        name: name.trim(),
        department: department.trim(),
        role,
        access_expires_at: guest ? (expires ?? null) : null,
      });
      return { user: updated.data };
    },
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["users"] });
      onOpenChange(false);
      if (mode === "create") onCreated?.(result.user, result.password);
    },
  });

  const title =
    mode === "create" ? "Add user" : mode === "approve" ? "Approve request" : "Edit user";
  const description =
    mode === "create"
      ? "The account is active at once. Leave the password empty to have one generated, shown to you once."
      : mode === "approve"
        ? `${user?.email} asked for an account. Choose what they can do before letting them in.`
        : `Changes apply to ${user?.email} straight away, including to sessions already open.`;

  const ready =
    (mode !== "create" || email.trim()) && (!guest || expires) && !save.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (ready) save.mutate();
          }}
        >
          <FieldGroup>
            {mode === "create" ? (
              <Field>
                <FieldLabel htmlFor="user-email">Email</FieldLabel>
                <Input
                  id="user-email"
                  type="email"
                  required
                  autoFocus
                  value={email}
                  placeholder="name@csis.or.id"
                  onChange={(event) => setEmail(event.target.value)}
                />
              </Field>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="user-name">Name</FieldLabel>
                <Input
                  id="user-name"
                  value={name}
                  maxLength={120}
                  onChange={(event) => setName(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="user-department">Department</FieldLabel>
                <Input
                  id="user-department"
                  value={department}
                  maxLength={120}
                  placeholder="e.g. Economics"
                  onChange={(event) => setDepartment(event.target.value)}
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="user-role">Role</FieldLabel>
                <Select
                  value={role}
                  onValueChange={(value) => setRole(String(value))}
                  items={ROLES.map((entry) => ({ value: entry.value, label: entry.label }))}
                >
                  <SelectTrigger id="user-role" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLES.map((entry) => (
                      <SelectItem key={entry.value} value={entry.value}>
                        {entry.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              {guest ? (
                <Field>
                  <FieldLabel htmlFor="user-until">Access until</FieldLabel>
                  <Input
                    id="user-until"
                    type="date"
                    required
                    min={toDateInput(new Date().toISOString())}
                    value={until}
                    onChange={(event) => setUntil(event.target.value)}
                  />
                </Field>
              ) : null}
            </div>
            <FieldDescription>
              {ROLES.find((entry) => entry.value === role)?.description}
              {guest ? " Sign-in, sessions and API tokens all stop at the end of that day." : null}
            </FieldDescription>

            {mode === "create" ? (
              <Field>
                <FieldLabel htmlFor="user-password">Password</FieldLabel>
                <Input
                  id="user-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  placeholder="Leave empty to generate one"
                  onChange={(event) => setPassword(event.target.value)}
                />
                <FieldDescription>
                  At least 12 characters. They can change it from their account page.
                </FieldDescription>
              </Field>
            ) : null}
          </FieldGroup>

          {save.isError ? (
            <Alert variant="destructive">
              <IconAlertTriangle />
              <AlertDescription>{errorMessage(save.error)}</AlertDescription>
            </Alert>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!ready}>
              {save.isPending
                ? "Saving…"
                : mode === "create"
                  ? "Add user"
                  : mode === "approve"
                    ? "Approve"
                    : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * A password the admin has to hand over, shown once.
 *
 * The API keeps only its hash, so closing this is the last time anybody can
 * read it — which the dialog says, rather than letting it be found out.
 */
export function SecretDialog({
  email,
  secret,
  onClose,
}: {
  email: string;
  secret: string | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  useEffect(() => setCopied(false), [secret]);

  return (
    <Dialog open={secret !== null} onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Password for {email}</DialogTitle>
          <DialogDescription>
            Copy it now and send it to them privately. It will not be shown again; they
            can change it from their account page.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <IconKey className="size-4 shrink-0 text-muted-foreground" />
          <code className="min-w-0 flex-1 truncate rounded border bg-muted px-2 py-1.5 font-mono text-sm">
            {secret}
          </code>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              if (secret) void copyToClipboard(secret).then(setCopied);
            }}
          >
            {copied ? <IconCheck className="size-4" /> : <IconCopy className="size-4" />}
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  if (error instanceof Error) return error.message;
  return "The API could not be reached.";
}
