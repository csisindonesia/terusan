package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"github.com/csis/terusan/services/api/internal/storage"
)

// The datasets as an OData v4 feed, for Excel and Power BI.
//
// A researcher's figures end up in a spreadsheet, and until now the way there
// was a CSV downloaded by hand and pasted in, which goes stale the day it is
// pasted. Excel's "Get Data → From OData Feed" (and Power BI's, which is the
// same Power Query underneath) instead keeps a connection: the workbook holds
// the URL and refreshes from it. OData is the protocol that dialog speaks, so
// this is the one place the serving layer speaks it.
//
// The shape is the one Excel's navigator presents best. The service root
// lists every dataset as its own entity set, named from its slug
// (`adb_key_indicators`), so a reader picks the collection they want and gets
// its figures as a table; `Datasets` beside them is the catalogue, with each
// one's title and its feed's URL, for a reader who knows the title and not the
// slug. Every dataset's rows are the same `Observation`, with the columns the
// figures table shows.
//
// Not the §53 envelope. Every other route answers `data` + `meta`, and this one
// cannot: Power Query reads OData's own JSON format and nothing else. What the
// envelope is for still holds — one parser for every answer — because OData's
// format is one shape for every set.
//
// Read-only, like the rest of the figures. Who may read is decided the same
// way it is for every other route (withAuth); the only addition is how Excel
// proves it, which is HTTP Basic with an API token as the password (see
// odataToken), because Basic is the one scheme Excel's dialog offers that
// carries a token.

const odataPrefix = "/v1/odata"

// Rows per page. Power Query follows `@odata.nextLink` until there is none,
// so this decides the number of round trips and not what the reader sees.
//
// Five thousand, and a client's `odata.maxpagesize` can lower it but not
// raise it, because of how DuckDB plans an ORDER BY with a LIMIT. Up to 5,000
// rows (limit and offset together) it always keeps a top-N heap; past that it
// keeps one only where it estimates the limit is under 0.7% of the rows, and
// the keyset condition on every page after the first makes that estimate
// small. Over the food prices, 8.7 million rows, a keyset page of 5,000
// measured 0.16s and one of 5,001 measured 15s, because it sorted them all.
//
// The page is asked for exactly, with no row beyond it to say whether another
// follows: that row would be the 5,001st. A full page therefore always
// carries a next link, and the last one may be empty, which costs a reader
// one quick request.
const (
	odataPageSize    = 5_000
	odataMaxPageSize = 5_000
)

func isODataPath(path string) bool {
	return path == odataPrefix || strings.HasPrefix(path, odataPrefix+"/")
}

// odataProperty is one column of an entity type: what OData calls it, what
// type it declares, and the SQL behind it. The SQL is the server's own and is
// what $filter and $orderby are compiled against, so a caller can only ever
// name a column, never write one.
type odataProperty struct {
	Name     string
	Type     string
	Expr     string
	Nullable bool
}

func (p odataProperty) filterExpr() string { return p.Expr }

func (p odataProperty) kind() odataKind {
	switch p.Type {
	case "Edm.Decimal", "Edm.Int64", "Edm.Int32":
		return kindNumber
	case "Edm.Date":
		return kindDate
	case "Edm.Boolean":
		return kindBool
	}
	return kindString
}

// selectExpr is the column as text, which is how every value is read back: a
// decimal through a float would drift, and a date through a time.Time would
// pick up a zone.
func (p odataProperty) selectExpr() string {
	if p.Type == "Edm.Date" {
		return "strftime(" + p.Expr + ", '%Y-%m-%d')"
	}
	return "CAST(" + p.Expr + " AS VARCHAR)"
}

