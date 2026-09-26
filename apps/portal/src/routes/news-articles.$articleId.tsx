import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { z } from "zod";
import {
  IconArrowLeft,
  IconCopy,
  IconExternalLink,
  IconPhoto,
} from "@tabler/icons-react";

import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { BELOW_STICKY_HEADER, StickyHeader } from "~/components/sticky-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { api, newsScreenshotUrl, type NewsArticleDetail } from "~/lib/api";
import { formatCount, formatDate, formatRelative } from "~/lib/format";

/** Which tab is open, so a link can point straight at the page as collected. */
const searchSchema = z.object({
  tab: z.enum(["overview", "page"]).optional(),
});

export const Route = createFileRoute("/news-articles/$articleId")({
  validateSearch: searchSchema,
  component: NewsArticlePage,
});

/**
 * The coded fields, in the order a coder fills them.
 *
 * Not alphabetical and not the order the columns happen to sit in: where and
 * when, then what happened, then who, then what it cost. It is how the VEWS
 * form reads, and a verifier checking a coding against an article reads down
 * it in that order.
 */
const CODED_FIELDS: {
  label: string;
  of: (coding: NonNullable<NewsArticleDetail["coding"]>) => string | undefined;
  /** Which classifier answer backs this field, for the confidence figure. */
  confidence?: string;
  /**
   * True where the answer is a yes/no.
   *
   * The classifier reports the probability of *yes*, so beside a `TIDAK` it
   * has to be inverted — otherwise "nobody intervened, 50%" reads as the model
   * being half sure nobody did, when it means it was half sure somebody had.
   */
  yesNo?: boolean;
}[] = [
  { label: "District", of: (c) => c.district_city },
  { label: "Province", of: (c) => c.province },
  { label: "Date", of: (c) => c.date },
  {
    label: "Form of violence",
    of: (c) => c.violence_form,
    confidence: "violence_form1",
  },
  { label: "Weapon", of: (c) => c.weapon_type, confidence: "weapon_type1" },
  { label: "Issue", of: (c) => c.issue_type, confidence: "issue_type1" },
  { label: "First side", of: (c) => c.actor1, confidence: "actor1a" },
  { label: "Second side", of: (c) => c.actor2, confidence: "actor2a" },
  {
    label: "Anyone intervened",
    of: (c) => c.intervene,
    confidence: "intervene",
    yesNo: true,
  },
  // Last, and the only field here VEWS has no column for: how far it got. A
  // count of incidents cannot say whether they are getting worse — a standoff
  // that dispersed and a village burned down are both one incident.
  { label: "Escalation", of: (c) => c.escalation, confidence: "escalation" },
];

