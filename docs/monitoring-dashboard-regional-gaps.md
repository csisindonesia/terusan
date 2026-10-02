# Monitoring dashboard, regional catalogue: what the lake has

This checks the `Catalogue_Regional` tab of `20260722_Monitoring dashboard_Index_v3.xlsx` (82 indicators) against the local lake as of 2026-09-30. Every one of the seven sources the catalogue names is already registered and collecting.

The tables that were only in Bronze have since been taken into Silver: OJK fintech lending, the rest of the BI surveys, and the SPI's rural bank tables (parser version 26). Every series checked here is filed in the collection **Fiscal Monitoring** (`col-339afe0e2619`) on terusan.csis.or.id. The series built on 2026-09-30 reach the site only after the NAS is deployed and re-extracted.

## Summary

| Status | Count | Meaning |
|---|---:|---|
| ✅ In Silver | 59 | Queryable now, by province or city |
| 🟡 Derivable | 6 | Not stored as its own series, but a sum or ratio of Silver series gives it |
| 🟡 Short history | 15 | In Silver, but only 2026-09-23 to 2026-09-25 (SP2KP by regency) |
| ❌ Not collected | 2 | The source only gives us these at national level |

What is still missing:

1. **SP2KP regency prices (15 indicators).** Backfill daily history. Silver starts on 2026-09-23.
2. **Fintech history before December 2024.** The source fetches only the latest workbook, so older editions need an archive walk.
3. **Anything after June 2025 for rural bank offices and credit, and for commercial bank branch offices.** The SPI archive ends there. No SJK cube this pipeline reads carries rural bank offices or credit. SJK's `BU_InformasiKantor` is in Bronze but not in Silver, and its sample rows have no province (`DATI1` is empty).
4. **Fresher BI surveys.** Both end in late 2025 (consumer 2025-11, retail 2025-10). Re-run the sources with `make bi-surveys-silver`.

## Fiscal conditions (DJPK, `djpk-apbd`)

All 13 are in Silver. Each line item comes in four series: budgeted and realised year to date, for one regional government (466 governments) and summed by province (39 geographies). Monthly, 2011-12 to 2026-09. Values are in IDR, not IDR billion, so the dashboard divides by 10⁹.

| # | Indicator | Status | Silver (realised YTD: per government / province sum) |
|---|---|---|---|
| 4 | Fiscal balance | ✅ | `z4c49hxj` / `ugtw28z8` |
| 5 | Regional Revenue | ✅ | `1qkkdixl` / `4gidkmwf` |
| 6 | Own-Source Revenue (PAD) | ✅ | `od0oiggg` / `hqhrgkyq` |
| 7 | Transfers (TKDD) | ✅ | `3o78oqvj` / `8a9zkw07` |
| 8 | Other Revenue | ✅ | `i2rhfbsm` / `irvry7my` |
| 9 | Regional Expenditure | ✅ | `b15w6ol4` / `4jyxu05z` |
| 10 | Personnel Expenditure | ✅ | `ze3t0z5b` / `cx4nzgzy` |
| 11 | Goods and Services | ✅ | `b645oesr` / `b1rlvi7r` |
| 12 | Capital Expenditure | ✅ | `gkhwjb99` / `rqqit49z` |
| 13 | Other Expenditure | ✅ | `0ecp0359` / `e8zc18ku` |
| 14 | Regional Financing (net) | ✅ | `ewglelfl` / `rc60tfa2` |
| 15 | Financing Receipts | ✅ | `5j35q3sg` / `1wea0q93` |
| 16 | Financing Disbursements | ✅ | `dcgjq8p2` / `3y49h1hj` ("financing expenditures") |

## Financial market (OJK)

Commercial bank series come from two sources that together cover 2015 to 2026. SPI (`ojk-banking-spi`) runs 2015-06 to 2025-06 and ends there because OJK stopped the archive. SJK (`ojk-banking-sjk`) runs 2023-01 to 2026-07. Both are by province (32 to 39 geographies).

