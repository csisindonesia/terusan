package httpapi

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/csis/terusan/services/api/internal/collections"
)

// The shelf: folders of records, and the queries worth re-running.
//
// The rest of this API derives its answers from the lake, which makes every
// route a pure function of what the pipelines wrote. These do not: a folder is
// something a reader assembled, it exists nowhere else, and it is the first
// thing this service stores rather than reads. That is why it is separated —
// its own package, its own file, its own database — instead of being added to
// the warehouse queries.
//
// Why it is here at all: a collection kept in the browser is a collection that
// cannot be sent to a colleague, survives only until site data is cleared, and
// is invisible to everything but the one browser that made it. A URL for a
// folder is the point of a folder, and a URL only works if the server knows
// what is in it.
//
// There is no authentication in front of this (program.md §34), so one
// deployment is one shelf and whoever can reach the API can edit it. That is
// stated in `/v1/capabilities` rather than implied, and a deployment where it
// would be wrong is started without COLLECTIONS_WRITE.

// What a collection may hold. Closed rather than free text, because each kind
// is a catalogue this API already answers for, and an item of a kind nothing
// can resolve is a row that renders as a dead link forever.
var itemKinds = map[string]bool{
	"indicator":  true,
	"dataset":    true,
	"document":   true,
	"regulation": true,
	"commodity":  true,
	"topic":      true,
}

// What a saved query may reopen, mirroring the portal's routes.
var queryKinds = map[string]bool{
	"observations": true,
	"documents":    true,
	"regulations":  true,
	"indicators":   true,
	"datasets":     true,
	"commodities":  true,
	"search":       true,
}

// A shelf identifier: the portal's `col-…` and `qry-…`, or a UUID. Narrow
// because it goes in a URL and comes back out of one.
var shelfIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

const (
	maxNameLength        = 200
	maxDescriptionLength = 2000
	maxNoteLength        = 1000
	// A folder is something a person assembled and reads. Past this it is a
	// query result, and the API already has endpoints that return those.
	maxItemsPerCollection = 1000
	// A saved query is filters, not a payload.
	maxSearchBytes = 16 << 10
	// What an import may carry in one request.
	maxImportBytes = 8 << 20
)

// collectionsReady answers the request itself when the shelf is unavailable or
// read-only, and reports whether the caller should carry on.
func (s *Server) collectionsReady(w http.ResponseWriter, write bool) bool {
	if s.shelf == nil {
		writeError(w, http.StatusNotFound, CodeNotFound,
			"this serving layer keeps no collections",
			"set COLLECTIONS_DB where the API runs; until then the portal keeps them "+
				"in the browser instead")
		return false
	}
	if write && !s.shelf.Writable() {
		writeError(w, http.StatusForbidden, CodeForbidden,
			"this shelf is read-only",
			"set COLLECTIONS_WRITE=true where the API runs to allow changes")
		return false
	}
	return true
}

// shelfError maps the store's errors onto the envelope.
func (s *Server) shelfError(w http.ResponseWriter, context string, err error) {
	switch {
	case errors.Is(err, collections.ErrNotFound):
		notFound(w, "no such collection", "")
	case errors.Is(err, collections.ErrReadOnly):
		writeError(w, http.StatusForbidden, CodeForbidden, "this shelf is read-only", "")
	default:
		internalError(w, s.log, context, err)
	}
}

func (s *Server) handleCollections(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, false) {
		return
	}
	list, err := s.shelf.List(r.Context())
	if err != nil {
		s.shelfError(w, "list collections", err)
		return
	}
	writeData(w, list, &Meta{Total: int64(len(list))})
}

func (s *Server) handleCollection(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, false) {
		return
	}
	id := r.PathValue("id")
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed collection id")
		return
	}
	found, err := s.shelf.Get(r.Context(), id)
	if err != nil {
		s.shelfError(w, "get collection", err)
		return
	}
	writeData(w, found, nil)
}

// collectionBody is what a caller may send when making or changing a folder.
//
// Pointers on the two editable fields, because "leave the description alone"
// and "remove the description" are different requests and a plain string
// cannot tell them apart.
type collectionBody struct {
	ID          string  `json:"id"`
	Name        *string `json:"name"`
	Description *string `json:"description"`
	Items       []struct {
		Kind  string `json:"kind"`
		ID    string `json:"id"`
		Label string `json:"label"`
		Note  string `json:"note"`
	} `json:"items"`
}

