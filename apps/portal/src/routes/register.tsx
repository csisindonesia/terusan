import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import {
  IconAlertTriangle,
  IconBuilding,
  IconCircleCheck,
  IconLock,
  IconMail,
  IconUser,
} from "@tabler/icons-react";
import { useState } from "react";

import { AuthShell } from "~/components/auth-shell";
import { errorMessage } from "~/components/user-dialogs";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { InputGroup, InputGroupAddon, InputGroupInput } from "~/components/ui/input-group";
import { api } from "~/lib/api";
import { capabilitiesQuery } from "~/lib/session";

/**
 * Asking for an account.
 *
 * Nobody gets in by filling this: a request lands as a pending researcher
 * account, which cannot sign in until an admin approves it from the Users page
 * and decides whether it is a researcher, a guest with an end date, or an
 * admin. The page says so up front, so nobody submits it and then wonders why
 * the login still refuses them.
 *
 * The answer is the same whether or not the address already had an account,
 * so the form cannot be used to find out who is registered.
 */

export const Route = createFileRoute("/register")({
  component: Register,
});

const MIN_PASSWORD = 12;

function Register() {
  const capabilities = useQuery(capabilitiesQuery);
  const open = capabilities.data?.registration ?? false;

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [department, setDepartment] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  const mismatch = confirm.length > 0 && password !== confirm;
  const short = password.length > 0 && password.length < MIN_PASSWORD;
  const ready =
    name.trim() && email.trim() && password.length >= MIN_PASSWORD && password === confirm;

  const register = useMutation({
    mutationFn: () =>
      api.register({
        name: name.trim(),
        email: email.trim(),
        department: department.trim(),
        password,
      }),
  });

  const footer = (
    <p className="text-sm text-muted-foreground">
      Already have an account?{" "}
      <Link
        to="/login"
        className="font-medium text-primary underline-offset-4 hover:underline"
      >
        Log in
      </Link>
    </p>
  );

  if (register.isSuccess) {
    return (
      <AuthShell footer={footer}>
        <div className="space-y-4">
          <IconCircleCheck className="size-10 text-primary" />
          <h1 className="font-heading text-3xl font-semibold tracking-tight">
            Request sent
          </h1>
          <p className="text-sm text-muted-foreground">
            An administrator will review it. Once approved you can log in as{" "}
            <span className="font-medium text-foreground">{email.trim()}</span> with the
            password you just chose.
          </p>
          <Button variant="outline" render={<Link to="/login" />}>
            Back to log in
          </Button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell footer={footer}>
      <div className="space-y-1.5">
        <h1 className="font-heading text-3xl font-semibold tracking-tight">
          Request access
        </h1>
        <p className="text-sm text-muted-foreground">
          An administrator approves every account before it can sign in.
        </p>
      </div>

      {!capabilities.isLoading && !open ? (
        <Alert>
          <IconAlertTriangle />
          <AlertDescription>
            This deployment does not take requests. Ask whoever runs it for an account,
            or read{" "}
            <Link to="/contact" className="underline underline-offset-4">
              the contact page
            </Link>
            .
          </AlertDescription>
        </Alert>
      ) : (
        <form
          className="space-y-6"
          onSubmit={(event) => {
            event.preventDefault();
            if (ready) register.mutate();
          }}
        >
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="name">Full name</FieldLabel>
              <InputGroup className="h-11">
                <InputGroupAddon>
                  <IconUser className="size-4 text-muted-foreground" />
                </InputGroupAddon>
                <InputGroupInput
                  id="name"
                  autoComplete="name"
                  autoFocus
                  required
                  maxLength={120}
                  placeholder="Your name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </InputGroup>
            </Field>

            <Field>
              <FieldLabel htmlFor="email">Work email</FieldLabel>
              <InputGroup className="h-11">
                <InputGroupAddon>
                  <IconMail className="size-4 text-muted-foreground" />
                </InputGroupAddon>
                <InputGroupInput
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  placeholder="name@csis.or.id"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </InputGroup>
            </Field>

            <Field>
              <FieldLabel htmlFor="department">Department</FieldLabel>
              <InputGroup className="h-11">
                <InputGroupAddon>
                  <IconBuilding className="size-4 text-muted-foreground" />
                </InputGroupAddon>
                <InputGroupInput
                  id="department"
                  autoComplete="organization-title"
                  maxLength={120}
                  placeholder="e.g. Economics"
                  value={department}
                  onChange={(event) => setDepartment(event.target.value)}
                />
              </InputGroup>
            </Field>

            <Field>
              <FieldLabel htmlFor="password">Password</FieldLabel>
              <InputGroup className="h-11">
                <InputGroupAddon>
                  <IconLock className="size-4 text-muted-foreground" />
                </InputGroupAddon>
                <InputGroupInput
                  id="password"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={password}
                  aria-invalid={short || undefined}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </InputGroup>
              <FieldDescription>
                At least {MIN_PASSWORD} characters. A short phrase is easier to remember
                than symbols.
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel htmlFor="confirm">Confirm password</FieldLabel>
              <InputGroup className="h-11">
                <InputGroupAddon>
                  <IconLock className="size-4 text-muted-foreground" />
                </InputGroupAddon>
                <InputGroupInput
                  id="confirm"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={confirm}
                  aria-invalid={mismatch || undefined}
                  onChange={(event) => setConfirm(event.target.value)}
                />
              </InputGroup>
              {mismatch ? (
                <FieldDescription className="text-destructive">
                  The two passwords do not match.
                </FieldDescription>
              ) : null}
            </Field>
          </FieldGroup>

          {register.isError ? (
            <Alert variant="destructive">
              <IconAlertTriangle />
              <AlertDescription>{errorMessage(register.error)}</AlertDescription>
            </Alert>
          ) : null}

          <Button
            type="submit"
            size="lg"
            className="h-11 w-full"
            disabled={!ready || register.isPending}
          >
            {register.isPending ? "Sending…" : "Request access"}
          </Button>
        </form>
      )}
    </AuthShell>
  );
}