| # | Indicator | Status | Where |
|---|---|---|---|
| 17 | Bank: demand deposits | ✅ | SPI `h5lua8b9` / SJK `834crrp1`, category `Giro` |
| 18 | Bank: savings deposits | ✅ | same, `Tabungan` |
| 19 | Bank: time deposits | ✅ | same, `Deposito` |
| 20 | Bank: third party funds (total) | 🟡 | Sum of 17 to 19, or of the currency series SPI `jednem2y` / SJK `l877buhf` |
| 21 | Bank: DPK by location of bank (1.34.a) | 🟡 | Same total as 20. Sheet `DPK per Lok_1.34.a.` is in Bronze but not extracted |
| 22 | Bank: branch offices | ✅ to 2025-06 | SPI `dtn104jc`. No provincial source after that (see work item 5) |
| 23 | Rural bank: savings deposits | ✅ | SJK `es0ay906`, `Tabungan`, 2019-12 to 2026-07 |
| 24 | Rural bank: time deposits | ✅ | SJK `es0ay906`, `Deposito` |
| 25 | Rural bank: total DPK | 🟡 | Sum of 23 and 24 |
| 26 | Rural bank: share of total DPK (%) | 🟡 | Province total ÷ national total from `es0ay906` |
| 27 | Rural bank: number of BPR and offices (2.14) | ✅ to 2025-06 | SPI `gx35kehi`. Categories are head offices (the number of rural banks), branch offices and cash offices. 108 of 111 months: the 2016-06, 2024-08 and 2025-01 editions reprinted another month's table |
| 28 | Rural bank: credit by location (3.18.a) | ✅ to 2025-06 | SPI `twnbbuji`, IDR billion, 2015-06 to 2025-06 |
| 29 | Bank credit: working capital | ✅ | SPI `4xfcohyy` / SJK `lztc9qtj`, `Modal Kerja` |
| 30 | Bank credit: investment | ✅ | same, `Investasi` |
| 31 | Bank credit: consumption | ✅ | same, `Konsumsi` |
| 32 | Bank credit: export orientation | ✅ | SPI `03lz1wnt` / SJK `y7n3newv`, `Ekspor` |
| 33 | Bank credit: import orientation | ✅ | same, `Impor` |
| 34 | Bank credit: other orientation | ✅ | same, `Lainnya` |
| 35 | Bank credit by location (total) | 🟡 | Sum of 29 to 31, or of SJK currency series `0wiubjlx` |
| 36 | Bank NPL by location | 🟡 | Sum of SPI `z0lpn37f` (NPL by purpose), or of SJK `fdpuhv5c` (grades Kurang Lancar, Diragukan, Macet) |
| 37 | Bank: SME credit and MKM credit | ✅ SME only | SPI `dvilfazc` (total), SJK `046g3h8b` (micro, small and medium). The MKM section of 3.22.a is not in Silver |
| 38 | Fintech: lender accounts (Tabel 5) | ✅ | `x51l6uwa` |
| 39 | Fintech: lender funds (Tabel 5) | ✅ | `cgxugolx`, IDR billion |
| 40 | Fintech: loan recipients (Tabel 6) | ✅ | `0zs4d9jv` |
| 41 | Fintech: loan disbursements (Tabel 6) | ✅ | `j5j14nh5`, IDR billion |
| 42 | Fintech: active borrower accounts (Tabel 9) | ✅ | `0y7218y4` |
| 43 | Fintech: outstanding loans (Tabel 9) | ✅ | `ufjjqmnv`, IDR billion |
| 44 | Fintech: TWP 90 (Tabel 9) | ✅ | `jg178d6a`, stored as a fraction (0.043), not a percentage |
| 45 | Fintech: accumulated lender accounts (Tabel 13) | ✅ | `8dtf3ywy` |
| 46 | Fintech: accumulated borrower accounts (Tabel 14) | ✅ | `424ds2ng` |
| 47 | Fintech: accumulated lender transactions (Tabel 15) | ✅ | `a3udsi9e` |
| 48 | Fintech: accumulated borrower transactions (Tabel 16) | ✅ | `jhl92rxq` |
| 49 | Fintech: accumulated funds provided (Tabel 17) | ✅ | `edl3d9ir`, IDR billion |
| 50 | Fintech: accumulated loans disbursed (Tabel 18) | ✅ | `79cq87yz`, IDR billion |

