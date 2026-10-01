package httpapi

import (
	"context"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/csis/terusan/services/api/internal/storage"
)

// The places a question names, and the years each listed series has figures
// for at them.
//
// A series' span is its widest: the APBD of one regional government runs
// from 2011 to 2026 because some governments report every month, while
// Kabupaten Bandung has December 2011 and 2026 and nothing between. Told the
// span, the answer said Bandung's personnel spending for 2015 to 2023 was
// held. Where the question names a place, the years are read at that place.

// askedPlace is a province or regency the question names.
type askedPlace struct {
	ID, Name string
}

// placeYears is the years a series has figures for at one place.
type placeYears struct {
	Name  string
	Years []int
}

const (
	// Past these, the question is about the country rather than its places.
	answerMaxPlaces = 3
	placesTimeout   = 5 * time.Second
)

// levelWords are what make a one-word regency name a place rather than a
// word: "Malang", "Batang" and "Bone" are words too, "Kabupaten Malang" is
// not.
var levelWords = map[string]bool{
	"kabupaten": true, "kab": true, "kota": true, "regency": true, "city": true, "district": true,
}

// placeAliases are the names a reader uses for a province that its own name
// is longer than.
var placeAliases = map[string][]string{
	"dki jakarta":   {"jakarta"},
	"di yogyakarta": {"yogyakarta", "jogja", "jogjakarta", "diy"},
}

// placesAsked is the provinces and regencies the question names, longest
// name first, so "Kota Bandung" is the city and not also the regency.
func (s *Server) placesAsked(ctx context.Context, question string) []askedPlace {
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "geography") {
		return nil
	}
	geography, err := s.source(storage.LayerSilver, "geography")
	if err != nil {
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, placesTimeout)
	defer cancel()
	rows, err := s.warehouse.DB().QueryContext(ctx,
		"SELECT geo_id, name, geo_type FROM "+geography+" WHERE geo_type IN ('province', 'regency')")
	if err != nil {
		s.log.Warn("assistant.places_failed", "error", err)
		return nil
	}
	defer rows.Close()
	var known []placeName
	for rows.Next() {
		var p placeName
		if err := rows.Scan(&p.id, &p.name, &p.kind); err != nil {
			return nil
		}
		known = append(known, p)
	}
	return matchPlaces(question, known)
}

type placeName struct{ id, name, kind string }

// matchPlaces finds the places named in a question among those known.
func matchPlaces(question string, known []placeName) []askedPlace {
	words := questionWords(question)
	text := " " + strings.Join(words, " ") + " "
	leveled := false
	for _, word := range words {
		leveled = leveled || levelWords[word]
	}
	type candidate struct {
		place  askedPlace
		phrase string
		rank   int // provinces before regencies of the same name
	}
	var candidates []candidate
	for _, p := range known {
		name := strings.Join(questionWords(p.name), " ")
		phrases := append([]string{name}, placeAliases[name]...)
		if rest, ok := strings.CutPrefix(name, "kota administrasi "); ok {
			phrases = append(phrases, rest)
		}
		for _, phrase := range phrases {
			if phrase == "" || !strings.Contains(text, " "+phrase+" ") {
				continue
			}
			if p.kind == "regency" && !strings.Contains(phrase, " ") && !leveled {
				continue
			}
			rank := 0
			if p.kind == "regency" {
				rank = 1
			}
			candidates = append(candidates, candidate{askedPlace{p.id, p.name}, phrase, rank})
		}
	}
	sort.SliceStable(candidates, func(a, b int) bool {
		if len(candidates[a].phrase) != len(candidates[b].phrase) {
			return len(candidates[a].phrase) > len(candidates[b].phrase)
		}
		return candidates[a].rank < candidates[b].rank
	})
	// Longest first, each claiming its words: what a longer name took is
	// not another place.
	var out []askedPlace
	claimed := text
	seen := map[string]bool{}
	for _, c := range candidates {
		if seen[c.place.ID] || !strings.Contains(claimed, " "+c.phrase+" ") {
			continue
		}
		seen[c.place.ID] = true
		claimed = strings.Replace(claimed, " "+c.phrase+" ", " # ", 1)
		out = append(out, c.place)
		if len(out) == answerMaxPlaces {
			break
		}
	}
	return out
}

