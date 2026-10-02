// Package articles keeps the Portal's articles that are written in the portal
// rather than in the repository.
//
// An article is a title, a short standfirst and an ordered list of blocks:
// prose, a chart of warehouse series, or a chart borrowed from one of the
// articles built in code (apps/portal/src/articles). The prose may carry
// figure tokens — `{{change indicator=… from=2023-07 to=2024-03}}` — which
// the serving layer evaluates against the lake when the article is read, so a
// number in a sentence is as current as the chart beside it.
//
// Stored beside the showcase in the application database. Written by
// administrators only, like the showcase, and published into it: a published
// article is also a Portal entry, which this package does not know about and
// the HTTP layer keeps in step.
package articles

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/csis/terusan/services/api/internal/appdb"
)

var (
	// ErrNotFound is an article this deployment does not hold.
	ErrNotFound = errors.New("no such article")
	// ErrExists is a slug another article already has.
	ErrExists = errors.New("an article already has this slug")
)

// Statuses. A draft is seen by administrators only.
const (
	Draft     = "draft"
	Published = "published"
)

// Limits. An article longer than this is a report, and belongs in a file.
const (
	MaxBlocks      = 80
	MaxText        = 20000
	MaxTitle       = 300
	MaxDek         = 1000
	MaxQueryValues = 50
)

// Slugs name the article in its URL: lowercase words joined by hyphens.
var slugPattern = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)

// reserved are slugs the portal routes on itself.
var reserved = map[string]bool{"new": true, "edit": true, "figures": true, "measures": true}

// Values in a chart's query: the same shape the observations routes accept.
var (
	identifier = regexp.MustCompile(`^[A-Za-z0-9_.:@+-]{1,200}$`)
	period     = regexp.MustCompile(`^\d{4}(-(\d{2}|Q[1-4]|S[12])(-\d{2})?)?$`)
	dimensions = map[string]bool{
		"": true, "geography": true, "commodity": true, "both": true,
		"geography-category": true, "category": true, "none": true,
	}
)

// Query is what a chart block draws: the filters of GET
// /v1/observations/series, kept on the block itself rather than pointing at a
// saved query, so deleting the query does not blank the article.
type Query struct {
	Indicator   []string `json:"indicator"`
	Geo         []string `json:"geo,omitempty"`
	GeoType     []string `json:"geo_type,omitempty"`
	Commodity   []string `json:"commodity,omitempty"`
	PeriodStart string   `json:"period_start,omitempty"`
	PeriodEnd   string   `json:"period_end,omitempty"`
	Dimension   string   `json:"dimension,omitempty"`
	Members     int      `json:"members,omitempty"`
}

// Block is one part of an article. Which fields matter depends on Type:
//
//   - "text", "quote", "callout", "footnote": Markdown, which may carry
//     figure tokens; a quote's Note is who said it;
//   - "heading": Markdown, and Level 2 or 3;
//   - "divider": nothing;
//   - "chart": Title, Note and Query;
//   - "embed": Article and Chart naming a chart a code-built article exports;
//   - "table": Rows of cells, each markdown with tokens, Header when the
//     first row names the columns, and Title and Note;
//   - "regulation": Key, a regulation in the warehouse;
//   - "dataset": Dataset, a dataset in the warehouse;
//   - "stat": a dashboard's headline number. Markdown is the figure, usually
//     one token with a unit around it; Title names it, Note qualifies it.
//
// On a dashboard every block is a tile, and Width and Height are how many
// columns and rows of the grid it spans.
type Block struct {
	Type     string     `json:"type"`
	Markdown string     `json:"markdown,omitempty"`
	Level    int        `json:"level,omitempty"`
	Title    string     `json:"title,omitempty"`
	Note     string     `json:"note,omitempty"`
	Query    *Query     `json:"query,omitempty"`
	Article  string     `json:"article,omitempty"`
	Chart    string     `json:"chart,omitempty"`
	Rows     [][]string `json:"rows,omitempty"`
	Header   bool       `json:"header,omitempty"`
	Key      string     `json:"key,omitempty"`
	Dataset  string     `json:"dataset,omitempty"`
	Width    int        `json:"width,omitempty"`
	Height   int        `json:"height,omitempty"`
}

// Layouts. A dashboard lays its blocks out as tiles on a grid.
const (
	LayoutArticle   = "article"
	LayoutDashboard = "dashboard"
)

// The grid a dashboard's tiles are placed on.
const (
	GridColumns = 4
	GridRows    = 3
)

// Table limits: a table larger than this is a dataset, and belongs in one.
const (
	MaxRows = 60
	MaxCols = 12
	MaxCell = 1000
)

