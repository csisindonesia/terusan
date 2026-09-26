package httpapi

import (
	"strings"
	"testing"
)

func TestACursorRoundTrips(t *testing.T) {
	keys := observationSorts["-period"]
	geo := "ID-11"
	row := Observation{
		ObservationID: "obs_1e65cc3dabeb6f6153ec",
		Period:        "2026-09-21",
		GeoID:         &geo,
	}

	values, err := decodeCursor(cursorFor(keys, row), keys)
	if err != nil {
		t.Fatalf("decodeCursor: %v", err)
	}
	if len(values) != 3 || *values[0] != row.Period || *values[1] != geo ||
		*values[2] != row.ObservationID {
		t.Fatalf("cursor carried %v", values)
	}
}

func TestACursorFromAnotherOrderIsRefused(t *testing.T) {
	// The columns a cursor holds are the columns of the sort it was issued
	// under. Answering one from another sort would page through an order
	// nobody asked for, skipping rows on the way.
	issued := cursorFor(observationSorts["-period"], Observation{Period: "2026-09-21"})
	if _, err := decodeCursor(issued, observationSorts["-place"]); err == nil {
		t.Error("decodeCursor accepted a cursor from a different order")
	}
	if _, err := decodeCursor("not a cursor", observationSorts["period"]); err == nil {
		t.Error("decodeCursor accepted something it never issued")
	}
}

func TestSortingByValueCannotBePagedByCursor(t *testing.T) {
	// A decimal boundary round-tripped through a cursor has to compare equal
	// to the stored value exactly, and a figure is not a thing to bet that on.
	if cursorable(observationSorts["value"]) || cursorable(observationSorts["-value"]) {
		t.Error("a sort by value claims to be cursorable")
	}
	for _, order := range []string{"period", "-period", "geo", "place", "-place", "commodity", "-commodity"} {
		if !cursorable(observationSorts[order]) {
			t.Errorf("order=%s cannot be paged by cursor", order)
		}
	}
}

func TestEverySortIsTotal(t *testing.T) {
	// Two rows that compare equal on every column have no stable order, and a
	// page boundary between them returns one twice and skips the other.
	for order, keys := range observationSorts {
		if keys[len(keys)-1].expr != "o.observation_id" {
			t.Errorf("order=%s does not end in the observation id", order)
		}
		if !strings.Contains(observationOrder[order], "NULLS LAST") {
			t.Errorf("order=%s leaves the null order to the engine", order)
		}
	}
}

func TestTheKeysetStepsPastTheBoundaryRow(t *testing.T) {
	keys := observationSorts["-period"]
	period, geo, id := "2026-09-21", "ID-11", "obs_1"

	clause, args := keysetAfter(keys, []*string{&period, &geo, &id})
	// Newest first, so a later page holds earlier periods; within one period
	// the places ascend.
	for _, want := range []string{
		"(o.period < ? OR o.period IS NULL)",
		"o.period IS NOT DISTINCT FROM ? AND (o.geo_id > ?",
		"o.observation_id > ?",
	} {
		if !strings.Contains(clause, want) {
			t.Errorf("keyset %q is missing %q", clause, want)
		}
	}
	// One branch per column: 1 + 2 + 3 bound values.
	if len(args) != 6 {
		t.Errorf("bound %d values, want 6: %v", len(args), args)
	}
}

func TestNothingSortsAfterANull(t *testing.T) {
	// Nulls sort last, so a boundary row with no place has no rows after it on
	// that column — only rows that tie with it and sort later on the next one.
	keys := observationSorts["-period"]
	period, id := "2026-09-21", "obs_1"

	clause, _ := keysetAfter(keys, []*string{&period, nil, &id})
	if strings.Contains(clause, "o.geo_id >") {
		t.Errorf("keyset %q steps past a null", clause)
	}
	if !strings.Contains(clause, "o.geo_id IS NOT DISTINCT FROM ?") {
		t.Errorf("keyset %q does not pin the null place", clause)
	}

	// The very last row of a sort: nothing follows it at all.
	if clause, _ := keysetAfter(keys, []*string{nil, nil, nil}); clause != " AND false" {
		t.Errorf("keyset after an all-null row = %q", clause)
	}
}
