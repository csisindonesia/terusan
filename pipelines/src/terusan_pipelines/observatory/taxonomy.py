"""What each publisher's labels mean in the observatory's terms, declared once.

Every provider counts something slightly different and calls it something
slightly different. TikTok's "content actioned" does not say whether the item
was taken down or geo-blocked; Google's "items removed — legal" is a removal
for a legal reason, not a request; Komdigi's "penanganan konten" is what the
ministry handled, which is neither. So nothing here is inferred from a label's
wording at run time: each label a publisher uses is mapped by hand, below, to a
metric, a scope and a requester type, and a label nobody has mapped is not
guessed at — the record carrying it is quarantined until someone reads the
publisher's definition and adds a line (docs/observatory/methodology.md).

The extractor reads these tables to name the Silver series each as-published
figure belongs to, and the adapters read them to build observations, so the
two cannot disagree about what a column means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import cache


class Provider(StrEnum):
    META = "meta"
    TIKTOK = "tiktok"
    X = "x"
    GOOGLE = "google"
    KOMDIGI = "komdigi"
    OTHER = "other"


class Metric(StrEnum):
    """What a figure counts.

    Wider than the spec's list in one direction only: where a provider does not
    say whether an item was removed or restricted, the figure is `*_actioned`
    rather than being filed under one of the two. Preserving takedown versus
    geo-blocking where the data supports it means not inventing the
    distinction where it does not.
    """

    REQUESTS_RECEIVED = "requests_received"
    REQUESTS_ACTIONED = "requests_actioned"
    ITEMS_SPECIFIED = "items_specified"
    ITEMS_ACTIONED = "items_actioned"
    ITEMS_REMOVED = "items_removed"
    ITEMS_RESTRICTED = "items_restricted"
    ITEMS_NOT_ACTIONED = "items_not_actioned"
    ACCOUNTS_SPECIFIED = "accounts_specified"
    ACCOUNTS_ACTIONED = "accounts_actioned"
    ACCOUNTS_RESTRICTED = "accounts_restricted"
    ACCOUNTS_NOT_ACTIONED = "accounts_not_actioned"
    URLS_BLOCKED = "urls_blocked"
    #: Komdigi's "penanganan konten": items the ministry handled on a
    #: platform. Not a request count and not a platform action — its own metric
    #: so that it cannot be charted on the same axis as either by accident.
    CONTENT_HANDLED = "content_handled"
    #: Published ratios. Kept because the publisher states them, and kept apart
    #: from the counts by their unit; the observatory never computes one from
    #: counts that do not share a definition (analytics.py).
    COMPLIANCE_RATE = "compliance_rate"
    REMOVAL_RATE = "removal_rate"
    OTHER = "other"


class Unit(StrEnum):
    COUNT = "count"
    #: 0–100, as Google publishes its compliance figure.
    PERCENT = "percent"
    #: 0–1, as TikTok publishes its removal rate.
    RATIO = "ratio"


class RequesterType(StrEnum):
    GOVERNMENT = "government"
    COURT = "court"
    LAW_ENFORCEMENT = "law_enforcement"
    #: Google's pre-2019 "Other Requests (Executive, Police, etc.)": the
    #: executive and the police together, which cannot be split.
    EXECUTIVE_OR_POLICE = "executive_or_police"
    #: A total across every requester the provider counts.
    ALL = "all"
    UNKNOWN = "unknown"


class Scope(StrEnum):
    LOCAL_LAW = "local_law"
    TERMS_OF_SERVICE = "terms_of_service"
    ALL = "all"
    UNSPECIFIED = "unspecified"


class GeographicBasis(StrEnum):
    REQUEST_ORIGIN = "request_origin"
    RESTRICTION_LOCATION = "restriction_location"
    OTHER = "other"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceMethod(StrEnum):
    OFFICIAL_DOWNLOAD = "official_download"
    PUBLIC_HTML = "public_html"
    PDF = "pdf"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Measure:
    """One publisher label, and what it means here."""

    #: The label exactly as the publisher writes it: a column header or a
    #: value of a `metric_name` column.
    label: str
    metric: Metric
    scope: Scope = Scope.ALL
    unit: Unit = Unit.COUNT
    #: Who asked, where the label itself says — Google's pre-2019 columns are
    #: split by branch of government in the header.
    requester: RequesterType | None = None
    confidence: Confidence = Confidence.HIGH
    #: The Silver series an as-published country figure is filed under, where
    #: there is one. None keeps the figure in the observatory's own tables only
    #: — a breakdown row, or a figure whose unit the publisher does not define.
    indicator: str | None = None
    #: What a reader sees beside the identifier.
    series_name: str | None = None
    note: str | None = None


# -- Google ----------------------------------------------------------------

#: `requestor` in Google's 2019-onward CSV. A category of requester, never an
#: agency: "Information and Communications Authority" in Indonesia's rows is the
#: closest Google comes to naming Komdigi, and the observatory does not close
#: the gap for it.
GOOGLE_REQUESTERS: dict[str, RequesterType] = {
    "Court Order directed at 3rd party": RequesterType.COURT,
    "Court Order directed at Google": RequesterType.COURT,
    "Police": RequesterType.LAW_ENFORCEMENT,
    "Military": RequesterType.GOVERNMENT,
    "Government Officials": RequesterType.GOVERNMENT,
    "Information and Communications Authority": RequesterType.GOVERNMENT,
    "Consumer Protection Authority": RequesterType.GOVERNMENT,
    "Data Protection Authority": RequesterType.GOVERNMENT,
    # Google does not say who issues these, and in most jurisdictions a court
    # does — but "most" is an inference, so the type stays unknown and the
    # label stays on the row.
    "Supression Orders": RequesterType.UNKNOWN,
    "Other": RequesterType.UNKNOWN,
}

#: Google's products, as platforms. YouTube is its own platform in the spec's
#: list; everything else Google runs is `google_other`, with the product kept.
GOOGLE_PLATFORMS: dict[str, str] = {"YouTube": "youtube"}
GOOGLE_DEFAULT_PLATFORM = "google_other"


#: The 2019-onward CSV: one row per country, period, requester, product and
#: reason, with every item's outcome. None of it is a country total, and a sum
#: of it is not one either: for 2019-S1, the one half-year Google published in
#: both formats, the rows add up to the stated total for 33 of 78 countries'
#: requests — Indonesia's rows sum to 35 where Google states 36, and to 121
#: items where it states 140. So the rows reach Silver as published, one figure
#: per requester, product and reason, under series that say they are a
#: breakdown — and no total is computed from them.
def _google_breakdown(label: str, metric: Metric, suffix: str, name: str, **kw: object) -> Measure:
    return Measure(
        label,
        metric,
        indicator=f"google_gov_breakdown_{suffix}",
        series_name=f"Google: {name}, by requester, product and reason",
        **kw,  # type: ignore[arg-type]
    )


GOOGLE_DETAIL_MEASURES: tuple[Measure, ...] = (
    _google_breakdown(
        "number_of_requests", Metric.REQUESTS_RECEIVED, "requests", "government removal requests"
    ),
    _google_breakdown(
        "items_requested_removal",
        Metric.ITEMS_SPECIFIED,
        "items_requested",
        "items requested for removal",
    ),
    _google_breakdown(
        "items_removed_legal",
        Metric.ITEMS_REMOVED,
        "items_removed_legal",
        "items removed for legal reasons",
        scope=Scope.LOCAL_LAW,
    ),
    _google_breakdown(
        "items_removed_policy",
        Metric.ITEMS_REMOVED,
        "items_removed_policy",
        "items removed for policy reasons",
        scope=Scope.TERMS_OF_SERVICE,
    ),
    _google_breakdown(
        "items_content_not_found",
        Metric.ITEMS_NOT_ACTIONED,
        "items_not_found",
        "items not actioned, content not found",
    ),
    _google_breakdown(
        "items_not_enough_info",
        Metric.ITEMS_NOT_ACTIONED,
        "items_not_enough_info",
        "items not actioned, not enough information",
    ),
    _google_breakdown(
        "items_no_action_taken",
        Metric.ITEMS_NOT_ACTIONED,
        "items_no_action",
        "items on which no action was taken",
    ),
    _google_breakdown(
        "items_content_already_removed",
        Metric.ITEMS_NOT_ACTIONED,
        "items_already_removed",
        "items not actioned, content already removed",
    ),
)

#: The outcome columns that between them account for every item requested.
#: Google's rows should add up across these; where one does not, it is flagged.
GOOGLE_ITEM_OUTCOMES = tuple(
    m.label
    for m in GOOGLE_DETAIL_MEASURES
    if m.label.startswith("items_") and m.label != "items_requested_removal"
)


def _google_branch(
    prefix: str, requester: RequesterType, suffix: str, name: str
) -> tuple[Measure, ...]:
    return (
        Measure(
            f"{prefix}: Number of Requests",
            Metric.REQUESTS_RECEIVED,
            requester=requester,
            indicator=f"google_gov_requests_{suffix}",
            series_name=f"Google: government removal requests, {name}",
        ),
        Measure(
            f"{prefix}: % Fully Or Partially Complied With",
            Metric.COMPLIANCE_RATE,
            unit=Unit.PERCENT,
            requester=requester,
            indicator=f"google_gov_compliance_pct_{suffix}",
            series_name=f"Google: share of removal requests complied with in full or part, {name}",
        ),
        Measure(
            f"{prefix}: Items Requested To Be Removed",
            Metric.ITEMS_SPECIFIED,
            requester=requester,
            indicator=f"google_gov_items_requested_{suffix}",
            series_name=f"Google: items requested for removal, {name}",
        ),
    )


#: The pre-2019 country totals, which Google published as such — the only
#: Google figures that are a country's own total and so the only ones Silver
#: carries.
GOOGLE_TOTAL_MEASURES: tuple[Measure, ...] = (
    *_google_branch("All Requests", RequesterType.ALL, "all", "all requesters"),
    *_google_branch("Court Orders", RequesterType.COURT, "court", "court orders"),
    *_google_branch(
        "Other Requests (Executive, Police, etc.)",
        RequesterType.EXECUTIVE_OR_POLICE,
        "executive",
        "executive and police",
    ),
)

#: The pre-2019 breakdown by product and reason.
GOOGLE_HISTORICAL_DETAIL_MEASURES: tuple[Measure, ...] = (
    Measure(
        "Court Orders",
        Metric.REQUESTS_RECEIVED,
        requester=RequesterType.COURT,
        confidence=Confidence.MEDIUM,
        note="Read as a count of requests, as the totals file's column of the same name is.",
    ),
    Measure(
        "Executive, Police, etc.",
        Metric.REQUESTS_RECEIVED,
        requester=RequesterType.EXECUTIVE_OR_POLICE,
        confidence=Confidence.MEDIUM,
        note="Read as a count of requests, as the totals file's column of the same name is.",
    ),
    Measure("Items Requested To Be Removed", Metric.ITEMS_SPECIFIED, requester=RequesterType.ALL),
)

#: `historical/google-government-detailed-removal-requests.csv` has one figure
#: per product and reason, headed `Total`, and the archive's README does not say
#: whether it counts requests or items. Kept, as `other`, and never compared.
GOOGLE_HISTORICAL_TOTAL_MEASURES: tuple[Measure, ...] = (
    Measure(
        "Total",
        Metric.OTHER,
        requester=RequesterType.ALL,
        confidence=Confidence.LOW,
        note="Google's README does not define whether Total counts requests or items.",
    ),
)

# -- TikTok ----------------------------------------------------------------


def _tiktok(label: str, metric: Metric, suffix: str, name: str, **kw: object) -> Measure:
    return Measure(
        label,
        metric,
        indicator=f"tiktok_gov_{suffix}",
        series_name=f"TikTok: {name}",
        requester=RequesterType.GOVERNMENT,
        **kw,  # type: ignore[arg-type]
    )


#: `metric_name` in TikTok's CSV. "Actioned" is TikTok's word and it does not
#: say whether the item was removed or restricted in the country, so neither is
#: claimed.
TIKTOK_MEASURES: tuple[Measure, ...] = (
    _tiktok(
        "Total requests received",
        Metric.REQUESTS_RECEIVED,
        "requests_received",
        "government removal requests received",
    ),
    _tiktok(
        "Total content received",
        Metric.ITEMS_SPECIFIED,
        "content_specified",
        "content items specified in government requests",
    ),
    _tiktok(
        "Total accounts received",
        Metric.ACCOUNTS_SPECIFIED,
        "accounts_specified",
        "accounts specified in government requests",
    ),
    _tiktok(
        "Content actioned due to (local) law violations",
        Metric.ITEMS_ACTIONED,
        "content_actioned_local_law",
        "content actioned for local law violations",
        scope=Scope.LOCAL_LAW,
    ),
    _tiktok(
        "Content actioned due to community guidelines violations",
        Metric.ITEMS_ACTIONED,
        "content_actioned_guidelines",
        "content actioned for community guidelines violations",
        scope=Scope.TERMS_OF_SERVICE,
    ),
    _tiktok(
        "Content not actioned",
        Metric.ITEMS_NOT_ACTIONED,
        "content_not_actioned",
        "content in government requests not actioned",
    ),
    _tiktok(
        "Accounts actioned due to (local) law violations",
        Metric.ACCOUNTS_ACTIONED,
        "accounts_actioned_local_law",
        "accounts actioned for local law violations",
        scope=Scope.LOCAL_LAW,
    ),
    _tiktok(
        "Accounts actioned due to community guidelines violations",
        Metric.ACCOUNTS_ACTIONED,
        "accounts_actioned_guidelines",
        "accounts actioned for community guidelines violations",
        scope=Scope.TERMS_OF_SERVICE,
    ),
    _tiktok(
        "Accounts not actioned",
        Metric.ACCOUNTS_NOT_ACTIONED,
        "accounts_not_actioned",
        "accounts in government requests not actioned",
    ),
    _tiktok(
        "Removal rate",
        Metric.REMOVAL_RATE,
        "removal_rate",
        "removal rate for government requests",
        unit=Unit.RATIO,
        note="TikTok's own published rate; its definition is TikTok's, not recomputed here.",
    ),
    # Worldwide only, for 2019–2021: the years before the CSV breaks requests
    # down by country. No Silver series — it is not any country's figure.
    Measure(
        "Total Government Requests", Metric.REQUESTS_RECEIVED, requester=RequesterType.GOVERNMENT
    ),
)

#: The components TikTok's totals are made of. Checked on every country and
#: period, because TikTok's file is the one place both sides are published.
TIKTOK_TOTALS: dict[str, tuple[str, ...]] = {
    "Total content received": (
        "Content actioned due to (local) law violations",
        "Content actioned due to community guidelines violations",
        "Content not actioned",
    ),
    "Total accounts received": (
        "Accounts actioned due to (local) law violations",
        "Accounts actioned due to community guidelines violations",
        "Accounts not actioned",
    ),
}

# -- Komdigi ---------------------------------------------------------------

#: Platform names as Komdigi's tables write them, upper-cased. Twitter and X
#: are one platform renamed in 2023; the label Komdigi used stays on the row.
KOMDIGI_PLATFORMS: dict[str, str] = {
    "TWITTER": "x",
    "X": "x",
    "META": "meta",
    "GOOGLE": "google_all",
    "TIKTOK": "tiktok",
    "FILE SHARING": "file_sharing",
}

KOMDIGI_SOCIAL_MEASURE = Measure(
    "Jumlah",
    Metric.CONTENT_HANDLED,
    scope=Scope.UNSPECIFIED,
    requester=RequesterType.GOVERNMENT,
    confidence=Confidence.MEDIUM,
    note=(
        "Komdigi does not publish a definition of a handled item, nor whether handling "
        "means a request to the platform, a block, or both."
    ),
)

KOMDIGI_WEBSITE_MEASURE = Measure(
    "Jumlah Pemblokiran",
    Metric.URLS_BLOCKED,
    scope=Scope.UNSPECIFIED,
    requester=RequesterType.GOVERNMENT,
    confidence=Confidence.MEDIUM,
)


#: What a reader sees for a platform key. Named by the key rather than by
#: Komdigi's label, because one platform under two labels — Twitter in 2022, X
#: in 2024 — is one series and must have one name.
PLATFORM_NAMES: dict[str, str] = {
    "x": "X (Twitter)",
    "meta": "Meta",
    "google_all": "Google",
    "tiktok": "TikTok",
    "file_sharing": "file sharing services",
    "michat": "MiChat",
    "bigo_live": "Bigo Live",
    "mango_live": "Mango Live",
    "snack_video": "Snack Video",
    "app_store": "app stores",
    "hello_app": "Hello App",
}


def platform_name(platform: str, label: str) -> str:
    return PLATFORM_NAMES.get(platform) or label.strip().title()


def platform_slug(label: str) -> str:
    """A Komdigi platform label as a platform key."""
    upper = label.strip().upper()
    if upper in KOMDIGI_PLATFORMS:
        return KOMDIGI_PLATFORMS[upper]
    return re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")


def komdigi_indicator(platform: str) -> str:
    return f"komdigi_content_handled_{platform}"


# -- countries -------------------------------------------------------------

#: The comparison the spec asks for first.
COMPARATORS: tuple[str, ...] = ("MY", "TH", "VN", "PH", "SG")
ASEAN: tuple[str, ...] = ("ID", "MY", "TH", "VN", "PH", "SG", "BN", "KH", "LA", "MM")

#: Names TikTok writes that the World Bank's country list spells otherwise.
#: Only names seen in TikTok's file are listed; one that is not stays
#: unresolved, with the raw name kept.
_COUNTRY_ALIASES: dict[str, str] = {
    "South Korea": "KR",
    "Korea": "KR",
    "Russia": "RU",
    "Vietnam": "VN",
    "Turkey": "TR",
    "Türkiye": "TR",
    "Taiwan": "TW",
    "Hong Kong": "HK",
    "Egypt": "EG",
    "Iran": "IR",
    "Laos": "LA",
    "Venezuela": "VE",
    "Slovakia": "SK",
    "Kyrgyzstan": "KG",
    "Czech Republic": "CZ",
    "Czechia": "CZ",
    "Brunei": "BN",
    "Macau": "MO",
    "Macao": "MO",
    "Palestine": "PS",
    "Syria": "SY",
    "Yemen": "YE",
    "Gambia": "GM",
    "Bahamas": "BS",
    "Micronesia": "FM",
    "Kosovo": "XK",
    "UAE": "AE",
    "United Arab Emirates": "AE",
    "UK": "GB",
    "United Kingdom": "GB",
    "USA": "US",
    "United States": "US",
    "Ivory Coast": "CI",
    "Côte d'Ivoire": "CI",
    "Cote d'Ivoire": "CI",
    "Democratic Republic of the Congo": "CD",
    "Democratic Republic of Congo": "CD",
    "Cote d’Ivoire": "CI",
    "Macedonia": "MK",
    "Somalia": "SO",
    "Mayotte": "YT",
    "Republic of the Congo": "CG",
    "North Macedonia": "MK",
    "Saint Lucia": "LC",
}


@cache
def _country_names() -> dict[str, str]:
    from ..normalize.reference import load_countries

    names = {c.name.casefold(): c.iso_code for c in load_countries() if c.iso_code}
    names.update({k.casefold(): v for k, v in _COUNTRY_ALIASES.items()})
    return names


def country_code(name: str) -> str | None:
    """ISO 3166-1 alpha-2 for a country name, or None where it is not known."""
    cleaned = name.strip()
    if len(cleaned) == 2 and cleaned.isalpha() and cleaned.isupper():
        return cleaned
    return _country_names().get(cleaned.casefold())


# -- reasons ---------------------------------------------------------------

#: Komdigi's content categories, translated. A translation and not a mapping
#: onto anyone else's taxonomy: "SARA" has no English equivalent, and filing it
#: under Google's "Hate speech" would assert an equivalence nobody has checked.
#: The confidence is the translation's.
KOMDIGI_REASONS: dict[str, tuple[str, Confidence]] = {
    "pornografi": ("pornography", Confidence.HIGH),
    "perjudian": ("gambling", Confidence.HIGH),
    "penipuan": ("fraud", Confidence.HIGH),
    "hak kekayaan intelektual (hki)": ("intellectual_property", Confidence.HIGH),
    "konten negatif rekomendasi instansi": ("agency_recommended_negative_content", Confidence.HIGH),
    "terorisme/radikalisme": ("terrorism_radicalism", Confidence.HIGH),
    "pelanggaran keamanan informasi": ("information_security_violation", Confidence.HIGH),
    "suku, agama, ras, antargolongan (sara)": (
        "sara_ethnic_religious_racial_intergroup",
        Confidence.HIGH,
    ),
    "perdagangan produk dengan aturan": ("regulated_product_trade", Confidence.MEDIUM),
    "pelanggaran nilai sosial dan budaya": ("social_cultural_values_violation", Confidence.HIGH),
    "berita bohong/hoaks": ("hoax_false_news", Confidence.HIGH),
    "konten meresahkan masyarakat": ("publicly_disturbing_content", Confidence.MEDIUM),
    "separatisme/organisasi terlarang": ("separatism_banned_organisation", Confidence.HIGH),
    "fitnah": ("defamation", Confidence.HIGH),
    "kekerasan pada anak": ("child_abuse", Confidence.HIGH),
}


def reason_category(provider: str, raw: str) -> tuple[str | None, Confidence | None]:
    """A raw reason label as this provider's normalized category.

    Google's labels differ between releases only in capitals — "Privacy and
    security" and "Privacy and Security" — so folding case is the whole
    normalization and it is certain. Komdigi's are translated. Anything else is
    left unnormalized rather than guessed at.
    """
    text = raw.strip()
    if not text:
        return None, None
    if provider == Provider.GOOGLE:
        return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_"), Confidence.HIGH
    if provider == Provider.KOMDIGI:
        found = KOMDIGI_REASONS.get(text.casefold())
        return found if found else (None, None)
    return None, None