// odataObservation is the row of every dataset's entity set, in the order the
// columns appear in Excel.
//
// The value is a decimal, which Excel reads as a number — the reason the rest
// of the API sends figures as strings is that a JSON encoder going through a
// float64 rounds them, and this one does not: the digits DuckDB holds are
// written into the payload as they are (odataNumber). A missing figure is a
// null beside a Status that says why, never a zero.
var odataObservation = []odataProperty{
	{"ObservationID", "Edm.String", "o.observation_id", false},
	{"IndicatorID", "Edm.String", "o.indicator_id", false},
	{"IndicatorName", "Edm.String", "n.name", true},
	{"Period", "Edm.String", "o.period", false},
	{"PeriodStart", "Edm.Date", "o.period_start", true},
	{"PeriodEnd", "Edm.Date", "o.period_end", true},
	{"Resolution", "Edm.String", "o.temporal_resolution", true},
	{"Value", "Edm.Decimal", "o.value", true},
	{"Unit", "Edm.String", "o.unit", true},
	{"Status", "Edm.String", "o.status", true},
	{"ValueUnambiguous", "Edm.Boolean", "o.value_unambiguous", true},
	{"GeoID", "Edm.String", "o.geo_id", true},
	{"GeoName", "Edm.String", memberGeoExpr, true},
	{"GeoType", "Edm.String", "g.geo_type", true},
	{"CommodityID", "Edm.String", "o.commodity_id", true},
	{"CommodityName", "Edm.String", memberCommodityExpr, true},
	{"Category", "Edm.String", "o.category", true},
	{"SourceID", "Edm.String", "o.source_id", true},
	{"SourceURL", "Edm.String", "o.source_url", true},
}

// odataDataset is the catalogue's row.
var odataDataset = []odataProperty{
	{"DatasetID", "Edm.String", "d.dataset_id", false},
	{"EntitySet", "Edm.String", "d.entity_set", false},
	{"Title", "Edm.String", "d.title", true},
	{"Description", "Edm.String", "d.description", true},
	{"Slug", "Edm.String", "d.slug", true},
	{"SourceID", "Edm.String", "d.source_id", true},
	{"SourceName", "Edm.String", "d.source_name", true},
	{"Organization", "Edm.String", "d.organization", true},
	{"License", "Edm.String", "d.license", true},
	{"Series", "Edm.Int32", "d.series", true},
	{"Observations", "Edm.Int64", "d.observations", true},
	{"PeriodStart", "Edm.String", "d.period_start", true},
	{"PeriodEnd", "Edm.String", "d.period_end", true},
	{"LastUpdated", "Edm.String", "d.last_updated", true},
	{"FeedURL", "Edm.String", "d.feed_url", true},
}

// odataCatalogueSet is the catalogue's own entity set. Capitalised, which no
// set named from a slug can be, so the two cannot collide.
const odataCatalogueSet = "Datasets"

func propertyMap(props []odataProperty) map[string]odataProperty {
	out := make(map[string]odataProperty, len(props))
	for _, p := range props {
		out[p.Name] = p
	}
	return out
}

var (
	odataObservationProps = propertyMap(odataObservation)
	odataDatasetProps     = propertyMap(odataDataset)
)

// ---- naming --------------------------------------------------------------

var odataUnsafe = regexp.MustCompile(`[^a-z0-9_]+`)

// assignODataSets names each dataset's entity set, from its slug.
//
// An OData name is an identifier — letters, digits, underscores, not starting
// with a digit — so `adb-key-indicators` becomes `adb_key_indicators`. The
// datasets arrive ordered by id, so where two slugs fold to the same name the
// same one keeps it from one build to the next, and the other is told apart
// by its id: a workbook holds the name, and a name that moved between
// datasets would refresh one into the other.
func assignODataSets(datasets []Dataset) {
	taken := map[string]bool{}
	for i := range datasets {
		d := &datasets[i]
		base := ""
		if d.Slug != nil {
			base = odataUnsafe.ReplaceAllString(strings.ToLower(*d.Slug), "_")
			base = strings.Trim(base, "_")
		}
		if base == "" {
			base = "dataset_" + odataUnsafe.ReplaceAllString(strings.ToLower(d.DatasetID), "_")
		}
		if base[0] >= '0' && base[0] <= '9' {
			base = "d_" + base
		}
		if len(base) > 100 {
			base = strings.TrimRight(base[:100], "_")
		}
		name := base
		if taken[name] {
			name = base + "_" + odataUnsafe.ReplaceAllString(strings.ToLower(d.DatasetID), "_")
		}
		taken[name] = true
		d.ODataSet = name
	}
}