func (s *Server) handleCreateCollection(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, true) {
		return
	}
	var body collectionBody
	if !decodeBody(w, r, &body, maxImportBytes) {
		return
	}

	name := ""
	if body.Name != nil {
		name = strings.TrimSpace(*body.Name)
	}
	if name == "" {
		badRequest(w, "invalid body", "name: a collection needs a name")
		return
	}
	if len(name) > maxNameLength {
		badRequest(w, "invalid body", "name: is too long")
		return
	}

	id := strings.TrimSpace(body.ID)
	if id == "" {
		// The portal supplies its own id so that the folder it just drew and
		// the row this writes are the same thing. A caller who does not care
		// gets one here.
		id = "col-" + newShelfID()
	}
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid body", "id: is not a well-formed collection id")
		return
	}

	items, err := s.readItems(body)
	if err != nil {
		badRequest(w, "invalid body", err.Error())
		return
	}

	now := time.Now().UTC()
	created, err := s.shelf.Create(r.Context(), collections.Collection{
		ID:          id,
		Name:        name,
		Description: trimTo(body.Description, maxDescriptionLength),
		CreatedAt:   now,
		UpdatedAt:   now,
		Items:       items,
	})
	if err != nil {
		if strings.Contains(err.Error(), "already exists") {
			writeError(w, http.StatusConflict, CodeConflict,
				"a collection with this id is already here", "id: "+id)
			return
		}
		s.shelfError(w, "create collection", err)
		return
	}
	writeJSON(w, http.StatusCreated, Response[collections.Collection]{Data: created})
}

func (s *Server) handleUpdateCollection(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, true) {
		return
	}
	id := r.PathValue("id")
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed collection id")
		return
	}
	var body collectionBody
	if !decodeBody(w, r, &body, 1<<20) {
		return
	}
	if body.Name != nil && len(strings.TrimSpace(*body.Name)) > maxNameLength {
		badRequest(w, "invalid body", "name: is too long")
		return
	}
	if body.Description != nil && len(*body.Description) > maxDescriptionLength {
		badRequest(w, "invalid body", "description: is too long")
		return
	}

	updated, err := s.shelf.Update(r.Context(), id, body.Name, body.Description)
	if err != nil {
		s.shelfError(w, "update collection", err)
		return
	}
	writeData(w, updated, nil)
}

func (s *Server) handleDeleteCollection(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, true) {
		return
	}
	id := r.PathValue("id")
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed collection id")
		return
	}
	if err := s.shelf.Delete(r.Context(), id); err != nil {
		s.shelfError(w, "delete collection", err)
		return
	}
	writeData(w, map[string]string{"deleted": id}, nil)
}

func (s *Server) handleAddItems(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, true) {
		return
	}
	id := r.PathValue("id")
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed collection id")
		return
	}
	var body collectionBody
	if !decodeBody(w, r, &body, maxImportBytes) {
		return
	}
	items, err := s.readItems(body)
	if err != nil {
		badRequest(w, "invalid body", err.Error())
		return
	}
	if len(items) == 0 {
		badRequest(w, "invalid body", "items: nothing to file")
		return
	}

	updated, added, err := s.shelf.AddItems(r.Context(), id, items)
	if err != nil {
		s.shelfError(w, "add items", err)
		return
	}
	// The count says what happened: filing a record the folder already holds
	// is a thing readers do, and it is not an error.
	writeJSON(w, http.StatusOK, Response[collections.Collection]{
		Data: updated,
		Meta: &Meta{Total: int64(added)},
	})
}

func (s *Server) handleRemoveItem(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, true) {
		return
	}
	id := r.PathValue("id")
	kind := r.PathValue("kind")
	ref := r.PathValue("ref")
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed collection id")
		return
	}
	if !itemKinds[kind] {
		badRequest(w, "invalid parameter", "kind: "+kind+" is not a kind a collection holds")
		return
	}
	if ref == "" {
		badRequest(w, "invalid parameter", "ref: is empty")
		return
	}

	updated, err := s.shelf.RemoveItem(r.Context(), id, kind, ref)
	if err != nil {
		s.shelfError(w, "remove item", err)
		return
	}
	writeData(w, updated, nil)
}

// readItems validates the items a body carries.
//
// A commodity is identified by the name its source printed — "Minyak Goreng
// Kemasan Bermerk 1" — so items are not held to `identifierPattern`; what they
// are held to is a length and a kind this API can resolve.
func (s *Server) readItems(body collectionBody) ([]collections.Item, error) {
	if len(body.Items) > maxItemsPerCollection {
		return nil, errors.New("items: a collection holds at most 1000 records")
	}
	now := time.Now().UTC()
	items := make([]collections.Item, 0, len(body.Items))
	for _, raw := range body.Items {
		if !itemKinds[raw.Kind] {
			return nil, errors.New("items: " + raw.Kind + " is not a kind a collection holds")
		}
		id := strings.TrimSpace(raw.ID)
		if id == "" || len(id) > 300 {
			return nil, errors.New("items: an item needs an identifier")
		}
		label := strings.TrimSpace(raw.Label)
		if label == "" {
			label = id
		}
		if len(label) > 500 {
			label = label[:500]
		}
		note := strings.TrimSpace(raw.Note)
		if len(note) > maxNoteLength {
			note = note[:maxNoteLength]
		}
		items = append(items, collections.Item{
			Kind: raw.Kind, ID: id, Label: label, Note: note, AddedAt: now,
		})
	}
	return items, nil
}

