import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  IconAlertTriangle,
  IconEye,
  IconEyeOff,
  IconKey,
  IconLock,
  IconMail,
} from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { z } from "zod";

import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldGroup, FieldLabel } from "~/components/ui/field";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "~/components/ui/input-group";
import { ApiRequestError, api } from "~/lib/api";
import { SESSION_KEY, useSessionState } from "~/lib/session";
import { asText, textParam } from "~/lib/search-params";
import logo from "~/assets/logo.png";
import preview from "~/assets/portal-preview.png";

/**
 * The way in.
 *
 * Two halves, and the right one is not decoration: somebody arriving at a bare
 * login form on an internal tool often cannot tell which internal tool it is.
 * The name, the one-line claim and a picture of the thing itself answer that
 * before anyone types an address.
 *
 * The form is deliberately plain. One account, one password, no sign-up — the
 * portal serves one organisation's warehouse, and an account is granted rather
 * than taken (`services/api/cmd/authctl`). What is not built is said rather
 * than implied: the single-sign-on button is visibly unavailable instead of
 * being left out, because "can I use my work login" is the first question
 * anyone asks of a page like this.
 */

const searchSchema = z.object({
  /** Where to go once in — the page that sent them here. */
  redirect: textParam,
});

export const Route = createFileRoute("/login")({
  validateSearch: searchSchema,
  component: Login,
});