// ---- answering -----------------------------------------------------------

// odataRoot is the service root as the caller reached it, for the absolute
// URLs the payload carries. Behind the tunnel the request arrives over plain
// HTTP with the public host intact, which is what AUTH_SECURE_COOKIES already
// tells the cookies; a proxy's forwarded headers are believed only where
// AUTH_TRUST_PROXY says it is the only way in.
func (s *Server) odataRoot(r *http.Request) string {
	scheme := "http"
	forwarded := strings.ToLower(r.Header.Get("X-Forwarded-Proto"))
	switch {
	case r.TLS != nil:
		scheme = "https"
	case s.cfg.Auth.TrustProxy && (forwarded == "https" || forwarded == "http"):
		scheme = forwarded
	case s.cfg.Auth.SecureCookies:
		scheme = "https"
	}
	host := r.Host
	if s.cfg.Auth.TrustProxy {
		if fh, _, _ := strings.Cut(r.Header.Get("X-Forwarded-Host"), ","); odataHost.MatchString(strings.TrimSpace(fh)) {
			host = strings.TrimSpace(fh)
		}
	}
	if !odataHost.MatchString(host) {
		host = "localhost"
	}
	return scheme + "://" + host + odataPrefix
}

var odataHost = regexp.MustCompile(`^[A-Za-z0-9.\-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](:\d{1,5})?$`)

func odataHeaders(w http.ResponseWriter) {
	w.Header().Set("OData-Version", "4.0")
}

// odataError answers in OData's error format, which is what Power Query shows
// the reader in its error banner.
func odataError(w http.ResponseWriter, status int, code, message string) {
	odataHeaders(w)
	writeJSON(w, status, map[string]any{
		"error": map[string]string{"code": code, "message": message},
	})
}

// odataFail sorts an error from reading the query options into the status it
// deserves.
func odataFail(w http.ResponseWriter, option string, err error) {
	var unsupported odataUnsupported
	if errors.As(err, &unsupported) {
		odataError(w, http.StatusNotImplemented, "not_implemented", option+": "+err.Error())
		return
	}
	odataError(w, http.StatusBadRequest, CodeBadRequest, option+": "+err.Error())
}

// handleODataService is the service document: every entity set, by name.
func (s *Server) handleODataService(w http.ResponseWriter, r *http.Request) {
	datasets, err := s.odataDatasets(r.Context())
	if err != nil {
		internalError(w, s.log, "odata datasets", err)
		return
	}
	root := s.odataRoot(r)
	sets := []map[string]string{{
		"name": odataCatalogueSet, "kind": "EntitySet", "url": odataCatalogueSet,
		"title": "Every dataset, with its title and the URL of its feed",
	}}
	for _, d := range datasets {
		entry := map[string]string{"name": d.ODataSet, "kind": "EntitySet", "url": d.ODataSet}
		if d.Title != nil {
			entry["title"] = *d.Title
		}
		sets = append(sets, entry)
	}
	odataHeaders(w)
	w.Header().Set("Content-Type", "application/json;odata.metadata=minimal;charset=utf-8")
	if err := json.NewEncoder(w).Encode(map[string]any{
		"@odata.context": root + "/$metadata",
		"value":          sets,
	}); err != nil {
		s.log.Warn("odata.encode_failed", "error", err)
	}
}