// ---- saved queries --------------------------------------------------------

func (s *Server) handleSavedQueries(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, false) {
		return
	}
	list, err := s.shelf.Queries(r.Context())
	if err != nil {
		s.shelfError(w, "list queries", err)
		return
	}
	writeData(w, list, &Meta{Total: int64(len(list))})
}

type queryBody struct {
	ID      string          `json:"id"`
	Name    string          `json:"name"`
	Kind    string          `json:"kind"`
	Path    string          `json:"path"`
	Search  json.RawMessage `json:"search"`
	Summary string          `json:"summary"`
}

func (s *Server) handleSaveQuery(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, true) {
		return
	}
	var body queryBody
	if !decodeBody(w, r, &body, maxSearchBytes+4096) {
		return
	}

	name := strings.TrimSpace(body.Name)
	if name == "" || len(name) > maxNameLength {
		badRequest(w, "invalid body", "name: a query needs a name")
		return
	}
	if !queryKinds[body.Kind] {
		badRequest(w, "invalid body", "kind: "+body.Kind+" is not a kind of query")
		return
	}
	// A path, not a URL: this is reopened by the portal's router, and an
	// absolute address would let a saved query send a reader anywhere.
	if !strings.HasPrefix(body.Path, "/") || strings.Contains(body.Path, "//") {
		badRequest(w, "invalid body", "path: must be a portal route such as /observations")
		return
	}
	if len(body.Search) > maxSearchBytes {
		badRequest(w, "invalid body", "search: is too large for a set of filters")
		return
	}
	// Stored as text, so it is checked as JSON here rather than trusted and
	// handed back broken to every reader after.
	if len(body.Search) > 0 && !json.Valid(body.Search) {
		badRequest(w, "invalid body", "search: is not valid JSON")
		return
	}

	id := strings.TrimSpace(body.ID)
	if id == "" {
		id = "qry-" + newShelfID()
	}
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid body", "id: is not a well-formed query id")
		return
	}

	query := collections.Query{
		ID:        id,
		Name:      name,
		Kind:      body.Kind,
		Path:      body.Path,
		Search:    body.Search,
		Summary:   strings.TrimSpace(body.Summary),
		CreatedAt: time.Now().UTC(),
	}
	if err := s.shelf.PutQuery(r.Context(), query); err != nil {
		s.shelfError(w, "save query", err)
		return
	}
	writeJSON(w, http.StatusCreated, Response[collections.Query]{Data: query})
}

func (s *Server) handleRenameQuery(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, true) {
		return
	}
	id := r.PathValue("id")
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed query id")
		return
	}
	var body queryBody
	if !decodeBody(w, r, &body, 1<<20) {
		return
	}
	updated, err := s.shelf.RenameQuery(r.Context(), id, body.Name)
	if err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			notFound(w, "no such query", "id: "+id)
			return
		}
		s.shelfError(w, "rename query", err)
		return
	}
	writeData(w, updated, nil)
}

func (s *Server) handleDeleteQuery(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, true) {
		return
	}
	id := r.PathValue("id")
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed query id")
		return
	}
	if err := s.shelf.DeleteQuery(r.Context(), id); err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			notFound(w, "no such query", "id: "+id)
			return
		}
		s.shelfError(w, "delete query", err)
		return
	}
	writeData(w, map[string]string{"deleted": id}, nil)
}

// ---- import ---------------------------------------------------------------

