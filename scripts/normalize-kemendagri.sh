#!/usr/bin/env bash
#
# Kemendagri's provincial profile, Bronze into Silver.
#
# The interior ministry's `data-profil-daerah` endpoint answers with one row
# per province carrying sixty-odd fields, and about twenty of them are figures
# with a year attached: population, poverty, the human development index, the
# Gini ratio, inflation, unemployment, growth, budget realisation, life
# expectancy and schooling. Thirty-eight provinces each, from one request.
#
# Each figure carries its own period column — `jumlah_penduduk` is dated by
# `tahun_jumlah_penduduk` and the inflation figures by a month name beside the
# year — because the ministry refreshes them independently. That is why this is
# a loop over declared mappings rather than one invocation: there is no single
# period column, and pretending there is would date every figure by whichever
# one was picked.
#
# The number format is declared per figure and not guessed. The ministry mixes
# both conventions in one row: `pertumbuhan_ekonomi_yoy` is `5.31` and
# `indeks_pembangunan_manusia_metode_baru` is `74,99`. Read the second as
# English and it becomes 7,499.
#
# Geography resolves on `kode_provinsi`, the ministry's own dotted code, rather
# than on the province name — `Gorontalo` names both a province and a regency
# inside it, and the registry rightly refuses to guess between them. Kemendagri
# numbers the Papua provinces differently from BPS; see the note in
# reference/geography/indonesia-provinces.csv.
#
# Four provinces carry `Belum ada penilaian` — not yet assessed — for the
# governance score, and those land as unparseable rather than as zero. They are
# the Papua provinces created in 2022, which have not been through an
# assessment cycle yet, and a zero there would read as the worst score in the
# country.
#
# Idempotent: normalization rebuilds an indicator from scratch.

set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE="kemendagri-wilayah"
DATASET="region-profiles"

# indicator | value column | period column(s) | unit | number format
#
# Two period columns, comma-separated, means the figure carries a month name
# beside its year — `Agustus` and `2026` — and they are passed together as
# `--period-parts`, which reads them as one month.
MAPPINGS=(
  "provincial_population|jumlah_penduduk|bulan_jumlah_penduduk,tahun_jumlah_penduduk|people|en"
  "provincial_poverty_headcount|jumlah_penduduk_miskin|bulan_jumlah_penduduk_miskin,tahun_jumlah_penduduk_miskin|thousand people|en"
  "provincial_poverty_rate|persentase_jumlah_penduduk_miskin|tahun_persentase_jumlah_penduduk_miskin|percent|en"
  "provincial_hdi|indeks_pembangunan_manusia_metode_baru|tahun_indeks_pembangunan_manusia_metode_baru|index|id"
  "provincial_hdi_male|indeks_pembangunan_manusia_laki2|tahun_indeks_pembangunan_manusia_laki2|index|id"
  "provincial_hdi_female|indeks_pembangunan_manusia_perempuan|tahun_indeks_pembangunan_manusia_perempuan|index|id"
  "provincial_gini_ratio|gini_rasio|bulan_gini_rasio,tahun_gini_rasio|ratio|en"
  "provincial_inflation_mom|inflasi_mom|bulan_inflasi_mom,tahun_inflasi_mom|percent|en"
  "provincial_inflation_yoy|inflasi_yoy|bulan_inflasi_yoy,tahun_inflasi_yoy|percent|en"
  "provincial_unemployment_rate|tingkat_pengangguran_terbuka|bulan_tingkat_pengangguran_terbuka,tahun_tingkat_pengangguran_terbuka|percent|en"
  "provincial_economic_growth_yoy|pertumbuhan_ekonomi_yoy|bulan_pertumbuhan_ekonomi_yoy,tahun_pertumbuhan_ekonomi_yoy|percent|en"
  "provincial_revenue_realisation|realisasi_pendapatan|tahun_realisasi_pendapatan|IDR|en"
  "provincial_expenditure_realisation|realisasi_belanja|tahun_realisasi_belanja|IDR|en"
  "provincial_revenue_realisation_rate|persentase_realisasi_pendapatan|tahun_persentase_realisasi_pendapatan|percent|id"
  "provincial_expenditure_realisation_rate|persentase_realisasi_belanja|tahun_persentase_realisasi_belanja|percent|id"
  "provincial_life_expectancy_male|angka_harapan_hidup_laki2|tahun_angka_harapan_hidup_laki2|years|id"
  "provincial_life_expectancy_female|angka_harapan_hidup_perempuan|tahun_angka_harapan_hidup_perempuan|years|id"
  "provincial_expected_schooling_male|angka_harapan_lama_sekolah_laki2|tahun_angka_harapan_lama_sekolah_laki2|years|id"
  "provincial_expected_schooling_female|angka_harapan_lama_sekolah_perempuan|tahun_angka_harapan_lama_sekolah_perempuan|years|id"
  "provincial_mean_schooling_male|rerata_lama_sekolah_laki2|tahun_rerata_lama_sekolah_laki2|years|id"
  "provincial_mean_schooling_female|rerata_lama_sekolah_perempuan|tahun_rerata_lama_sekolah_perempuan|years|id"
  "provincial_minimum_service_standard|standar_pelayanan_minimal|tahun_standar_pelayanan_minimal|index|en"
  "provincial_innovation_index|indeks_inovasi_daerah|tahun_indeks_inovasi_daerah|index|id"
  "provincial_governance_score|skor_evaluasi_penyelenggaraan_pemda|tahun_skor_evaluasi_penyelenggaraan_pemda|index|en"
)

for mapping in "${MAPPINGS[@]}"; do
  IFS='|' read -r indicator value period unit format <<<"$mapping"

  if [[ "$period" == *,* ]]; then
    period_flag=(--period-parts "$period")
  else
    period_flag=(--period-column "$period")
  fi

  uv --project pipelines run terusan silver normalize "$indicator" \
    --dataset "$DATASET" \
    --source "$SOURCE" \
    "${period_flag[@]}" \
    --value-column "$value" \
    --geo-column kode_provinsi \
    --unit "$unit" \
    --number-format "$format" \
    "$@"
done
