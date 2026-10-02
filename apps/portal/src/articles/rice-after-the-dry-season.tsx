import { Link } from "@tanstack/react-router";

import { TimeSeriesChart } from "~/components/time-series-chart";
import { formatPercent } from "~/lib/format";
import { SERIES_DARK_SLOTS, SERIES_LIGHT_SLOTS } from "~/lib/viz";

import {
  ChangeColumns,
  DivergingBars,
  RainPriceScatter,
  type ProvinceRow,
} from "./charts";
import figures from "./rice-after-the-dry-season.json";

/**
 * Rice After the Dry Season.
 *
 * Every number in the prose is read from the figures file, which
 * `terusan articles build rice-after-the-dry-season` writes from the lake. The
 * sentences around them were written against the figures of 30 September
 * 2026; a rebuild keeps the numbers current, and a rebuild that turned a
 * finding around would need the words changed with it.
 */

type Summary = {
  provinces: number;
  rain_median: number;
  drier: number;
  rice_median: number;
  rho: number | null;
  rho_outside_core: number | null;
  outside_core: number;
  rho_chili: number | null;
  dry_count: number;
  dry_rice_median: number | null;
  rest_count: number;
  rest_rice_median: number | null;
};

const F = figures as typeof figures & { y2023: Summary; y2019: Summary };
const provinces = F.provinces as ProvinceRow[];

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
const month = (period: string) =>
  `${MONTHS[Number(period.slice(5)) - 1]} ${period.slice(0, 4)}`;
const rp = (value: number) => `Rp${value.toLocaleString("en-US")}`;
const less = (change: number) => `${Math.abs(change).toFixed(0)}%`;
const rho = (value: number | null) =>
  value === null ? "undefined" : value.toFixed(2).replace("-", "−");
const ordinal = (n: number) =>
  `${n}${n % 10 === 1 && n % 100 !== 11 ? "st" : n % 10 === 2 && n % 100 !== 12 ? "nd" : n % 10 === 3 && n % 100 !== 13 ? "rd" : "th"}`;
const byName = (name: string) => provinces.find((p) => p.name === name);

const windows = F.windows as { year: number; change: number }[];
const ordinary = windows.filter((w) => w.year !== 2022 && w.year !== 2023);
const peakIndex = F.national.findIndex((row) => row.period === "2024-03");

/**
 * The article's charts, by name. The body draws them from here, and the
 * portal's article editor offers them as blocks, so an article written in the
 * portal can borrow a chart built from this analysis and show the same thing.
 */
export const charts: Record<
  string,
  { title: string; render: () => React.ReactElement }
> = {
  "price-line": {
    title: "Rice price in traditional markets, monthly",
    render: () => (
      <Chart
        title="Rice price in traditional markets, monthly"
        note="Mean of province averages, so each province counts once, with the cheapest and dearest province each month. IDR per kg."
      >
        <TimeSeriesChart
          framed={false}
          dashed
          unit="IDR/kg"
          series={[
            {
              name: "Mean of provinces",
              points: F.national.map((row) => ({ label: row.period, value: row.mean })),
            },
            {
              name: "Cheapest province",
              points: F.national.map((row) => ({ label: row.period, value: row.low })),
            },
            {
              name: "Dearest province",
              points: F.national.map((row) => ({ label: row.period, value: row.high })),
            },
          ]}
          annotations={
            peakIndex >= 0
              ? [
                  {
                    series: 0,
                    index: peakIndex,
                    label: `${rp(F.rice_mar_2024)}, March 2024`,
                  },
                ]
              : []
          }
        />
      </Chart>
    ),
  },
  "july-to-march": {
    title: "Change from July to the following March",
    render: () => (
      <Chart
        title="Change from July to the following March"
        note="National mean price, same window each year."
      >
        <ChangeColumns
          label="July to March price change by year"
          columns={windows.map((w) => ({
            label: `${w.year}/${String(w.year + 1).slice(2)}`,
            value: w.change,
            note: `July ${w.year} to March ${w.year + 1}: ${formatPercent(w.change)}`,
          }))}
        />
      </Chart>
    ),
  },
  "rain-by-province": {
    title: "Rainfall against normal by province, June–October 2023",
    render: () => (
      <Chart
        title="June–October 2023 rainfall against the 1991–2020 normal"
        note="Total precipitation over the five months, province average, ERA5-Land."
      >
        <DivergingBars
          label="Rainfall against normal by province, June to October 2023"
          rows={provinces}
        />
      </Chart>
    ),
  },
  "rain-and-rice": {
    title: "Rainfall deficit and rice price rise, by province",
    render: () => (
      <Chart
        title="Rainfall deficit and rice price rise, by province"
        note="Across: June–October 2023 rainfall against normal. Up: rice price change, July 2023 to March 2024. Dashed: least-squares line."
        legend={[
          { color: "var(--series-2)", label: "Java, Bali, Nusa Tenggara" },
          { color: "var(--series-1)", label: "Other provinces" },
        ]}
      >
        <RainPriceScatter
          label="Drier provinces tend to have larger rice price rises"
          rows={provinces}
          named={["DI Yogyakarta", "DKI Jakarta", "Gorontalo", "Jawa Barat", "Aceh"]}
        />
      </Chart>
    ),
  },
};

