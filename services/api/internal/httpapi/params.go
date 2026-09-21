package httpapi

import (
	"fmt"
	"net/http"
	"regexp"
	"strconv"
	"strings"
)

// Query parameters are validated rather than interpolated. Every value that
// reaches SQL goes in as a bound parameter, except sort order, which cannot be
// bound and is therefore matched against an allow-list.

const (
	DefaultLimit = 100
	MaxLimit     = 10_000

	// A filter naming hundreds of values is a query the caller should be
	// sending to the SQL surface instead.
	MaxFilterValues = 50
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

// searchPattern is what a free-text query may contain. Narrow on purpose: the
// value reaches SQL bound, but a query of punctuation matches everything and
// costs a full scan to discover it.
var searchPattern = regexp.MustCompile(`^[\p{L}\p{N} ._:@+-]{1,100}$`)

// stringListParam returns validated values for a parameter that may repeat or
// carry a comma-separated list.
//
// Both forms are accepted because both are in the wild: `?geo_type=a&geo_type=b`
// is what an HTML form produces, `?geo_type=a,b` is what a hand-written URL
// usually looks like. Each value is validated separately, so one bad entry
// fails the request rather than being quietly dropped.
func stringListParam(r *http.Request, name string, pattern *regexp.Regexp) ([]string, error) {
	raw := r.URL.Query()[name]
	if len(raw) == 0 {
		return nil, nil
	}

	seen := make(map[string]bool)
	values := make([]string, 0, len(raw))
	for _, entry := range raw {
		for _, value := range strings.Split(entry, ",") {
			value = strings.TrimSpace(value)
			if value == "" {
				continue
			}
			if !pattern.MatchString(value) {
				return nil, &paramError{param: name, reason: "contains a value that is not well-formed"}
			}
			if !seen[value] {
				seen[value] = true
				values = append(values, value)
			}
		}
	}
	if len(values) > MaxFilterValues {
		return nil, &paramError{
			param:  name,
			reason: fmt.Sprintf("accepts at most %d values", MaxFilterValues),
		}
	}
	return values, nil
}

// inClause builds `column IN (?, ?)` with one placeholder per value.
//
// Placeholders rather than a joined literal: the values are validated, but a
// list built by string concatenation is one forgotten check away from being an
// injection, and there is no reason to rely on the check.
func inClause(column string, values []string) (string, []any) {
	if len(values) == 0 {
		return "", nil
	}
	if len(values) == 1 {
		return column + " = ?", []any{values[0]}
	}
	placeholders := strings.TrimSuffix(strings.Repeat("?, ", len(values)), ", ")
	args := make([]any, 0, len(values))
	for _, value := range values {
		args = append(args, value)
	}
	return column + " IN (" + placeholders + ")", args
}

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

// boolParam returns a flag, or false when absent.
//
// A bare `?dry_run` counts as true: that is how a person writes a flag by hand,
// and refusing it would be pedantry rather than safety.
func boolParam(r *http.Request, name string) (bool, error) {
	if !r.URL.Query().Has(name) {
		return false, nil
	}
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return true, nil
	}
	value, err := strconv.ParseBool(raw)
	if err != nil {
		return false, &paramError{param: name, reason: "must be true or false"}
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

// pathIdentifier reads a path segment and holds it to the same shape as a
// query identifier.
//
// A path value reaches SQL bound, like every other input, but an unvalidated
// one still buys a full scan for a string that could never match — and a
// caller who fat-fingered a key deserves "that is not a key" rather than an
// empty result.
func pathIdentifier(r *http.Request, name string) (string, error) {
	value := r.PathValue(name)
	if value == "" {
		return "", &paramError{param: name, reason: "is required"}
	}
	if !identifierPattern.MatchString(value) {
		return "", &paramError{param: name, reason: "is not a valid identifier"}
	}
	return value, nil
}
