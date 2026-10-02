import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

import { AuthShell } from "~/components/auth-shell";
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
import { ApiRequestError, api, microsoftLoginUrl } from "~/lib/api";
import { SESSION_KEY, capabilitiesQuery, useSessionState } from "~/lib/session";
import { portalPath } from "~/lib/paths";
import { asText, textParam } from "~/lib/search-params";
/**
 * The way in.
 *
 * The form is deliberately plain: one account, one password. Somebody without
 * an account can ask for one (`/register`), which an admin then approves from
 * the Users page — an account is granted rather than taken. What is not built
 * is said rather than implied: the single-sign-on button is visibly unavailable instead of
 * being left out, because "can I use my work login" is the first question
 * anyone asks of a page like this.
 */

const searchSchema = z.object({
  /** Where to go once in — the page that sent them here. */
  redirect: textParam,
  /** Why a Microsoft sign-in came back without a session (see sso.go). */
  sso_error: textParam,
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
  const capabilities = useQuery(capabilitiesQuery).data;
  const registration = capabilities?.registration ?? false;
  const microsoft = capabilities?.microsoft_login ?? false;
  const ssoError = ssoErrorMessage(asText(search.sso_error));

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
    <AuthShell
      footer={
        <p className="text-sm text-muted-foreground">
          No account?{" "}
          {registration ? (
            <Link
              to="/register"
              className="font-medium text-primary underline-offset-4 hover:underline"
            >
              Request access
            </Link>
          ) : (
            "Accounts are issued by whoever runs this deployment"
          )}
          . Read{" "}
          <Link to="/about" className="underline underline-offset-4">
            what this portal is
          </Link>
          .
        </p>
      }
    >
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
            <code className="font-mono">APP_DB</code> where the API runs, then create
            one with <code className="font-mono">authctl create</code>.{" "}
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

          {ssoError && !login.isError ? (
            <Alert variant="destructive">
              <IconAlertTriangle />
              <AlertDescription>{ssoError}</AlertDescription>
            </Alert>
          ) : null}

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

          {microsoft ? (
            // A plain link: the browser has to leave for Microsoft's pages,
            // and the "remember" box above applies to this way in too.
            <Button
              variant="outline"
              size="lg"
              className="h-11 w-full"
              nativeButton={false}
              render={<a href={microsoftLoginUrl(target, remember)} />}
            >
              <MicrosoftLogo />
              Log in with Microsoft 365
            </Button>
          ) : (
            // Listed and disabled, like the unbuilt pages in the sidebar:
            // showing the shape of the thing is honest, wiring a button to
            // nothing is not. Branded all the same, so a reader recognises
            // the office login they were told to expect and can see it is
            // switched off here rather than missing.
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="h-11 w-full"
              disabled
              title="Microsoft 365 sign-in is not configured for this deployment"
            >
              <MicrosoftLogo />
              Log in with Microsoft 365
            </Button>
          )}
        </form>
      )}
    </AuthShell>
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
 * What to say when a Microsoft sign-in came back without a session.
 *
 * The API sends one of a fixed set of words rather than any text of its own or
 * Microsoft's, so what a reader sees here is only ever what is written below —
 * a link somebody crafted cannot put words on this page.
 */
function ssoErrorMessage(code: string | undefined): string | null {
  switch (code) {
    case undefined:
    case "":
      return null;
    case "cancelled":
      return "The Microsoft sign-in was cancelled.";
    case "expired":
      return "That sign-in took too long or was started in another browser. Try again.";
    case "domain":
      return "That Microsoft account is not on one of this office's domains.";
    case "pending":
      return "Your account has been requested and is waiting for an administrator to approve it.";
    case "not_invited":
      return "There is no account here for that address yet. Ask whoever administers this deployment.";
    case "disabled":
      return "This account is disabled. Ask whoever administers this deployment.";
    case "access_ended":
      return "This account's access period has ended. Ask whoever administers this deployment.";
    case "conflict":
      return "This address is already linked to a different Microsoft account. Ask an administrator to check it.";
    case "queue_full":
      return "Too many requests for access are waiting. Try again later.";
    case "unavailable":
      return "Microsoft sign-in is not configured on this deployment.";
    case "refused":
      return "Microsoft refused the sign-in. Your office may need to grant this application access.";
    default:
      return "The Microsoft sign-in did not complete. Try again, or use your password.";
  }
}

/** The four squares, in Microsoft's own colours, which is what people look for. */
function MicrosoftLogo() {
  return (
    <svg viewBox="0 0 21 21" className="size-4" aria-hidden="true">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

/**
 * A redirect target that cannot leave the portal.
 *
 * Anything that is not a plain path — a full URL, a protocol-relative `//host`,
 * a `/\host` a browser reads the same way — is dropped and the reader lands on
 * the overview instead.
 */
function safeRedirect(value: string | undefined): string {
  return portalPath(value);
}
