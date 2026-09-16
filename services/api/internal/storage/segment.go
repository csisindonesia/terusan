package storage

import (
	"fmt"
	"strings"
	"unicode"
)

// MaxSegmentLength keeps a single path component well under the 255-byte limit
// most filesystems impose, leaving room for a suffix or part number.
const MaxSegmentLength = 180

// ErrUnsafeSegment reports a path component that cannot be used as-is.
type ErrUnsafeSegment struct {
	Segment string
	Reason  string
}

func (e *ErrUnsafeSegment) Error() string {
	return fmt.Sprintf("unsafe path segment %q: %s", e.Segment, e.Reason)
}

// Slugify reduces a value to an ASCII-safe path segment. `=` survives so that
// Hive-style partition segments such as `year=2026` round-trip (program.md §46).
func Slugify(value string) (string, error) {
	var b strings.Builder
	lastDash := false
	for _, r := range strings.ToLower(value) {
		switch {
		case r > unicode.MaxASCII:
			// Drop non-ASCII rather than transliterating: the Python side
			// does the same via NFKD + ascii-ignore.
			continue
		case (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '.' || r == '_' || r == '=' || r == '-':
			if r == '-' {
				if lastDash {
					continue
				}
				lastDash = true
			} else {
				lastDash = false
			}
			b.WriteRune(r)
		default:
			if !lastDash {
				b.WriteRune('-')
				lastDash = true
			}
		}
	}
	out := strings.Trim(b.String(), "-.")
	if len(out) > MaxSegmentLength {
		out = strings.TrimRight(out[:MaxSegmentLength], "-.")
	}
	if out == "" {
		return "", &ErrUnsafeSegment{Segment: value, Reason: "empty after sanitising"}
	}
	return out, nil
}

// CheckSegment validates a segment the caller believes is already safe.
// Rewriting it silently would produce a path that no longer matches what the
// catalog recorded, so this reports an error instead.
func CheckSegment(segment string) error {
	switch segment {
	case "", ".", "..":
		return &ErrUnsafeSegment{Segment: segment, Reason: "not a usable path component"}
	}
	if strings.ContainsAny(segment, `/\`) {
		return &ErrUnsafeSegment{Segment: segment, Reason: "contains a path separator"}
	}
	slug, err := Slugify(segment)
	if err != nil {
		return err
	}
	if slug != segment {
		return &ErrUnsafeSegment{Segment: segment, Reason: "expected " + slug}
	}
	return nil
}
