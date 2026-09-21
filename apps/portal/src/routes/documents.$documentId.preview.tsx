import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { IconArrowLeft, IconExternalLink, IconFileDownload } from "@tabler/icons-react";

import { Button } from "~/components/ui/button";
import { ButtonGroup } from "~/components/ui/button-group";
import { Skeleton } from "~/components/ui/skeleton";
import { api, documentFileUrl } from "~/lib/api";
import { formatBytes } from "~/lib/format";

export const Route = createFileRoute("/documents/$documentId/preview")({
  component: DocumentPreviewPage,
});

/**
 * The document, read rather than described.
 *
 * A page of its own instead of a panel on the detail page, because the two are
 * different sittings: the detail page answers "is this the document I want and
 * where did it come from", and this one is for reading it. Sharing the reading
 * view as its own URL is also the point — a colleague sent this link opens the
 * handbook, not a page about the handbook.
 *
 * The chrome is deliberately thin. Everything a reader needs while reading —
 * search, zoom, page navigation, printing — belongs to the viewer inside the
 * frame, and a second set of controls around it would only compete.
 */
function DocumentPreviewPage() {
  const { documentId } = Route.useParams();

  const detail = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.document(documentId),
    retry: false,
  });

  if (detail.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-[70vh] w-full" />
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

  // Only a PDF renders in the frame: the API grants inline for that alone, and
  // anything else would load as a download prompt inside the page.
  const readable = document.file_available && document.media_type === "application/pdf";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          {/* Back to the record, not to the list: a reader who came here to
              check a figure wants the licence and the provenance next, and the
              list is one more click from there. */}
          <Link
            to="/documents/$documentId"
            params={{ documentId }}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
          >
            <IconArrowLeft className="size-4" />
            {document.title}
          </Link>
          <p className="text-xs text-muted-foreground">
            {[
              document.subtitle,
              document.publisher,
              document.page_count ? `${document.page_count} pages` : undefined,
              formatBytes(document.size_bytes),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>

        {/* The two things to do with a file you are looking at, joined because
            they are one decision — keep it, or go to the source. */}
        <ButtonGroup>
          <Button
            variant="outline"
            size="sm"
            render={<a href={documentFileUrl(document.document_id)} download />}
          >
            <IconFileDownload className="size-4" />
            Download
          </Button>
          {document.source_url ? (
            <Button
              variant="outline"
              size="sm"
              render={<a href={document.source_url} target="_blank" rel="noreferrer" />}
            >
              <IconExternalLink className="size-4" />
              At the publisher
            </Button>
          ) : null}
        </ButtonGroup>
      </div>

      {readable ? (
        // An iframe of the API's own inline route rather than a bundled
        // renderer: the browser's PDF viewer already has search, zoom, page
        // navigation and printing, and reimplementing those badly would be the
        // only thing a library bought us. The route serves it sandboxed, and
        // for PDFs alone.
        <iframe
          src={documentFileUrl(document.document_id, {
            inline: true,
            name: document.original_filename,
          })}
          title={document.title}
          className="h-[calc(100vh-13rem)] min-h-[34rem] w-full rounded-lg border bg-muted"
        />
      ) : (
        <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
          {document.file_available
            ? // Said plainly rather than shown as an empty frame: a reader who
              // followed a Read link deserves to know why there is nothing to
              // read, and the file is still theirs to take.
              `A ${document.media_type ?? "file"} cannot be read in the browser. Download it to open it.`
            : "The preserved copy is not served from this deployment."}
        </div>
      )}
    </div>
  );
}
