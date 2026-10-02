package httpapi

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"math"
	"strings"

	"github.com/csis/terusan/services/api/internal/articles"
	"github.com/csis/terusan/services/api/internal/storage"
)

// The figure tokens in an article's prose, evaluated against the lake.
//
// Each token is one of the measures articles.Measures names, over one series,
// and every value in it is bound: the only SQL text chosen here is which of a
// few fixed statements to run. A token that cannot be answered — no rows in
// the period, a series that is not in the lake — comes back with a reason
// rather than a blank, and the portal prints the reason where the number would
// have been, because a missing figure must never read as zero.

// ArticleFigure is one token's answer.
type ArticleFigure struct {
	// What the prose shows in the token's place: "16,017", "+18.0%", "3rd of 34".
	Display string `json:"display,omitempty"`
	// The number behind it, as a string like every other figure this API
	// serves; absent for a period or a rank.
	Value *string `json:"value,omitempty"`
	// The period the figure is for, where the measure picked it.
	Period string `json:"period,omitempty"`
	// Why there is no figure, instead of one.
	Error string `json:"error,omitempty"`
}

var errNoFigure = errors.New("no figures for this in the warehouse")

// articleFigures evaluates every token in texts, keyed by the token as written.
func (s *Server) articleFigures(ctx context.Context, texts []string) map[string]ArticleFigure {
	out := map[string]ArticleFigure{}
	for _, text := range texts {
		parsed := articles.Parse(text)
		for raw, why := range parsed.Invalid {
			out[raw] = ArticleFigure{Error: why}
		}
		for _, token := range parsed.Tokens {
			if _, done := out[token.Raw]; done {
				continue
			}
			figure, err := s.evaluateToken(ctx, token)
			if err != nil {
				figure = ArticleFigure{Error: err.Error()}
			}
			out[token.Raw] = figure
		}
	}
	return out
}

func (s *Server) evaluateToken(ctx context.Context, t articles.Token) (ArticleFigure, error) {
	if s.warehouse == nil || !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		return ArticleFigure{}, errNoFigure
	}
	from, err := s.observationSource(ctx, []string{t.Indicator})
	if err != nil {
		return ArticleFigure{}, err
	}

	// The series, narrowed to one place and one commodity where the token
	// names them. Missing figures are not values, so they are not averaged.
	where := []string{"o.indicator_id = ?", "o.value IS NOT NULL"}
	args := []any{t.Indicator}
	if t.Geo != "" {
		where = append(where, "o.geo_id = ?")
		args = append(args, t.Geo)
	}
	if t.Commodity != "" {
		where = append(where, "o.commodity_id = ?")
		args = append(args, t.Commodity)
	}
	series := func(extra ...string) (string, []any) {
		clauses := append(append([]string{}, where...), extra...)
		return from + " WHERE " + strings.Join(clauses, " AND "), append([]any{}, args...)
	}

	// The mean over one period, for value and change.
	meanOver := func(period string) (float64, error) {
		start, end, err := articles.Span(period)
		if err != nil {
			return 0, err
		}
		body, a := series("o.period_start >= ?", "o.period_start < ?")
		var v sql.NullFloat64
		if err := s.warehouse.DB().QueryRowContext(ctx,
			"SELECT avg(CAST(o.value AS DOUBLE)) FROM "+body, append(a, start, end)...,
		).Scan(&v); err != nil {
			return 0, err
		}
		if !v.Valid {
			return 0, fmt.Errorf("no figures for %s in %s", t.Indicator, period)
		}
		return v.Float64, nil
	}

	switch t.Measure {
	case "value":
		v, err := meanOver(t.Period)
		if err != nil {
			return ArticleFigure{}, err
		}
		return number(v, t.Digits, t.Period), nil

	case "change":
		before, err := meanOver(t.From)
		if err != nil {
			return ArticleFigure{}, err
		}
		after, err := meanOver(t.To)
		if err != nil {
			return ArticleFigure{}, err
		}
		if before == 0 {
			return ArticleFigure{}, fmt.Errorf("no change from zero in %s", t.From)
		}
		change := (after/before - 1) * 100
		value := fmt.Sprintf("%.6f", change)
		return ArticleFigure{Display: articles.Percent(change, t.Digits), Value: &value}, nil

	case "latest", "latest-period":
		body, a := series()
		var v sql.NullFloat64
		var at sql.NullString
		// The mean of the last period's figures, where there are several: a
		// national series without a place named is one row per province.
		if err := s.warehouse.DB().QueryRowContext(ctx,
			"SELECT avg(CAST(o.value AS DOUBLE)), any_value(o.period) FROM "+body+
				" AND o.period_start = (SELECT max(o.period_start) FROM "+body+")",
			append(a, a...)...,
		).Scan(&v, &at); err != nil {
			return ArticleFigure{}, err
		}
		if !v.Valid {
			return ArticleFigure{}, errNoFigure
		}
		if t.Measure == "latest-period" {
			return ArticleFigure{Display: at.String, Period: at.String}, nil
		}
		return number(v.Float64, t.Digits, at.String), nil

	case "mean", "median", "min", "max", "rank":
		return s.acrossPlaces(ctx, t, from)
	}
	return ArticleFigure{}, fmt.Errorf("%q is not a measure", t.Measure)
}