function Login() {
  const search = Route.useSearch();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { session, hasAuth, isLoading, isUnreachable } = useSessionState();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  // Off by default, and never remembered between visits: the reason to show a
  // password is a typo on this attempt, not a preference — and a field that
  // comes back readable is one somebody eventually types into over a shoulder.
  const [showPassword, setShowPassword] = useState(false);

  // Only ever a path within the portal. A `redirect` that could name another
  // site turns a login page into an open redirect, which is how phishing links
  // borrow somebody else's domain.
  const target = safeRedirect(asText(search.redirect));

  const login = useMutation({
    mutationFn: () => api.login(email.trim(), password, remember),
    onSuccess: async (result) => {
      queryClient.setQueryData(SESSION_KEY, result.data);
      // Everything fetched while signed out was fetched as nobody; a shelf in
      // particular answers differently now.
      await queryClient.invalidateQueries();
      void navigate({ to: target, replace: true });
    },
  });

  // A browser that is already signed in has no business on this page — the
  // usual way here is a bookmark, or the back button after signing in.
  useEffect(() => {
    if (session) void navigate({ to: target, replace: true });
  }, [session, target, navigate]);

  return (
    <div className="flex min-h-svh flex-col bg-muted/30 p-3 lg:flex-row lg:p-4">
      <div className="flex flex-1 items-center justify-center px-4 py-10 sm:px-8">
        <div className="flex w-full max-w-[26rem] flex-col gap-10">
          <div className="flex items-center gap-2.5">
            <img src={logo} alt="" className="size-8 rounded-full" />
            <span className="font-heading text-2xl font-semibold tracking-tight">
              Terusan
            </span>
          </div>

          <div className="space-y-1.5">
            <h1 className="font-heading text-3xl font-semibold tracking-tight">
              Log in to your account
            </h1>
            <p className="text-sm text-muted-foreground">Please enter your details</p>
          </div>

          {/* The form is what this page is for, so it is drawn first and
              replaced only once the API has actually said otherwise. Waiting
              for the capability check would show a skeleton where the email
              field goes on every single load, including the fast ones. */}
          {isUnreachable ? (
            <Alert variant="destructive">
              <IconAlertTriangle />
              <AlertDescription>
                The API is not answering, so nobody can be signed in. Start it with{" "}
                <code className="font-mono">make dev-api</code>, or check{" "}
                <code className="font-mono">VITE_API_URL</code>.
              </AlertDescription>
            </Alert>
          ) : !isLoading && !hasAuth ? (
            <Alert>
              <IconKey />
              <AlertDescription>
                {/* A deployment without an application database has no accounts
                    at all. Saying so beats a form that refuses every password. */}
                This deployment keeps no accounts, so there is nothing to log in to. Set{" "}
                <code className="font-mono">APP_DB</code> where the API runs, then
                create one with <code className="font-mono">authctl create</code>.{" "}
                <Link to="/" className="underline underline-offset-4">
                  Go to the portal
                </Link>
                .
              </AlertDescription>
            </Alert>
          ) : (
            <form
              className="space-y-6"
              onSubmit={(event) => {
                event.preventDefault();
                if (!email.trim() || !password) return;
                login.mutate();
              }}
            >
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="email">Email</FieldLabel>
                  <InputGroup className="h-11">
                    <InputGroupAddon>
                      <IconMail className="size-4 text-muted-foreground" />
                    </InputGroupAddon>
                    <InputGroupInput
                      id="email"
                      type="email"
                      name="email"
                      autoComplete="username"
                      autoFocus
                      required
                      placeholder="Enter your email"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
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
                      type={showPassword ? "text" : "password"}
                      name="password"
                      autoComplete="current-password"
                      required
                      placeholder="••••••••"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                    />
                    <InputGroupAddon align="inline-end">
                      {/* A button rather than a checkbox, and labelled for a
                          screen reader as what it does now — "Show password"
                          while it is hidden — with the state on the control
                          itself so an assistive reader is not left guessing
                          which way round it is. */}
                      <InputGroupButton
                        size="icon-xs"
                        aria-label={showPassword ? "Hide password" : "Show password"}
                        aria-pressed={showPassword}
                        title={showPassword ? "Hide password" : "Show password"}
                        onClick={() => setShowPassword((shown) => !shown)}
                      >
                        {showPassword ? (
                          <IconEyeOff className="text-muted-foreground" />
                        ) : (
                          <IconEye className="text-muted-foreground" />
                        )}
                      </InputGroupButton>
                    </InputGroupAddon>
                  </InputGroup>
                </Field>
              </FieldGroup>

              {login.isError ? (
                <Alert variant="destructive">
                  <IconAlertTriangle />
                  <AlertDescription>{loginError(login.error)}</AlertDescription>
                </Alert>
              ) : null}

              <div className="flex items-center justify-between gap-3">
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={remember}
                    onCheckedChange={(checked) => setRemember(checked === true)}
                  />
                  {/* Thirty days is the API's own default for a remembered
                      session, so the label is a fact rather than a promise the
                      server does not keep. */}
                  Remember for 30 days
                </label>
                <Link
                  to="/help"
                  className="text-sm font-medium text-primary underline-offset-4 hover:underline"
                >
                  Forgot password
                </Link>
              </div>

              <Button
                type="submit"
                size="lg"
                className="h-11 w-full"
                disabled={login.isPending || !email.trim() || !password}
              >
                {login.isPending ? "Logging in…" : "Log in"}
              </Button>

              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span className="h-px flex-1 bg-border" />
                OR
                <span className="h-px flex-1 bg-border" />
              </div>

              {/* Listed and disabled, like the unbuilt pages in the sidebar:
                  showing the shape of the thing is honest, wiring a button to
                  nothing is not. */}
              <Button
                type="button"
                variant="outline"
                size="lg"
                className="h-11 w-full"
                disabled
                title="No identity provider is configured for this deployment"
              >
                <IconKey className="size-4" />
                Log in with single sign-on
              </Button>
            </form>
          )}

          <p className="mt-auto text-sm text-muted-foreground">
            Accounts are issued by whoever runs this deployment. Read{" "}
            <Link to="/about" className="underline underline-offset-4">
              what this portal is
            </Link>
            .
          </p>
        </div>
      </div>

      {/* The brand half. Hidden on a phone, where it would push the form off
          the first screen for no gain. */}
      <div className="relative hidden flex-1 overflow-hidden rounded-2xl bg-primary lg:block">
        {/* Two soft washes rather than a flat fill: the screenshot below is
            mostly white, and a flat panel makes it look pasted on. */}
        <div
          aria-hidden
          className="absolute inset-0 bg-[radial-gradient(120%_120%_at_10%_0%,color-mix(in_oklab,var(--primary),white_18%)_0%,var(--primary)_45%,color-mix(in_oklab,var(--primary),black_25%)_100%)]"
        />
        <div className="relative flex h-full flex-col gap-10 p-10 xl:p-14">
          <div className="flex items-center gap-2.5">
            <img src={logo} alt="" className="size-9 rounded-full bg-white/90 p-1" />
            <span className="font-heading text-2xl font-semibold tracking-tight text-primary-foreground">
              Terusan
            </span>
          </div>

          <div className="max-w-md">
            <h2 className="font-heading text-4xl leading-tight font-semibold text-primary-foreground xl:text-5xl">
              Every figure, back to its source
            </h2>
            <p className="mt-4 text-base text-primary-foreground/75">
              Indonesian statistics, the documents they were read out of, and the
              regulations around them — one warehouse, one provenance trail.
            </p>
          </div>

          {/* Tilted and bleeding off the edge, so it reads as a window onto
              something larger rather than as a framed thumbnail. */}
          <div className="relative -mr-24 mt-auto -mb-16 xl:-mr-32">
            <img
              src={preview}
              alt="The Terusan portal, showing the data explorer"
              className="w-full max-w-4xl rotate-[-6deg] rounded-xl shadow-2xl ring-1 ring-white/20"
              loading="lazy"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * What to say when a login fails.
 *
 * The API's own words where it has any: "too many attempts, try again in 1m50s"
 * is worth reading, and a generic "login failed" would throw it away. The one
 * thing it never says, by design, is whether the address exists.
 */
function loginError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (error.status === 401) return "That email and password do not match an account.";
    return error.message;
  }
  return "The API could not be reached. Check that it is running.";
}

/**
 * A redirect target that cannot leave the portal.
 *
 * Anything that is not a plain path — a full URL, a protocol-relative `//host`
 * — is dropped and the reader lands on the overview instead.
 */
function safeRedirect(value: string | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}
