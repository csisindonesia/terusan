import type { ReactNode } from "react";

import logo from "~/assets/logo.png";
import preview from "~/assets/portal-preview.png";

/**
 * The frame around the pages a stranger sees: logging in and asking for an
 * account.
 *
 * Two halves, and the right one is not decoration: somebody arriving at a bare
 * form on an internal tool often cannot tell which internal tool it is. The
 * name, the one-line claim and a picture of the thing itself answer that
 * before anyone types an address.
 */
export function AuthShell({ children, footer }: { children: ReactNode; footer?: ReactNode }) {
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

          {children}

          {footer ? <div className="mt-auto">{footer}</div> : null}
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