// acrossPlaces answers the measures that compare places: each place's mean
// over the period, then the mean, median, lowest or highest of those, or one
// place's rank among them.
//
// The national row is left out unless the token names a kind of place, since
// a country among its own provinces is not one of them.
func (s *Server) acrossPlaces(ctx context.Context, t articles.Token, from string) (ArticleFigure, error) {
	start, end, err := articles.Span(t.Period)
	if err != nil {
		return ArticleFigure{}, err
	}
	clauses := []string{"o.indicator_id = ?", "o.value IS NOT NULL", "o.geo_id IS NOT NULL",
		"o.period_start >= ?", "o.period_start < ?"}
	a := []any{t.Indicator, start, end}
	if t.Commodity != "" {
		clauses = append(clauses, "o.commodity_id = ?")
		a = append(a, t.Commodity)
	}
	if t.GeoType != "" {
		clauses = append(clauses, "g.geo_type = ?")
		a = append(a, t.GeoType)
	} else {
		clauses = append(clauses, "o.geo_id <> 'IDN'")
	}
	places := "SELECT o.geo_id AS geo_id, avg(CAST(o.value AS DOUBLE)) AS v FROM " + from +
		" WHERE " + strings.Join(clauses, " AND ") + " GROUP BY o.geo_id"

	if t.Measure == "rank" {
		// Chosen from two fixed strings, never from the request.
		order := "DESC"
		if !t.Descending {
			order = "ASC"
		}
		var rank, of sql.NullInt64
		if err := s.warehouse.DB().QueryRowContext(ctx,
			"SELECT max(r) FILTER (WHERE geo_id = ?), count(*) FROM (SELECT geo_id, "+
				"rank() OVER (ORDER BY v "+order+") AS r FROM ("+places+"))",
			append([]any{t.Geo}, a...)...,
		).Scan(&rank, &of); err != nil {
			return ArticleFigure{}, err
		}
		if !rank.Valid {
			return ArticleFigure{}, fmt.Errorf("no figure for %s in %s", t.Geo, t.Period)
		}
		return ArticleFigure{Display: articles.Ordinal(int(rank.Int64)) + " of " +
			fmt.Sprint(of.Int64)}, nil
	}

	aggregate := map[string]string{
		"mean": "avg(v)", "median": "median(v)", "min": "min(v)", "max": "max(v)",
	}[t.Measure]
	var v sql.NullFloat64
	if err := s.warehouse.DB().QueryRowContext(ctx,
		"SELECT "+aggregate+" FROM ("+places+")", a...,
	).Scan(&v); err != nil {
		return ArticleFigure{}, err
	}
	if !v.Valid {
		return ArticleFigure{}, fmt.Errorf("no places with figures for %s in %s", t.Indicator, t.Period)
	}
	return number(v.Float64, t.Digits, t.Period), nil
}

func number(v float64, digits int, period string) ArticleFigure {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return ArticleFigure{Error: "not a number"}
	}
	value := fmt.Sprintf("%.6f", v)
	return ArticleFigure{Display: articles.Format(v, digits), Value: &value, Period: period}
}
