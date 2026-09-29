import { IconLock, IconUserPlus, IconUsers, IconX } from "@tabler/icons-react";
import { useState } from "react";

import { Avatar, AvatarFallback } from "~/components/ui/avatar";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "~/components/ui/popover";
import { useUser } from "~/lib/session";
import {
  addMember,
  claimCollection,
  personLabel,
  removeMember,
  useShelf,
  type Collection,
} from "~/lib/workspace";
import type { ShelfPerson } from "~/lib/api";

/**
 * Who is in a collection, and — for its owner — who else to let in.
 *
 * Membership is what turns a folder into a place to send things: a colleague
 * who is a member sees it in the three-dots "Add to collection" menu on every
 * row, and what they file there lands on the owner's shelf. Members are added
 * by the address they sign in with rather than picked from a list, because a
 * list would be the deployment's staff directory handed to every reader.
 *
 * Nothing here is drawn ahead of the server. Adding an address that has no
 * account is refused there, and a name that appears and then vanishes would
 * be worse than a button that waits a moment.
 */
export function CollectionMembers({ collection }: { collection: Collection }) {
  const shelf = useShelf();
  const me = useUser();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  // A browser-kept shelf, or a deployment without accounts, has nobody to add.
  if (!shelf.members) return null;

  const members = collection.members ?? [];
  const owns = collection.role === "owner";
  const writable = shelf.writable;

  async function run(change: () => Promise<void>) {
    setBusy(true);
    setError(undefined);
    try {
      await change();
      return true;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "that did not save");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(event: React.FormEvent) {
    event.preventDefault();
    const address = email.trim();
    if (!address) return;
    if (await run(() => addMember(collection.id, address))) setEmail("");
  }

  const label =
    collection.role === "shared"
      ? "Everyone"
      : collection.role === "member"
        ? `${personLabel(collection.owner)}'s`
        : members.length
          ? `${members.length + 1} people`
          : "Only you";

  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button variant="outline" size="sm">
            {collection.role === "shared" || members.length ? (
              <IconUsers className="size-4" />
            ) : (
              <IconLock className="size-4" />
            )}
            {label}
          </Button>
        }
      />
      <PopoverContent align="end" className="w-80 space-y-3 p-3">
        {collection.role === "shared" ? (
          // A folder from before there were owners. Said as what it is, with
          // the one way out of it, rather than an add form that would fail.
          <div className="space-y-2 text-sm">
            <p className="text-muted-foreground">
              Made before collections had owners, so everyone on this deployment can see
              and change it. Make it yours to choose who else is in.
            </p>
            {writable ? (
              <Button
                size="sm"
                disabled={busy}
                onClick={() => void run(() => claimCollection(collection.id))}
              >
                <IconLock className="size-4" />
                Make private to me
              </Button>
            ) : null}
          </div>
        ) : (
          <>
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">
                {owns
                  ? "Members can add records to this collection and remove them."
                  : "You can add records here from any row's menu, and remove them."}
              </p>
            </div>

            <ul className="space-y-1">
              {collection.owner ? (
                <PersonRow person={collection.owner} role="Owner" you={me?.id} />
              ) : null}
              {members.map((member) => {
                const self = member.id === me?.id;
                // The owner removes anyone; a member may only leave.
                const removable = writable && (owns || self);
                return (
                  <PersonRow
                    key={member.id}
                    person={member}
                    role="Member"
                    you={me?.id}
                    onRemove={
                      removable
                        ? () => void run(() => removeMember(collection.id, member.id))
                        : undefined
                    }
                    removeLabel={self ? "Leave" : undefined}
                    busy={busy}
                  />
                );
              })}
            </ul>

            {owns && writable ? (
              <form onSubmit={add} className="flex items-center gap-1.5">
                <Input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="Their sign-in email"
                  aria-label="Email of the person to add"
                  className="h-8"
                />
                <Button type="submit" size="sm" disabled={busy || !email.trim()}>
                  <IconUserPlus className="size-4" />
                  Add
                </Button>
              </form>
            ) : null}
          </>
        )}

        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function PersonRow({
  person,
  role,
  you,
  onRemove,
  removeLabel,
  busy,
}: {
  person: ShelfPerson;
  role: string;
  you?: string;
  onRemove?: () => void;
  /** Text instead of the cross, for a member taking themselves out. */
  removeLabel?: string;
  busy?: boolean;
}) {
  const name = personLabel(person);
  return (
    <li className="flex items-center gap-2 py-0.5">
      <Avatar size="sm">
        <AvatarFallback className="text-[10px]">
          {name.slice(0, 2).toUpperCase()}
        </AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1 leading-tight">
        <div className="truncate text-sm">
          {name}
          {person.id === you ? (
            <span className="text-muted-foreground"> (you)</span>
          ) : null}
        </div>
        {person.name && person.email ? (
          <div className="truncate text-xs text-muted-foreground">{person.email}</div>
        ) : null}
      </div>
      <span className="shrink-0 text-xs text-muted-foreground">{role}</span>
      {onRemove ? (
        removeLabel ? (
          <Button variant="ghost" size="sm" disabled={busy} onClick={onRemove}>
            {removeLabel}
          </Button>
        ) : (
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={busy}
            aria-label={`Remove ${name}`}
            onClick={onRemove}
          >
            <IconX className="size-4" />
          </Button>
        )
      ) : null}
    </li>
  );
}