// handleImportShelf merges a whole exported shelf in.
//
// The same file the portal writes when it exports, so a shelf can be carried
// from a browser to a deployment, or from one deployment to another, without a
// format in between. Ids already here are skipped, which makes importing twice
// a no-op rather than a duplication.
func (s *Server) handleImportShelf(w http.ResponseWriter, r *http.Request) {
	if !s.collectionsReady(w, true) {
		return
	}

	var body struct {
		Collections []struct {
			ID          string `json:"id"`
			Name        string `json:"name"`
			Description string `json:"description"`
			CreatedAt   string `json:"created_at"`
			UpdatedAt   string `json:"updated_at"`
			Items       []struct {
				Kind    string `json:"kind"`
				ID      string `json:"id"`
				Label   string `json:"label"`
				Note    string `json:"note"`
				AddedAt string `json:"added_at"`
			} `json:"items"`
		} `json:"collections"`
		Queries []struct {
			ID        string          `json:"id"`
			Name      string          `json:"name"`
			Kind      string          `json:"kind"`
			Path      string          `json:"path"`
			Search    json.RawMessage `json:"search"`
			Summary   string          `json:"summary"`
			CreatedAt string          `json:"created_at"`
		} `json:"queries"`
	}
	if !decodeBody(w, r, &body, maxImportBytes) {
		return
	}

	now := time.Now().UTC()
	shelf := collections.Shelf{}

	for _, raw := range body.Collections {
		id := strings.TrimSpace(raw.ID)
		name := strings.TrimSpace(raw.Name)
		if !shelfIDPattern.MatchString(id) || name == "" {
			// One malformed folder does not sink the file: the rest still
			// arrives, and the response says how much did.
			continue
		}
		c := collections.Collection{
			ID:          id,
			Name:        truncate(name, maxNameLength),
			Description: truncate(strings.TrimSpace(raw.Description), maxDescriptionLength),
			CreatedAt:   parseTime(raw.CreatedAt, now),
			UpdatedAt:   parseTime(raw.UpdatedAt, now),
		}
		for _, item := range raw.Items {
			if !itemKinds[item.Kind] || strings.TrimSpace(item.ID) == "" {
				continue
			}
			if len(c.Items) >= maxItemsPerCollection {
				break
			}
			label := strings.TrimSpace(item.Label)
			if label == "" {
				label = item.ID
			}
			c.Items = append(c.Items, collections.Item{
				Kind:    item.Kind,
				ID:      strings.TrimSpace(item.ID),
				Label:   truncate(label, 500),
				Note:    truncate(strings.TrimSpace(item.Note), maxNoteLength),
				AddedAt: parseTime(item.AddedAt, now),
			})
		}
		shelf.Collections = append(shelf.Collections, c)
	}

	for _, raw := range body.Queries {
		id := strings.TrimSpace(raw.ID)
		name := strings.TrimSpace(raw.Name)
		if !shelfIDPattern.MatchString(id) || name == "" || !queryKinds[raw.Kind] {
			continue
		}
		if !strings.HasPrefix(raw.Path, "/") || len(raw.Search) > maxSearchBytes {
			continue
		}
		if len(raw.Search) > 0 && !json.Valid(raw.Search) {
			continue
		}
		shelf.Queries = append(shelf.Queries, collections.Query{
			ID:        id,
			Name:      truncate(name, maxNameLength),
			Kind:      raw.Kind,
			Path:      raw.Path,
			Search:    raw.Search,
			Summary:   truncate(strings.TrimSpace(raw.Summary), maxDescriptionLength),
			CreatedAt: parseTime(raw.CreatedAt, now),
		})
	}

	added, queries, err := s.shelf.Import(r.Context(), shelf)
	if err != nil {
		s.shelfError(w, "import shelf", err)
		return
	}
	writeData(w, map[string]int{
		"collections": added,
		"queries":     queries,
		// What was in the file but already here, so an import that looks like
		// it did nothing can say why.
		"skipped": (len(shelf.Collections) - added) + (len(shelf.Queries) - queries),
	}, nil)
}

// ---- helpers --------------------------------------------------------------

// decodeBody reads a JSON body, bounded, and answers the request itself if it
// cannot. Bounded because this is the one surface a caller supplies bytes to,
// and an unbounded decoder is an unbounded allocation.
func decodeBody(w http.ResponseWriter, r *http.Request, into any, limit int64) bool {
	decoder := json.NewDecoder(io.LimitReader(r.Body, limit))
	if err := decoder.Decode(into); err != nil {
		badRequest(w, "invalid body", "the request body is not the JSON this expects")
		return false
	}
	return true
}

// newShelfID is what a caller gets when it does not bring its own id. Short
// because it lands in a URL a person copies, and a folder is not a thing
// anyone guesses their way to — there is nothing private on this shelf.
func newShelfID() string {
	return strings.ReplaceAll(uuid.NewString(), "-", "")[:12]
}

func trimTo(value *string, max int) string {
	if value == nil {
		return ""
	}
	return truncate(strings.TrimSpace(*value), max)
}

func truncate(value string, max int) string {
	if len(value) <= max {
		return value
	}
	return value[:max]
}

// parseTime keeps an imported timestamp where it is readable and falls back to
// now, so a shelf written by hand still imports.
func parseTime(value string, fallback time.Time) time.Time {
	if value == "" {
		return fallback
	}
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil {
		return fallback
	}
	return parsed.UTC()
}
