package auth

import (
	"context"
	"database/sql"
	"strings"
	"time"

	"github.com/google/uuid"
)

// The access log: when an account was used, from which address, and — where
// the proxy in front says so — from where.
//
// Not a request log. A page that fires twenty requests is one visit, and a
// row per request would turn every read into a write and bury the one line
// an administrator is looking for ("was this account used from somewhere it
// should not have been?") under thousands that say the same thing. So a
// login is always written, and after that an account's activity is written
// at most once per few minutes per address and kind.

// Client is who a request says it came from.
//
// The address is the connection's unless the deployment trusts the proxy in
// front of it; the location only ever comes from that proxy (Cloudflare's
// CF-IPCountry and, where its visitor-location headers are enabled,
// CF-IPCity and CF-Region). Neither is proof of anything — a proxy header is
// whatever reached the proxy — which is why the log calls it "reported".
type Client struct {
	IP        string
	Country   string
	Region    string
	City      string
	UserAgent string
}

// The kinds of access the log tells apart.
const (
	AccessLogin = "login"
	// A request on a cookie session, i.e. the portal.
	AccessWeb = "web"
	// A request on an API token.
	AccessAPI = "api"
)

// AccessEvent is one row of an account's access log.
type AccessEvent struct {
	ID        string    `json:"id"`
	Kind      string    `json:"kind"`
	At        time.Time `json:"at"`
	IP        string    `json:"ip,omitempty"`
	Country   string    `json:"country,omitempty"`
	Region    string    `json:"region,omitempty"`
	City      string    `json:"city,omitempty"`
	UserAgent string    `json:"user_agent,omitempty"`
}

const accessSchema = `
CREATE TABLE IF NOT EXISTS access_events (
    event_id    TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    kind        TEXT NOT NULL,
    ip          TEXT,
    country     TEXT,
    region      TEXT,
    city        TEXT,
    user_agent  TEXT
);
`

const (
	// How often continuing activity from one address is written down.
	touchEvery = 5 * time.Minute
	// How long the log is kept. Long enough to answer "who was using this
	// account last quarter", short enough that it is not a surveillance
	// archive of everybody's working hours.
	accessRetention = 180 * 24 * time.Hour
)

// RecordAccess writes one row to the access log and moves the account's
// last-active time. Logins call it directly; everything else goes through
// Touch.
func (s *Service) RecordAccess(ctx context.Context, userID, kind string, client Client) error {
	now := time.Now().UTC()
	if _, err := s.db.ExecContext(ctx, `
		INSERT INTO access_events (event_id, user_id, occurred_at, kind, ip, country, region, city, user_agent)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		uuid.NewString(), userID, now, kind,
		nullable(client.IP), nullable(client.Country), nullable(client.Region),
		nullable(client.City), nullable(clip(client.UserAgent, 400))); err != nil {
		return err
	}
	if _, err := s.db.ExecContext(ctx,
		`UPDATE users SET last_active_at = ? WHERE user_id = ?`, now, userID); err != nil {
		return err
	}
	if kind == AccessLogin {
		// Swept at login, like expired sessions: logins are rare enough to be
		// cheap and frequent enough that the table never grows far past the
		// retention window.
		if _, err := s.db.ExecContext(ctx,
			`DELETE FROM access_events WHERE occurred_at < ?`, now.Add(-accessRetention)); err != nil {
			return err
		}
	}
	s.mu.Lock()
	s.touched[touchKey(userID, kind, client.IP)] = now
	s.mu.Unlock()
	return nil
}

// Touch records ongoing use of an account, at most once per touchEvery for
// the same account, kind and address. Called on every signed-in request, so
// it is cheap when it has nothing to write.
func (s *Service) Touch(ctx context.Context, userID, kind string, client Client) error {
	key := touchKey(userID, kind, client.IP)
	now := time.Now()
	s.mu.Lock()
	last, seen := s.touched[key]
	if seen && now.Sub(last) < touchEvery {
		s.mu.Unlock()
		return nil
	}
	// Bounded: a process that has seen a great many addresses forgets them
	// all rather than growing without limit. The cost is one early row each.
	if len(s.touched) > 50_000 {
		s.touched = map[string]time.Time{}
	}
	s.touched[key] = now
	s.mu.Unlock()
	return s.RecordAccess(ctx, userID, kind, client)
}

func touchKey(userID, kind, ip string) string {
	return userID + "|" + kind + "|" + ip
}

// AccessLog is an account's most recent access, newest first.
func (s *Service) AccessLog(ctx context.Context, userID string, limit int) ([]AccessEvent, error) {
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT event_id, kind, occurred_at, ip, country, region, city, user_agent
		FROM access_events WHERE user_id = ?
		ORDER BY occurred_at DESC LIMIT ?`, userID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []AccessEvent{}
	for rows.Next() {
		var event AccessEvent
		var ip, country, region, city, agent sql.NullString
		if err := rows.Scan(&event.ID, &event.Kind, &event.At,
			&ip, &country, &region, &city, &agent); err != nil {
			return nil, err
		}
		event.At = event.At.UTC()
		event.IP, event.Country, event.Region = ip.String, country.String, region.String
		event.City, event.UserAgent = city.String, agent.String
		out = append(out, event)
	}
	return out, rows.Err()
}

func clip(value string, limit int) string {
	value = strings.TrimSpace(value)
	if len(value) > limit {
		return value[:limit]
	}
	return value
}
