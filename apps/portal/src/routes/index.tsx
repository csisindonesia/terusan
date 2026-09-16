import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { IconDatabase, IconMapPin, IconRuler } from "@tabler/icons-react";

import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { api } from "~/lib/api";
import { formatCount } from "~/lib/format";

export const Route = createFileRoute("/")({ component: Overview });

function Overview() {
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const indicators = useQuery({
    queryKey: ["indicators"],
    queryFn: () => api.indicators(),
  });

  const rows = datasets.data?.data.reduce((total, d) => total + d.rows, 0) ?? 0;

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h1 className="font-heading text-3xl font-semibold tracking-tight">
          Research data warehouse
        </h1>
        <p className="max-w-2xl text-muted-foreground">
          Source-traceable statistics, documents and regulations. Every figure carries
          the document it came from and the run that produced it.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          icon={<IconDatabase className="size-4" />}
          label="Datasets"
          value={datasets.data?.data.length}
          loading={datasets.isLoading}
        />
        <Stat
          icon={<IconRuler className="size-4" />}
          label="Indicators"
          value={indicators.data?.data.length}
          loading={indicators.isLoading}
        />
        <Stat
          icon={<IconMapPin className="size-4" />}
          label="Rows"
          value={rows}
          loading={datasets.isLoading}
        />
      </div>

      {datasets.isError ? (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            The serving layer is not answering. Start it with{" "}
            <code className="rounded bg-muted px-1.5 py-0.5">make dev-api</code>.
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <Panel
          to="/observations"
          title="Observations"
          body="Statistical figures with bounded periods, resolved geography and explicit units."
        />
        <Panel
          to="/geography"
          title="Geography"
          body="Countries, World Bank aggregates and Indonesia's provinces, with BPS codes."
        />
      </div>
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
  loading,
}: {
  icon: React.ReactNode;
  label: string;
  value?: number;
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
          {icon}
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <p className="font-heading text-3xl font-semibold tabular-nums">
            {value === undefined ? "—" : formatCount(value)}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function Panel({ to, title, body }: { to: string; title: string; body: string }) {
  return (
    <Link to={to} className="group">
      <Card className="h-full transition-colors group-hover:border-foreground/20">
        <CardHeader>
          <CardTitle className="font-heading">{title}</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">{body}</CardContent>
      </Card>
    </Link>
  );
}
