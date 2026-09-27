package httpapi

import (
	"compress/gzip"
	"net/http"
	"strings"
	"sync"
)

// Compression for the JSON answers.
//
// The series list is 18 MB of JSON with the BPS catalogue in it, and it is
// what half the portal's pages load first; gzipped it is a tenth of that. On
// the NAS, Cloudflare compresses what reaches a browser, but the hop from the
// box to the edge, and every request on a laptop, carries the full size.
//
// Only JSON is compressed, decided when the handler sets its content type: the
// assistant's event stream must reach the reader a piece at a time, and a
// document file is served with ranges that compression would break.

var gzipWriters = sync.Pool{New: func() any {
	w, _ := gzip.NewWriterLevel(nil, gzip.BestSpeed)
	return w
}}

func (s *Server) withCompression(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
			next.ServeHTTP(w, r)
			return
		}
		w.Header().Add("Vary", "Accept-Encoding")
		compressed := &gzipResponse{ResponseWriter: w}
		defer compressed.close()
		next.ServeHTTP(compressed, r)
	})
}

// gzipResponse compresses the body when the response turns out to be JSON,
// and passes everything else through untouched.
type gzipResponse struct {
	http.ResponseWriter
	decided bool
	gz      *gzip.Writer
}

func (g *gzipResponse) decide(status int) {
	if g.decided {
		return
	}
	g.decided = true
	header := g.Header()
	if status == http.StatusOK && header.Get("Content-Encoding") == "" &&
		strings.HasPrefix(header.Get("Content-Type"), "application/json") {
		header.Set("Content-Encoding", "gzip")
		header.Del("Content-Length")
		g.gz = gzipWriters.Get().(*gzip.Writer)
		g.gz.Reset(g.ResponseWriter)
	}
}

func (g *gzipResponse) WriteHeader(status int) {
	g.decide(status)
	g.ResponseWriter.WriteHeader(status)
}

func (g *gzipResponse) Write(p []byte) (int, error) {
	if !g.decided {
		g.WriteHeader(http.StatusOK)
	}
	if g.gz != nil {
		return g.gz.Write(p)
	}
	return g.ResponseWriter.Write(p)
}

// Flush keeps a streamed answer streaming, compressed or not.
func (g *gzipResponse) Flush() {
	if g.gz != nil {
		_ = g.gz.Flush()
	}
	if flusher, ok := g.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

// Unwrap lets http.ResponseController reach the connection beneath, for the
// assistant's write deadline.
func (g *gzipResponse) Unwrap() http.ResponseWriter { return g.ResponseWriter }

func (g *gzipResponse) close() {
	if g.gz != nil {
		_ = g.gz.Close()
		gzipWriters.Put(g.gz)
		g.gz = nil
	}
}
