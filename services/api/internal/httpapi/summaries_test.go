package httpapi

import (
	"compress/gzip"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func summaryServer() *Server {
	return &Server{log: slog.New(slog.NewTextHandler(io.Discard, nil))}
}

func TestASummaryIsComputedOnceAndHeld(t *testing.T) {
	s := summaryServer()
	var runs atomic.Int32
	route := s.summary("/v1/storage", func(w http.ResponseWriter, r *http.Request) {
		runs.Add(1)
		time.Sleep(50 * time.Millisecond) // a scan of every observation
		writeData(w, []string{"x"}, nil)
	})

	// Ten readers at a cold route start one scan between them.
	var wg sync.WaitGroup
	for range 10 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			recorder := httptest.NewRecorder()
			route(recorder, httptest.NewRequest(http.MethodGet, "/v1/storage", nil))
			if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), `"x"`) {
				t.Errorf("answer = %d %s", recorder.Code, recorder.Body.String())
			}
		}()
	}
	wg.Wait()
	if runs.Load() != 1 {
		t.Errorf("computed %d times for ten cold readers, want once", runs.Load())
	}

	recorder := httptest.NewRecorder()
	route(recorder, httptest.NewRequest(http.MethodGet, "/v1/storage", nil))
	if recorder.Header().Get("X-Summary") != "held" || runs.Load() != 1 {
		t.Errorf("second read: X-Summary %q after %d runs", recorder.Header().Get("X-Summary"), runs.Load())
	}
}

func TestAStaleSummaryIsServedWhileItIsRecomputed(t *testing.T) {
	s := summaryServer()
	var version atomic.Int32
	version.Store(1)
	route := s.summary("/v1/datasets", func(w http.ResponseWriter, r *http.Request) {
		writeData(w, map[string]int32{"version": version.Load()}, nil)
	})
	read := func() *httptest.ResponseRecorder {
		recorder := httptest.NewRecorder()
		route(recorder, httptest.NewRequest(http.MethodGet, "/v1/datasets", nil))
		return recorder
	}
	read()

	// A pipeline ran: the lake changed and the held answer is stale.
	version.Store(2)
	s.staleSummaries()
	stale := read()
	if stale.Header().Get("X-Summary") != "stale" || !strings.Contains(stale.Body.String(), `"version":1`) {
		t.Errorf("after the run: %s %s, want the held answer served at once",
			stale.Header().Get("X-Summary"), stale.Body.String())
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if fresh := read(); strings.Contains(fresh.Body.String(), `"version":2`) {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Error("the recomputed answer never replaced the stale one")
}

func TestAFailedSummaryIsNotHeld(t *testing.T) {
	s := summaryServer()
	var runs atomic.Int32
	route := s.summary("/v1/indicators", func(w http.ResponseWriter, r *http.Request) {
		runs.Add(1)
		http.Error(w, "warehouse down", http.StatusInternalServerError)
	})
	for range 2 {
		recorder := httptest.NewRecorder()
		route(recorder, httptest.NewRequest(http.MethodGet, "/v1/indicators", nil))
		if recorder.Code != http.StatusInternalServerError {
			t.Errorf("status = %d, want the failure passed through", recorder.Code)
		}
	}
	if runs.Load() < 2 {
		t.Error("a failure was held and served again")
	}
}

func TestJSONIsGzippedAndAnEventStreamIsNot(t *testing.T) {
	s := summaryServer()
	handler := s.withCompression(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/stream" {
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = w.Write([]byte("data: {}\n\n"))
			return
		}
		writeData(w, strings.Repeat("figure ", 1000), nil)
	}))

	request := httptest.NewRequest(http.MethodGet, "/v1/indicators", nil)
	request.Header.Set("Accept-Encoding", "gzip, br")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Header().Get("Content-Encoding") != "gzip" {
		t.Fatalf("JSON not compressed: %v", recorder.Header())
	}
	reader, err := gzip.NewReader(recorder.Body)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(reader)
	if !strings.Contains(string(body), "figure figure") {
		t.Errorf("decompressed body = %.80q", body)
	}

	stream := httptest.NewRequest(http.MethodGet, "/stream", nil)
	stream.Header.Set("Accept-Encoding", "gzip")
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, stream)
	if recorder.Header().Get("Content-Encoding") != "" || recorder.Body.String() != "data: {}\n\n" {
		t.Errorf("event stream altered: %v %q", recorder.Header(), recorder.Body.String())
	}
}
