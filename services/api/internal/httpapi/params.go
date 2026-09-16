package httpapi

import (
	"fmt"
	"net/http"
	"regexp"
	"strconv"
)

// Query parameters are validated rather than interpolated. Every value that
// reaches SQL goes in as a bound parameter, except sort order, which cannot be
// bound and is therefore matched against an allow-list.

const (
	DefaultLimit = 100
	MaxLimit     = 10_000
)

// identifierPattern is what an indicator, geography or dataset id may look like.
// Deliberately narrow: these are our own identifiers, not free text.
var identifierPattern = regexp.MustCompile(`^[A-Za-z0-9_.:@+-]{1,200}$`)

// periodPattern covers the canonical labels Silver writes: 2026, 2026-01,
// 2026-Q1, 2026-S1, 2026-01-15.
var periodPattern = regexp.MustCompile(`^\d{4}(-(\d{2}|Q[1-4]|S[12])(-\d{2})?)?$`)

type paramError struct {
	param  string
	reason string
}

func (e *paramError) Error() string { return fmt.Sprintf("%s: %s", e.param, e.reason) }

// stringParam returns a validated value, or "" when absent.
func stringParam(r *http.Request, name string, pattern *regexp.Regexp) (string, error) {
	value := r.URL.Query().Get(name)
	if value == "" {
		return "", nil
	}
	if !pattern.MatchString(value) {
		return "", &paramError{param: name, reason: "is not a well-formed value"}
	}
	return value, nil
}

// intParam returns a bounded integer, or the fallback when absent.
func intParam(r *http.Request, name string, fallback, min, max int) (int, error) {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return 0, &paramError{param: name, reason: "must be an integer"}
	}
	if value < min || value > max {
		return 0, &paramError{
			param:  name,
			reason: fmt.Sprintf("must be between %d and %d", min, max),
		}
	}
	return value, nil
}

// pagination reads limit and offset.
//
// A capped limit rather than an unbounded one: an analytical query over the
// whole warehouse is a legitimate thing to want, but it belongs in the export
// or SQL surface, not in a single JSON response nobody can stream
// (program.md §28, §29).
func pagination(r *http.Request) (limit, offset int, err error) {
	if limit, err = intParam(r, "limit", DefaultLimit, 1, MaxLimit); err != nil {
		return 0, 0, err
	}
	if offset, err = intParam(r, "offset", 0, 0, 10_000_000); err != nil {
		return 0, 0, err
	}
	return limit, offset, nil
}

// sortOrder maps a caller's `order` to SQL, from a fixed set.
//
// Order cannot be a bound parameter, so it is never interpolated from input:
// the caller picks a key and the server supplies the clause.
func sortOrder(r *http.Request, allowed map[string]string, fallback string) (string, error) {
	key := r.URL.Query().Get("order")
	if key == "" {
		return allowed[fallback], nil
	}
	clause, ok := allowed[key]
	if !ok {
		return "", &paramError{param: "order", reason: "is not a sortable field"}
	}
	return clause, nil
}
