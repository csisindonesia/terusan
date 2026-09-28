"""The words a reader finds a series by.

Nothing in the lake arrives tagged. A FRED series carries a title, a unit and a
frequency; a Yahoo quote carries less than that; a Bank Indonesia workbook
carries a filename. So tags are derived here, from facts already recorded,
rather than typed by hand into a sheet that stops being true the first time a
source is re-normalized. Derived means they are rebuilt with the record they
describe, and cannot drift from it.

Two kinds go in, deliberately mixed:

* **Facets** — who published it, how often it refreshes, what it is measured
  in, where it is about. These are the filters a catalogue needs, and they are
  the same words on every record that shares the fact.
* **Topics** — what the series is *about*, read off its title. "Liquid
  Liabilities to GDP for Indonesia" is banking and it is national accounts, and
  neither word appears in any column we store.

Every catalogue record carries at least `MIN_TAGS`. A record too thin to reach
it — an unnamed series with no unit, which does happen — is padded from
`_FALLBACK` rather than published under three tags, because a minimum that
holds only sometimes is not a minimum anyone can filter on.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: Every catalogue record carries at least this many tags.
MIN_TAGS = 5

#: Cap. Past a dozen the chips stop being a filter and start being prose, and
#: the long tail is the least specific part of what the title said anyway.
MAX_TAGS = 14

_UNSAFE = re.compile(r"[^a-z0-9]+")

#: Tags that say what kind of record carries them rather than what it is
#: about. Useful on the record itself; meaningless travelling from a dataset's
#: series onto the dataset, which is a different kind of thing.
_STRUCTURAL = frozenset({"indicator", "dataset", "source-registry"})

#: Used only to reach `MIN_TAGS` on a record that has nothing else to say.
#: Ordered from the most to the least informative, so a record one tag short
#: gains the one that is still worth something.
_FALLBACK = ("time-series", "statistics", "observations", "research-data", "catalogued")

#: Topics, keyed by what has to appear in the text for them to apply. Matched
#: on word boundaries against the title, the dataset and the unit together.
#:
#: Written out rather than inferred: a keyword list is auditable and a
#: classifier is not, and the cost of being wrong here is a reader filtering to
#: a tag and not finding the series they wanted.
_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"gdp|gross domestic product|national account", ("gdp", "national-accounts", "macroeconomy")),
    (r"gross national income|\bgni\b", ("national-accounts", "macroeconomy")),
    (r"inflation|consumer price|\bcpi\b|deflator|harga konsumen", ("inflation", "prices")),
    (r"producer price|wholesale price", ("prices", "producer-prices")),
    # A quote's open, high, low and close. Bounded, since "low" is inside
    # "flow", and never an estimate's or a confidence's high and low.
    (
        r"\bprice\b|harga|quote|\bclose\b|\bopen\b(?! shrubland)"
        r"|\b(?:high|low)\b(?! estimate)(?!,? (?:nominal|or|and|confidence))",
        ("prices", "markets"),
    ),
    (r"export|import|trade balance|balance of trade|perdagangan", ("trade", "external-sector")),
    (
        r"current account|balance of payment|financial account|reserve",
        ("external-sector", "balance-of-payments"),
    ),
    # "Nilai Tukar Petani" is what a farmer's harvest buys, not a currency,
    # and BPS publishes some three thousand series of it.
    (
        r"exchange rate|\bkurs\b|nilai tukar(?! (?:petani|nelayan|usaha|pembudidaya))|rupiah per",
        ("exchange-rate", "monetary"),
    ),
    (
        r"nilai tukar (?:petani|nelayan|usaha|pembudidaya)|\bntp[a-z]*\b|\bntn\b|\bntup\b",
        ("farmers-terms-of-trade", "agriculture"),
    ),
    (
        r"interest rate|policy rate|central bank rate|suku bunga|yield",
        ("interest-rates", "monetary"),
    ),
    (r"money supply|monetary|liquidity|liquid liabilit", ("monetary", "money")),
    (r"\bbank|banking|perbankan|credit|loan|kredit|deposit", ("banking", "finance")),
    (r"fintech|peer to peer|p2p lending", ("fintech", "finance")),
    (r"insurance|asuransi|mutual fund|pension", ("finance", "non-bank-finance")),
    (r"stock|equity|equities|share price|composite index|ihsg|nasdaq", ("equities", "markets")),
    (r"bond|debt securit|sukuk", ("bonds", "debt", "markets")),
    (r"debt|utang|borrowing", ("debt", "fiscal")),
    (
        r"budget|apbd|apbn|anggaran|revenue|expenditure|belanja|pendapatan|tax|pajak",
        ("fiscal", "public-finance"),
    ),
    (r"realisasi|transfer|dana desa|own revenue", ("public-finance", "subnational")),
    (r"employment|unemployment|labor|labour|wage|upah|tenaga kerja", ("labour", "employment")),
    (r"population|penduduk|demograph|birth|mortalit", ("demography", "population")),
    (r"povert|gini|inequalit|kemiskinan|kemisikinan|\bmiskin", ("poverty", "welfare")),
    (
        r"consumer confidence|consumer survey|sentiment|expectation|tendency",
        ("sentiment", "surveys"),
    ),
    (r"retail sale|penjualan eceran|consumption|konsumsi", ("retail", "consumption")),
    (r"manufactur|industrial production|industri|\bpmi\b", ("industry", "manufacturing")),
    (r"construction|konstruksi|building", ("construction",)),
    (r"tourism|wisata|visitor|arrival|\bhotel|akomodasi", ("tourism",)),
    (r"agricultur|pertanian|\bfood|pangan|\brice\b|\bberas\b|\bcrop", ("agriculture", "food")),
    (r"palm oil|kelapa sawit|cpo", ("palm-oil", "commodities", "agriculture")),
    (r"\bcoal\b|batubara", ("coal", "energy", "commodities")),
    (r"crude oil|brent|petroleum|minyak", ("oil", "energy", "commodities")),
    (r"\bgas\b|lng", ("gas", "energy", "commodities")),
    (r"\bgold\b|\bemas\b", ("gold", "metals", "commodities")),
    (
        r"copper|tembaga|nickel|nikel|\btin\b|timah|aluminium",
        ("metals", "mining", "commodities"),
    ),
    (r"cocoa|kakao", ("cocoa", "agriculture", "commodities")),
    (r"coffee|kopi", ("coffee", "agriculture", "commodities")),
    (
        r"electricity|energy|energi|listrik|\bpower\b|bahan bakar|\bbbm\b|pembangkit",
        ("energy",),
    ),
    (
        r"renewable|terbarukan|\bsolar\b|surya|geothermal|panas bumi|hydropower|biofuel|biodiesel",
        ("renewable-energy", "energy"),
    ),
    (r"pertambangan|\btambang\b|\bmining\b|\bmineral", ("mining",)),
    (r"perikanan|\bikan\b|nelayan|fisher|aquacultur", ("fisheries",)),
    (
        r"transport|angkutan|kendaraan|vehicle|penumpang|passenger|pelabuhan|bandara|airport"
        r"|penerbangan|kereta api",
        ("transport",),
    ),
    (
        r"internet|telekomunikasi|telecommunication|komputer|computer|telepon seluler|mobile phone",
        ("digital",),
    ),
    (
        r"military|defence|defense|milex|armed forces|pertahanan",
        ("defence", "security", "military-spending"),
    ),
    (
        r"education|pendidikan|school|sekolah|siswa|murid|\bguru\b|literacy|melek huruf|buta huruf",
        ("education",),
    ),
    (
        r"health|kesehatan|hospital|rumah sakit|puskesmas|stunting|\bgizi\b|nutrition|penyakit"
        r"|disease|imunisasi|berobat|morbidit",
        ("health",),
    ),
    (
        r"pembangunan manusia|human development|\bipm\b|harapan hidup|life expectancy"
        r"|pembangunan gender|\bipg\b",
        ("human-development", "welfare"),
    ),
    (r"environment|lingkungan", ("environment",)),
    (r"climate|iklim", ("climate", "environment")),
    # Emissions as a statistics office counts them, and as a reader asking
    # about decarbonisation means them.
    (
        # Bounded: BPS spells "kemiskinan" as "kemisikinan" in one table.
        # "CO2" alone is also a soft drink's: BPS lists "minuman bersoda/mengandung CO2".
        r"\bemission|\bemisi|greenhouse|gas rumah kaca|\bgrk\b|ton co2|co2e|carbon|karbon",
        ("emissions", "climate", "environment"),
    ),
    # "hutan" is bounded because it sits inside "hutang".
    (
        r"forest|\bhutan\b|kehutanan|perhutanan|deforest|tree cover|mangrove|kayu bulat"
        r"|\bhph\b|timber",
        ("forestry", "environment"),
    ),
    (
        r"kebakaran|burned area|burnt area|active fire|hotspot|titik panas|karhutla|wildfire",
        ("fire", "disasters", "environment"),
    ),
    (
        r"disaster|bencana|banjir|\bflood|gempa|earthquake|longsor|landslide|tsunami",
        ("disasters",),
    ),
    (
        r"air minum|drinking water|air bersih|sanitasi|sanitation|surface water|groundwater"
        r"|air tanah",
        ("water",),
    ),
    (
        r"pollution|polusi|air quality|kualitas udara|pm2\.5|pm10|\bno2\b|\bso2\b|aerosol",
        ("environment", "air-quality"),
    ),
    (r"investment|fdi|capital formation|investasi", ("investment",)),
    (r"business confidence|business tendency", ("business", "surveys")),
    (r"government|pemerintah|public sector", ("government",)),
)

_COMPILED = tuple((re.compile(pattern), topics) for pattern, topics in _TOPICS)

#: Every tag a topic rule can give. `retopic` takes these off a published
#: record before reading its title again, so a rule that was wrong — or has
#: been narrowed — stops applying to what it once tagged.
_TOPIC_TAGS = frozenset(tag for _, topics in _TOPICS for tag in topics)

#: A country name in a title is worth a tag; the country column rarely holds
#: one for a series about somewhere else.
_PLACES: tuple[tuple[str, str], ...] = (
    (r"indonesia|indonesian|jakarta", "indonesia"),
    (r"asean|southeast asia", "asean"),
    (r"\bworld\b|global", "global"),
)


@dataclass(frozen=True, slots=True)
class SourceFacts:
    """What the registry knows about a provider, in the shape tagging needs.

    A small structure of its own because the same facts reach here from two
    directions — `SourceMeta` in code when a source is published, and a Silver
    row when the catalogue is rebuilt — and neither should have to know about
    the other.
    """

    source_id: str
    name: str | None = None
    organization: str | None = None
    category: str | None = None
    source_type: str | None = None
    collection_method: str | None = None
    country: str | None = None
    update_frequency: str | None = None

    @classmethod
    def from_meta(cls, meta) -> SourceFacts:  # noqa: ANN001 - SourceMeta, avoiding a cycle
        return cls(
            source_id=meta.slug,
            name=meta.name,
            organization=meta.organization,
            category=str(meta.category),
            source_type=str(meta.source_type),
            collection_method=str(meta.collection_method),
            country=meta.country,
            update_frequency=str(meta.update_frequency),
        )

    @classmethod
    def from_row(cls, row: dict) -> SourceFacts:
        return cls(
            source_id=str(row["source_id"]),
            name=row.get("name"),
            organization=row.get("organization"),
            category=row.get("category"),
            source_type=row.get("source_type"),
            collection_method=row.get("collection_method"),
            country=row.get("country"),
            update_frequency=row.get("update_frequency"),
        )


def normalize_tag(value: str | None) -> str | None:
    """One tag, as it is stored: lowercase, ASCII, hyphenated.

    Returns None for anything that reduces to nothing, so a null column does
    not become an empty tag that filters to every record at once.
    """
    if not value:
        return None
    ascii_only = (
        unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").lower()
    )
    cleaned = _UNSAFE.sub("-", ascii_only).strip("-")
    if not cleaned or cleaned in {"none", "null", "unknown", "na", "n-a"}:
        return None
    # A tag long enough to wrap is a sentence, not a filter. Publisher names
    # and units are the ones that run long, and their first few words are what
    # a reader would type.
    return cleaned[:48].strip("-") or None


def topics_of(*texts: str | None) -> list[str]:
    """Topic tags for whatever the record says about itself."""
    haystack = " ".join(t.lower() for t in texts if t)
    if not haystack:
        return []

    found: list[str] = []
    for pattern, topics in _COMPILED:
        if pattern.search(haystack):
            found.extend(topics)
    for pattern, place in _PLACES:
        if re.search(pattern, haystack):
            found.append(place)
    return found


def retopic(
    tags: Sequence[str], *texts: str | None, source: SourceFacts | None = None
) -> list[str]:
    """A published record's tags, with its topics read again.

    The facets are kept as they are: they came from columns and a registry the
    record no longer carries beside it. Only the topics — which come from the
    title alone — are replaced, so the rules can be corrected without
    collecting a source again. Idempotent: a record retopicked twice is the
    record retopicked once.

    `source` is the registry record, where it is known: a facet can spell the
    same word as a topic — a source whose category is "government" — and is
    kept even where the title no longer says it.
    """
    facts = set()
    if source:
        facts = {
            normalize_tag(value)
            for value in (
                source.source_id,
                source.category,
                source.source_type,
                _publisher_tag(source.organization) or _publisher_tag(source.name),
            )
        }
    topics = [normalize_tag(topic) for topic in topics_of(*texts)]
    # A place the title names is both a facet and a topic; left where the
    # facets are, it would move on every pass.
    kept = [
        tag
        for tag in tags
        if (tag in facts or tag not in _TOPIC_TAGS) and tag not in _STRUCTURAL and tag not in topics
    ]
    structural = [tag for tag in tags if tag in _STRUCTURAL]
    # Past the cap, the last topics go rather than the structural tags, which
    # the catalogue filters on.
    return _finish([*kept, *topics])[: MAX_TAGS - len(structural)] + structural


def source_tags(source: SourceFacts) -> list[str]:
    """Tags for a provider (program.md §16).

    Every fact in the registry record that a reader might filter on: who it is,
    what kind of body, how the bytes arrive, how often, and where from.
    """
    return _finish(
        [
            source.source_id,
            _publisher_tag(source.organization) or _publisher_tag(source.name),
            source.category,
            source.source_type,
            source.collection_method,
            _frequency_tag(source.update_frequency),
            _country_tag(source.country),
            "source-registry",
            *topics_of(source.name, source.organization, source.source_id.replace("-", " ")),
        ]
    )


def indicator_tags(
    *,
    slug: str | None,
    name: str | None = None,
    unit: str | None = None,
    frequency: str | None = None,
    publisher: str | None = None,
    dataset_slug: str | None = None,
    source: SourceFacts | None = None,
    geographies: Iterable[str | None] = (),
) -> list[str]:
    """Tags for one series (program.md §10).

    The facets come from the columns beside it — its source, its dataset, the
    unit it is quoted in, how often it refreshes — and the topics from its
    title, which is the only place most series say what they are about.
    """
    places = [_country_tag(geo) for geo in geographies]
    return _finish(
        [
            *(
                [
                    source.source_id,
                    source.category,
                    source.source_type,
                    _publisher_tag(source.organization) or _publisher_tag(source.name),
                ]
                if source
                else []
            ),
            _publisher_tag(publisher),
            dataset_slug,
            _frequency_tag(frequency),
            _unit_tag(unit),
            *places,
            *topics_of(
                name,
                slug and slug.replace("_", " "),
                dataset_slug and dataset_slug.replace("-", " "),
                unit,
            ),
            "indicator",
        ]
    )


def dataset_tags(
    *,
    slug: str,
    title: str | None = None,
    description: str | None = None,
    declared: Sequence[str] = (),
    source: SourceFacts | None = None,
    indicator_tags_seen: Iterable[Iterable[str]] = (),
) -> list[str]:
    """Tags for a collection as its publisher issues it (program.md §9).

    A dataset is the series inside it, so what they are tagged with counts —
    but only where they agree. A tag carried by one of forty series describes
    that series, not the collection, and hanging it on the dataset would make
    the filter lie.
    """
    members = [[tag for tag in tags if tag not in _STRUCTURAL] for tags in indicator_tags_seen]
    shared: list[str] = []
    if members:
        common = set(members[0]).intersection(*(set(tags) for tags in members[1:]))
        # Sorted for determinism: a set's order would reshuffle the tags on
        # every run and make every rewrite look like a change.
        shared = sorted(common)

    return _finish(
        [
            *declared,
            slug,
            *(
                [
                    source.source_id,
                    source.category,
                    _publisher_tag(source.organization) or _publisher_tag(source.name),
                    _frequency_tag(source.update_frequency),
                    _country_tag(source.country),
                ]
                if source
                else []
            ),
            *topics_of(title, description, slug.replace("-", " ")),
            *shared,
            "dataset",
        ]
    )


# ---- internals ---------------------------------------------------------


def _finish(candidates: Iterable[str | None]) -> list[str]:
    """Normalize, deduplicate, pad to `MIN_TAGS`, cap at `MAX_TAGS`.

    Order is kept — the caller lists the most identifying facts first — so the
    cap drops the vaguest tags rather than an arbitrary few.
    """
    tags: list[str] = []
    for candidate in candidates:
        tag = normalize_tag(candidate)
        if tag and tag not in tags:
            tags.append(tag)

    for filler in _FALLBACK:
        if len(tags) >= MIN_TAGS:
            break
        if filler not in tags:
            tags.append(filler)

    return tags[:MAX_TAGS]


def _frequency_tag(value: str | None) -> str | None:
    """A refresh cadence, as one word.

    Silver states a series' resolution as `MONTHLY` or `monthly` depending on
    which enum it came through, and a reader filtering on "monthly" should not
    have to know which.
    """
    tag = normalize_tag(value)
    return tag


def _unit_tag(value: str | None) -> str | None:
    """The unit, short enough to be a chip.

    FRED states units as "Millions of US Dollars, Seasonally Adjusted Annual
    Rate", which is a sentence. The first clause is the unit; the rest is the
    adjustment, and it is already on the observation.
    """
    if not value:
        return None
    head = re.split(r"[,(]", str(value))[0]
    return normalize_tag(head)


def _publisher_tag(value: str | None) -> str | None:
    """A publisher, as the part of its name that identifies it.

    Registry names read "Bank Indonesia — Survei Konsumen": the body is the
    part worth filtering on, and the survey is already the dataset.
    """
    if not value:
        return None
    head = re.split(r"[—–\-|:]", str(value))[0]
    return normalize_tag(head) or normalize_tag(value)


def _country_tag(value: str | None) -> str | None:
    """A place, by the name a reader would type rather than its code."""
    tag = normalize_tag(value)
    if tag is None:
        return None
    known = {"id": "indonesia", "idn": "indonesia", "wld": "global", "world": "global"}
    return known.get(tag, tag)