The fintech series (`ojk-fintech-p2p`, read by `extract/ojk_fintech.py`) cover all 38 provinces plus the national total, 2024-12 to 2025-12. That is the 13 months in the one workbook in RAW, the December 2025 edition. The source fetches only the latest edition, so older history needs an archive backfill.

## Real sector

### PIHPS food prices (`bi-pihps-food-prices`)

All 10 are in Silver (rows 51 to 60: rice, chicken meat, beef, chicken eggs, shallots, garlic, red chili, bird's eye chili, cooking oil, granulated sugar). Traditional market prices are in `6yugbox8`. The same commodities are also available at modern market (`3n9kq80k`), wholesale (`chutd2mb`) and producer (`olb7xksq`) level. Daily, 2017-03 to 2026-09-25, across 35 geographies. Each commodity also has its quality grades (for example `Beras Kualitas Medium I`).

### SP2KP food prices (`kemendag-sp2kp-prices`, by regency)

| # | Indicator | Status |
|---|---|---|
| 62 | Rice, medium | 🟡 short history |
| 63 | Rice, premium | 🟡 short history |
| 64 | Rice, SPHP Bulog | ❌ national only (`kemendag-sp2kp-national` `qmttie5b`, from 2024-02) |
| 65 | Granulated sugar, bulk | 🟡 short history |
| 66 | Palm cooking oil, premium packaged | 🟡 short history |
| 67 | Palm cooking oil, bulk | 🟡 short history |
| 68 | Minyakita | 🟡 short history |
| 69 | Beef, hind quarter | 🟡 short history |
| 70 | Broiler chicken meat | 🟡 short history |
| 71 | Broiler chicken eggs | 🟡 short history |
| 72 | Wheat flour | 🟡 short history |
| 73 | Imported soybeans | ❌ national only (`qmttie5b`) |
| 74 | Curly red chili | 🟡 short history |
| 75 | Red bird's eye chili | 🟡 short history |
| 76 | Large red chili | 🟡 short history |
| 77 | Shallots | 🟡 short history |
| 78 | Honan garlic | 🟡 short history |

"Short history" means the series is `uzhpwzsd` across 378 to 514 regencies, but only from 2026-09-23 to 2026-09-25. The government price ceilings (HET/HA) are in `z0ylw7rq`. The national weighted series `qmttie5b` has all 17 commodities daily from 2024-02, and can stand in for these rows until the regional history is backfilled.

### BI retail sales survey (`bi-retail-sales-survey`)

Ten cities plus the national total, 2012-01 to 2025-10. Every city except Jakarta has an unresolved `geo_id`, so the name is kept as raw text.

| # | Indicator | Status | Where |
|---|---|---|---|
| 80 | Retail sales index, per city (Tabel 5) | ✅ | `i6osqvn1` |
| 81 | Annual growth (Tabel 6) | ✅ | `90zd94mf` |
| 82 | Monthly growth (Tabel 7) | ✅ | `0dtodluz` |
| 83 | Quarterly growth (Tabel 8) | ✅ | `f80x6xx3`, 2012-Q1 to 2025-Q3 |

### BI consumer survey (`bi-consumer-survey`)

Monthly, 2012-01 to 2025-11.

| # | Indicator | Status | Where |
|---|---|---|---|
| 85 | Consumer confidence index, national | ✅ | `44ghabm5`. The national CECI (`vthxgugc`) and CEI (`1uyecx7u`) too |
| 86 | CCI per city | ✅ | `tho5tk1m`, 18 cities |
| 87 | CECI per city | ✅ | `psishxt6` |
| 88 | CEI per city | ✅ | `ke23sbjl` |

`scripts/normalize-bi-surveys.sh` (`make bi-surveys-silver`) declares all ten survey series. Figures BI marks as provisional (`Nov*`, `Q4*`) are dropped until the next release restates them.

## How this was checked

The indicators came from columns B to H of the tab. Coverage came from `silver_indicators` and `silver_observations` (series, date range, number of geographies and categories) and from `bronze_records` (datasets, the SPI sheet names, and the BI survey `indicator` values). No source was re-run, so "not in Silver" means "not in this machine's lake today". A table's presence in Bronze was checked only by its sheet name or indicator key, not by reading its figures.