// handleODataMetadata is the CSDL document that tells Excel what columns each
// set has and of what type, so the table arrives typed rather than as text.
func (s *Server) handleODataMetadata(w http.ResponseWriter, r *http.Request) {
	datasets, err := s.odataDatasets(r.Context())
	if err != nil {
		internalError(w, s.log, "odata datasets", err)
		return
	}
	var b bytes.Buffer
	b.WriteString(`<?xml version="1.0" encoding="utf-8"?>` + "\n")
	b.WriteString(`<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">`)
	b.WriteString(`<edmx:DataServices><Schema Namespace="Terusan" xmlns="http://docs.oasis-open.org/odata/ns/edm">`)
	writeEntityType(&b, "Observation", "ObservationID", odataObservation)
	writeEntityType(&b, "Dataset", "DatasetID", odataDataset)
	b.WriteString(`<EntityContainer Name="Container">`)
	fmt.Fprintf(&b, `<EntitySet Name="%s" EntityType="Terusan.Dataset"/>`, odataCatalogueSet)
	for _, d := range datasets {
		b.WriteString(`<EntitySet Name="`)
		_ = xml.EscapeText(&b, []byte(d.ODataSet))
		b.WriteString(`" EntityType="Terusan.Observation"/>`)
	}
	b.WriteString(`</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>`)

	odataHeaders(w)
	w.Header().Set("Content-Type", "application/xml;charset=utf-8")
	_, _ = w.Write(b.Bytes())
}

func writeEntityType(b *bytes.Buffer, name, key string, props []odataProperty) {
	fmt.Fprintf(b, `<EntityType Name="%s"><Key><PropertyRef Name="%s"/></Key>`, name, key)
	for _, p := range props {
		fmt.Fprintf(b, `<Property Name="%s" Type="%s"`, p.Name, p.Type)
		if !p.Nullable {
			b.WriteString(` Nullable="false"`)
		}
		if p.Type == "Edm.Decimal" {
			b.WriteString(` Scale="variable"`)
		}
		b.WriteString(`/>`)
	}
	b.WriteString(`</EntityType>`)
}

// odataDatasets is the catalogue's datasets, each with its set's name.
func (s *Server) odataDatasets(ctx context.Context) ([]Dataset, error) {
	if !s.warehouse.Exists(ctx, storage.LayerSilver, "observations") {
		return nil, nil
	}
	catalogue, err := s.lakeCatalogue(ctx)
	if err != nil {
		return nil, err
	}
	return catalogue.datasets, nil
}

// handleODataSet answers one entity set, or its `$count`.
func (s *Server) handleODataSet(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("set")
	countOnly := false
	if rest := r.PathValue("rest"); rest != "" {
		if rest != "$count" {
			odataError(w, http.StatusNotImplemented, "not_implemented",
				"only an entity set and its $count can be addressed here")
			return
		}
		countOnly = true
	}
	ctx := r.Context()
	datasets, err := s.odataDatasets(ctx)
	if err != nil {
		internalError(w, s.log, "odata datasets", err)
		return
	}

	if name == odataCatalogueSet {
		set, err := s.catalogueSet(r, datasets)
		if err != nil {
			internalError(w, s.log, "odata catalogue", err)
			return
		}
		s.serveODataSet(w, r, set, countOnly)
		return
	}
	for _, d := range datasets {
		if d.ODataSet == name {
			set, err := s.observationSet(ctx, d)
			if err != nil {
				internalError(w, s.log, "odata observations", err)
				return
			}
			s.serveODataSet(w, r, set, countOnly)
			return
		}
	}
	odataError(w, http.StatusNotFound, CodeNotFound,
		fmt.Sprintf("there is no entity set called %q; the service root lists them", name))
}

// odataSet is everything serveODataSet needs to know about one entity set.
type odataSet struct {
	name  string
	props []odataProperty
	index map[string]odataProperty
	key   string
	// The FROM clause, and the conditions every row of the set meets, with
	// what is bound in each.
	from      string
	fromArgs  []any
	where     string
	whereArgs []any
	// The order used when the caller names none. Where it can be carried in
	// a cursor (cursor.go), the pages are taken by keyset, so that the
	// eight-hundredth page of a daily price series costs what the first does.
	order  []observationSortKey
	keyset bool
}

func (s *Server) observationSet(ctx context.Context, d Dataset) (odataSet, error) {
	from, err := s.observationSource(ctx, d.Indicators)
	if err != nil {
		return odataSet{}, err
	}
	if s.warehouse.Exists(ctx, storage.LayerSilver, "indicators") {
		names, err := s.source(storage.LayerSilver, "indicators")
		if err != nil {
			return odataSet{}, err
		}
		from += " LEFT JOIN (SELECT indicator_id, name FROM " + names + ") n ON n.indicator_id = o.indicator_id"
	} else {
		from += " LEFT JOIN (SELECT NULL AS indicator_id, NULL AS name) n ON false"
	}
	return odataSet{
		name: d.ODataSet, props: odataObservation, index: odataObservationProps,
		key: "ObservationID", from: from,
		where: "o.dataset_id = ?", whereArgs: []any{d.DatasetID},
		order: observationSorts["period"], keyset: true,
	}, nil
}

