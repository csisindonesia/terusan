package httpapi

import (
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/csis/terusan/services/api/internal/config"
	"github.com/csis/terusan/services/api/internal/storage"
)

// documentRequest builds a GET against the documents list.
func documentRequest(query string) *http.Request {
	return httptest.NewRequest(http.MethodGet, "/v1/documents?"+query, nil)
}

// serverOn returns a Server whose lake root is root. Only the storage resolver
// is wired: the tests below exercise path handling, which needs no warehouse.
func serverOn(t *testing.T, root string) *Server {
	t.Helper()
	cfg := &storage.Config{
		Profile: storage.ProfileLocal,
		Backend: storage.BackendLocal,
		Root:    root,
	}
	return &Server{
		cfg:     &config.Config{Storage: cfg},
		storage: storage.NewResolver(cfg),
	}
}

// ---- the preserved copy ---------------------------------------------------

func TestCataloguedPathResolvesUnderRaw(t *testing.T) {
	server := serverOn(t, "/lake")
	got, err := server.rawFile("statistics/esdm-heesi/handbook/doc_abc/handbook.pdf")
	if err != nil {
		t.Fatalf("rawFile: %v", err)
	}
	want := filepath.FromSlash("/lake/raw/statistics/esdm-heesi/handbook/doc_abc/handbook.pdf")
	if got != want {
		t.Errorf("rawFile = %q, want %q", got, want)
	}
}

// A catalogued path that escapes RAW is a corrupt catalogue rather than a
// caller's mistake — but a check that only holds while every writer behaves is
// not a check, and the id that selects the row does come from the caller.
func TestPathsEscapingRawAreRefused(t *testing.T) {
	server := serverOn(t, "/lake")
	for _, relative := range []string{
		"../../etc/passwd",
		"statistics/../../../etc/passwd",
		"/etc/passwd",
		"statistics/./../../secrets",
	} {
		got, err := server.rawFile(relative)
		if err == nil {
			t.Errorf("rawFile(%q) = %q, want an error", relative, got)
		}
	}
}

func TestPathsStayingInsideRawAreAllowed(t *testing.T) {
	server := serverOn(t, "/lake")
	// A `..` that does not escape is still a legitimate path.
	if _, err := server.rawFile("statistics/sub/../doc_abc/file.csv"); err != nil {
		t.Errorf("rawFile rejected a path that stays inside RAW: %v", err)
	}
}

// ---- the download header --------------------------------------------------

func TestDownloadNameUsesTheOriginalFilename(t *testing.T) {
	name := "handbook-2025.pdf"
	if got := downloadName(&name, "doc_abc"); got != name {
		t.Errorf("downloadName = %q, want %q", got, name)
	}
}

func TestDownloadNameFallsBackToTheDocumentID(t *testing.T) {
	if got := downloadName(nil, "doc_abc"); got != "doc_abc" {
		t.Errorf("downloadName = %q, want doc_abc", got)
	}
	empty := ""
	if got := downloadName(&empty, "doc_abc"); got != "doc_abc" {
		t.Errorf("downloadName = %q, want doc_abc", got)
	}
}

// The name goes into a quoted header value. Landing slugifies filenames so
// these characters never occur; this is here so that stays true.
func TestDownloadNameCannotBreakOutOfTheHeader(t *testing.T) {
	hostile := "evil\".pdf\r\nX-Injected: 1\r\n"
	got := downloadName(&hostile, "doc_abc")
	for _, forbidden := range []string{`"`, `\`, "\r", "\n"} {
		if strings.Contains(got, forbidden) {
			t.Errorf("downloadName kept %q in %q", forbidden, got)
		}
	}
}

// ---- reading it in the browser --------------------------------------------

func fileRequest(query string) *http.Request {
	return httptest.NewRequest(http.MethodGet, "/v1/documents/doc_abc/file?"+query, nil)
}

func TestAPdfCanBeAskedForInline(t *testing.T) {
	if !servesInline(fileRequest("inline=1"), "application/pdf") {
		t.Error("a PDF asked for inline was refused")
	}
}

// The caller chooses the disposition, never the type. A landed HTML page
// rendered inline in the API's own origin would run whatever script it
// arrived with, so inline is granted for PDFs alone.
func TestOnlyPdfsAreServedInline(t *testing.T) {
	for _, mediaType := range []string{
		"text/html",
		"text/csv",
		"image/svg+xml",
		"application/xml",
		"application/octet-stream",
		"",
	} {
		if servesInline(fileRequest("inline=1"), mediaType) {
			t.Errorf("%q was served inline", mediaType)
		}
	}
}

func TestTheDefaultIsStillAnAttachment(t *testing.T) {
	for _, query := range []string{"", "inline=0", "inline=true", "inline=yes"} {
		if servesInline(fileRequest(query), "application/pdf") {
			t.Errorf("%q served inline without being asked", query)
		}
	}
}