// yearsAtPlaces reads, for each listed series measured by place, the years
// it has figures for at each place asked, and keeps them on the items.
func (s *Server) yearsAtPlaces(ctx context.Context, items map[string]answerItem, places []askedPlace) {
	if len(places) == 0 {
		return
	}
	var ids []string
	for _, item := range items {
		if item.Kind == "indicator" && item.Places > 1 {
			ids = append(ids, item.ID)
		}
	}
	if len(ids) == 0 {
		return
	}
	sort.Strings(ids)
	observations, err := s.warehouse.SourceIn(storage.LayerSilver, "observations", "indicator_id", ids)
	if err != nil {
		return
	}
	idClause, idArgs := inClause("indicator_id", ids)
	geoIDs := make([]string, len(places))
	names := map[string]string{}
	for n, p := range places {
		geoIDs[n] = p.ID
		names[p.ID] = p.Name
	}
	geoClause, geoArgs := inClause("geo_id", geoIDs)
	ctx, cancel := context.WithTimeout(ctx, placesTimeout)
	defer cancel()
	rows, err := s.warehouse.DB().QueryContext(ctx, fmt.Sprintf(`
		SELECT indicator_id, geo_id, list_sort(list(DISTINCT substr(period, 1, 4)))
		FROM %s
		WHERE %s AND %s AND regexp_matches(period, '^[0-9]{4}')
		GROUP BY 1, 2`, observations, idClause, geoClause), append(idArgs, geoArgs...)...)
	if err != nil {
		s.log.Warn("assistant.place_years_failed", "error", err)
		return
	}
	defer rows.Close()
	found := map[string]map[string][]int{}
	for rows.Next() {
		var id, geo string
		var years any
		if err := rows.Scan(&id, &geo, &years); err != nil {
			return
		}
		if found[id] == nil {
			found[id] = map[string][]int{}
		}
		for _, year := range asStrings(years) {
			if n, err := strconv.Atoi(year); err == nil {
				found[id][geo] = append(found[id][geo], n)
			}
		}
	}
	if rows.Err() != nil {
		return
	}
	for ref, item := range items {
		if item.Kind != "indicator" || item.Places <= 1 {
			continue
		}
		// A place the series has nothing for is said too: no figures for
		// Bandung is the answer to a question about Bandung.
		for _, p := range places {
			item.AtPlaces = append(item.AtPlaces, placeYears{Name: names[p.ID], Years: found[item.ID][p.ID]})
		}
		items[ref] = item
	}
}

// placeCoverage is whether a place's years reach any, and all, of the years
// asked.
func placeCoverage(years []int, from, to string) (any, all bool) {
	first, errFrom := strconv.Atoi(from)
	last, errTo := strconv.Atoi(to)
	if errFrom != nil || errTo != nil {
		return len(years) > 0, len(years) > 0
	}
	have := map[int]bool{}
	for _, year := range years {
		have[year] = true
	}
	all = true
	for year := first; year <= last; year++ {
		if have[year] {
			any = true
		} else {
			all = false
		}
	}
	return any, all
}

// yearRuns writes years as runs: 2011, 2015–2020, 2026.
func yearRuns(years []int) string {
	var runs []string
	for n := 0; n < len(years); {
		end := n
		for end+1 < len(years) && years[end+1] == years[end]+1 {
			end++
		}
		if end == n {
			runs = append(runs, strconv.Itoa(years[n]))
		} else {
			runs = append(runs, fmt.Sprintf("%d–%d", years[n], years[end]))
		}
		n = end + 1
	}
	return strings.Join(runs, ", ")
}

// placeFacts is what an item holds at each place asked.
func placeFacts(item answerItem, from, to string, id bool) string {
	var parts []string
	for _, p := range item.AtPlaces {
		switch any, all := placeCoverage(p.Years, from, to); {
		case len(p.Years) == 0 && id:
			parts = append(parts, "tidak ada data untuk "+p.Name)
		case len(p.Years) == 0:
			parts = append(parts, "no figures for "+p.Name)
		case from == "" && id:
			parts = append(parts, "untuk "+p.Name+": "+yearRuns(p.Years))
		case from == "":
			parts = append(parts, "for "+p.Name+": "+yearRuns(p.Years))
		case all && id:
			parts = append(parts, "untuk "+p.Name+": mencakup semua tahun yang diminta")
		case all:
			parts = append(parts, "for "+p.Name+": covers all the years asked")
		case !any && id:
			parts = append(parts, "untuk "+p.Name+" hanya "+yearRuns(p.Years)+", tidak mencakup tahun yang diminta")
		case !any:
			parts = append(parts, "for "+p.Name+" only "+yearRuns(p.Years)+", none of the years asked")
		case id:
			parts = append(parts, "untuk "+p.Name+" hanya "+yearRuns(p.Years))
		default:
			parts = append(parts, "for "+p.Name+" only "+yearRuns(p.Years))
		}
	}
	return strings.Join(parts, "; ")
}