function BuiltChart({ id }: { id: string }) {
  const chart = charts[id];
  return chart ? chart.render() : null;
}

export const meta = {
  slug: F.slug,
  title: "Rice After the Dry Season",
  dek: `After the El Niño dry season of 2023, rice in traditional markets rose ${F.rice_change}% in eight months and never came back down. The provinces that dried out most saw the biggest rises. But 2019 was drier still, and rice barely moved.`,
  publisher: "CSIS · Terusan",
  published: "2026-09-30",
  datasets: F.datasets,
};

export function RiceAfterTheDrySeason() {
  const y23 = F.y2023;
  const y19 = F.y2019;
  const jakarta = byName("DKI Jakarta");
  const gorontalo = byName("Gorontalo");
  const deepest = [...provinces]
    .sort((a, b) => a.rain_change - b.rain_change)
    .slice(0, 4);
  const wet = provinces.filter((p) => p.rain_change >= 0).map((p) => p.name);

  return (
    <>
      <div className="grid gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-3">
        <Figure value={formatPercent(F.rice_change)}>
          national mean rice price, July 2023 to March 2024: {rp(F.rice_jul_2023)} to{" "}
          {rp(F.rice_mar_2024)} per kg.
        </Figure>
        <Figure value={`ρ = ${rho(y23.rho)}`}>
          rank correlation across {y23.provinces} provinces between the rainfall deficit
          and the price rise.
        </Figure>
        <Figure value={`${ordinal(F.core_rank_2023 ?? 0)} of ${F.core_years}`}>
          where 2023 ranks among the driest dry seasons on Java, Bali and Nusa Tenggara
          since {F.core_first_year}. {F.driest_year} was the driest.
        </Figure>
      </div>

      <h2>A step up that stayed</h2>
      <p>
        From 2018 to mid-2022 the price of medium-grade rice barely moved, holding near
        Rp11,800 per kilogram. It rose through late 2022 and early 2023, then climbed
        again once the dry season set in, peaking in March 2024 at {rp(F.rice_mar_2024)}
        . It settled a little lower after the 2024 harvest, but {month(F.latest_period)}{" "}
        is still {F.latest_change}% above July 2023.
      </p>
      <BuiltChart id="price-line" />

      <p>
        Rice usually gets dearer between the end of the dry season and the main harvest
        in March and April, but only slightly. In the {ordinary.length} ordinary years
        since 2018 the same July-to-March window moved between{" "}
        {formatPercent(Math.min(...ordinary.map((w) => w.change)))} and{" "}
        {formatPercent(Math.max(...ordinary.map((w) => w.change)))}. The two years that
        broke the pattern are 2022, when subsidised fuel prices rose in September, and
        2023.
      </p>
      <BuiltChart id="july-to-march" />

      <h2>Where the rain failed</h2>
      <p>
        Between June and October 2023, {y23.drier} of {y23.provinces} provinces had less
        rain than their 1991–2020 normal, and the median province had{" "}
        {less(y23.rain_median)} less. The deficit was deepest in the rice bowl:{" "}
        {deepest.map((p, i) => (
          <span key={p.geo_id}>
            {i === deepest.length - 1 ? "and " : ""}
            {p.name} {less(p.rain_change)}
            {i < deepest.length - 1 ? ", " : ""}
          </span>
        ))}{" "}
        below normal. Only {wet.length} provinces came through at or above normal:{" "}
        {wet.join(", ")}.
      </p>
      <BuiltChart id="rain-by-province" />

      <h2>Drier provinces, dearer rice</h2>
      <p>
        Every province saw rice get dearer, so the question is whether the dry ones saw
        more of it. They did. The {y23.dry_count} provinces that lost more than 30% of
        their dry-season rain had a median rise of {y23.dry_rice_median}%; the other{" "}
        {y23.rest_count} had {y23.rest_rice_median}%. The rank correlation is{" "}
        {rho(y23.rho)}.
      </p>
      <p>
        It would be easy to explain this away as Java, which is both the driest region
        in 2023 and the largest producer. But the pattern holds outside it: across the{" "}
        {y23.outside_core} provinces beyond Java, Bali and Nusa Tenggara the correlation
        is {rho(y23.rho_outside_core)}.
      </p>
      {jakarta && gorontalo ? (
        <p>
          Jakarta is the clearest exception. It lost {less(jakarta.rain_change)} of its
          rain and saw one of the smallest rises, {formatPercent(jakarta.rice_change)}.
          It grows almost no rice, and its price is set by the Cipinang wholesale market
          and government stock releases rather than by local harvests. At the other end,
          Gorontalo rose {formatPercent(gorontalo.rice_change, 0)} on a dry season{" "}
          {less(gorontalo.rain_change)} below normal.
        </p>
      ) : null}
      <BuiltChart id="rain-and-rice" />
      <p>
        Chilli, which is perishable and mostly grown and sold within a province, shows
        the same pattern more strongly. Curly red chilli from July to October 2023 has a
        rank correlation of {rho(y23.rho_chili)} with the rainfall deficit. Chilli has
        no national stockpile to smooth a local shortage, so a failed season reaches the
        market within weeks.
      </p>

      <h2>Then why not 2019?</h2>
      <p>
        If the dry season drove the rise, 2019 should have been worse. Across Java, Bali
        and Nusa Tenggara it was the {ordinal(F.core_rank_2019 ?? 0)} driest June to
        October in the {F.core_years} years of the series, and 2023 ranks{" "}
        {ordinal(F.core_rank_2023 ?? 0)}. Nationally the median province had{" "}
        {less(y19.rain_median)} less rain than normal in 2019 and{" "}
        {less(y23.rain_median)} less in 2023, so the two seasons were close.
      </p>
      <p>
        Yet the median province saw rice rise {formatPercent(y19.rice_median)} over the
        2019 window and {formatPercent(y23.rice_median)} over the 2023 one. In 2019
        prices barely moved anywhere, so there was little for the drought to sort: the
        drier provinces rose slightly more ({rho(y19.rho)}), but only because of Java;
        outside it the correlation was {rho(y19.rho_outside_core)}. The drought decided
        where prices rose in 2023. It did not decide how far.
      </p>
      <p>
        The warehouse cannot say what did, because it holds no series for it. The
        obvious candidates are outside Indonesia's weather. India, the largest rice
        exporter, stopped exporting non-basmati white rice in July 2023, and world
        prices rose. The fuel price rise of September 2022 had already lifted costs
        along the chain. Whether stocks were lower going into 2023 than 2019 would also
        matter.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm tabular-nums">
          <thead>
            <tr className="border-b text-muted-foreground">
              <th className="py-2 pr-4 text-left font-medium" />
              <th className="py-2 text-right font-medium">2019</th>
              <th className="py-2 text-right font-medium">2023</th>
            </tr>
          </thead>
          <tbody>
            <Row
              label="Median province rainfall, June–October, against normal"
              a={formatPercent(y19.rain_median)}
              b={formatPercent(y23.rain_median)}
            />
            <Row
              label={`Rank among driest, Java–Bali–Nusa Tenggara (of ${F.core_years})`}
              a={ordinal(F.core_rank_2019 ?? 0)}
              b={ordinal(F.core_rank_2023 ?? 0)}
            />
            <Row
              label="Rice price, July to March, median province"
              a={formatPercent(y19.rice_median)}
              b={formatPercent(y23.rice_median)}
            />
            <Row
              label="Rank correlation, deficit against rise"
              a={rho(y19.rho)}
              b={rho(y23.rho)}
            />
            <Row
              label="The same, outside Java–Bali–Nusa Tenggara"
              a={rho(y19.rho_outside_core)}
              b={rho(y23.rho_outside_core)}
            />
          </tbody>
        </table>
      </div>

      <h2>What this does not show</h2>
      <ul>
        <li>
          A correlation across {y23.provinces} provinces is not proof that the drought
          caused the differences between them. National policy moved at the same time:
          rice imports, stock releases and the reference prices the government sets all
          changed in 2023 and 2024, and none of them are in this article.
        </li>
        <li>
          Rainfall is averaged over each whole province, including forest and city.
          Irrigated rice on Java depends on reservoirs as much as on the season's rain.
        </li>
        <li>
          The national figure weights every province equally. A consumption-weighted
          index would lean towards Java and would read higher.
        </li>
        <li>
          Papua and the provinces formed from it in 2022 are left out of the province
          comparison. The price data still reports them as the old provinces while the
          rainfall series uses the new boundaries, so the two cannot be matched.
        </li>
        <li>
          About {F.missing_share}% of daily price reports are marked missing at source.
          They are left out rather than counted as zero. The latest month runs to{" "}
          {F.latest_day}.
        </li>
      </ul>

      <h2>Data and method</h2>
      <p>
        Rainfall is the ERA5-Land monthly precipitation series, summed over June to
        October for each year and compared with the 1991–2020 mean of the same sum; a
        year missing any of the five months is left out. Prices are daily PIHPS reports
        for Beras Kualitas Medium I in traditional markets, averaged to a monthly figure
        for each province. The national line is the mean of those province averages.
        Correlations are Spearman rank correlations across provinces.
      </p>
      <p>
        The figures are rebuilt from the lake with{" "}
        <code>terusan articles build rice-after-the-dry-season</code>; the code is in{" "}
        <code>pipelines/src/terusan_pipelines/articles/rice_dry_season.py</code>.
        Datasets:{" "}
        <Link to="/datasets/$datasetId" params={{ datasetId: "uv7fqbgj" }}>
          Strategic food prices
        </Link>{" "}
        and{" "}
        <Link to="/datasets/$datasetId" params={{ datasetId: "nbrxezkf" }}>
          Temperature, rainfall and soil water by province (ERA5-Land)
        </Link>
        .
      </p>
      <details className="rounded-lg border px-4 py-3">
        <summary className="cursor-pointer font-medium">
          All {provinces.length} provinces
        </summary>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[40rem] text-xs tabular-nums">
            <thead>
              <tr className="border-b text-muted-foreground">
                {[
                  "Province",
                  "Rain, normal",
                  "Rain, 2023",
                  "Against normal",
                  "Rice, Jul 2023",
                  "Rice, Mar 2024",
                  "Rice change",
                  "Chilli, Jul–Oct",
                ].map((h, i) => (
                  <th
                    key={h}
                    className={`py-2 pr-3 font-medium ${i ? "text-right" : "text-left"}`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...provinces]
                .sort((a, b) => a.rain_change - b.rain_change)
                .map((p) => (
                  <tr key={p.geo_id} className="border-b last:border-0">
                    <td className="py-1.5 pr-3">{p.name}</td>
                    <td className="py-1.5 pr-3 text-right">
                      {p.rain_normal.toLocaleString("en-US")} mm
                    </td>
                    <td className="py-1.5 pr-3 text-right">
                      {p.rain.toLocaleString("en-US")} mm
                    </td>
                    <td className="py-1.5 pr-3 text-right">
                      {formatPercent(p.rain_change)}
                    </td>
                    <td className="py-1.5 pr-3 text-right">{rp(p.rice_before)}</td>
                    <td className="py-1.5 pr-3 text-right">{rp(p.rice_after)}</td>
                    <td className="py-1.5 pr-3 text-right">
                      {formatPercent(p.rice_change)}
                    </td>
                    <td className="py-1.5 text-right">
                      {p.chili_change === null
                        ? "not reported"
                        : formatPercent(p.chili_change)}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

function Figure({ value, children }: { value: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1 bg-background p-4">
      <div className="font-heading text-2xl font-semibold tabular-nums">{value}</div>
      <p className="!mt-0 text-sm text-muted-foreground">{children}</p>
    </div>
  );
}

function Chart({
  title,
  note,
  legend,
  children,
}: {
  title: string;
  note: string;
  legend?: { color: string; label: string }[];
  children: React.ReactNode;
}) {
  return (
    <figure
      className={`article-card space-y-3 ${SERIES_DARK_SLOTS}`}
      style={SERIES_LIGHT_SLOTS}
    >
      <figcaption className="space-y-0.5">
        <div className="font-heading text-sm font-semibold">{title}</div>
        <div className="text-xs text-muted-foreground">{note}</div>
      </figcaption>
      {legend ? (
        <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
          {legend.map((item) => (
            <span key={item.label} className="inline-flex items-center gap-1.5">
              <span
                className="size-2.5 rounded-full"
                style={{ background: item.color }}
              />
              {item.label}
            </span>
          ))}
        </div>
      ) : null}
      {children}
    </figure>
  );
}

function Row({ label, a, b }: { label: string; a: string; b: string }) {
  return (
    <tr className="border-b last:border-0">
      <td className="py-2 pr-4">{label}</td>
      <td className="py-2 text-right">{a}</td>
      <td className="py-2 text-right">{b}</td>
    </tr>
  );
}
