import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import {
  IconArrowLeft,
  IconBook,
  IconCopy,
  IconExternalLink,
  IconFileDownload,
} from "@tabler/icons-react";

import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { BELOW_STICKY_HEADER, StickyHeader } from "~/components/sticky-header";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { ButtonGroup } from "~/components/ui/button-group";
import { Skeleton } from "~/components/ui/skeleton";
import { api, documentFileUrl, type Indicator } from "~/lib/api";
import { formatBytes, formatCount, formatDate, formatRelative } from "~/lib/format";
import { DOCUMENT_TYPES } from "./documents.index";

export const Route = createFileRoute("/documents/$documentId/")({
  component: DocumentPage,
});

/**
 * How many series to list before asking.
 *
 * The 2025 handbook produced 666 of them. Listed in full the page is fifty
 * thousand pixels tall and the file — which is what most readers came for —
 * is somewhere above the fold of a scroll nobody makes. Enough to see what
 * kind of figures came out of the document, and a button for the rest.
 */
const SERIES_SHOWN = 25;

function DocumentPage() {
  const { documentId } = Route.useParams();
  const [showAllSeries, setShowAllSeries] = useState(false);

  const detail = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.document(documentId),
    retry: false,
  });

  // The series read out of this document. Asked separately rather than folded
  // into the detail response: a handbook produces hundreds, and a reader who
  // came to download the PDF should not wait on a list they may never scroll.
  const indicators = useQuery({
    queryKey: ["document-indicators", documentId],
    queryFn: () => api.documentIndicators(documentId),
    enabled: detail.isSuccess,
  });

  if (detail.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const document = detail.data?.data;
  if (!document) {
    return (
      <div className="space-y-3">
        <Link to="/documents" className="text-sm text-muted-foreground hover:underline">
          <IconArrowLeft className="mr-1 inline size-4" />
          Documents
        </Link>
        <p className="text-sm">No document is catalogued under {documentId}.</p>
      </div>
    );
  }

  const kind = DOCUMENT_TYPES[document.document_type];
  const series = indicators.data?.data ?? [];
  // Only a PDF renders in the frame: the API grants inline for that alone, and
  // anything else would load as a download prompt inside the page.
  const readable = document.file_available && document.media_type === "application/pdf";

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <div className="space-y-3">
            <Link
              to="/documents"
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
            >
              <IconArrowLeft className="size-4" />
              Documents
            </Link>
            <PageHeader
              title={document.title}
              description={[document.subtitle, document.publisher]
                .filter(Boolean)
                .join(" · ")}
              actions={
                <div className="flex items-center gap-2">
                  {/* Read and Download, joined: they are one decision about
                      the same file — open it here or keep it — and two
                      separate buttons would read as two unrelated offers.
                      Read comes first because it is the cheaper one to try. */}
                  {document.file_available ? (
                    <ButtonGroup>
                      {readable ? (
                        <Button
                          variant="outline"
                          size="sm"
                          render={
                            <Link
                              to="/documents/$documentId/preview"
                              params={{ documentId: document.document_id }}
                            />
                          }
                        >
                          <IconBook className="size-4" />
                          Read
                        </Button>
                      ) : null}
                      <Button
                        variant="outline"
                        size="sm"
                        render={
                          <a href={documentFileUrl(document.document_id)} download />
                        }
                      >
                        <IconFileDownload className="size-4" />
                        Download
                      </Button>
                    </ButtonGroup>
                  ) : null}
                  <RowActions
                    actions={[
                      {
                        label: "Copy document id",
                        icon: IconCopy,
                        onSelect: () => void copyToClipboard(document.document_id),
                      },
                      {
                        label: "Copy content hash",
                        icon: IconCopy,
                        onSelect: () =>
                          document.content_hash
                            ? void copyToClipboard(document.content_hash)
                            : undefined,
                        hint: document.content_hash
                          ? undefined
                          : "No hash was recorded for this document",
                      },
                      ...(document.source_url
                        ? [
                            {
                              label: "Open at the publisher",
                              icon: IconExternalLink,
                              onSelect: () =>
                                window.open(document.source_url, "_blank"),
                            },
                          ]
                        : []),
                    ]}
                  />
                </div>
              }
            />
          </div>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
        {/* Metadata on the left, as on the indicator and regulation pages: it
            is what a reader checks while deciding whether this is the document
            they want. */}
        <aside className={`lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
          <h2 className="font-heading text-sm font-semibold tracking-tight">About</h2>
          <dl className="mt-3 space-y-3">
            <Fact
              label="Kind"
              value={kind?.label ?? document.document_type}
              hint={kind?.hint}
            />
            <Fact label="Published" value={formatDate(document.published_at)} />
            <Fact label="Publisher" value={document.publisher} />
            <Fact label="Format" value={document.media_type} />
            <Fact label="Size" value={formatBytes(document.size_bytes)} />
            <Fact
              label="Pages"
              value={document.page_count ? formatCount(document.page_count) : undefined}
            />
            <Fact label="Language" value={document.language} />
            {/* Where it sits in the lake. A reader following a figure back to
                its source wants the collection as well as the file. */}
            <div>
              <dt className="text-xs font-medium text-muted-foreground">Dataset</dt>
              <dd className="mt-0.5 text-sm">
                {document.dataset_id ? (
                  <Link
                    to="/datasets/$datasetId"
                    params={{ datasetId: document.dataset_id }}
                    className="underline underline-offset-4 hover:text-foreground"
                  >
                    {document.dataset_slug ?? document.dataset_id}
                  </Link>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </dd>
            </div>
            <Fact
              label="Source"
              value={document.source_name ?? document.source_id}
              hint={document.license}
            />
            <Fact
              label="Collected"
              value={formatDate(document.retrieved_at)}
              hint={formatRelative(document.retrieved_at)}
            />
            <Fact label="Original filename" value={document.original_filename} mono />
            {/* The identity of the bytes. This is what makes "is this the same
                document I read last year" answerable, so it is shown in full
                rather than truncated. */}
            <Fact label="Content hash" value={document.content_hash} mono />
            <Fact label="Layer" value="silver" hint="The document catalogue" />
          </dl>
        </aside>

        <div className="min-w-0 space-y-6">
          {/* Where the document actually is — both copies, and what each one
              is for. The distinction matters: a link to an agency's site is a
              citation that will eventually break, and the preserved copy is
              the one that will not (program.md §2.1). */}
          <Section
            title="The document"
            description={
              readable
                ? "The copy this warehouse preserved, as it was published."
                : "The original as it was retrieved, and where the publisher put it."
            }
          >
            <div className="space-y-3">
              {/* A link to the reader rather than the reader itself. The two
                  are different sittings — this page answers "is this the
                  document I want and where did it come from", and reading it
                  is a page of its own, shareable as one. */}
              {readable ? (
                <p className="text-sm">
                  <Link
                    to="/documents/$documentId/preview"
                    params={{ documentId: document.document_id }}
                    className="underline underline-offset-4"
                  >
                    Read it here
                  </Link>{" "}
                  <span className="text-muted-foreground">
                    —{" "}
                    {document.page_count
                      ? `${formatCount(document.page_count)} pages, in the browser.`
                      : "in the browser."}
                  </span>
                </p>
              ) : null}

              <div className="space-y-2 text-sm">
                {document.file_available ? (
                  <p>
                    <a
                      href={documentFileUrl(document.document_id)}
                      download
                      className="underline underline-offset-4"
                    >
                      Download the preserved copy
                    </a>{" "}
                    <span className="text-muted-foreground">
                      — {formatBytes(document.size_bytes)}
                      {document.media_type ? `, ${document.media_type}` : ""}. The bytes
                      exactly as they arrived, kept so the figures stay checkable after
                      the publisher moves the file.
                    </span>
                  </p>
                ) : (
                  <p className="text-muted-foreground">
                    The preserved copy is not served from this deployment. Follow the
                    publisher's link below.
                  </p>
                )}
                {document.source_url ? (
                  <p className="break-all">
                    <a
                      href={document.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="underline underline-offset-4"
                    >
                      {document.source_url}
                    </a>
                    <span className="text-muted-foreground">
                      {" "}
                      — where it was published. This is the citation.
                    </span>
                  </p>
                ) : null}
              </div>
            </div>
          </Section>

          {/* What came out of it. This is the link the catalogue exists for:
              every observation carries the document it was read from, so this
              list is the figures answering for themselves rather than the
              catalogue claiming them. */}
          <Section
            title="Figures read from this document"
            description={
              document.observation_count
                ? `${formatCount(document.observation_count)} observations across ${formatCount(document.indicator_count)} series.`
                : "Nothing has been read out of this document yet."
            }
          >
            {indicators.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
              </div>
            ) : series.length ? (
              <>
                <ul className="divide-y">
                  {(showAllSeries ? series : series.slice(0, SERIES_SHOWN)).map(
                    (indicator) => (
                      <SeriesRow key={indicator.indicator_id} indicator={indicator} />
                    ),
                  )}
                </ul>
                {series.length > SERIES_SHOWN ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowAllSeries((shown) => !shown)}
                  >
                    {showAllSeries
                      ? `Show the first ${formatCount(SERIES_SHOWN)}`
                      : `Show all ${formatCount(series.length)}`}
                  </Button>
                ) : null}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                It was collected and preserved, but no extractor has read figures out of
                it. That is a gap in the pipeline rather than in the document.
              </p>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}

function SeriesRow({ indicator }: { indicator: Indicator }) {
  return (
    <li className="flex items-baseline justify-between gap-4 py-2">
      <Link
        to="/indicators/$indicatorId"
        params={{ indicatorId: indicator.indicator_id }}
        className="min-w-0 text-sm underline-offset-4 hover:underline"
      >
        <span className="line-clamp-2">
          {indicator.name ?? indicator.slug ?? indicator.indicator_id}
        </span>
      </Link>
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
        {indicator.unit ? (
          <Badge variant="outline" className="mr-2 font-normal">
            {indicator.unit}
          </Badge>
        ) : null}
        {formatCount(indicator.observations)}
      </span>
    </li>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="font-heading text-sm font-semibold tracking-tight">{title}</h2>
        {description ? (
          <p className="text-xs text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

function Fact({
  label,
  value,
  hint,
  mono,
}: {
  label: string;
  value?: string;
  hint?: string;
  /** For a hash or a filename, which is read character by character. */
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className={`mt-0.5 text-sm${mono ? " font-mono text-xs break-all" : ""}`}>
        {value ?? <span className="text-muted-foreground">—</span>}
        {hint ? (
          <span className="ml-1.5 text-xs text-muted-foreground">{hint}</span>
        ) : null}
      </dd>
    </div>
  );
}
