"""What a collection of figures is called (program.md §9).

A dataset is the unit between a source and a series: Bank Indonesia's consumer
survey is one dataset holding four indicators, and a reader looking for "the
consumer survey" wants that rather than any one of them.

Extraction already names datasets — the name becomes a RAW path segment and
travels to Bronze on every record — but a name is not a title, and nothing
downstream knows that `apbd-national` is the national roll-up of provincial
budget realisations. That is what this registry holds: a title, a sentence, and
the tags a reader would search it by, one entry per collection.

Declared rather than derived, because none of it can be read off the figures.
A dataset that is not declared still works: `describe` falls back to what its
slug and its source say, so a new scraper is never blocked on editing this
file — it just reads less well until someone does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .identifiers import dataset_code
from .sources.gee.catalog import PRODUCTS as GEE_PRODUCTS


@dataclass(frozen=True, slots=True)
class DatasetMeta:
    """Registry record for one collection."""

    #: The name extraction gives it, and what Bronze records carry. Readable,
    #: and the only thing tying this record to the figures.
    slug: str

    title: str
    source: str
    description: str | None = None

    #: Topics the slug and the title cannot be read for. Tagging derives the
    #: rest — the source, its organization, the cadence, the place.
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def dataset_id(self) -> str:
        """The catalogue identifier: a derived code, not the slug."""
        return dataset_code(self.slug)


#: Every collection the pipelines currently produce.
DATASETS: tuple[DatasetMeta, ...] = (
    DatasetMeta(
        slug="apbd-national",
        title="APBD, national roll-up",
        source="djpk-apbd",
        description=(
            "Every regional government's budget and realisation summed to the national "
            "total, month by month and cumulative within the year, as DJPK publishes it."
        ),
        tags=("apbd", "fiscal", "public-finance", "subnational", "government"),
    ),
    DatasetMeta(
        slug="apbd-provinces",
        title="APBD by province",
        source="djpk-apbd",
        description=(
            "Each province's regional governments summed — the provincial government and "
            "all its regencies and cities — budgeted and realised, month by month."
        ),
        tags=("apbd", "fiscal", "public-finance", "subnational", "province"),
    ),
    DatasetMeta(
        slug="apbd-governments",
        title="APBD by regional government",
        source="djpk-apbd",
        description=(
            "One regional government's own budget and realisation: every provincial "
            "government, regency and city, month by month."
        ),
        tags=("apbd", "fiscal", "public-finance", "regency", "city", "government"),
    ),
    # -- BNPB, one collection per CKAN dataset ------------------------------
    #
    # The slugs are BNPB's own dataset names, carried through rather than
    # renamed: they are what the portal addresses these by, and a reader who
    # arrives from data.bnpb.go.id should find the same collection under the
    # same name. Several are unlovely — `datakejadian2000` — which is exactly
    # why they need a title here.
    DatasetMeta(
        slug="bnpb-impact",
        title="Disaster events and impact by province",
        source="bnpb-disaster",
        description=(
            "What disasters did to each province, by hazard and year: events, deaths, "
            "missing, injured, affected and displaced people, and the houses, schools, "
            "clinics, offices, bridges and factories damaged. Read out of BNPB's "
            "per-measure tables, which publish one hazard per column."
        ),
        tags=("disasters", "hazards", "casualties", "damage", "subnational", "bnpb"),
    ),
    DatasetMeta(
        slug="bnpb-catalogue",
        title="BNPB data catalogue",
        source="bnpb-disaster",
        description=(
            "BNPB's own listing of the spreadsheets it publishes: the datasets, their "
            "resources, licences and descriptions, as the portal answered on the day."
        ),
        tags=("disasters", "catalogue", "provenance", "open-data"),
    ),
    DatasetMeta(
        slug="bantuan-radio-tahun-2021",
        title="Radio equipment issued to provincial disaster agencies, 2021",
        source="bnpb-disaster",
        description="Radio sets BNPB distributed to BPBD offices in 2021.",
        tags=("disasters", "preparedness", "equipment", "bpbd"),
    ),
    DatasetMeta(
        slug="data-bencana",
        title="Disaster events to 31 August 2023",
        source="bnpb-disaster",
        description=(
            "Disaster events by province, regency and city, as counted on 31 August 2023."
        ),
        tags=("disasters", "hazards", "subnational", "events"),
    ),
    DatasetMeta(
        slug="data-bencana-indonesia",
        title="Disaster events and impact, all years",
        source="bnpb-disaster",
        description=(
            "BNPB's standing compilation of disaster events and their impact — deaths, "
            "injuries, displacement and damaged buildings — by regency and city."
        ),
        tags=("disasters", "hazards", "casualties", "damage", "subnational"),
    ),
    DatasetMeta(
        slug="datakejadian2000",
        title="Disaster events and impact, 2000",
        source="bnpb-disaster",
        description="Disaster events and their impact by regency and city in 2000.",
        tags=("disasters", "hazards", "casualties", "damage", "subnational"),
    ),
    DatasetMeta(
        slug="erupsi-gunung-ibu-januari-2025",
        title="Mount Ibu eruption response, January 2025",
        source="bnpb-disaster",
        description=(
            "The emergency response to the eruption of Mount Ibu, West Halmahera: "
            "displacement and the people affected, disaggregated."
        ),
        tags=("disasters", "volcanoes", "emergency-response", "displacement", "maluku"),
    ),
    DatasetMeta(
        slug="erupsigunungruang2024",
        title="Mount Ruang eruption, displacement service standards, 2024",
        source="bnpb-disaster",
        description=(
            "The service standards applied to people displaced by the 2024 Mount Ruang eruption."
        ),
        tags=("disasters", "volcanoes", "emergency-response", "displacement"),
    ),
    DatasetMeta(
        slug="gempabumi-cianjur-2022",
        title="Cianjur earthquake response, 2022",
        source="bnpb-disaster",
        description=(
            "The emergency response to the Cianjur earthquake of 21 November 2022: "
            "displaced people, disaggregated, and the service standards applied."
        ),
        tags=("disasters", "earthquakes", "emergency-response", "displacement", "west-java"),
    ),
    DatasetMeta(
        slug="jumlah-kejadian-dan-dampak-bencana-tahun-2001",
        title="Disaster events and impact, 2001",
        source="bnpb-disaster",
        description="Disaster events and their impact by regency and city in 2001.",
        tags=("disasters", "hazards", "casualties", "damage", "subnational"),
    ),
    DatasetMeta(
        slug="jumlah-penduduk-di-kawasan-rawan-bencana-gunung-ibu",
        title="Population in the Mount Ibu hazard zone",
        source="bnpb-disaster",
        description=(
            "People living inside the hazard zones around Mount Ibu, West Halmahera, "
            "from Dukcapil's December 2023 register."
        ),
        tags=("disasters", "volcanoes", "population", "risk", "maluku"),
    ),
    DatasetMeta(
        slug="jumlah-penduduk-kelompok-umur-5thn-di-kab-kepulauan-sitaro",
        title="Population of Sitaro Islands regency by five-year age group",
        source="bnpb-disaster",
        description=(
            "The population at risk from Mount Ruang, by five-year age group, from "
            "Dukcapil's December 2023 register."
        ),
        tags=("disasters", "volcanoes", "population", "age", "north-sulawesi"),
    ),
    DatasetMeta(
        slug="jumlah-penduduk-l-p-di-kab-kepulauan-sitaro",
        title="Population of Sitaro Islands regency by sex",
        source="bnpb-disaster",
        description=(
            "The population at risk from Mount Ruang, by sex, from Dukcapil's "
            "December 2023 register."
        ),
        tags=("disasters", "volcanoes", "population", "sex", "north-sulawesi"),
    ),
    DatasetMeta(
        slug="kalkulator-ppam-estimasi-penduduk-dan-kelompok-rentan-terdampak",
        title="PPAM calculator: people and vulnerable groups affected, Mount Ruang",
        source="bnpb-disaster",
        description=(
            "Estimated people affected and the vulnerable groups among them — pregnant "
            "women, women of reproductive age — as the health ministry's PPAM "
            "calculator derives them."
        ),
        tags=("disasters", "volcanoes", "population", "health", "vulnerability"),
    ),
    DatasetMeta(
        slug="kompilasi-data-jumlah-dan-dampak-kejadian-bencana-2024",
        title="Disaster events and impact, 2024",
        source="bnpb-disaster",
        description="Disaster events and their impact by province, regency and city in 2024.",
        tags=("disasters", "hazards", "casualties", "damage", "subnational"),
    ),
    DatasetMeta(
        slug="kompilasi-data-kejadian-dan-dampak-bencana-2025",
        title="Disaster events and impact, 2025",
        source="bnpb-disaster",
        description="Disaster events and their impact by province, regency and city in 2025.",
        tags=("disasters", "hazards", "casualties", "damage", "subnational"),
    ),
    DatasetMeta(
        slug="kompilasi-data-kejadian-dan-dampak-bencana-bansor-sumatera-2025",
        title="Sumatra floods and landslides, events and impact, 2025",
        source="bnpb-disaster",
        description=(
            "The 2025 Sumatra flood and landslide emergency — the cyclone Senyar "
            "sequence — and what it did, by affected regency."
        ),
        tags=("disasters", "floods", "landslides", "casualties", "sumatra"),
    ),
    DatasetMeta(
        slug="kompilasi-data-status-keadaan-darurat-bencana-seluruh-indonesia-tahun-2025",
        title="Disaster emergency status declarations, 2025",
        source="bnpb-disaster",
        description=(
            "Every emergency status a region declared in 2025 — alert, response and "
            "transition — with the decree behind it and how long it ran."
        ),
        tags=("disasters", "emergency-status", "governance", "subnational", "decrees"),
    ),
    DatasetMeta(
        slug="lewotobi-2024",
        title="Mount Lewotobi Laki-Laki eruption response, 2024",
        source="bnpb-disaster",
        description=(
            "The emergency response to the eruption of 3 November 2024: displacement "
            "sites, the people in them and what they were given."
        ),
        tags=("disasters", "volcanoes", "emergency-response", "displacement", "east-nusa-tenggara"),
    ),
    DatasetMeta(
        slug="brent-crude",
        title="Brent crude oil price",
        source="yahoo-brent-crude",
        description="Daily open, high, low and close for Brent crude futures.",
        tags=("oil", "energy", "commodities", "markets", "prices"),
    ),
    DatasetMeta(
        slug="cocoa",
        title="Cocoa price",
        source="yahoo-cocoa",
        description="Daily open, high, low and close for ICE cocoa futures.",
        tags=("cocoa", "agriculture", "commodities", "markets", "prices"),
    ),
    DatasetMeta(
        slug="coffee",
        title="Coffee price",
        source="yahoo-coffee",
        description="Daily open, high, low and close for ICE Arabica coffee futures.",
        tags=("coffee", "agriculture", "commodities", "markets", "prices"),
    ),
    DatasetMeta(
        slug="consumer-survey",
        title="Consumer survey",
        source="bi-consumer-survey",
        description=(
            "Bank Indonesia's Survei Konsumen: the consumer confidence index, its "
            "current-conditions and expectations components, and the city breakdown."
        ),
        tags=("sentiment", "surveys", "consumption", "households", "monetary"),
    ),
    DatasetMeta(
        slug="copper",
        title="Copper price",
        source="yahoo-copper",
        description="Daily open, high, low and close for COMEX copper futures.",
        tags=("copper", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="exchange-rates",
        title="Exchange rates",
        source="yahoo-exchange-rates",
        description=(
            "Daily open, high, low and close for eight currency pairs: the rupiah "
            "against the US dollar, euro, yen, pound, Singapore dollar, ringgit and "
            "baht, and the dollar against the yuan — which Yahoo carries with no "
            "rupiah cross behind it. Five years."
        ),
        tags=("currencies", "exchange-rates", "rupiah", "markets", "asean"),
    ),
    DatasetMeta(
        slug="food-prices",
        title="Strategic food prices",
        source="bi-pihps-food-prices",
        description=(
            "PIHPS daily prices for the ten strategic foods and their thirty-one "
            "grades, nationally and for each of the 34 provinces, in all four "
            "markets Bank Indonesia surveys — traditional, modern, wholesale and "
            "farmgate. Daily since March 2017."
        ),
        tags=("food", "agriculture", "prices", "inflation", "households"),
    ),
    DatasetMeta(
        slug="bps-indicators",
        title="BPS statistical tables",
        source="bps-indicators",
        description=(
            "Every table in Statistics Indonesia's national catalogue, from its "
            "web API: some 1,750 variables across 51 subjects — population, "
            "poverty, labour, prices and inflation, national and regional "
            "accounts, trade, agriculture, education, health and the SDG "
            "indicators — by province, city or category, one series per "
            "variable and breakdown. The publisher's own figures, where "
            "Kemendagri and Trading Economics republish them."
        ),
        tags=("population", "poverty", "labour", "prices", "growth", "sdg"),
    ),
    DatasetMeta(
        slug="sp2kp-national-prices",
        title="National weighted food prices",
        source="kemendag-sp2kp-national",
        description=(
            "The Ministry of Trade's harga nasional tertimbang: one weighted "
            "price for the whole country per staple good per day, for the 42 "
            "goods it prices, daily since February 2024. Read from the "
            "dashboard's own API rather than its Tableau view, so unlike the "
            "regency crosstab this can be rebuilt from nothing — and it covers "
            "42 goods where the crosstab covers 17, Bulog's SPHP rice and "
            "imported soybeans among them."
        ),
        tags=("food", "prices", "staples", "inflation", "trade-ministry"),
    ),
    DatasetMeta(
        slug="sp2kp-variants",
        title="SP2KP commodity master",
        source="kemendag-sp2kp-national",
        description=(
            "The ministry's own list of the goods it prices, with the unit and "
            "commodity group of each. Landed so a replay can name a series: "
            "the price endpoint answers with dates and figures and never says "
            "which good it priced."
        ),
        tags=("reference", "commodities", "trade-ministry"),
    ),
    DatasetMeta(
        slug="sp2kp-food-prices",
        title="Food prices by regency, and the ceiling",
        source="kemendag-sp2kp-prices",
        description=(
            "The Ministry of Trade's daily market monitoring: seventeen staple "
            "goods priced in each of Indonesia's 513 regencies and cities, with "
            "the government's ceiling or reference price (HET/HA) beside each "
            "one. Finer than Bank Indonesia's PIHPS, which stops at the "
            "province, and it carries Minyakita and wheat flour, which PIHPS "
            "does not. One day per run — the view exports the day it shows, so "
            "history accumulates forward and cannot be backfilled."
        ),
        tags=("food", "prices", "staples", "subnational", "regencies", "trade-ministry"),
    ),
    DatasetMeta(
        slug="fred-indonesia-series",
        title="FRED Indonesian series",
        source="fred-indonesia",
        description=(
            "Every series FRED carries for Indonesia, as FRED redistributes them from "
            "the OECD, the IMF, the World Bank and the BIS."
        ),
        tags=("macroeconomy", "international", "redistributed", "reference-series"),
    ),
    DatasetMeta(
        slug="gdp",
        title="Gross domestic product",
        source="worldbank-gdp",
        description="Indonesian GDP in current US dollars, World Bank series NY.GDP.MKTP.CD.",
        tags=("gdp", "national-accounts", "macroeconomy", "annual-series"),
    ),
    DatasetMeta(
        slug="gold",
        title="Gold price",
        source="yahoo-gold",
        description="Daily open, high, low and close for COMEX gold futures.",
        tags=("gold", "metals", "commodities", "markets", "prices"),
    ),
    DatasetMeta(
        slug="handbook",
        title="Handbook of Energy and Economic Statistics of Indonesia",
        source="esdm-heesi",
        description=(
            "ESDM's annual handbook, one PDF per edition: energy production, "
            "consumption, generation, reserves, trade and prices, by fuel and by "
            "sector. Held as a document and nothing more — no series is published "
            "from it, because nothing here reads its tables yet."
        ),
        tags=("energy", "electricity", "mining", "oil", "gas", "coal", "handbook"),
    ),
    DatasetMeta(
        slug="ihsg",
        title="Jakarta Composite Index (IHSG)",
        source="yahoo-ihsg",
        description="Daily open, high, low, close and volume for the Jakarta Composite Index.",
        tags=("equities", "markets", "ihsg", "indonesia", "prices"),
    ),
    DatasetMeta(
        slug="indonesia-indicators",
        title="Indonesian headline indicators",
        source="tradingeconomics-indonesia",
        description=(
            "Trading Economics' Indonesian indicator pages: the latest print for each "
            "headline series, with its previous value and release calendar."
        ),
        tags=("macroeconomy", "headline-indicators", "indonesia", "market-data"),
    ),
    DatasetMeta(
        slug="movement-distribution",
        title="Movement distribution",
        source="hdx-meta-movement-distribution",
        description=(
            "How far people travel from where they live, by regency and day: the "
            "share of movements staying within a district, reaching 10 km, 100 km, "
            "or further. Aggregated by Meta from Facebook location histories and "
            "released through HDX."
        ),
        tags=("mobility", "movement", "population", "social", "meta", "hdx"),
    ),
    DatasetMeta(
        slug="movement-distribution-releases",
        title="Movement distribution releases",
        source="hdx-meta-movement-distribution",
        description=(
            "The HDX package listing as it stood on the day of collection: which "
            "resources existed, what they cover, and under what licence. A CSV "
            "states none of that about itself."
        ),
        tags=("mobility", "metadata", "provenance", "hdx", "listing"),
    ),
    DatasetMeta(
        slug="milex",
        title="Military expenditure",
        source="sipri-milex",
        description=(
            "SIPRI military expenditure for Indonesia: constant and current dollars, "
            "local currency, per capita, and shares of GDP and government spending."
        ),
        tags=("defence", "security", "military-spending", "fiscal", "international"),
    ),
    DatasetMeta(
        slug="organized-violence",
        title="Organized violence and conflict deaths",
        source="ucdp-organized-violence",
        description=(
            "UCDP's country-year account of organized violence in Indonesia since 1989: "
            "deaths in state-based, non-state and one-sided violence with the low and "
            "high estimates that bound them, who the dead were, and how many pairs of "
            "actors were fighting each year."
        ),
        tags=("conflict", "violence", "security", "casualties", "peace", "international"),
    ),
    # -- VEWS, the incidents and the series counted off them ---------------
    #
    # Two collections out of one set of exports, because they are two grains:
    # the incidents are the dataset VEWS publishes, and the series are what
    # Silver can hold. Only the second reaches the portal's catalogue, which
    # is derived from the observations — the first has no series in it.
    DatasetMeta(
        slug="collective-violence-early-warning",
        title="Collective violence early warning",
        source="vews-collective-violence",
        description=(
            "Collective violence in Indonesia, counted off VEWS's verified yearly "
            "releases: how many incidents each province saw, how many people were "
            "killed and injured, how many of them were women and children, how much "
            "was damaged and destroyed, and how often a third party intervened. "
            "Indonesia carries the whole year rather than the sum of the provinces, "
            "so adding the two levels counts the year twice. One year per release, "
            "and only incidents dated to the year that release covers."
        ),
        tags=(
            "conflict",
            "violence",
            "security",
            "casualties",
            "early-warning",
            "social-conflict",
            "subnational",
        ),
    ),
    DatasetMeta(
        slug="collective-violence-incidents",
        title="Collective violence incidents, as coded",
        source="vews-collective-violence",
        description=(
            "One row per incident VEWS coded and verified: the date, the district, "
            "the two sides and whether either was a state actor, the form the "
            "violence took, the weapon, the issue behind it, who was hurt, what was "
            "damaged, who intervened and what came of it, with the coder's own "
            "description. The detail the early warning series are counted from; no "
            "series is published from it, because an incident is not an observation."
        ),
        tags=(
            "conflict",
            "violence",
            "security",
            "early-warning",
            "social-conflict",
            "microdata",
            "events",
        ),
    ),
    DatasetMeta(
        slug="palm-oil",
        title="Crude palm oil price",
        source="yahoo-palm-oil",
        description="Daily open, high, low and close for CME Malaysian crude palm oil futures.",
        tags=("palm-oil", "agriculture", "commodities", "markets", "prices"),
    ),
    DatasetMeta(
        slug="population",
        title="Population, total",
        source="worldbank-population",
        description="Indonesian population, World Bank series SP.POP.TOTL.",
        tags=("population", "demography", "annual-series", "social"),
    ),
    DatasetMeta(
        slug="retail-sales",
        title="Retail sales survey",
        source="bi-retail-sales-survey",
        description=(
            "Bank Indonesia's Survei Penjualan Eceran: the real sales index and its "
            "year-on-year growth."
        ),
        tags=("retail", "consumption", "surveys", "sentiment", "monthly-series"),
    ),
    DatasetMeta(
        slug="seki-tables",
        title="Statistik Ekonomi dan Keuangan Indonesia",
        source="bi-seki",
        description=(
            "SEKI, Bank Indonesia's monthly compendium of Indonesian economic and "
            "financial statistics: national accounts, prices, government finance, "
            "trade and the balance of payments, read out of the published workbooks "
            "cell by cell as `reference/seki/variables.csv` declares them."
        ),
        tags=(
            "seki",
            "macroeconomy",
            "national-accounts",
            "prices",
            "public-finance",
            "trade",
            "monetary",
        ),
    ),
    DatasetMeta(
        slug="thermal-coal",
        title="Thermal coal price",
        source="yahoo-thermal-coal",
        description="Daily open, high, low and close for API2 CIF ARA thermal coal futures.",
        tags=("coal", "energy", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="newcastle-coal",
        title="Thermal coal price (Newcastle)",
        source="tradingeconomics-coal",
        description=(
            "Daily close of ICE Newcastle front-month thermal coal futures, the Asian "
            "seaborne benchmark Indonesian coal is priced against, from December 2008."
        ),
        tags=("coal", "energy", "mining", "commodities", "prices"),
    ),
    # -- Metals: Yahoo, the LME, and China's exchanges --------------------
    #
    # Nickel and tin have no Yahoo contract, so the LME's official prices come
    # through Westmetall and Shanghai's through Sina. Some metals appear in
    # more than one market on purpose: London, New York and Shanghai quote
    # them in different currencies and part company often enough to matter.
    DatasetMeta(
        slug="aluminium",
        title="Aluminium price (COMEX)",
        source="yahoo-aluminium",
        description="Daily open, high, low and close for COMEX aluminium futures.",
        tags=("aluminium", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="zinc",
        title="Zinc price (COMEX)",
        source="yahoo-zinc",
        description="Daily open, high, low and close for COMEX zinc futures.",
        tags=("zinc", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="iron-ore",
        title="Iron ore price",
        source="yahoo-iron-ore",
        description=(
            "Daily open, high, low and close for iron ore 62% Fe CFR China (TSI) futures on COMEX."
        ),
        tags=("iron-ore", "steel", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="silver",
        title="Silver price",
        source="yahoo-silver",
        description="Daily open, high, low and close for COMEX silver futures.",
        tags=("silver", "precious-metals", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="platinum",
        title="Platinum price",
        source="yahoo-platinum",
        description="Daily open, high, low and close for NYMEX platinum futures.",
        tags=("platinum", "precious-metals", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="palladium",
        title="Palladium price",
        source="yahoo-palladium",
        description="Daily open, high, low and close for NYMEX palladium futures.",
        tags=("palladium", "precious-metals", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="hot-rolled-coil",
        title="Hot-rolled coil steel price",
        source="yahoo-hot-rolled-coil",
        description=(
            "Daily open, high, low and close for US Midwest hot-rolled coil steel "
            "futures, in dollars per short ton."
        ),
        tags=("steel", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="lme-nickel",
        title="Nickel price (LME)",
        source="westmetall-lme",
        description=(
            "The London Metal Exchange's daily official cash settlement and three-month "
            "prices in USD/t, and LME warehouse stocks in tonnes, from 2008."
        ),
        tags=("nickel", "batteries", "lme", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="lme-tin",
        title="Tin price (LME)",
        source="westmetall-lme",
        description=(
            "The London Metal Exchange's daily official cash settlement and three-month "
            "prices in USD/t, and LME warehouse stocks in tonnes, from 2008."
        ),
        tags=("tin", "lme", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="lme-copper",
        title="Copper price (LME)",
        source="westmetall-lme",
        description=(
            "The London Metal Exchange's daily official cash settlement and three-month "
            "prices in USD/t, and LME warehouse stocks in tonnes, from 2008."
        ),
        tags=("copper", "lme", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="lme-aluminium",
        title="Aluminium price (LME)",
        source="westmetall-lme",
        description=(
            "The London Metal Exchange's daily official cash settlement and three-month "
            "prices in USD/t, and LME warehouse stocks in tonnes, from 2008."
        ),
        tags=("aluminium", "lme", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="lme-zinc",
        title="Zinc price (LME)",
        source="westmetall-lme",
        description=(
            "The London Metal Exchange's daily official cash settlement and three-month "
            "prices in USD/t, and LME warehouse stocks in tonnes, from 2008."
        ),
        tags=("zinc", "lme", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="lme-lead",
        title="Lead price (LME)",
        source="westmetall-lme",
        description=(
            "The London Metal Exchange's daily official cash settlement and three-month "
            "prices in USD/t, and LME warehouse stocks in tonnes, from 2008."
        ),
        tags=("lead", "lme", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="shfe-nickel",
        title="Nickel price (Shanghai)",
        source="sina-shfe-nickel",
        description=(
            "Daily open, high, low, close and settlement for the continuous contract in "
            "nickel on the Shanghai Futures Exchange, in yuan per tonne, from 2015."
        ),
        tags=("nickel", "china", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="shfe-tin",
        title="Tin price (Shanghai)",
        source="sina-shfe-tin",
        description=(
            "Daily open, high, low, close and settlement for the continuous contract in "
            "tin on the Shanghai Futures Exchange, in yuan per tonne, from 2015."
        ),
        tags=("tin", "china", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="shfe-stainless",
        title="Stainless steel price (Shanghai)",
        source="sina-shfe-stainless",
        description=(
            "Daily open, high, low, close and settlement for the continuous contract in "
            "stainless steel on the Shanghai Futures Exchange, in yuan per tonne, from 2019."
        ),
        tags=("steel", "nickel", "china", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="shfe-aluminium",
        title="Aluminium price (Shanghai)",
        source="sina-shfe-aluminium",
        description=(
            "Daily open, high, low, close and settlement for the continuous contract in "
            "aluminium on the Shanghai Futures Exchange, in yuan per tonne, from 2005."
        ),
        tags=("aluminium", "china", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="shfe-zinc",
        title="Zinc price (Shanghai)",
        source="sina-shfe-zinc",
        description=(
            "Daily open, high, low, close and settlement for the continuous contract in "
            "zinc on the Shanghai Futures Exchange, in yuan per tonne, from 2007."
        ),
        tags=("zinc", "china", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="dce-iron-ore",
        title="Iron ore price (Dalian)",
        source="sina-dce-iron-ore",
        description=(
            "Daily open, high, low, close and settlement for the continuous contract in "
            "iron ore on the Dalian Commodity Exchange, in yuan per tonne, from 2013."
        ),
        tags=("iron-ore", "steel", "china", "metals", "mining", "commodities", "prices"),
    ),
    DatasetMeta(
        slug="dce-coking-coal",
        title="Coking coal price (Dalian)",
        source="sina-dce-coking-coal",
        description=(
            "Daily open, high, low, close and settlement for the continuous contract in "
            "coking coal on the Dalian Commodity Exchange, in yuan per tonne, from 2013."
        ),
        tags=("coal", "energy", "china", "mining", "commodities", "prices"),
    ),
    # -- The Indonesian agency portals ------------------------------------
    #
    # One entry per collection the portal sources in `sources/` land. The
    # sources that are registered but gated — a paid AIS feed, a portal behind
    # a login, a host that stopped resolving — have no entry here, because
    # this registry describes collections that exist in the lake and they do
    # not yet. What they hold is in their `notes` instead.
    DatasetMeta(
        slug="agriculture-listing",
        title="Satu Data Pertanian catalogue pages",
        source="kementan-satudata",
        description=(
            "The agriculture ministry's dataset listing as it stood on the day of "
            "collection: which datasets were published and what they were called."
        ),
        tags=("agriculture", "catalogue", "provenance", "open-data"),
    ),
    DatasetMeta(
        slug="agriculture-statistics",
        title="Agricultural production, area and yield",
        source="kementan-satudata",
        description=(
            "What Indonesia grows and how much of it: production, harvested area "
            "and yield by commodity and province, as the agriculture ministry "
            "publishes them."
        ),
        tags=("agriculture", "production", "food", "commodities", "subnational"),
    ),
    DatasetMeta(
        slug="apbn-kita",
        title="APBN KiTa, the monthly state budget report",
        source="kemenkeu-apbn-kita",
        description=(
            "Central government revenue, spending, deficit and financing against "
            "the budget, month by month. DJPK's APBD covers the regional half."
        ),
        tags=("fiscal", "public-finance", "budget", "government", "central"),
    ),
    DatasetMeta(
        slug="apbn-kita-listing",
        title="APBN KiTa editions",
        source="kemenkeu-apbn-kita",
        description="The finance ministry's own listing of which monthly reports exist.",
        tags=("fiscal", "catalogue", "provenance"),
    ),
    DatasetMeta(
        slug="bawaslu-publications",
        title="Election supervision publications",
        source="bawaslu-publications",
        description=(
            "What the election supervisor published: violations, rulings and the "
            "supervision of each stage of an election."
        ),
        tags=("elections", "oversight", "governance", "violations", "democracy"),
    ),
    DatasetMeta(
        slug="bps-domain-catalogue",
        title="BPS domains: every region BPS keys figures by",
        source="bps-webapi",
        description=(
            "The region codes BPS files statistics under, from the national domain "
            "down to the regency, as its web API answers them."
        ),
        tags=("statistics", "regions", "reference", "codes", "bps"),
    ),
    DatasetMeta(
        slug="bps-subject-catalogue",
        title="BPS subjects",
        source="bps-webapi",
        description="The subject tree BPS files its tables under.",
        tags=("statistics", "catalogue", "reference", "bps"),
    ),
    DatasetMeta(
        slug="bps-var-catalogue",
        title="BPS variables, national domain",
        source="bps-webapi",
        description=(
            "Every variable BPS publishes for the national domain: what can be "
            "asked for, before asking for it."
        ),
        tags=("statistics", "catalogue", "reference", "indicators", "bps"),
    ),
    DatasetMeta(
        slug="earthquake-latest",
        title="Latest earthquake",
        source="bmkg-earthquakes",
        description=(
            "The most recent earthquake of any magnitude, with its depth, "
            "coordinates, felt intensities and shakemap, as BMKG reports it."
        ),
        tags=("earthquakes", "hazards", "geophysics", "realtime", "bmkg"),
    ),
    DatasetMeta(
        slug="earthquakes-recent",
        title="Recent earthquakes at M5.0 and above",
        source="bmkg-earthquakes",
        description=(
            "The last fifteen earthquakes BMKG recorded at magnitude 5.0 or more, "
            "with location, depth and tsunami potential."
        ),
        tags=("earthquakes", "hazards", "geophysics", "realtime", "bmkg"),
    ),
    DatasetMeta(
        slug="earthquakes-felt",
        title="Earthquakes reported felt",
        source="bmkg-earthquakes",
        description=(
            "The last fifteen earthquakes people reported feeling, with the "
            "modified Mercalli intensity recorded for each place."
        ),
        tags=("earthquakes", "hazards", "intensity", "realtime", "bmkg"),
    ),
    DatasetMeta(
        slug="esdm-statistics",
        title="ESDM sectoral statistics publications",
        source="esdm-publications",
        description=(
            "Oil and gas, electricity, and mineral and coal statistics, as the "
            "ministry publishes them. The annual energy handbook is not among "
            "them: its tables need a reader that is not ready yet."
        ),
        tags=("energy", "oil", "gas", "electricity", "mining", "statistics"),
    ),
    DatasetMeta(
        slug="esdm-statistics-listing",
        title="ESDM publication shelf",
        source="esdm-publications",
        description="Which statistical publications the ministry listed on the day.",
        tags=("energy", "catalogue", "provenance"),
    ),
    DatasetMeta(
        slug="food-prices-consumer",
        title="Food prices at consumer level",
        source="badanpangan-panel-harga",
        description=(
            "Daily consumer prices for the strategic foods by province, from the "
            "food agency's own enumerator panel — the second independent measure "
            "beside Bank Indonesia's PIHPS survey."
        ),
        tags=("food", "prices", "inflation", "households", "agriculture"),
    ),
    DatasetMeta(
        slug="food-prices-producer",
        title="Food prices at producer level",
        source="badanpangan-panel-harga",
        description=(
            "Daily farmgate prices for the strategic foods by province, as the "
            "food agency's panel collects them."
        ),
        tags=("food", "prices", "agriculture", "farmgate", "producers"),
    ),
    DatasetMeta(
        slug="food-prices-wholesale",
        title="Food prices at wholesale level",
        source="badanpangan-panel-harga",
        description="Daily wholesale prices for the strategic foods by province.",
        tags=("food", "prices", "wholesale", "agriculture", "markets"),
    ),
    DatasetMeta(
        slug="gdelt-events",
        title="GDELT coded events",
        source="gdelt-events",
        description=(
            "World news coded into events every fifteen minutes: who did what to "
            "whom, where, under CAMEO. Global; Indonesia is selected at extraction."
        ),
        tags=("news", "events", "conflict", "media", "international"),
    ),
    DatasetMeta(
        slug="gdelt-mentions",
        title="GDELT event mentions",
        source="gdelt-events",
        description=(
            "Every article mentioning each coded event, with its source and tone — "
            "how loudly something was reported, as distinct from that it happened."
        ),
        tags=("news", "media", "events", "coverage", "international"),
    ),
    DatasetMeta(
        slug="gdelt-knowledge-graph",
        title="GDELT global knowledge graph",
        source="gdelt-events",
        description=(
            "Themes, people, organizations and tone extracted from the same news. "
            "Collected only when a run asks for it: six megabytes a quarter-hour."
        ),
        tags=("news", "media", "themes", "entities", "international"),
    ),
    DatasetMeta(
        slug="geoportal-pages",
        title="Ina-Geoportal pages",
        source="big-inageoportal",
        description=(
            "The national geospatial clearing house as served: what BIG publishes "
            "under the One Map policy, and the state of its API."
        ),
        tags=("geospatial", "boundaries", "maps", "one-map", "reference"),
    ),
    DatasetMeta(
        slug="gfw-catalogue",
        title="Global Forest Watch dataset catalogue",
        source="gfw-catalogue",
        description=(
            "Every dataset the GFW data API serves, with its licence, its fields "
            "and what it was derived from."
        ),
        tags=("forests", "catalogue", "remote-sensing", "environment", "provenance"),
    ),
    DatasetMeta(
        slug="gfw-dataset-versions",
        title="Forest change dataset versions",
        source="gfw-catalogue",
        description=(
            "The versions of the forest datasets an Indonesian deforestation "
            "question is asked of — tree cover loss, cover density, carbon "
            "emissions and integrated alerts. A GFW figure without its version "
            "cannot be reproduced."
        ),
        tags=("forests", "deforestation", "versions", "remote-sensing", "environment"),
    ),
    DatasetMeta(
        slug="air-quality",
        title="Air quality by province",
        source="gee-air-quality",
        description=(
            "Monthly pollution over each of the 38 provinces: NO2, SO2, CO, ozone, "
            "formaldehyde and the aerosol index from Sentinel-5P, and surface PM2.5 "
            "and PM10 from the CAMS model, averaged per province in Google Earth "
            "Engine. The satellite figures are atmospheric columns, not ground-level "
            "readings, and are not comparable with ISPU."
        ),
        tags=("environment", "air-quality", "pollution", "remote-sensing", "province"),
    ),
    DatasetMeta(
        slug="jakarta-catalogue",
        title="Jakarta Open Data catalogue",
        source="jakarta-opendata",
        description=(
            "The capital's own figures — traffic, waste, flooding, permits and "
            "district budgets — as its open data portal lists them."
        ),
        tags=("jakarta", "subnational", "open-data", "urban", "catalogue"),
    ),
    DatasetMeta(
        slug="jdihn-listings",
        title="JDIHN network listings",
        source="jdihn-documents",
        description=(
            "The federated legal index across several hundred member "
            "documentation centres, and which institutions publish through it."
        ),
        tags=("law", "regulations", "index", "jdih", "governance"),
    ),
    DatasetMeta(
        slug="kemendag-catalogue",
        title="Satu Data Perdagangan catalogue",
        source="kemendag-satudata",
        description=(
            "What the trade ministry's portal exposes to a keyed caller: trade "
            "values by commodity and partner, and the licensing figures."
        ),
        tags=("trade", "exports", "imports", "catalogue", "commerce"),
    ),
    DatasetMeta(
        slug="kemkes-health-data",
        title="Health data room",
        source="kemkes-health-data",
        description=(
            "Health indicators and the facility register, as the health ministry's "
            "SATUSEHAT data room renders them."
        ),
        tags=("health", "facilities", "indicators", "public-health", "kemenkes"),
    ),
    DatasetMeta(
        slug="kemnaker-portal",
        title="Satu Data Ketenagakerjaan portal",
        source="kemnaker-satudata",
        description=(
            "The manpower ministry's data portal as served: vacancies, minimum "
            "wages, industrial relations and vocational training output."
        ),
        tags=("labour", "employment", "wages", "training", "portal"),
    ),
    DatasetMeta(
        slug="klhk-publications",
        title="Environment ministry publications",
        source="klhk-environment",
        description=(
            "What the environment ministry publishes since the 2024 split of KLHK: "
            "state-of-the-environment reporting, emissions and programme documents."
        ),
        tags=("environment", "emissions", "publications", "sustainability"),
    ),
    DatasetMeta(
        slug="kpu-elections",
        title="Election candidates, parties and decisions",
        source="kpu-elections",
        description=(
            "Who stood and under which party, as InfoPemilu renders it, with the "
            "KPU regulations and decrees that made each list official."
        ),
        tags=("elections", "candidates", "parties", "democracy", "governance"),
    ),
    DatasetMeta(
        slug="osm-editions",
        title="OpenStreetMap Indonesia extract editions",
        source="osm-geofabrik",
        description=(
            "Which daily cut of the Indonesian extract was current, by checksum, "
            "with the boundary polygon it was cut to."
        ),
        tags=("openstreetmap", "geospatial", "editions", "provenance", "maps"),
    ),
    DatasetMeta(
        slug="osm-extract",
        title="OpenStreetMap Indonesia extract",
        source="osm-geofabrik",
        description=(
            "The full Indonesian OSM extract: roads, buildings, land use and every "
            "mapped point of interest. Collected only when a run asks for it."
        ),
        tags=("openstreetmap", "geospatial", "infrastructure", "maps", "points-of-interest"),
    ),
    DatasetMeta(
        slug="peraturan-pusat",
        title="Central legislation listings",
        source="bpk-peraturan-pusat",
        description=(
            "Acts, government and presidential regulations and ministerial "
            "regulations as BPK's legal portal lists them, newest first, with the "
            "in-force status each carries."
        ),
        tags=("law", "regulations", "legislation", "central-government", "bpk"),
    ),
    DatasetMeta(
        slug="region-profiles",
        title="Provincial profile: people, prices and public finance",
        source="kemendagri-wilayah",
        description=(
            "What the interior ministry records about each of the 38 provinces: "
            "population and poverty, the human development index by sex, the Gini "
            "ratio, inflation, unemployment, economic growth, budget realisation, "
            "life expectancy and years of schooling — each dated by its own year, "
            "because the ministry refreshes them independently. It also carries "
            "the Kemendagri region codes themselves, which are the key every "
            "subnational figure in this warehouse joins on."
        ),
        tags=(
            "provinces",
            "subnational",
            "poverty",
            "human-development",
            "public-finance",
            "codes",
            "kemendagri",
        ),
    ),
    DatasetMeta(
        slug="satudata-catalogue",
        title="Satu Data Indonesia catalogue pages",
        source="satudata-indonesia",
        description=(
            "The cross-government catalogue as rendered: what each ministry and "
            "region has registered, which is how a publisher worth collecting "
            "properly is found."
        ),
        tags=("open-data", "catalogue", "discovery", "government", "metadata"),
    ),
    DatasetMeta(
        slug="school-register",
        title="School register",
        source="kemdikbud-referensi",
        description=(
            "Every Indonesian school with its NPSN identifier, level and location, "
            "as the education ministry's reference register publishes it."
        ),
        tags=("education", "schools", "registry", "reference", "subnational"),
    ),
    DatasetMeta(
        slug="trade-exports",
        title="Exports by partner, as partners report them",
        source="comtrade-indonesia",
        description=(
            "Indonesia's annual exports by partner country at the commodity total, "
            "from UN Comtrade — the independent account against which Kemendag's "
            "own figures can be checked."
        ),
        tags=("trade", "exports", "partners", "international", "comtrade"),
    ),
    DatasetMeta(
        slug="trade-exports-total",
        title="Exports to the world, annual total",
        source="comtrade-indonesia",
        description=(
            "Indonesia's total annual exports in US dollars, across every partner "
            "and every mode of transport, as UN Comtrade holds them. Asked for on "
            "its own because the partner breakdown can run past the response cap "
            "before the total is reached."
        ),
        tags=("trade", "exports", "totals", "international", "comtrade"),
    ),
    DatasetMeta(
        slug="trade-imports-total",
        title="Imports from the world, annual total",
        source="comtrade-indonesia",
        description=(
            "Indonesia's total annual imports in US dollars, across every partner "
            "and every mode of transport, as UN Comtrade holds them."
        ),
        tags=("trade", "imports", "totals", "international", "comtrade"),
    ),
    DatasetMeta(
        slug="tradestats",
        title="Trade competitiveness by sector",
        source="wits-tradestats",
        description=(
            "Revealed comparative advantage, exports and export shares by HS section, "
            "SITC group and stage of processing, for Indonesia, ASEAN and peer "
            "economies, as the World Bank's WITS computes them from UN Comtrade."
        ),
        tags=("trade", "exports", "competitiveness", "rca", "asean", "international"),
    ),
    DatasetMeta(
        slug="rca-atlas-hs6",
        title="Environmental goods competitiveness and economic complexity",
        source="rca-seed",
        description=(
            "Exports, revealed comparative advantage and advantaged-product counts for "
            "eight environmental goods lists (TESSD, APEC, ACCTS, SAGEA, EU–NZ, UK–NZ, "
            "OECD, UNCTAD), with the Economic Complexity Index and its companions, for "
            "Indonesia, ASEAN and peers — summed from the Atlas of Economic Complexity "
            "at HS92 six digits."
        ),
        tags=(
            "trade",
            "exports",
            "competitiveness",
            "rca",
            "environmental-goods",
            "complexity",
            "asean",
            "international",
        ),
    ),
    DatasetMeta(
        slug="rca-indonesia-hs6",
        title="Indonesia's environmental goods exports",
        source="rca-seed",
        description=(
            "Indonesia's exports of each environmental goods list, their share of "
            "total exports, and how many listed products carry a revealed comparative "
            "advantage — summed from WITS HS92 six-digit trade, 1995–2025."
        ),
        tags=("trade", "exports", "competitiveness", "rca", "environmental-goods"),
    ),
    DatasetMeta(
        slug="trade-imports",
        title="Imports by partner, as partners report them",
        source="comtrade-indonesia",
        description=(
            "Indonesia's annual imports by partner country at the commodity total, "
            "from UN Comtrade."
        ),
        tags=("trade", "imports", "partners", "international", "comtrade"),
    ),
    DatasetMeta(
        slug="transport-listing",
        title="Kemenhub publication shelf",
        source="kemenhub-statistics",
        description="Which transport statistics and publications the ministry listed.",
        tags=("transport", "catalogue", "provenance"),
    ),
    DatasetMeta(
        slug="transport-statistics",
        title="Transport statistics",
        source="kemenhub-statistics",
        description=(
            "Passengers and freight by road, rail, sea and air, port by port and "
            "airport by airport — the connectivity an archipelago's other figures "
            "are read against."
        ),
        tags=("transport", "logistics", "connectivity", "ports", "aviation"),
    ),
    DatasetMeta(
        slug="village-development",
        title="Village development index and the village fund",
        source="kemendesa-sid",
        description=(
            "The Indeks Desa Membangun score and classification per village, and "
            "the village fund that follows from it. Joins to PODES and to the "
            "school and facility registers on the village code."
        ),
        tags=("villages", "development", "idm", "village-fund", "subnational"),
    ),
    # -- News monitoring, one collection per layer of the same corpus --------
    #
    # Four, and they are genuinely four different things. An article is a piece
    # of writing. A coding is what a classifier made of one article. An event
    # is what several reports of one incident agree happened. A count is how
    # many of those there were. Collapsing any two of them would lose the
    # question a reader is actually asking.
    DatasetMeta(
        slug="news-articles",
        title="Indonesian news corpus",
        source="news-monitoring",
        description=(
            "The articles the daily crawl kept, from two newspapers per province plus "
            "two national papers: the outlet, the date, the headline, the lead and the "
            "body. Kept, not read — every article a paper published is read, and only "
            "the ones about a monitored issue are stored. What the rest amounted to is "
            "in the daily tallies."
        ),
        tags=("news", "media", "corpus", "indonesia", "monitoring"),
    ),
    DatasetMeta(
        slug="news-daily-tallies",
        title="What the press published, and how much was violence",
        source="news-monitoring",
        description=(
            "One row per newspaper per day: how many articles were read, how many "
            "matched the violence vocabulary, and how many a classifier confirmed, "
            "beside the candidates discovery turned up. The denominator every rate "
            "needs — without it a rise in incidents cannot be told from a crawl that "
            "reached further that week. A row exists for every outlet a run visited, "
            "including the ones that yielded nothing: no row at all means the crawl "
            "has never reached that paper, which is a different fact from a quiet week."
        ),
        tags=("news", "media", "coverage", "monitoring", "indonesia"),
    ),
    DatasetMeta(
        slug="news-violence-codings",
        title="Collective violence, coded per article",
        source="news-monitoring",
        description=(
            "One row per article a classifier read for collective violence, in the "
            "column names VEWS coders use. One row per report, not per incident: five "
            "papers covering one brawl produce five of these."
        ),
        tags=("news", "collective-violence", "coding", "machine-coded", "indonesia"),
    ),
    DatasetMeta(
        slug="news-violence-events",
        title="Collective violence incidents, from the press",
        source="news-monitoring",
        description=(
            "Reports collapsed into the incidents they describe — same district, "
            "within a day, same form of violence, overlapping actors. Machine-coded "
            "and never verified by a second reader, which is what separates these from "
            "the VEWS incidents they are shaped to be comparable with."
        ),
        tags=("news", "collective-violence", "incidents", "machine-coded", "indonesia"),
    ),
    DatasetMeta(
        slug="news-violence-counts",
        title="Press-reported collective violence, counted",
        source="news-monitoring",
        description=(
            "The incidents counted per province and month, with the dead, the injured, "
            "the women and children among them, the structures damaged and how often "
            "someone intervened. Counts what the press reported, which is not the same "
            "as what happened."
        ),
        tags=("news", "collective-violence", "subnational", "monthly", "indonesia"),
    ),
    DatasetMeta(
        slug="news-screenshots",
        title="News article screenshots",
        source="news-monitoring",
        description=(
            "One row per article photographed at the moment it was collected, pointing "
            "at the image in RAW. Taken for every article that matched an issue, "
            "because the page as it looked on the day is what a human verifier reads — "
            "and what survives a correction, a paywall or a deletion."
        ),
        tags=("news", "provenance", "evidence", "screenshots"),
    ),
)

# The Earth Engine products declare their own, beside the computation that
# produces them: thirty entries that would otherwise be restated here.
DATASETS += tuple(
    DatasetMeta(
        slug=product.slug,
        title=product.title,
        source=product.source_slug,
        description=product.description,
        tags=(*product.tags, "google-earth-engine"),
    )
    for product in GEE_PRODUCTS
)

_BY_SLUG = {dataset.slug: dataset for dataset in DATASETS}
_BY_CODE = {dataset.dataset_id: dataset for dataset in DATASETS}


def get(slug: str) -> DatasetMeta | None:
    """The declared record for a dataset slug, where there is one."""
    return _BY_SLUG.get(slug)


def by_code(code: str) -> DatasetMeta | None:
    """The declared record carrying this catalogue identifier."""
    return _BY_CODE.get(code)


def describe(slug: str, *, source: str | None = None) -> DatasetMeta:
    """The record for a dataset, declared or inferred.

    An undeclared dataset is described from its slug — `thermal-coal` becomes
    "Thermal coal" — which reads poorly but is true, and keeps a new scraper's
    figures in the catalogue instead of out of it.
    """
    declared = _BY_SLUG.get(slug)
    if declared is not None:
        return declared
    return DatasetMeta(
        slug=slug,
        title=slug.replace("-", " ").replace("_", " ").strip().capitalize(),
        source=source or "unknown",
    )
