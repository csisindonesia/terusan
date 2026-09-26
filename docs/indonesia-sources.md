# Indonesian sources: what the lake collects, and what it cannot

This is the survey in `indonesia_dataset_sources.xlsx` turned into code. Each of
its fifty rows has a source registered against it, so `terusan sources list` is
the answer to "do we hold X" rather than a spreadsheet in somebody's folder.

Twenty-seven of the fifty collect today. Twenty-three are registered and cannot,
and that is the more useful half of this document: each says in its `access`
note exactly what would open it. Not one of them is blocked on parsing. Every
gap is an account, a key, a subscription, a reconnaissance session, or a
hostname that stopped resolving.

Registering an unreachable source rather than leaving it out is deliberate. A
catalogue that silently omits AIS reads as a lake with no shipping data and no
opinion about it; one that lists `marinetraffic-ais` as gated says the gap is
known, priced, and someone's decision.

```bash
terusan sources list                          # everything registered, gated included
terusan sources list --scheduled              # what actually runs
terusan sources run bmkg-earthquakes
terusan sources run osm-geofabrik --param full=true    # the 1 GB extract, deliberately
```

## The gaps, by what would close them

**A free registration.** Two:

| Source | What it opens | How |
|---|---|---|
| `eog-viirs-nighttime-lights` | Economic activity below the level official statistics reach | Free account at [eogdata.mines.edu](https://eogdata.mines.edu/products/vnl/) |
| `copernicus-sentinel` | Radar and optical imagery — the independent check on any spatial claim | Free account at [dataspace.copernicus.eu](https://dataspace.copernicus.eu/) |

**A key the agency issues on request.** `kemendag-satudata`
(`KEMENDAG_API_KEY`) and `badanpangan-panel-harga` (`PANEL_HARGA_API_KEY`);
both names are in `.env.example`. The food price panel is worth the letter: it
is the second independent measure of Indonesian food prices beside Bank
Indonesia's PIHPS, and two measures are how either is known to be wrong.

**An hour with a browser's network tab.** Three portals are Angular or Vue
applications whose backend answers but whose listing route was not found from
outside. Each source records the trail so far rather than leaving it to be
rediscovered: `kemenkeu-apbn-kita` (backend is `media.kemenkeu.go.id`;
`/menu/getmenu?lang=id` answers and names the section), `big-inageoportal`
(`/api-inageo/` answers a welcome message and 404s every guessed path), and
`esdm-modi` (behind a WAF that rejects scripted requests outright).

**A network inside Indonesia.** `jakarta-opendata` and `jabar-opendata` are the
two provincial portals in the sheet that publish figures rather than links to
them, and both refuse this network — Jakarta by resetting the connection on
every catalogue request, West Java by answering its cloud gateway's status page.
Both may simply work from an Indonesian address, which is one afternoon's test.

**An institutional arrangement.** `ma-putusan` (several million court
decisions, behind a Cloudflare challenge), `kpk-elhkpn` (asset declarations,
one official at a time behind a captcha), `ahu-company-registry` and
`ahu-beneficial-ownership` (login and per-document fees), `bps-podes` (village
census microdata, per approved SILASTIK request), `lkpp-sirup` and
`lkpp-inaproc` (the open feed closed; requests now go through an API gateway
that issues credentials per consumer). These are asked for, not worked around.

**Money.** `marinetraffic-ais` alone. Anything in this lake that claims to
measure shipping without it — port statistics, export volumes — is measuring
the paperwork.

## Four things the sheet records that are no longer true

- **KLHK was split in 2024.** `menlhk.go.id` now serves only a notice about it.
  The environment half is `kemenlh.go.id` and is collected; the forestry half's
  hosts, `geoportal.menlhk.go.id` among them, do not resolve.
- **Kemdikbud was renamed.** The school register moved to
  `referensi.data.kemendikdasmen.go.id`, which is what `kemdikbud-referensi`
  reads.
- **LKPP's open data feed closed.** `isb.lkpp.go.id` serves a migration notice
  pointing at an API gateway, and `sirup.lkpp.go.id` no longer resolves
  publicly.
- **`data.kemkes.go.id` and `satudata.kkp.go.id` do not resolve.** Health data
  is reachable through SATUSEHAT's data room instead; fisheries is not reachable
  at all.

## Row by row

`Priority` is the sheet's own. `collecting` means a scheduled run lands
something today.

| # | Sheet row | Priority | Source slug | State |
|---|---|---|---|---|
| 1 | Satu Data Indonesia | High | `satudata-indonesia` | collecting |
| 2 | BPS Statistics Indonesia | Critical | `bps-webapi` | collecting (catalogues) |
| 3 | PODES / Village Potential Statistics | Critical | `bps-podes` | registered, gated |
| 4 | Bank Indonesia Statistics | Critical | `bi-seki`, `bi-sdds-real-sector`, `bi-consumer-survey`, `bi-retail-sales-survey`, `bi-pihps-food-prices` | collecting |
| 5 | OJK Statistics | High | `ojk-banking-spi`, `ojk-fintech-p2p` | collecting |
| 6 | Satu Data Perdagangan | Critical | `kemendag-satudata`, `kemendag-sp2kp-prices` | collecting |
| 7 | APBN KiTa / Fiscal Data | High | `kemenkeu-apbn-kita` | registered, gated |
| 8 | DJPK Data Portal | Critical | `djpk-apbd` | collecting |
| 9 | SiRUP | Critical | `lkpp-sirup` | registered, gated |
| 10 | INAPROC / National Procurement Portal | High | `lkpp-inaproc` | registered, gated |
| 11 | KPU Election Data | Critical | `kpu-elections` | collecting |
| 12 | Bawaslu Data & Publications | Medium | `bawaslu-publications` | collecting |
| 13 | e-LHKPN | High | `kpk-elhkpn` | registered, gated |
| 14 | Peraturan BPK | Critical | `bpk-peraturan-pusat`, `bpk-peraturan-daerah` | collecting |
| 15 | JDIHN | High | `jdihn-documents` | collecting |
| 16 | Direktori Putusan | Critical | `ma-putusan` | registered, gated |
| 17 | DPR RI Open Information / Legislation | High | `dpr-legislation` | registered, gated |
| 18 | AHU Online | High | `ahu-company-registry` | registered, gated |
| 19 | Beneficial Ownership Portal | High | `ahu-beneficial-ownership` | registered, gated |
| 20 | MODI Minerba | Critical | `esdm-modi` | registered, gated |
| 21 | Minerba One Data Indonesia / ESDM Minerba | High | `esdm-minerba-prices` | registered, gated |
| 22 | ESDM Data & Information | High | `esdm-publications`, `esdm-heesi` | collecting |
| 23 | Ina-Geoportal | Critical | `big-inageoportal` | registered, gated |
| 24 | Kode & Data Wilayah | Critical | `kemendagri-wilayah` | collecting |
| 25 | Satu Data Bencana Indonesia | Critical | `bnpb-disaster` | collecting |
| 26 | Data Online BMKG | Critical | `bmkg-dataonline` | registered, gated |
| 27 | BMKG Open Information | High | `bmkg-earthquakes` | collecting |
| 28 | MAGMA Indonesia | High | `esdm-magma` | registered, gated |
| 29 | KLHK Environmental Data / Geoportal | Critical | `klhk-environment` | collecting |
| 30 | Geoportal KLHK | High | `klhk-geoportal` | registered, gated |
| 31 | SATUSEHAT Data / Kemenkes Data | High | `kemkes-health-data` | collecting |
| 32 | Satu Data Kesehatan / Kemenkes | High | `kemkes-health-data` | collecting |
| 33 | Referensi Data Pendidikan | Critical | `kemdikbud-referensi` | collecting |
| 34 | Rapor Pendidikan / Education Statistics | Medium | `kemdikbud-rapor-pendidikan` | registered, gated |
| 35 | Satu Data Ketenagakerjaan | High | `kemnaker-satudata` | collecting |
| 36 | Sistem Informasi Desa / Indeks Desa | Critical | `kemendesa-sid` | collecting |
| 37 | Panel Harga Pangan | High | `badanpangan-panel-harga` | registered, gated |
| 38 | Satu Data Pertanian | High | `kementan-satudata` | collecting |
| 39 | Satu Data KKP | High | `kkp-satudata` | registered, gated |
| 40 | Open Data / Statistics Kemenhub | Medium | `kemenhub-statistics` | collecting |
| 41 | Open Data Jabar | High | `jabar-opendata` | registered, gated |
| 42 | Jakarta Open Data | High | `jakarta-opendata` | registered, gated |
| 43 | VIIRS Nighttime Lights | High | `eog-viirs-nighttime-lights` | registered, gated |
| 44 | Global Forest Watch | High | `gfw-catalogue` | collecting |
| 45 | Copernicus Data Space / Sentinel | Medium | `copernicus-sentinel` | registered, gated |
| 46 | OpenStreetMap Indonesia | High | `osm-geofabrik` | collecting |
| 47 | MarineTraffic / AIS providers | Medium | `marinetraffic-ais` | registered, gated |
| 48 | UN Comtrade | High | `comtrade-indonesia` | collecting |
| 49 | World Bank Data | Medium | `worldbank-gdp`, `worldbank-population` | collecting |
| 50 | GDELT | High | `gdelt-events` | collecting |

## What reaches the portal

Landing bytes is not the same as publishing figures. The portal's `/datasets`
page is built from Silver observations, so a collection appears there only once
a column mapping has been declared for it — which is deliberate: a column headed
`2026` is a period in a wide table and a value in a long one, and nothing in the
data settles which.

Two of these sources have that mapping today.

| Source | Indicators | What it publishes |
|---|---|---|
| `kemendagri-wilayah` | 24 × 38 provinces | Population, poverty, HDI by sex, Gini, inflation, unemployment, growth, budget realisation, life expectancy, schooling, governance scores — from one request, [scripts/normalize-kemendagri.sh](../scripts/normalize-kemendagri.sh) |
| `comtrade-indonesia` | 2 | Annual exports and imports to the world in US dollars, [scripts/normalize-comtrade.sh](../scripts/normalize-comtrade.sh) |
| `wits-tradestats` | 86 × 21 economies | RCA, exports and export shares by HS section, SITC group and stage of processing, for Indonesia, ASEAN and peers, [scripts/normalize-rca.sh](../scripts/normalize-rca.sh) |
| `djpk-apbd` | 26 regional totals + 26 per government | Budget and realisation of revenue, PAD, transfers, expenditure by type, financing and the fiscal balance, monthly and cumulative within the year — for the nation, each province's governments summed, and each provincial government, regency and city on its own, [scripts/normalize-apbd.sh](../scripts/normalize-apbd.sh) |
| `bps-indicators` | one per variable × breakdown, across the ~1,750 variables of BPS's national catalogue | Everything BPS's web API publishes for the national domain — population, poverty, labour, prices, national accounts, trade, agriculture, SDG indicators — by province, city or category, one Silver indicator per series named from BPS's own titles, [scripts/normalize-bps.sh](../scripts/normalize-bps.sh); `make bps-silver` monthly, `make bps-backfill` for the whole history |
| `rca-seed` | 59 | Environmental goods lists' exports, basket RCA and advantaged-product counts, plus ECI, from the HS6 working files, [scripts/normalize-rca.sh](../scripts/normalize-rca.sh) |

ESDM's energy handbook is the one source here held as a document and nothing
more: `esdm-heesi` lands a PDF per edition, and no series is published from it
because the reader for its fourteen tables was begun and not finished. It is on
the Documents page, not the Datasets page, which is the honest place for a
publication nothing has parsed.

Publishing those took four changes to the pipeline, each of which applies well
beyond these two sources: the JSON extractor now finds a result set nested
inside a wrapper object (BMKG answers `{"Infogempa": {"gempa": [...]}}`); the
period parser reads an ISO timestamp as its day; `silver normalize` takes
`--geo` for a series whose publisher never names the country, as Comtrade's
public endpoint does not; and it now publishes the series' name, merging rather
than replacing, so a wide table normalized one column at a time ends with all
its columns named instead of only the last.

The rest stop at Bronze, where they are queryable but not charted:

```bash
terusan warehouse query "SELECT dataset, count(*) FROM read_parquet('…/bronze/records/**/*.parquet') GROUP BY 1"
```

Two of them stop there for a reason worth stating rather than fixing. **BMKG's
earthquakes are events, not a series**: two quakes on one day are two figures
for one indicator and period, which Silver's observation identity refuses — and
it is right to. The series a reader wants is a count or a daily maximum, which
is an aggregation this warehouse does not yet have a step for. **GDELT's
events and mentions reach Bronze and stop**: its own extractor keeps the events
involving Indonesia and the mentions of them, and like BMKG's they are events
rather than a series.

## How these are built

Forty portals would have been forty scrapers, so they are three engines and
forty declarations — an API, an index of files, a page that is itself the
document, and a fourth kind that refuses honestly. See
[adding-a-source.md](adding-a-source.md#portal-shapes) and
[sources/portals.py](../pipelines/src/terusan_pipelines/sources/portals.py).