// catalogueSet serves the catalogue through the same engine as the figures,
// as a VALUES list, so that $filter and $orderby mean the same thing on both.
// It is a few hundred rows, held in the process already.
func (s *Server) catalogueSet(r *http.Request, datasets []Dataset) (odataSet, error) {
	columns := []struct {
		name string
		cast string
	}{
		{"dataset_id", "VARCHAR"}, {"entity_set", "VARCHAR"}, {"title", "VARCHAR"},
		{"description", "VARCHAR"}, {"slug", "VARCHAR"}, {"source_id", "VARCHAR"},
		{"source_name", "VARCHAR"}, {"organization", "VARCHAR"}, {"license", "VARCHAR"},
		{"series", "INTEGER"}, {"observations", "BIGINT"}, {"period_start", "VARCHAR"},
		{"period_end", "VARCHAR"}, {"last_updated", "VARCHAR"}, {"feed_url", "VARCHAR"},
	}
	names := make([]string, len(columns))
	holes := make([]string, len(columns))
	for i, c := range columns {
		names[i] = c.name
		holes[i] = "CAST(? AS " + c.cast + ")"
	}
	row := "(" + strings.Join(holes, ", ") + ")"

	root := s.odataRoot(r)
	var from string
	var args []any
	if len(datasets) == 0 {
		from = "(SELECT * FROM (VALUES " + row + ") v(" + strings.Join(names, ", ") + ") WHERE false) d"
		args = make([]any, len(columns))
	} else {
		rows := make([]string, len(datasets))
		args = make([]any, 0, len(datasets)*len(columns))
		for i, d := range datasets {
			rows[i] = row
			args = append(args, d.DatasetID, d.ODataSet, orNull(d.Title), orNull(d.Description),
				orNull(d.Slug), d.SourceID, orNull(d.SourceName), orNull(d.Organization),
				orNull(d.License), d.Series, d.Observations, d.PeriodStart, d.PeriodEnd,
				orNull(d.LastUpdated), root+"/"+d.ODataSet)
		}
		from = "(VALUES " + strings.Join(rows, ", ") + ") d(" + strings.Join(names, ", ") + ")"
	}
	return odataSet{
		name: odataCatalogueSet, props: odataDataset, index: odataDatasetProps,
		key: "DatasetID", from: from, fromArgs: args,
		order: []observationSortKey{{"d.entity_set", false}},
	}, nil
}

