-- Which instrument a regulation is: UU, PP, Perda, Perbup, ...
--
-- The parsed document carries a guess, read out of the PDF's text, and the
-- guess is often the first instrument the text names rather than the one the
-- text is. A Perbup's preamble cites the Undang-Undang it implements, and
-- 323 regional regulations came out labelled "UU" that way — so filtering the
-- portal on UU returned Peraturan Bupati and Peraturan Walikota.
--
-- Two sources that are surer than the guess, in order:
--
--   1. The catalogue title. Where BPK writes it out — "Peraturan Bupati
--      (Perbup) Kabupaten Kaur Nomor 20 ..." — the opening words are the
--      instrument, and nothing else in the record is as reliable. Only the
--      opening words: a parenthesis later in a title is as likely to be the
--      subject's acronym (RKPD, HET, UPTD) as the instrument's.
--   2. The track. The regional corpora (perda, pkd) hold regional instruments
--      only, so a central one there (UU, PP, Permen ...) is the parser reading
--      a citation. It is replaced by what the track and the region make it:
--      a Perda in the perda track; in pkd, Perbup, Perwali or Pergub by
--      whether the region is a kabupaten, a kota or a provinsi.
--
-- Loaded by scripts/import-regulations.sh and by
-- scripts/fix-regulation-instruments.sh, so a re-import and a repair agree.

CREATE OR REPLACE MACRO title_instrument(t) AS (
  WITH x AS (SELECT regexp_replace(lower(trim(t)), '\s+', ' ', 'g') AS s)
  SELECT CASE
    WHEN s LIKE 'peraturan pemerintah pengganti undang%' THEN 'Perpu'
    WHEN s LIKE 'undang-undang darurat%' OR s LIKE 'undang undang darurat%' THEN 'UU Darurat'
    WHEN s LIKE 'undang-undang%' OR s LIKE 'undang undang%' THEN 'UU'
    WHEN s LIKE 'peraturan pemerintah%' THEN 'PP'
    WHEN s LIKE 'peraturan presiden%' THEN 'Perpres'
    WHEN s LIKE 'keputusan presiden%' THEN 'Keppres'
    WHEN s LIKE 'instruksi presiden%' THEN 'Inpres'
    WHEN s LIKE 'peraturan menteri dalam negeri%' THEN 'Permendagri'
    WHEN s LIKE 'keputusan menteri dalam negeri%' THEN 'Kepmendagri'
    WHEN s LIKE 'peraturan menteri%' THEN 'Permen'
    WHEN s LIKE 'keputusan menteri%' THEN 'Kepmen'
    WHEN s LIKE 'instruksi menteri%' THEN 'Inmen'
    WHEN s LIKE 'peraturan daerah%' THEN 'Perda'
    WHEN s LIKE 'qanun%' THEN 'Qanun'
    WHEN s LIKE 'peraturan gubernur%' THEN 'Pergub'
    WHEN s LIKE 'keputusan gubernur%' THEN 'Kepgub'
    WHEN s LIKE 'peraturan bupati%' THEN 'Perbup'
    WHEN s LIKE 'keputusan bupati%' THEN 'Kepbup'
    WHEN s LIKE 'peraturan wali kota%' OR s LIKE 'peraturan walikota%' THEN 'Perwali'
    WHEN s LIKE 'keputusan wali kota%' OR s LIKE 'keputusan walikota%' THEN 'Kepwali'
    WHEN s LIKE 'peraturan badan%' THEN 'Perbadan'
    WHEN s LIKE 'peraturan kepala%' THEN 'Perka'
    -- The regulations of state institutions — Peraturan Lembaga, and the
    -- commissions, courts, councils, authorities and the central bank that
    -- title theirs by name (Peraturan Komisi Pemilihan Umum, Peraturan
    -- Mahkamah Konstitusi, Peraturan Bank Indonesia) — are one instrument.
    WHEN regexp_matches(s, '^peraturan (lembaga|komisi|mahkamah|dewan|otoritas|otorita|bank|ombudsman|majelis)\b')
      THEN 'Perlembaga'
    WHEN s LIKE 'surat edaran%' THEN 'SE'
  END FROM x);

-- Instruments only the central government issues.
CREATE OR REPLACE MACRO central_instrument(i) AS i IN (
  'UU', 'UU Darurat', 'Perpu', 'PP', 'Perpres', 'Keppres', 'Inpres',
  'Permen', 'Kepmen', 'Inmen', 'Permendagri', 'Kepmendagri',
  'Perbadan', 'Perka', 'Perlembaga', 'SE'
);

-- The instrument a regional record must be when its parsed one cannot be.
CREATE OR REPLACE MACRO regional_instrument(track, region_type) AS CASE
  WHEN track = 'perda' THEN 'Perda'
  WHEN track = 'pkd' AND lower(region_type) = 'kabupaten' THEN 'Perbup'
  WHEN track = 'pkd' AND lower(region_type) = 'kota' THEN 'Perwali'
  WHEN track = 'pkd' AND lower(region_type) = 'provinsi' THEN 'Pergub'
END;

CREATE OR REPLACE MACRO regulation_instrument(title, parsed, track, region_type) AS
  coalesce(
    title_instrument(title),
    CASE
      WHEN track IN ('perda', 'pkd') AND central_instrument(parsed)
        THEN regional_instrument(track, region_type)
      ELSE parsed
    END
  );
