package httpapi

import (
	"context"
	"net/http"

	"github.com/csis/terusan/services/api/internal/collections"
)

// A collection's own API: the figures of the series filed in it, under
// /v1/collections/{id}/….
//
// The same routes as the warehouse-wide ones — observations, series, facets —
// with the same parameters, pagination and cursors, narrowed to the
// collection. Not a copy of them: each handler here decides who may ask and
// which series, and then hands the request to the handler the lake-wide route
// uses. A filter or a format added there is here the next time this builds.
//
// Who may ask is whoever may open the collection — its owner and members, or
// everyone for a folder from before there were owners — with a session or
// their own API token (`Authorization: Bearer trs_…`). There is no key per
// collection: a key would be a second way in that outlives the member who
// copied it, and a token already names a person who can be removed.
//
// Off until the owner turns it on in the collection's settings. A folder is a
// reading list first, and an endpoint that another program polls is a promise
// that renaming or emptying it will break something.
//
// Uncached, unlike the lake routes they delegate to: the answer depends on who
// is asking and on a folder that changes when a member files into it, and the
// cache keys on neither.

type scopeKey struct{}

// indicatorScope is the set of series a collection route may read, carried to
// observationParams in the request's context. Replaces whatever `indicator`
// the caller sent, having already been narrowed by it.
func indicatorScope(ctx context.Context) ([]string, bool) {
	scope, ok := ctx.Value(scopeKey{}).([]string)
	return scope, ok
}

// collectionScope answers the request itself when the collection cannot be
// read through its API, and otherwise returns the collection and the series
// the request is about.
//
// `indicator` narrows within the collection, as it narrows the warehouse on
// the lake-wide route. A series asked for that is not in the collection is
// refused rather than dropped: a caller who got fewer series than they named,
// silently, would chart a gap that is not there.
func (s *Server) collectionScope(w http.ResponseWriter, r *http.Request) (collections.Collection, []string, bool) {
	if !s.collectionsReady(w, false) {
		return collections.Collection{}, nil, false
	}
	id := r.PathValue("id")
	if !shelfIDPattern.MatchString(id) {
		badRequest(w, "invalid parameter", "id: is not a well-formed collection id")
		return collections.Collection{}, nil, false
	}
	found, err := s.shelf.Get(r.Context(), s.viewer(r), id)
	if err != nil {
		s.shelfError(w, "collection api", err)
		return collections.Collection{}, nil, false
	}
	if !found.API {
		writeError(w, http.StatusForbidden, CodeForbidden,
			"this collection's API is off",
			"its owner can turn it on in the collection's settings")
		return collections.Collection{}, nil, false
	}

	held := make([]string, 0, len(found.Items))
	inside := map[string]bool{}
	for _, item := range found.Items {
		if item.Kind == collections.ItemKind && !inside[item.ID] {
			inside[item.ID] = true
			held = append(held, item.ID)
		}
	}
	if len(held) == 0 {
		// Answered as empty-and-why rather than handed on: an empty scope
		// reaching the lake route is no filter at all, which is every series
		// in the warehouse.
		notFound(w, "this collection holds no indicators yet",
			"add series to it from any indicator's menu")
		return collections.Collection{}, nil, false
	}

	asked, err := stringListParam(r, "indicator", identifierPattern)
	if err != nil {
		badRequest(w, "invalid parameter", err.Error())
		return collections.Collection{}, nil, false
	}
	if len(asked) == 0 {
		return found, held, true
	}
	for _, indicator := range asked {
		if !inside[indicator] {
			badRequest(w, "invalid parameter",
				"indicator: "+indicator+" is not in this collection")
			return collections.Collection{}, nil, false
		}
	}
	return found, asked, true
}

// scoped runs a lake route's handler under a collection's scope.
func (s *Server) scoped(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		_, scope, ok := s.collectionScope(w, r)
		if !ok {
			return
		}
		next(w, r.WithContext(context.WithValue(r.Context(), scopeKey{}, scope)))
	}
}

// handleCollectionIndicators is the catalogue entry of every series in the
// collection — what each is, its unit and frequency, and the span its figures
// cover — in the order they were filed.
//
// A series filed and since dropped from the lake is left out rather than
// failing the list; `meta.total` counts what is served, and the collection
// itself still names what was filed.
func (s *Server) handleCollectionIndicators(w http.ResponseWriter, r *http.Request) {
	_, scope, ok := s.collectionScope(w, r)
	if !ok {
		return
	}
	catalogue, err := s.lakeCatalogue(r.Context())
	if err != nil {
		internalError(w, s.log, "build catalogue", err)
		return
	}
	byID := make(map[string]Indicator, len(catalogue.series))
	for _, series := range catalogue.series {
		byID[series.IndicatorID] = series
	}
	out := make([]Indicator, 0, len(scope))
	for _, id := range scope {
		if series, ok := byID[id]; ok {
			out = append(out, series)
		}
	}
	writeData(w, out, &Meta{Total: int64(len(out)), Layer: "silver"})
}