// Article is one article.
type Article struct {
	ID          string  `json:"id"`
	Slug        string  `json:"slug"`
	Title       string  `json:"title"`
	Dek         string  `json:"dek,omitempty"`
	Publisher   string  `json:"publisher,omitempty"`
	PublishedOn string  `json:"published_on,omitempty"`
	Status      string  `json:"status"`
	Layout      string  `json:"layout"`
	Blocks      []Block `json:"blocks"`
	// The Portal entry this article is published as, when it is.
	ShowcaseID     string    `json:"showcase_id,omitempty"`
	CreatedBy      string    `json:"created_by"`
	CreatedByEmail string    `json:"created_by_email"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}

// Indicators are every indicator the article's charts draw, in order of first
// appearance, which is what its datasets are worked out from.
func (a Article) Indicators() []string {
	seen := map[string]bool{}
	out := []string{}
	for _, block := range a.Blocks {
		if block.Query == nil {
			continue
		}
		for _, id := range block.Query.Indicator {
			if !seen[id] {
				seen[id] = true
				out = append(out, id)
			}
		}
	}
	return out
}

// Texts are the article's prose blocks, which is where tokens live.
func (a Article) Texts() []string {
	out := []string{}
	for _, block := range a.Blocks {
		for _, text := range []string{block.Markdown, block.Title, block.Note} {
			if text != "" {
				out = append(out, text)
			}
		}
		for _, row := range block.Rows {
			out = append(out, row...)
		}
	}
	return out
}

type Store struct {
	db *appdb.DB
}

const schema = `
CREATE TABLE IF NOT EXISTS articles (
    article_id        TEXT PRIMARY KEY,
    slug              TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL,
    dek               TEXT,
    publisher         TEXT,
    published_on      DATE,
    status            TEXT NOT NULL,
    -- The blocks as JSON: read and written whole, never queried into.
    blocks            TEXT NOT NULL,
    showcase_id       TEXT,
    created_by        TEXT NOT NULL,
    created_by_email  TEXT NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at        TIMESTAMP WITH TIME ZONE NOT NULL
);
-- Added with dashboards; an article from before them is laid out as one.
ALTER TABLE articles ADD COLUMN IF NOT EXISTS layout TEXT;
`

func New(db *appdb.DB) (*Store, error) {
	if db == nil {
		return nil, errors.New("no database")
	}
	if _, err := db.Exec(schema); err != nil {
		return nil, fmt.Errorf("articles schema: %w", err)
	}
	return &Store{db: db}, nil
}

func checkList(name string, values []string, pattern *regexp.Regexp) error {
	if len(values) > MaxQueryValues {
		return fmt.Errorf("%s: at most %d", name, MaxQueryValues)
	}
	for _, v := range values {
		if !pattern.MatchString(v) {
			return fmt.Errorf("%s: %q is not a well-formed identifier", name, v)
		}
	}
	return nil
}

func checkBlock(n int, b *Block) error {
	at := func(err error) error { return fmt.Errorf("blocks[%d].%w", n, err) }
	b.Markdown = strings.TrimSpace(b.Markdown)
	b.Title = strings.TrimSpace(b.Title)
	b.Note = strings.TrimSpace(b.Note)
	if len(b.Title) > MaxTitle || len(b.Note) > MaxDek {
		return at(errors.New("title: or note: is too long"))
	}
	if b.Width < 0 || b.Width > GridColumns || b.Height < 0 || b.Height > GridRows {
		return at(fmt.Errorf("width: 1 to %d columns, height: 1 to %d rows", GridColumns, GridRows))
	}
	// Whatever a type does not use is cleared, so a block turned from one type
	// into another does not carry the old one's fields.
	keep := func(markdown, query, embed, table, key, dataset bool) {
		if !markdown {
			b.Markdown, b.Level = "", 0
		}
		if !query {
			b.Query = nil
		}
		if !embed {
			b.Article, b.Chart = "", ""
		}
		if !table {
			b.Rows, b.Header = nil, false
		}
		if !key {
			b.Key = ""
		}
		if !dataset {
			b.Dataset = ""
		}
	}
	switch b.Type {
	case "text", "quote", "callout", "footnote", "heading":
		if b.Markdown == "" {
			return at(errors.New("markdown: an empty block has nothing to show"))
		}
		if len(b.Markdown) > MaxText {
			return at(fmt.Errorf("markdown: at most %d characters", MaxText))
		}
		level := b.Level
		keep(true, false, false, false, false, false)
		if b.Type == "heading" {
			if level != 2 && level != 3 {
				level = 2
			}
			b.Level = level
		}
	case "divider":
		keep(false, false, false, false, false, false)
		b.Title, b.Note = "", ""
	case "stat":
		if b.Markdown == "" || len(b.Markdown) > 1000 {
			return at(errors.New("markdown: the figure a stat shows, such as {{latest …}}"))
		}
		keep(true, false, false, false, false, false)
		b.Level = 0
	case "table":
		if len(b.Rows) == 0 || len(b.Rows) > MaxRows {
			return at(fmt.Errorf("rows: between 1 and %d", MaxRows))
		}
		for r, row := range b.Rows {
			if len(row) == 0 || len(row) > MaxCols {
				return at(fmt.Errorf("rows[%d]: between 1 and %d cells", r, MaxCols))
			}
			for c, cell := range row {
				if len(cell) > MaxCell {
					return at(fmt.Errorf("rows[%d][%d]: at most %d characters", r, c, MaxCell))
				}
				row[c] = strings.TrimSpace(cell)
			}
		}
		keep(false, false, false, true, false, false)
	case "regulation":
		if !identifier.MatchString(b.Key) {
			return at(errors.New("key: which regulation"))
		}
		keep(false, false, false, false, true, false)
	case "dataset":
		if !identifier.MatchString(b.Dataset) {
			return at(errors.New("dataset: which dataset"))
		}
		keep(false, false, false, false, false, true)
	case "chart":
		q := b.Query
		if q == nil || len(q.Indicator) == 0 {
			return at(errors.New("query.indicator: a chart needs at least one series"))
		}
		for name, values := range map[string][]string{
			"indicator": q.Indicator, "geo": q.Geo, "geo_type": q.GeoType, "commodity": q.Commodity,
		} {
			if err := checkList("query."+name, values, identifier); err != nil {
				return at(err)
			}
		}
		for name, value := range map[string]string{"period_start": q.PeriodStart, "period_end": q.PeriodEnd} {
			if value != "" && !period.MatchString(value) {
				return at(fmt.Errorf("query.%s: %q is not a period", name, value))
			}
		}
		if !dimensions[q.Dimension] {
			return at(fmt.Errorf("query.dimension: %q is not one the chart can split by", q.Dimension))
		}
		if q.Members < 0 || q.Members > 8 {
			return at(errors.New("query.members: between 1 and 8 lines"))
		}
		keep(false, true, false, false, false, false)
	case "embed":
		if !slugPattern.MatchString(b.Article) || !identifier.MatchString(b.Chart) {
			return at(errors.New("article: and chart: name a chart a built article exports"))
		}
		keep(false, false, true, false, false, false)
	default:
		return at(fmt.Errorf("type: %q is not a block an article can hold", b.Type))
	}
	return nil
}

// clean trims an article and says what is wrong with it, if anything.
func clean(input Article) (Article, error) {
	input.Slug = strings.TrimSpace(input.Slug)
	input.Title = strings.TrimSpace(input.Title)
	input.Dek = strings.TrimSpace(input.Dek)
	input.Publisher = strings.TrimSpace(input.Publisher)
	input.PublishedOn = strings.TrimSpace(input.PublishedOn)
	if input.Status == "" {
		input.Status = Draft
	}
	if input.Layout == "" {
		input.Layout = LayoutArticle
	}
	if input.Layout != LayoutArticle && input.Layout != LayoutDashboard {
		return Article{}, fmt.Errorf("layout: %q is not article or dashboard", input.Layout)
	}
	switch {
	case !slugPattern.MatchString(input.Slug) || len(input.Slug) > 120:
		return Article{}, errors.New("slug: lowercase words joined by hyphens, such as rice-prices-2024")
	case reserved[input.Slug]:
		return Article{}, fmt.Errorf("slug: %q is a path the portal uses itself", input.Slug)
	case input.Title == "" || len(input.Title) > MaxTitle:
		return Article{}, errors.New("title: an article needs a title, at most 300 characters")
	case len(input.Dek) > MaxDek:
		return Article{}, errors.New("dek: at most 1000 characters")
	case input.Status != Draft && input.Status != Published:
		return Article{}, fmt.Errorf("status: %q is not draft or published", input.Status)
	case len(input.Blocks) > MaxBlocks:
		return Article{}, fmt.Errorf("blocks: at most %d", MaxBlocks)
	}
	if input.PublishedOn != "" {
		if _, err := time.Parse(time.DateOnly, input.PublishedOn); err != nil {
			return Article{}, errors.New("published_on: is not a date written YYYY-MM-DD")
		}
	}
	if input.Blocks == nil {
		input.Blocks = []Block{}
	}
	for n := range input.Blocks {
		if err := checkBlock(n, &input.Blocks[n]); err != nil {
			return Article{}, err
		}
	}
	return input, nil
}

// Create adds an article.
func (s *Store) Create(ctx context.Context, input Article) (Article, error) {
	input, err := clean(input)
	if err != nil {
		return Article{}, err
	}
	if _, err := s.Get(ctx, input.Slug); err == nil {
		return Article{}, ErrExists
	} else if !errors.Is(err, ErrNotFound) {
		return Article{}, err
	}
	blocks, err := json.Marshal(input.Blocks)
	if err != nil {
		return Article{}, err
	}
	input.ID = uuid.NewString()
	now := time.Now().UTC()
	input.CreatedAt, input.UpdatedAt = now, now
	_, err = s.db.ExecContext(ctx, `
		INSERT INTO articles (article_id, slug, title, dek, publisher, published_on, status,
		                      layout, blocks, showcase_id, created_by, created_by_email,
		                      created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)`,
		input.ID, input.Slug, input.Title, nullable(input.Dek), nullable(input.Publisher),
		nullable(input.PublishedOn), input.Status, input.Layout, string(blocks),
		input.CreatedBy, input.CreatedByEmail, now, now)
	if err != nil {
		return Article{}, err
	}
	return input, nil
}

// Update replaces an article's fields and blocks. Its slug may change; who
// wrote it, and when, stay as they were.
func (s *Store) Update(ctx context.Context, slug string, input Article) (Article, error) {
	current, err := s.Get(ctx, slug)
	if err != nil {
		return Article{}, err
	}
	input, err = clean(input)
	if err != nil {
		return Article{}, err
	}
	if input.Slug != current.Slug {
		if _, err := s.Get(ctx, input.Slug); err == nil {
			return Article{}, ErrExists
		} else if !errors.Is(err, ErrNotFound) {
			return Article{}, err
		}
	}
	blocks, err := json.Marshal(input.Blocks)
	if err != nil {
		return Article{}, err
	}
	if _, err := s.db.ExecContext(ctx, `
		UPDATE articles SET slug = ?, title = ?, dek = ?, publisher = ?, published_on = ?,
		                    status = ?, layout = ?, blocks = ?, updated_at = ?
		WHERE article_id = ?`,
		input.Slug, input.Title, nullable(input.Dek), nullable(input.Publisher),
		nullable(input.PublishedOn), input.Status, input.Layout, string(blocks), time.Now().UTC(),
		current.ID); err != nil {
		return Article{}, err
	}
	return s.Get(ctx, input.Slug)
}

// SetShowcase records the Portal entry an article is published as, or clears
// it with "".
func (s *Store) SetShowcase(ctx context.Context, id, showcaseID string) error {
	_, err := s.db.ExecContext(ctx,
		`UPDATE articles SET showcase_id = ? WHERE article_id = ?`, nullable(showcaseID), id)
	return err
}

const selectArticle = `
	SELECT article_id, slug, title, COALESCE(dek, ''), COALESCE(publisher, ''),
	       COALESCE(CAST(published_on AS VARCHAR), ''), status, COALESCE(layout, 'article'), blocks,
	       COALESCE(showcase_id, ''), created_by, created_by_email, created_at, updated_at
	FROM articles`

func scan(row interface{ Scan(...any) error }) (Article, error) {
	var a Article
	var blocks string
	if err := row.Scan(&a.ID, &a.Slug, &a.Title, &a.Dek, &a.Publisher, &a.PublishedOn,
		&a.Status, &a.Layout, &blocks, &a.ShowcaseID, &a.CreatedBy, &a.CreatedByEmail,
		&a.CreatedAt, &a.UpdatedAt); err != nil {
		return Article{}, err
	}
	if err := json.Unmarshal([]byte(blocks), &a.Blocks); err != nil {
		return Article{}, fmt.Errorf("article %s: blocks: %w", a.Slug, err)
	}
	return a, nil
}

func (s *Store) Get(ctx context.Context, slug string) (Article, error) {
	a, err := scan(s.db.QueryRowContext(ctx, selectArticle+" WHERE slug = ?", slug))
	if errors.Is(err, sql.ErrNoRows) {
		return Article{}, ErrNotFound
	}
	return a, err
}

// List returns the articles, drafts too when drafts is set, most recently
// changed first.
func (s *Store) List(ctx context.Context, drafts bool) ([]Article, error) {
	where := " WHERE status = 'published'"
	if drafts {
		where = ""
	}
	rows, err := s.db.QueryContext(ctx, selectArticle+where+" ORDER BY updated_at DESC")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Article{}
	for rows.Next() {
		a, err := scan(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

func (s *Store) Delete(ctx context.Context, slug string) error {
	result, err := s.db.ExecContext(ctx, `DELETE FROM articles WHERE slug = ?`, slug)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err == nil && affected == 0 {
		return ErrNotFound
	}
	return nil
}

func nullable(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}
