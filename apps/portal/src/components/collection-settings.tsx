import { useNavigate } from "@tanstack/react-router";
import {
  IconApi,
  IconLock,
  IconTrash,
  IconUserPlus,
  IconWorld,
  IconX,
} from "@tabler/icons-react";
import { useState } from "react";

import { Section } from "~/components/section";
import { Avatar, AvatarFallback } from "~/components/ui/avatar";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import type { ShelfPerson, ShelfVisibility } from "~/lib/api";
import { useUser } from "~/lib/session";
import { cn } from "~/lib/utils";
import {
  addMember,
  claimCollection,
  deleteCollection,
  managesCollection,
  personLabel,
  removeMember,
  setVisibility,
  updateCollection,
  useShelf,
  type Collection,
} from "~/lib/workspace";

/**
 * What a collection is called, who may open it, and whether it serves an API.
 *
 * Everyone who can open the collection sees every setting, and only the
 * owner can change most of them: a member who cannot see who else is in a
 * folder does not know who they are sending things to.
 */
export function CollectionSettings({ collection }: { collection: Collection }) {
  const shelf = useShelf();
  const manages = managesCollection(collection) && shelf.writable;

  return (
    <div className="max-w-3xl space-y-10">
      <Details collection={collection} editable={manages} />
      {shelf.mode === "server" ? <Access collection={collection} /> : null}
      {shelf.mode === "server" ? (
        <ApiSetting collection={collection} editable={manages} />
      ) : null}
      {manages ? <Danger collection={collection} /> : null}
    </div>
  );
}

function Details({
  collection,
  editable,
}: {
  collection: Collection;
  editable: boolean;
}) {
  const [name, setName] = useState(collection.name);
  const [description, setDescription] = useState(collection.description ?? "");
  const changed =
    name.trim() !== collection.name ||
    description.trim() !== (collection.description ?? "");

  return (
    <Section
      title="Details"
      description="What the collection is called and what it is for, as everyone in it sees them."
    >
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!name.trim()) return;
          updateCollection(collection.id, { name, description });
        }}
      >
        <label className="grid gap-1.5">
          <span className="text-sm font-medium">Name</span>
          <Input
            value={name}
            disabled={!editable}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="grid gap-1.5">
          <span className="text-sm font-medium">Description</span>
          <Textarea
            value={description}
            disabled={!editable}
            placeholder="What this collection is for"
            rows={3}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        {editable ? (
          <div className="flex justify-end gap-1.5">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!changed}
              onClick={() => {
                setName(collection.name);
                setDescription(collection.description ?? "");
              }}
            >
              Reset
            </Button>
            <Button type="submit" size="sm" disabled={!changed || !name.trim()}>
              Save
            </Button>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Only the collection&rsquo;s owner renames it.
          </p>
        )}
      </form>
    </Section>
  );
}

const VISIBILITY: {
  value: ShelfVisibility;
  label: string;
  icon: typeof IconLock;
  what: string;
}[] = [
  {
    value: "limited",
    label: "Limited",
    icon: IconLock,
    what: "Only you and the members below can open it, read its figures and call its API. To anyone else its link is a page that says there is no such collection.",
  },
  {
    value: "public",
    label: "Public",
    icon: IconWorld,
    what: "Anyone who can sign in to this deployment can open it with its link, read its figures and call its API. Only you and its members add series or take them out, and it is not listed on anyone else's shelf.",
  },
];

/**
 * Who may open the collection: whether beyond its people at all, and who its
 * people are.
 *
 * Nothing here is drawn ahead of the server. Adding an address that has no
 * account is refused there, and a folder shown as public that the server kept
 * limited is a link sent to a colleague that then does not open.
 */
