import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
  IconCircleCheck,
  IconEye,
  IconKey,
  IconLock,
  IconLockOpen,
  IconPencil,
  IconTrash,
} from "@tabler/icons-react";
import { useState } from "react";

import { RowActions, type RowAction } from "~/components/row-actions";
import {
  SecretDialog,
  UserFormDialog,
  errorMessage,
  type UserFormMode,
} from "~/components/user-dialogs";
import { api, type User } from "~/lib/api";
import { displayName } from "~/lib/users";

/**
 * The three dots on a user's row, and on their page.
 *
 * What is offered depends on where the account stands: a registration can be
 * approved or rejected and nothing else; an active account can be edited,
 * have its password reset, or be disabled. An admin is never offered the
 * actions that would lock themselves out — the API refuses them too.
 */
export function UserActions({
  user,
  selfId,
  showView = true,
  onRemoved,
}: {
  user: User;
  /** The signed-in admin, who cannot disable or demote themselves. */
  selfId?: string;
  /** Hidden on the user's own page, where "view details" goes nowhere. */
  showView?: boolean;
  /** After a rejected registration, which no longer exists to be shown. */
  onRemoved?: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<UserFormMode | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const self = user.id === selfId;
  const pending = user.status === "pending";

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["users"] }),
      queryClient.invalidateQueries({ queryKey: ["user", user.id] }),
    ]);

  const act = useMutation({
    mutationFn: async (action: "reject" | "reset" | "disable" | "enable") => {
      switch (action) {
        case "reject":
          await api.rejectUser(user.id);
          return null;
        case "reset":
          return (await api.resetUserPassword(user.id)).data.password;
        case "disable":
        case "enable":
          await api.updateUser(user.id, { disabled: action === "disable" });
          return null;
      }
    },
    onSuccess: async (password, action) => {
      await refresh();
      if (password) setSecret(password);
      if (action === "reject") onRemoved?.();
    },
    onError: (error) => window.alert(errorMessage(error)),
  });

  const actions: RowAction[] = [];
  if (showView) {
    actions.push({
      label: "View details",
      icon: IconEye,
      onSelect: () => void navigate({ to: "/users/$userId", params: { userId: user.id } }),
    });
  }
  if (pending) {
    actions.push(
      { label: "Approve…", icon: IconCircleCheck, onSelect: () => setForm("approve") },
      {
        label: "Reject request",
        icon: IconTrash,
        destructive: true,
        onSelect: () => {
          if (window.confirm(`Reject the request from ${user.email}? It will be deleted.`)) {
            act.mutate("reject");
          }
        },
      },
    );
  } else {
    actions.push(
      { label: "Edit…", icon: IconPencil, onSelect: () => setForm("edit") },
      {
        label: "Reset password",
        icon: IconKey,
        hint: self ? "Change your own password from your account page" : undefined,
        onSelect: self
          ? undefined
          : () => {
              if (
                window.confirm(
                  `Give ${displayName(user)} a new password? They will be signed out everywhere.`,
                )
              ) {
                act.mutate("reset");
              }
            },
      },
      user.disabled
        ? {
            label: "Enable account",
            icon: IconLockOpen,
            onSelect: () => act.mutate("enable"),
          }
        : {
            label: "Disable account",
            icon: IconLock,
            destructive: true,
            hint: self ? "You cannot disable your own account" : undefined,
            onSelect: self
              ? undefined
              : () => {
                  if (
                    window.confirm(
                      `Disable ${displayName(user)}? They are signed out at once and their API tokens stop working.`,
                    )
                  ) {
                    act.mutate("disable");
                  }
                },
          },
    );
  }

  return (
    <>
      <RowActions bare actions={actions} label={`Actions for ${displayName(user)}`} />
      <UserFormDialog
        mode={form ?? "edit"}
        user={user}
        open={form !== null}
        onOpenChange={(open) => (open ? null : setForm(null))}
      />
      <SecretDialog email={user.email} secret={secret} onClose={() => setSecret(null)} />
    </>
  );
}
