# Excel and Power BI (OData)

Every dataset in the warehouse is also an OData v4 feed. Excel and Power BI
connect to the feed and keep the connection, so a workbook refreshes from the
warehouse instead of holding a CSV that went stale the day it was downloaded.

The feed is read-only and serves the same Silver figures as the portal and the
JSON API. It lives at `/v1/odata` on the API's own address, for example
`https://terusan.csis.or.id/v1/odata`.

## Connecting from Excel

1. Create an API token on your profile page (**Profile → API tokens**). Copy it
   when it is shown, because it is shown only once. It starts with `trs_`.
2. In Excel, choose **Data → Get Data → From Other Sources → From OData Feed**.
3. Enter the feed's address: `https://terusan.csis.or.id/v1/odata` for every
   dataset, or one dataset's own address (a dataset page's actions menu has
   **Copy Excel (OData) feed URL**).
4. When Excel asks how to sign in, choose **Basic**. Put anything in the user
   name, paste the token as the password, and choose **Connect**.
5. The navigator lists every dataset by name. Pick one, then choose
   **Transform Data** to filter it first, or **Load** to take all of it.

Power BI Desktop is the same: **Get Data → OData feed**, then Basic with the
token as the password.

Excel remembers the credentials for the address. To change them, open
**Data → Get Data → Data Source Settings**, select the address and choose
**Edit Permissions**. When a token expires or is revoked, the refresh fails
with a sign-in error. Create a new token and enter it there.

## What the feed holds

The navigator shows one table per dataset, named from its slug: the dataset
`adb-key-indicators` is the table `adb_key_indicators`. The names stay the same
from one refresh to the next, which a workbook relies on.

`Datasets` is the catalogue. It has each dataset's title, publisher, licence,
coverage, row count and `FeedURL`, which is the address of that dataset's own
table.

Every dataset's table has the same columns:

| Column | Type | What it is |
| --- | --- | --- |
| `ObservationID` | text | The figure's identifier |
| `IndicatorID`, `IndicatorName` | text | Which series the figure belongs to |
| `Period` | text | The period as published: `2024`, `2024-03`, `2024-Q1`, `2024-03-15` |
| `PeriodStart`, `PeriodEnd` | date | The days the period covers |
| `Resolution` | text | `annual`, `monthly`, `daily`, … |
| `Value` | decimal | The figure. Empty when the figure is missing |
| `Unit` | text | |
| `Status` | text | `ok`, or why a figure is missing: `missing`, `suppressed`, `not_applicable`, … |
| `ValueUnambiguous` | true/false | False where the number format had to be assumed |
| `GeoID`, `GeoName`, `GeoType` | text | The place, where the series varies by place |
| `CommodityID`, `CommodityName` | text | The commodity, where it varies by commodity |
| `Category` | text | Any other breakdown (an age group, a sector) |
| `SourceID`, `SourceURL` | text | Where the figure came from |

A missing figure has an empty `Value` and a `Status` that says why. It is never
a zero, so do not replace the blanks with zeros before you average.

## Filter before you load

Most of the datasets are small, but some are not. The food prices hold close to
nine million rows and the BPS tables about six million, and one Excel sheet
holds just over one million.

Power Query sends most of the steps you take in its editor to the warehouse
(this is called query folding). Only the rows you keep are downloaded. The
warehouse answers these steps:

- filters on any column: equals, does not equal, greater or less than, is
  empty, begins with, ends with, contains, the text in a list, and the year,
  month or day of a date, combined with **and**, **or** and **not**
- **Choose Columns** / **Remove Columns**
- **Keep Top Rows** and **Remove Top Rows**
- **Count Rows**

Sorting is answered too, but sort after loading when the table is large. A
table sorted in the editor is fetched in that order, page by page, and every
page of a multi-million-row dataset then costs a full sort. In the feed's own
order (by period, then place), each page costs a fraction of a second.

A step the warehouse does not answer, such as arithmetic inside a filter or a
column calculated from two others, fails with an error that names it ("…is not
supported by this service"). Such a step is never silently skipped. Put the
step after the filter, or apply it after loading.

## How the feed works

Operators and anyone calling the feed directly can skip this section.

- **Protocol.** OData v4, JSON with minimal metadata. `GET /v1/odata` returns
  the service document, `GET /v1/odata/$metadata` the CSDL, and
  `GET /v1/odata/<set>` and `GET /v1/odata/<set>/$count` a table and its
  count. The query options answered are `$filter`, `$select`, `$orderby`,
  `$top`, `$skip`, `$count` and `$skiptoken`. The service refuses `$expand`,
  `$search`, `$apply` and `$compute` with a 501 Not Implemented, and any other
  `$` option with a 400 Bad Request. The code is in
  `services/api/internal/httpapi/odata.go`; `$filter` is compiled in
  `odata_filter.go`.
- **Not the §53 envelope.** Power Query reads OData's own JSON format and
  nothing else, so this is the one part of the API that does not answer
  `data` + `meta`.
- **Values.** `Value` is an `Edm.Decimal`, written as the exact digits Silver
  holds, without going through a float. A client that sends
  `IEEE754Compatible=true` in its `Accept` header gets decimals and 64-bit
  counts as strings, as the OData standard specifies.
- **Paging.** Pages hold 5,000 rows and end in `@odata.nextLink`. A client can
  ask for fewer with `Prefer: odata.maxpagesize=N`, but not more. The limit
  comes from DuckDB's planner, which keeps a top-N heap for an ORDER BY with a
  LIMIT of up to 5,000 rows and above that may sort the whole table. A page of
  the food prices takes 0.16s at 5,000 rows and 15s at 5,001. In the default
  order, the pages after the first are taken by keyset, so the last page costs
  what the first does.
- **Sign-in.** Access is governed by the same `AUTH_REQUIRED` setting as every
  other route. On top of the usual bearer token, the feed also accepts HTTP
  Basic with an API token as the password. This is the only scheme Excel's
  dialog offers that can carry a token. Basic is accepted on `/v1/odata` and
  nowhere else, and a request without credentials gets a
  `WWW-Authenticate: Basic` challenge so that Excel asks for them. Each request
  is recorded in the account's access log as an API access.
- **Addresses.** The links in a response (`@odata.context`,
  `@odata.nextLink`, `FeedURL`) are built from the request's host. The scheme
  is `https` behind the tunnel, because `AUTH_SECURE_COOKIES=true` there. Set
  `AUTH_TRUST_PROXY` to use the `X-Forwarded-*` headers instead.
- **Caching.** Responses are not cached. Each refresh is a walk through the
  pages, and no page is asked for twice in a row.

## Troubleshooting

| What Excel says | What it means |
| --- | --- |
| "We couldn't authenticate with the credentials provided" | The password is not a valid token: wrong, expired or revoked. Create a new one and update **Data Source Settings**. |
| "Access to the resource is forbidden" | The account is pending approval or disabled. |
| "…is not supported by this service" | A step in the editor cannot be sent to the warehouse. Move it after the filter, or apply it after loading. |
| "there is no entity set called …" | The dataset was renamed or removed. Pick it again from the navigator. |
| The refresh is slow | The table is large. Filter it in the editor before loading, and do not sort it there. |