function NewsArticlePage() {
  const { articleId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const tab = search.tab ?? "overview";

  const detail = useQuery({
    queryKey: ["news-article", articleId],
    queryFn: () => api.newsArticle(articleId),
    retry: false,
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

  const article = detail.data?.data;
  if (!article) {
    return (
      <div className="space-y-3">
        <Link
          to="/news-outlets"
          className="text-sm text-muted-foreground hover:underline"
        >
          <IconArrowLeft className="mr-1 inline size-4" />
          News outlets
        </Link>
        <p className="text-sm">No article is collected under {articleId}.</p>
      </div>
    );
  }

  const coding = article.coding;
  const coded = coding?.accepted === true;
  const awaiting = coding?.accepted === null;

  return (
    <div className="space-y-5">
      {/* The tab rides in the header with the title, as on the indicator page,
          and the choice lives in the URL: a colleague sent a link to the page
          as it was collected should land on it, not on a page describing it.
          That is what the documents preview buys by being its own route. */}
      <Tabs
        value={tab}
        onValueChange={(next) =>
          navigate({
            search: (prev) => ({
              ...prev,
              tab: next === "overview" ? undefined : (next as "page"),
            }),
          })
        }
      >
        <StickyHeader
          heading={
            <div className="space-y-3">
              <Link
                to="/news-outlets/$host"
                params={{ host: article.outlet_host }}
                className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
              >
                <IconArrowLeft className="size-4" />
                {article.outlet}
              </Link>
              <PageHeader
                title={article.title}
                description={[
                  article.outlet,
                  article.outlet_province,
                  formatDate(article.published_at),
                ]
                  .filter(Boolean)
                  .join(" · ")}
                actions={
                  <div className="flex items-center gap-2">
                    {/* The screenshot has a tab now, so the action opens the
                      image itself — the size a browser will actually zoom. */}
                    {article.screenshot ? (
                      <Button
                        variant="outline"
                        size="sm"
                        render={
                          <a
                            href={newsScreenshotUrl(article.document_id)}
                            target="_blank"
                            rel="noreferrer"
                          />
                        }
                      >
                        <IconPhoto className="size-4" />
                        Open the image
                      </Button>
                    ) : null}
                    <Button
                      variant="outline"
                      size="sm"
                      render={<a href={article.url} target="_blank" rel="noreferrer" />}
                    >
                      <IconExternalLink className="size-4" />
                      Read at the paper
                    </Button>
                    <RowActions
                      actions={[
                        {
                          label: "Copy document id",
                          icon: IconCopy,
                          onSelect: () => void copyToClipboard(article.document_id),
                        },
                        {
                          label: "Copy the article link",
                          icon: IconCopy,
                          onSelect: () => void copyToClipboard(article.url),
                        },
                        {
                          label: "Copy content hash",
                          icon: IconCopy,
                          onSelect: () =>
                            article.content_hash
                              ? void copyToClipboard(article.content_hash)
                              : undefined,
                          hint: article.content_hash
                            ? undefined
                            : "No hash was recorded",
                        },
                      ]}
                    />
                  </div>
                }
              />
              {/* Only offered where there is one. An article collected before
                screenshots were taken, or one whose page would not render,
                has nothing behind the tab and should not advertise it. */}
              {article.screenshot ? (
                <TabsList>
                  <TabsTrigger value="overview">Overview</TabsTrigger>
                  <TabsTrigger value="page">The page that day</TabsTrigger>
                </TabsList>
              ) : null}
            </div>
          }
        />

        <TabsContent value="overview" className="pt-2">
          <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
            {/* Metadata on the left, as on the document and indicator pages: it is
            what a reader checks while deciding whether to trust what is on the
            right. */}
            <aside className={`lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
              <h2 className="font-heading text-sm font-semibold tracking-tight">
                About
              </h2>
              <dl className="mt-3 space-y-3">
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">
                    Newspaper
                  </dt>
                  <dd className="mt-0.5 text-sm">
                    <Link
                      to="/news-outlets/$host"
                      params={{ host: article.outlet_host }}
                      className="underline underline-offset-4 hover:text-foreground"
                    >
                      {article.outlet}
                    </Link>
                  </dd>
                </div>
                <Fact label="Province of the paper" value={article.outlet_province} />
                <Fact label="Published" value={formatDate(article.published_at)} />
                <Fact
                  label="Collected"
                  value={formatDate(article.retrieved_at)}
                  hint={formatRelative(article.retrieved_at)}
                />
                <Fact
                  label="Found by"
                  value={article.discovered_by}
                  hint="The route that reached it"
                />
                {/* Zero is the interesting case and must not read as "unknown":
                a body that extracted to nothing means the classifier had the
                headline and the lead and no more, which is how much of the
                coding a reader should take on trust. */}
                <Fact
                  label="Text read"
                  value={
                    article.body_chars === undefined
                      ? undefined
                      : article.body_chars > 0
                        ? `${formatCount(article.body_chars)} characters`
                        : "headline and lead only"
                  }
                  hint={
                    article.body_chars
                      ? "What the coding was made from"
                      : "No body text extracted"
                  }
                />
                <Fact label="Classifier" value={coding?.engine} />
                <Fact label="Format" value={article.media_type} />
                {/* The identity of the bytes: what makes "is this the same page I
                read last year" answerable after the paper has edited it. */}
                <Fact label="Content hash" value={article.content_hash} mono />
                <Fact label="Archived at" value={article.raw_path} mono />
                <Fact label="Layer" value="bronze" hint="The news corpus" />
              </dl>
            </aside>

            <div className="min-w-0 space-y-6">
              <Section
                title="What the paper published"
                description="The lead only. The whole page is archived so a coding stays reproducible, but the words belong to the newspaper."
              >
                <div className="space-y-3">
                  {article.lead ? <p className="text-sm">{article.lead}</p> : null}
                  <p className="text-sm break-all">
                    <a
                      href={article.url}
                      target="_blank"
                      rel="noreferrer"
                      className="underline underline-offset-4"
                    >
                      {article.url}
                    </a>
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {article.matched_terms.map((term) => (
                      <Badge key={term} variant="outline" className="font-normal">
                        {term}
                      </Badge>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    The dictionary terms it carried
                    {article.matched_categories?.length
                      ? `, from ${article.matched_categories.join(", ")}`
                      : ""}
                    . An article reaches the classifier when it names the act and either
                    who was involved or what it left behind — so recall is bounded by
                    this dictionary, and an incident reported in words none of it
                    matches is one the corpus never sees.
                  </p>
                </div>
              </Section>

              <Section
                title="How it was coded"
                description={
                  coded
                    ? "A classifier read the article and answered the questions a VEWS coder answers. Nobody has verified it."
                    : awaiting
                      ? "The classifier was unreachable when this was collected. It is kept as a candidate and will be coded on a later pass."
                      : "No coding was made."
                }
              >
                {coded && coding ? (
                  <div className="space-y-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge>Collective violence</Badge>
                      {coding.gate_probability !== undefined ? (
                        <span className="text-xs text-muted-foreground">
                          the classifier was {Math.round(coding.gate_probability * 100)}
                          % sure this reports collective violence
                        </span>
                      ) : null}
                    </div>

                    <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
                      {CODED_FIELDS.map(({ label, of, confidence, yesNo }) => {
                        const value = of(coding);
                        if (!value) return null;
                        const reported = confidence
                          ? article.confidence?.[confidence]
                          : undefined;
                        const sure =
                          reported !== undefined &&
                          yesNo &&
                          value.toUpperCase() !== "IYA"
                            ? 1 - reported
                            : reported;
                        return (
                          <div key={label}>
                            <dt className="text-xs font-medium text-muted-foreground">
                              {label}
                            </dt>
                            <dd className="mt-0.5 text-sm">
                              {value}
                              {/* The number the model put on this field. Shown
                              because a coding a reader should doubt is not
                              obvious from the answer itself. */}
                              {sure !== undefined ? (
                                <span className="ml-1.5 text-xs text-muted-foreground tabular-nums">
                                  {Math.round(sure * 100)}%
                                </span>
                              ) : null}
                            </dd>
                          </div>
                        );
                      })}
                      <Harm label="Killed" value={coding.deaths} />
                      <Harm label="Injured" value={coding.injured} />
                    </dl>

                    {/* Which reader answered what. Shown only where a second one
                    was called, because on most articles the first was sure and
                    there is nothing to explain. */}
                    {article.escalation_reason ? (
                      <p className="text-xs text-muted-foreground">
                        {article.deepened?.length
                          ? `A second, larger model read this article because the classifier was unsure (${article.escalation_reason}), and supplied ${article.deepened.join(", ")}. Fields the classifier answered confidently are left as it answered them.`
                          : `The classifier was unsure of this coding (${article.escalation_reason}) and a second reading was not made. It is queued for one.`}
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <Badge variant="outline">
                    {awaiting ? "Awaiting coding" : "Not coded"}
                  </Badge>
                )}
              </Section>

              {article.event_id ? (
                <Section
                  title="The incident"
                  description="Several papers reporting one event are collapsed into a single incident. This article is one of the reports behind it."
                >
                  <dl className="space-y-3">
                    <Fact label="Incident" value={article.event_id} mono />
                    <div>
                      <dt className="text-xs font-medium text-muted-foreground">
                        Also reported by
                      </dt>
                      <dd className="mt-0.5 text-sm">
                        {article.also_reported_by?.length ? (
                          <div className="flex flex-wrap gap-1.5">
                            {article.also_reported_by.map((host) => (
                              <Link
                                key={host}
                                to="/news-outlets/$host"
                                params={{ host }}
                                className="underline underline-offset-4"
                              >
                                {host}
                              </Link>
                            ))}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">
                            No other paper in the list reported it
                          </span>
                        )}
                      </dd>
                    </div>
                  </dl>
                </Section>
              ) : null}
            </div>
          </div>
        </TabsContent>

        {/* The page as it stood, on its own. Full width rather than beside the
          metadata column: it is a photograph of a newspaper page and the whole
          reason to look at it is to read what it says. */}
        <TabsContent value="page" className="pt-2">
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              What a human verifier reads. It survives a correction, a paywall or a
              deletion, none of which the link to the paper does.
            </p>
            <a
              href={newsScreenshotUrl(article.document_id)}
              target="_blank"
              rel="noreferrer"
              className="block overflow-hidden rounded-lg border"
            >
              <img
                src={newsScreenshotUrl(article.document_id)}
                alt={`${article.title}, as the page looked when it was collected`}
                className="w-full"
              />
            </a>
          </div>
        </TabsContent>
      </Tabs>
    </div>
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

/**
 * A casualty figure.
 *
 * Absent is shown as "not stated", never as zero: an incident nobody counted
 * and an incident where nobody was hurt are different facts, and the whole
 * counting pipeline rests on keeping them apart.
 */
function Harm({ label, value }: { label: string; value?: number }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm">
        {value === undefined ? (
          <span className="text-muted-foreground">not stated in the report</span>
        ) : (
          <span className="tabular-nums">{formatCount(value)}</span>
        )}
      </dd>
    </div>
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
  /** For a hash or a path, which is read character by character. */
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className={`mt-0.5 text-sm${mono ? " font-mono text-xs break-all" : ""}`}>
        {value ?? <span className="text-muted-foreground">—</span>}
        {hint && value ? (
          <span className="ml-1.5 text-xs text-muted-foreground">{hint}</span>
        ) : null}
      </dd>
    </div>
  );
}
