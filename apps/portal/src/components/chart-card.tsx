import {
  IconDownload,
  IconMaximize,
  IconMinimize,
  IconShare,
} from "@tabler/icons-react";
import { toJpeg, toPng } from "html-to-image";
import { useEffect, useRef, useState } from "react";

import logoUrl from "~/assets/logo.png";
import { Button } from "~/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { cn } from "~/lib/utils";
import { GRID, SERIES_DARK_SLOTS, SERIES_LIGHT_SLOTS } from "~/lib/viz";

/**
 * A chart as a card that stands on its own: a title with its span, what is
 * drawn, the chart, and a footer that says where the figures come from.
 *
 * Every chart a reader might take away is framed in this one card — the
 * assistant's, a series' own, a dataset's price — so a figure lifted from any
 * of them reads as the same publication. The card downloads as PNG or JPG
 * exactly as shown, without the controls: anything marked `data-export-skip`
 * means nothing on paper and is left out of the picture.
 */
export function ChartCard({
  title,
  span,
  subtitle,
  reason,
  sources,
  link,
  note,
  plain = false,
  className,
  children,
}: {
  /** The heading: the finding where there is one, else what is drawn. */
  title: string;
  /** The periods drawn, beside the title — or in the subtitle where there is one. */
  span?: string;
  /** What is drawn, under a title that states the finding. */
  subtitle?: string;
  /** Why the chart looks the way it does, in the reader's words. */
  reason?: React.ReactNode;
  /** Who publishes the figures. */
  sources: string[];
  /** Where to read more about the figures. */
  link?: React.ReactNode;
  note?: React.ReactNode;
  /**
   * The page's own heading type rather than the serif of a publication, with
   * the span on its own line beneath — for a chart that is a section of a
   * page, not a card a reader lifts out of a reply.
   */
  plain?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const cardRef = useRef<HTMLElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const page = typeof window === "undefined" ? "" : window.location.href;

  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === cardRef.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);

  function flash(message: string) {
    setNotice(message);
    window.setTimeout(() => setNotice(null), 2000);
  }

  async function download(format: "png" | "jpeg") {
    const node = cardRef.current;
    if (!node) return;
    const background = getComputedStyle(node).backgroundColor || "#ffffff";
    // Hidden for real rather than only filtered out of the copy, so the
    // picture is as tall as what is left and not as tall as the card.
    const controls = [...node.querySelectorAll<HTMLElement>("[data-export-skip]")];
    const shownAs = controls.map((control) => control.style.display);
    controls.forEach((control) => (control.style.display = "none"));
    await new Promise((settled) => requestAnimationFrame(settled));
    const options = {
      pixelRatio: 2,
      backgroundColor: background,
      // The controls are for the page, not the picture.
      filter: (element: HTMLElement) => !element.dataset?.exportSkip,
    };
    try {
      const render = format === "png" ? toPng : toJpeg;
      let url: string;
      try {
        url = await render(node, { ...options, quality: 0.95 });
      } catch {
        // A stylesheet it cannot read (an extension's, a CDN's) stops font
        // embedding; the system fonts are an acceptable picture.
        url = await render(node, { ...options, quality: 0.95, skipFonts: true });
      }
      const anchor = document.createElement("a");
      anchor.download = `${fileName(subtitle ?? title)}.${format === "png" ? "png" : "jpg"}`;
      anchor.href = url;
      anchor.click();
    } catch {
      flash("Could not render the image.");
    } finally {
      controls.forEach((control, at) => (control.style.display = shownAs[at]!));
    }
  }

  async function share() {
    try {
      await navigator.clipboard.writeText(page);
      flash("Link copied");
    } catch {
      flash("Copy the address bar to share.");
    }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void cardRef.current?.requestFullscreen();
  }

  return (
    <figure
      ref={cardRef}
      className={cn(
        "space-y-4 rounded-xl bg-card p-5 text-card-foreground sm:p-6",
        fullscreen && "overflow-auto rounded-none",
        SERIES_DARK_SLOTS,
        className,
      )}
      style={{ ...SERIES_LIGHT_SLOTS, "--viz-grid": GRID } as React.CSSProperties}
    >
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          {/* A reader takes away the sentence at the top, so where there is a
              finding it is the title and what is drawn goes beneath it. */}
          <h3
            className={
              plain
                ? "font-heading text-lg leading-snug font-semibold tracking-tight"
                : "font-serif text-xl leading-snug font-semibold tracking-tight sm:text-2xl"
            }
          >
            {title}
            {!plain && !subtitle && span ? (
              <span className="ml-2 font-sans text-base font-normal whitespace-nowrap text-muted-foreground">
                {span}
              </span>
            ) : null}
          </h3>
          {plain && !subtitle && span ? (
            <p className="text-sm text-muted-foreground">{span}</p>
          ) : null}
          {subtitle ? (
            <p className="text-sm font-medium text-muted-foreground">
              {subtitle}
              {span ? ` · ${span}` : ""}
            </p>
          ) : null}
          {reason ? <p className="text-sm text-muted-foreground">{reason}</p> : null}
        </div>
        <img src={logoUrl} alt="Terusan" className="h-9 w-auto shrink-0" />
      </header>

      {children}

      <footer className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 text-sm">
        <div className="min-w-0 space-y-1 text-muted-foreground">
          <p>
            <span className="font-semibold text-foreground">Data source:</span>{" "}
            {sources.length ? sources.join("; ") : "Terusan catalogue"}
            {link ? <> – {link}</> : null}
          </p>
          {note ? (
            <p className="text-xs">
              <span className="font-semibold text-foreground">Note:</span> {note}
            </p>
          ) : null}
          <p className="text-xs">
            {page ? `${hostAndPath(page)} | ` : ""}Terusan · CSIS Indonesia
          </p>
        </div>

        <div data-export-skip="true" className="flex flex-wrap items-center gap-2">
          {notice ? (
            <span className="text-xs text-muted-foreground">{notice}</span>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="secondary" size="sm" />}>
              <IconDownload className="size-4" />
              Download
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => void download("png")}>
                Image (PNG)
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => void download("jpeg")}>
                Image (JPG)
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="secondary" size="sm" onClick={() => void share()}>
            <IconShare className="size-4" />
            Share
          </Button>
          <Button variant="secondary" size="sm" onClick={toggleFullscreen}>
            {fullscreen ? (
              <IconMinimize className="size-4" />
            ) : (
              <IconMaximize className="size-4" />
            )}
            {fullscreen ? "Exit full-screen" : "Enter full-screen"}
          </Button>
        </div>
      </footer>
    </figure>
  );
}

function hostAndPath(href: string): string {
  try {
    const url = new URL(href);
    return `${url.host}${url.pathname}`;
  } catch {
    return href;
  }
}

function fileName(title: string): string {
  return (
    title
      .normalize("NFKD")
      .replace(/[^\w\s-]/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .toLowerCase()
      .slice(0, 80) || "terusan-chart"
  );
}