function Access({ collection }: { collection: Collection }) {
  const shelf = useShelf();
  const me = useUser();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const members = collection.members ?? [];
  const owns = collection.role === "owner";
  const writable = shelf.writable;
  const visibility = collection.visibility ?? "limited";

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

  // A folder from before there were owners, or a deployment without
  // accounts: there is nobody to limit it to. Said as what it is, with the
  // one way out of it, rather than a choice that would fail.
  if (collection.role === "shared" || !shelf.members) {
    return (
      <Section
        title="Access"
        description={
          shelf.members
            ? "Made before collections had owners, so everyone on this deployment can see and change it. Make it yours to choose who else may open it."
            : "This deployment has no accounts, so there is nobody to limit a collection to: everyone who can reach it can open and change every collection."
        }
      >
        {shelf.members && writable ? (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => claimCollection(collection.id))}
          >
            <IconLock className="size-4" />
            Make it mine
          </Button>
        ) : null}
        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}
      </Section>
    );
  }

  return (
    <Section
      title="Access"
      description={
        owns
          ? "Who may open this collection. Members add series to it and take them out; renaming it, deleting it and deciding who else is in are yours."
          : "Who may open this collection. Only its owner changes this."
      }
    >
      <div
        role="radiogroup"
        aria-label="Who may open this collection"
        className="grid gap-2 sm:grid-cols-2"
      >
        {VISIBILITY.map((option) => {
          const chosen = visibility === option.value;
          const Icon = option.icon;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={chosen}
              disabled={!owns || !writable || busy}
              onClick={() => {
                if (!chosen) void run(() => setVisibility(collection.id, option.value));
              }}
              className={cn(
                "space-y-1 rounded-lg border p-3 text-left transition-colors",
                chosen
                  ? "border-foreground/60 bg-muted/40"
                  : "hover:bg-muted/30 disabled:hover:bg-transparent",
                "disabled:cursor-not-allowed",
                !chosen && (!owns || !writable) && "opacity-60",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <Icon className="size-4" />
                {option.label}
                {chosen ? (
                  <span className="ml-auto text-xs font-normal text-muted-foreground">
                    Current
                  </span>
                ) : null}
              </span>
              <span className="block text-xs text-muted-foreground">{option.what}</span>
            </button>
          );
        })}
      </div>

      <div className="space-y-2 pt-2">
        <h3 className="text-sm font-medium">People</h3>
        <ul className="divide-y rounded-lg border">
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

        {/* Added by the address they sign in with rather than picked from a
            list, because a list would be the deployment's staff directory
            handed to every owner. */}
        {owns && writable ? (
          <form onSubmit={add} className="flex max-w-md items-center gap-1.5">
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
              Add member
            </Button>
          </form>
        ) : null}
      </div>

      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </Section>
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
    <li className="flex items-center gap-2 px-3 py-2">
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

function ApiSetting({
  collection,
  editable,
}: {
  collection: Collection;
  editable: boolean;
}) {
  return (
    <Section
      title="API"
      description="Serve this collection's figures at their own path, so a script or a notebook can read exactly these series without naming them. The API tab has the requests."
    >
      <label
        className={cn(
          "flex items-start gap-3 rounded-lg border p-3",
          editable ? "cursor-pointer" : "opacity-80",
        )}
      >
        <Checkbox
          checked={collection.api ?? false}
          disabled={!editable}
          onCheckedChange={(checked) =>
            updateCollection(collection.id, { api: checked === true })
          }
          aria-label="Serve this collection through the API"
          className="mt-0.5"
        />
        <span className="space-y-0.5">
          <span className="flex items-center gap-2 text-sm font-medium">
            <IconApi className="size-4" />
            {collection.api ? "Served through the API" : "Not served"}
          </span>
          <span className="block text-xs text-muted-foreground">
            {editable
              ? "Off until you turn it on: an endpoint other programs poll is a promise that renaming or emptying the collection will break something."
              : "Only the collection's owner turns this on or off."}
          </span>
        </span>
      </label>
    </Section>
  );
}

function Danger({ collection }: { collection: Collection }) {
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);

  return (
    <Section
      title="Delete"
      description="Removes the collection for everyone in it, along with its API path. The series themselves stay in the warehouse; only this list of them goes."
    >
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/40 p-3">
          <p className="min-w-0 flex-1 text-sm">
            Delete &ldquo;{collection.name}&rdquo;? This cannot be undone.
          </p>
          <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => {
              deleteCollection(collection.id);
              void navigate({ to: "/collections" });
            }}
          >
            <IconTrash className="size-4" />
            Delete collection
          </Button>
        </div>
      ) : (
        <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
          <IconTrash className="size-4" />
          Delete collection…
        </Button>
      )}
    </Section>
  );
}
