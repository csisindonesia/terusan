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
        title="APBD realisation, national roll-up",
        source="djpk-apbd",
        description=(
            "Realised regional government revenue, own-source revenue, transfers and "
            "expenditure, summed to the national total, as DJPK publishes them."
        ),
        tags=("apbd", "fiscal", "public-finance", "subnational", "government"),
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
        slug="handbook",
        title="Handbook of Energy and Economic Statistics of Indonesia",
        source="esdm-heesi",
        description=(
            "ESDM's annual handbook: energy production, consumption, generation, "
            "reserves, trade and prices, by fuel and by sector."
        ),
        tags=("energy", "electricity", "mining", "oil", "gas", "coal", "heesi"),
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