// orNull binds an absent string as SQL null rather than as a pointer the
// driver may not dereference.
func orNull(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

// odataOptions is what the caller asked of a set.
type odataOptions struct {
	selected  []odataProperty
	filter    string
	args      []any
	orderBy   string
	top       int // -1 for none
	skip      int
	count     bool
	skiptoken string
	pageSize  int
	ieee754   bool
}

// The system query options this service answers, and those it knows of and
// will not. Anything else starting with `$` is a mistake, said so, rather
// than an option silently not applied.
var (
	odataKnownOptions = map[string]bool{
		"$select": true, "$filter": true, "$orderby": true, "$top": true,
		"$skip": true, "$count": true, "$skiptoken": true, "$format": true,
	}
	odataRefusedOptions = map[string]bool{
		"$expand": true, "$search": true, "$apply": true, "$compute": true,
		"$levels": true, "$deltatoken": true, "$index": true, "$schemaversion": true,
	}
)

var odataPrefer = regexp.MustCompile(`(?i)odata\.maxpagesize\s*=\s*(\d+)`)

func readODataOptions(r *http.Request, set odataSet) (odataOptions, string, error) {
	opts := odataOptions{top: -1, pageSize: odataPageSize}
	query := r.URL.Query()
	for name := range query {
		// The `$` is optional in OData 4.01, and Power Query does not send
		// it bare, but another client might.
		canonical := name
		if !strings.HasPrefix(canonical, "$") && (odataKnownOptions["$"+canonical] || odataRefusedOptions["$"+canonical]) {
			canonical = "$" + canonical
		}
		switch {
		case odataRefusedOptions[canonical]:
			return opts, name, odataUnsupported{name}
		case strings.HasPrefix(canonical, "$") && !odataKnownOptions[canonical]:
			return opts, name, errors.New("is not a system query option")
		}
		if canonical != name {
			query[canonical] = query[name]
		}
	}

	if format := query.Get("$format"); format != "" {
		if !strings.HasPrefix(strings.ToLower(format), "json") &&
			!strings.HasPrefix(strings.ToLower(format), "application/json") {
			return opts, "$format", odataUnsupported{"any format but JSON"}
		}
		if strings.Contains(strings.ToLower(format), "ieee754compatible=true") {
			opts.ieee754 = true
		}
	}
	if strings.Contains(strings.ToLower(r.Header.Get("Accept")), "ieee754compatible=true") {
		opts.ieee754 = true
	}

	opts.selected = set.props
	if selection := strings.TrimSpace(query.Get("$select")); selection != "" && selection != "*" {
		opts.selected = nil
		seen := map[string]bool{}
		for _, item := range strings.Split(selection, ",") {
			item = strings.TrimSpace(item)
			if item == "*" {
				opts.selected = set.props
				break
			}
			prop, ok := set.index[item]
			if !ok {
				return opts, "$select", fmt.Errorf("%s is not a property of this entity set", item)
			}
			if !seen[item] {
				seen[item] = true
				opts.selected = append(opts.selected, prop)
			}
		}
	}

	if filter := strings.TrimSpace(query.Get("$filter")); filter != "" {
		where, args, err := compileFilter(filter, set.index)
		if err != nil {
			return opts, "$filter", err
		}
		opts.filter, opts.args = where, args
	}
	if order := strings.TrimSpace(query.Get("$orderby")); order != "" {
		clause, err := compileOrderBy(order, set.index, set.key)
		if err != nil {
			return opts, "$orderby", err
		}
		opts.orderBy = clause
	}

	for _, option := range []struct {
		name string
		into *int
	}{{"$top", &opts.top}, {"$skip", &opts.skip}} {
		if raw := query.Get(option.name); raw != "" {
			n, err := strconv.Atoi(raw)
			if err != nil || n < 0 {
				return opts, option.name, errors.New("is not a non-negative whole number")
			}
			*option.into = n
		}
	}
	switch strings.ToLower(query.Get("$count")) {
	case "", "false":
	case "true":
		opts.count = true
	default:
		return opts, "$count", errors.New("is neither true nor false")
	}
	opts.skiptoken = query.Get("$skiptoken")

	if m := odataPrefer.FindStringSubmatch(r.Header.Get("Prefer")); m != nil {
		if n, err := strconv.Atoi(m[1]); err == nil && n > 0 {
			opts.pageSize = min(n, odataMaxPageSize)
		}
	}
	return opts, "", nil
}

// serveODataSet answers one page of a set.
func (s *Server) serveODataSet(w http.ResponseWriter, r *http.Request, set odataSet, countOnly bool) {
	ctx := r.Context()
	opts, option, err := readODataOptions(r, set)
	if err != nil {
		odataFail(w, option, err)
		return
	}

	conditions := []string{}
	args := append([]any{}, set.fromArgs...)
	if set.where != "" {
		conditions = append(conditions, set.where)
		args = append(args, set.whereArgs...)
	}
	if opts.filter != "" {
		// Null is not true: a row the filter cannot decide is not a row it
		// kept, in OData as in SQL.
		conditions = append(conditions, "coalesce("+opts.filter+", false)")
		args = append(args, opts.args...)
	}
	where := ""
	if len(conditions) > 0 {
		where = " WHERE " + strings.Join(conditions, " AND ")
	}

	var total int64
	if opts.count || countOnly {
		if err := s.warehouse.DB().QueryRowContext(ctx,
			"SELECT count(*) FROM "+set.from+where, args...).Scan(&total); err != nil {
			internalError(w, s.log, "odata count", err)
			return
		}
	}
	if countOnly {
		odataHeaders(w)
		w.Header().Set("Content-Type", "text/plain;charset=utf-8")
		_, _ = fmt.Fprint(w, total)
		return
	}

	// Where the page starts. A keyset token names the last row sent, an
	// offset token how many rows precede the page; which one the set issues
	// depends only on its order, so a token from one cannot be read as the
	// other without the prefix saying so.
	keyset := set.keyset && opts.orderBy == ""
	order := opts.orderBy
	if order == "" {
		order = sortClause(set.order)
	}
	offset := opts.skip
	pageWhere, pageArgs := where, args
	switch {
	case opts.skiptoken == "":
	case keyset && strings.HasPrefix(opts.skiptoken, "k"):
		values, err := decodeCursor(opts.skiptoken[1:], set.order)
		if err != nil {
			odataError(w, http.StatusBadRequest, CodeBadRequest, "$skiptoken: "+err.Error())
			return
		}
		clause, bound := keysetAfter(set.order, values)
		if pageWhere == "" {
			pageWhere = " WHERE true"
		}
		pageWhere += clause
		pageArgs = append(append([]any{}, args...), bound...)
		offset = 0
	case !keyset && strings.HasPrefix(opts.skiptoken, "o"):
		n, err := strconv.Atoi(opts.skiptoken[1:])
		if err != nil || n < 0 {
			odataError(w, http.StatusBadRequest, CodeBadRequest, "$skiptoken: is not one this server issued")
			return
		}
		offset = n
	default:
		odataError(w, http.StatusBadRequest, CodeBadRequest, "$skiptoken: belongs to a different order")
		return
	}

	limit := opts.pageSize
	if opts.top >= 0 && opts.top < limit {
		limit = opts.top
	}

	columns := make([]string, 0, len(opts.selected)+len(set.order))
	for _, p := range opts.selected {
		columns = append(columns, p.selectExpr())
	}
	// The sort key, after the columns asked for, so that the next page's
	// token can be written whether or not they were selected.
	if keyset {
		for _, key := range set.order {
			columns = append(columns, "CAST("+key.expr+" AS VARCHAR)")
		}
	}
	// The limit and offset written into the statement rather than bound:
	// both are integers this function computed, not text from the caller,
	// and the planner chooses a top-N heap over a full sort only for a
	// limit it can see (see odataPageSize).
	querySQL := fmt.Sprintf("SELECT %s FROM %s%s ORDER BY %s LIMIT %d OFFSET %d",
		strings.Join(columns, ", "), set.from, pageWhere, order, limit, offset)

	var rows [][]*string
	if limit > 0 {
		result, err := s.warehouse.DB().QueryContext(ctx, querySQL, pageArgs...)
		if err != nil {
			internalError(w, s.log, "odata query", err)
			return
		}
		defer result.Close()
		for result.Next() {
			values := make([]*string, len(columns))
			holders := make([]any, len(columns))
			for i := range values {
				holders[i] = &values[i]
			}
			if err := result.Scan(holders...); err != nil {
				internalError(w, s.log, "odata scan", err)
				return
			}
			rows = append(rows, values)
		}
		if err := result.Err(); err != nil {
			internalError(w, s.log, "odata read", err)
			return
		}
	}
	more := limit > 0 && len(rows) == limit

	root := s.odataRoot(r)
	var b bytes.Buffer
	b.WriteString(`{"@odata.context":`)
	writeJSONString(&b, root+"/$metadata#"+set.name+selectionSuffix(opts, set))
	if opts.count {
		b.WriteString(`,"@odata.count":`)
		if opts.ieee754 {
			fmt.Fprintf(&b, `"%d"`, total)
		} else {
			fmt.Fprintf(&b, "%d", total)
		}
	}
	b.WriteString(`,"value":[`)
	for n, row := range rows {
		if n > 0 {
			b.WriteByte(',')
		}
		b.WriteByte('{')
		for i, p := range opts.selected {
			if i > 0 {
				b.WriteByte(',')
			}
			writeJSONString(&b, p.Name)
			b.WriteByte(':')
			writeODataValue(&b, p, row[i], opts.ieee754)
		}
		b.WriteByte('}')
	}
	b.WriteByte(']')

	// The next page, where there is one and the caller's $top has not been
	// reached. The same options, so that Power Query can follow it blind;
	// $skip already spent, the remaining $top carried, and the count, which
	// the first page answered, not asked again.
	remaining := opts.top - len(rows)
	if more && (opts.top < 0 || remaining > 0) {
		next := url.Values{}
		for name, values := range r.URL.Query() {
			next[name] = values
		}
		for _, name := range []string{"$skip", "skip", "$count", "count", "$skiptoken", "skiptoken"} {
			next.Del(name)
		}
		if opts.top >= 0 {
			next.Del("top")
			next.Set("$top", strconv.Itoa(remaining))
		}
		if keyset {
			last := rows[len(rows)-1][len(opts.selected):]
			next.Set("$skiptoken", "k"+encodeCursor(last))
		} else {
			next.Set("$skiptoken", "o"+strconv.Itoa(offset+len(rows)))
		}
		b.WriteString(`,"@odata.nextLink":`)
		writeJSONString(&b, root+"/"+set.name+"?"+next.Encode())
	}
	b.WriteString("}\n")

	odataHeaders(w)
	if opts.pageSize != odataPageSize {
		w.Header().Set("Preference-Applied", "odata.maxpagesize="+strconv.Itoa(opts.pageSize))
	}
	contentType := "application/json;odata.metadata=minimal;odata.streaming=true"
	if opts.ieee754 {
		contentType += ";IEEE754Compatible=true"
	}
	w.Header().Set("Content-Type", contentType+";charset=utf-8")
	_, _ = w.Write(b.Bytes())
}

// sortClause is an order as SQL, nulls last throughout as the cursor expects.
func sortClause(keys []observationSortKey) string {
	parts := make([]string, len(keys))
	for i, key := range keys {
		direction := " ASC"
		if key.desc {
			direction = " DESC"
		}
		parts[i] = key.expr + direction + " NULLS LAST"
	}
	return strings.Join(parts, ", ")
}

// selectionSuffix is the context URL's note of which columns a page carries.
func selectionSuffix(opts odataOptions, set odataSet) string {
	if len(opts.selected) == len(set.props) {
		return ""
	}
	names := make([]string, len(opts.selected))
	for i, p := range opts.selected {
		names[i] = p.Name
	}
	return "(" + strings.Join(names, ",") + ")"
}

var odataPlainNumber = regexp.MustCompile(`^-?\d+(\.\d+)?$`)

// writeODataValue writes one value as its declared type.
func writeODataValue(b *bytes.Buffer, p odataProperty, value *string, ieee754 bool) {
	if value == nil {
		b.WriteString("null")
		return
	}
	switch p.Type {
	case "Edm.Boolean":
		if *value == "true" {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case "Edm.Decimal", "Edm.Int64", "Edm.Int32":
		number := odataNumber(*value)
		// IEEE754Compatible asks for the wide numbers as strings, for a
		// client that would otherwise read them through a double.
		if !odataPlainNumber.MatchString(number) || ieee754 && p.Type != "Edm.Int32" {
			writeJSONString(b, number)
			return
		}
		b.WriteString(number)
	default:
		writeJSONString(b, *value)
	}
}

// odataNumber is the decimal as DuckDB prints it, without the trailing zeros
// its fixed scale adds: Silver holds `1234.5` as `1234.500000000`, and the
// zeros are not part of the figure.
func odataNumber(text string) string {
	if strings.Contains(text, ".") {
		text = strings.TrimRight(text, "0")
		text = strings.TrimSuffix(text, ".")
	}
	if text == "-0" {
		return "0"
	}
	return text
}

func writeJSONString(b *bytes.Buffer, s string) {
	encoded, _ := json.Marshal(s)
	b.Write(encoded)
}
