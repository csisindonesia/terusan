package httpapi

import (
	"errors"
	"fmt"
	"math/big"
	"regexp"
	"strings"
	"unicode"
)

// $filter and $orderby, read into SQL.
//
// Excel does not fetch a whole table and filter it on the reader's machine
// when it can avoid it: Power Query "folds" the steps a reader takes in its
// editor — keep the rows where the province is Aceh, the year after 2019 —
// into the OData request, and expects the service to answer them. A service
// that ignored a `$filter` it did not understand would hand back every row
// labelled as the filtered ones, which is the worst answer there is; one that
// refused everything would make the editor useless. So the comparisons,
// boolean logic and string and date functions Power Query actually writes are
// answered here, and anything else is refused by name (501, as the protocol
// asks), never ignored.
//
// Nothing a caller writes reaches the SQL as text. A property is looked up in
// the entity set's own list and replaced by the expression the server chose
// for it; a literal becomes a bound parameter; the operators and functions are
// a closed set spelled by this file.

// odataUnsupported is a request this service understands and will not answer.
// It becomes a 501 rather than a 400: the request is well-formed OData, and
// the reader should be told the feature is missing rather than that they made
// a mistake.
type odataUnsupported struct{ what string }

func (e odataUnsupported) Error() string { return e.what + " is not supported by this service" }

// The bounds on one expression. A filter Power Query writes for a reader is a
// few hundred characters; these exist so that a crafted one cannot make the
// parser, or the engine, do unbounded work.
const (
	odataMaxFilterLength = 8 << 10
	odataMaxDepth        = 64
	odataMaxArgs         = 1000
)

// odataKind is what an expression evaluates to, as far as the compiler needs
// to know: enough to bind a literal as the type of the column it is compared
// with, and to refuse `contains(Value, 'x')` before the engine does.
type odataKind int

const (
	kindNull odataKind = iota
	kindString
	kindNumber
	kindDate
	kindBool
)

// odataExpr is one compiled piece of an expression: SQL with `?` in it, and
// the values bound to them in order.
type odataExpr struct {
	sql  string
	args []any
	kind odataKind
	// Set on a bare literal, which the comparison binds once it knows the
	// type on the other side.
	literal *odataLiteral
}

type odataLiteral struct {
	text string
	kind odataKind
}

// ---- tokens --------------------------------------------------------------

type odataTokenKind int

const (
	tokEOF odataTokenKind = iota
	tokIdent
	tokString
	tokNumber
	tokDate
	tokDateTime
	tokOpen
	tokClose
	tokComma
	tokSlash
)

type odataToken struct {
	kind odataTokenKind
	text string
}

var (
	odataDatePattern     = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}`)
	odataDateTimePattern = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,12})?)?(Z|[+-]\d{2}:\d{2})`)
	odataNumberPattern   = regexp.MustCompile(`^-?\d+(\.\d+)?([eE][+-]?\d+)?`)
)

// odataTokens splits an expression into tokens.
func odataTokens(input string) ([]odataToken, error) {
	var tokens []odataToken
	for i := 0; i < len(input); {
		c := input[i]
		switch {
		case c == ' ' || c == '\t' || c == '\n' || c == '\r':
			i++
		case c == '(':
			tokens = append(tokens, odataToken{kind: tokOpen})
			i++
		case c == ')':
			tokens = append(tokens, odataToken{kind: tokClose})
			i++
		case c == ',':
			tokens = append(tokens, odataToken{kind: tokComma})
			i++
		case c == '/':
			tokens = append(tokens, odataToken{kind: tokSlash})
			i++
		case c == '\'':
			// A quote inside a string is written twice: 'Jum''at'.
			var b strings.Builder
			j := i + 1
			for {
				if j >= len(input) {
					return nil, errors.New("a string is not closed")
				}
				if input[j] == '\'' {
					if j+1 < len(input) && input[j+1] == '\'' {
						b.WriteByte('\'')
						j += 2
						continue
					}
					break
				}
				b.WriteByte(input[j])
				j++
			}
			tokens = append(tokens, odataToken{kind: tokString, text: b.String()})
			i = j + 1
		case c >= '0' && c <= '9' || c == '-' && i+1 < len(input) && input[i+1] >= '0' && input[i+1] <= '9':
			rest := input[i:]
			// The longest reading wins: 2024-01-01T00:00:00Z is one instant,
			// not a date followed by garbage, and 2024-01-01 is not a
			// subtraction.
			if m := odataDateTimePattern.FindString(rest); m != "" {
				tokens = append(tokens, odataToken{kind: tokDateTime, text: m})
				i += len(m)
				continue
			}
			if m := odataDatePattern.FindString(rest); m != "" {
				tokens = append(tokens, odataToken{kind: tokDate, text: m})
				i += len(m)
				continue
			}
			m := odataNumberPattern.FindString(rest)
			if m == "" {
				return nil, fmt.Errorf("unexpected %q", rest[:1])
			}
			i += len(m)
			// OData 4.0 lets a client mark a literal's type with a suffix —
			// 12.5M for a decimal, 3L for a 64-bit integer. Power Query writes
			// them for older services; the value is the same.
			if i < len(input) && strings.ContainsRune("mMdDfFlL", rune(input[i])) &&
				(i+1 >= len(input) || !isIdentRune(rune(input[i+1]))) {
				i++
			}
			tokens = append(tokens, odataToken{kind: tokNumber, text: m})
		case isIdentStart(rune(c)):
			j := i
			for j < len(input) && isIdentRune(rune(input[j])) {
				j++
			}
			tokens = append(tokens, odataToken{kind: tokIdent, text: input[i:j]})
			i = j
		default:
			return nil, fmt.Errorf("unexpected %q", string(c))
		}
	}
	return append(tokens, odataToken{kind: tokEOF}), nil
}

func isIdentStart(r rune) bool { return r == '_' || r < unicode.MaxASCII && unicode.IsLetter(r) }
func isIdentRune(r rune) bool {
	return isIdentStart(r) || r >= '0' && r <= '9' || r == '.'
}

// ---- parser --------------------------------------------------------------

type odataParser struct {
	tokens []odataToken
	at     int
	props  map[string]odataProperty
	depth  int
	args   int
}

func (p *odataParser) peek() odataToken { return p.tokens[p.at] }
func (p *odataParser) next() odataToken {
	t := p.tokens[p.at]
	if t.kind != tokEOF {
		p.at++
	}
	return t
}

// keyword reports whether the next token is the given word, and takes it.
// Operators are case-sensitive in OData, and lower case.
func (p *odataParser) keyword(word string) bool {
	if t := p.peek(); t.kind == tokIdent && t.text == word {
		p.at++
		return true
	}
	return false
}

func (p *odataParser) expect(kind odataTokenKind, what string) error {
	if p.next().kind != kind {
		return fmt.Errorf("expected %s", what)
	}
	return nil
}

// compileFilter turns a $filter into a SQL condition over the given
// properties.
func compileFilter(input string, props map[string]odataProperty) (string, []any, error) {
	if len(input) > odataMaxFilterLength {
		return "", nil, fmt.Errorf("is longer than %d characters", odataMaxFilterLength)
	}
	tokens, err := odataTokens(input)
	if err != nil {
		return "", nil, err
	}
	p := &odataParser{tokens: tokens, props: props}
	e, err := p.or()
	if err != nil {
		return "", nil, err
	}
	if p.peek().kind != tokEOF {
		return "", nil, errors.New("unexpected text after the expression")
	}
	if e, err = p.condition(e); err != nil {
		return "", nil, err
	}
	return e.sql, e.args, nil
}

// condition insists that what was parsed is true or false, rather than a
// column or a number standing where a test belongs.
func (p *odataParser) condition(e odataExpr) (odataExpr, error) {
	if e.literal != nil {
		var err error
		if e, err = p.bind(e, e.literal.kind); err != nil {
			return e, err
		}
	}
	if e.kind != kindBool {
		return e, errors.New("is not a condition")
	}
	return e, nil
}

func (p *odataParser) or() (odataExpr, error) {
	left, err := p.and()
	if err != nil {
		return left, err
	}
	for p.keyword("or") {
		right, err := p.and()
		if err != nil {
			return right, err
		}
		if left, err = p.logical(left, right, "OR"); err != nil {
			return left, err
		}
	}
	return left, nil
}

func (p *odataParser) and() (odataExpr, error) {
	left, err := p.unary()
	if err != nil {
		return left, err
	}
	for p.keyword("and") {
		right, err := p.unary()
		if err != nil {
			return right, err
		}
		if left, err = p.logical(left, right, "AND"); err != nil {
			return left, err
		}
	}
	return left, nil
}

func (p *odataParser) logical(left, right odataExpr, op string) (odataExpr, error) {
	left, err := p.condition(left)
	if err != nil {
		return left, err
	}
	if right, err = p.condition(right); err != nil {
		return right, err
	}
	return odataExpr{
		sql:  "(" + left.sql + " " + op + " " + right.sql + ")",
		args: append(left.args, right.args...),
		kind: kindBool,
	}, nil
}

func (p *odataParser) unary() (odataExpr, error) {
	if p.keyword("not") {
		p.depth++
		if p.depth > odataMaxDepth {
			return odataExpr{}, errors.New("is nested too deeply")
		}
		inner, err := p.unary()
		p.depth--
		if err != nil {
			return inner, err
		}
		if inner, err = p.condition(inner); err != nil {
			return inner, err
		}
		// `coalesce(…, false)` because a comparison with a null is null, and
		// NOT of null is null again: without it, `not (GeoName eq 'Aceh')`
		// would drop every row with no place at all, where a reader asking
		// for "everything but Aceh" expects them kept.
		return odataExpr{sql: "(NOT coalesce(" + inner.sql + ", false))", args: inner.args, kind: kindBool}, nil
	}
	return p.comparison()
}

var odataComparisons = map[string]string{
	"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<=",
}

func (p *odataParser) comparison() (odataExpr, error) {
	left, err := p.primary()
	if err != nil {
		return left, err
	}
	t := p.peek()
	if t.kind != tokIdent {
		return left, nil
	}
	if t.text == "in" {
		p.next()
		return p.in(left)
	}
	switch t.text {
	case "add", "sub", "mul", "div", "divby", "mod":
		return left, odataUnsupported{"arithmetic (" + t.text + ")"}
	case "has":
		return left, odataUnsupported{"the has operator"}
	}
	op, ok := odataComparisons[t.text]
	if !ok {
		return left, nil
	}
	p.next()
	right, err := p.primary()
	if err != nil {
		return right, err
	}
	return p.compare(left, right, t.text, op)
}

// compare writes one comparison, binding a literal on either side as the type
// of the other.
func (p *odataParser) compare(left, right odataExpr, name, op string) (odataExpr, error) {
	// `eq null` is a question about absence, which SQL spells differently:
	// `x = NULL` is never true. `ne null` likewise.
	leftNull := left.literal != nil && left.literal.kind == kindNull
	rightNull := right.literal != nil && right.literal.kind == kindNull
	if leftNull || rightNull {
		other := left
		if leftNull {
			other = right
		}
		if other.literal != nil {
			// `null eq null`, or a literal against null: a constant.
			other.literal = nil
			other.sql, other.args = "NULL", nil
		}
		switch name {
		case "eq":
			return odataExpr{sql: "(" + other.sql + " IS NULL)", args: other.args, kind: kindBool}, nil
		case "ne":
			return odataExpr{sql: "(" + other.sql + " IS NOT NULL)", args: other.args, kind: kindBool}, nil
		default:
			// Ordering against null is never true, in OData as in SQL.
			return odataExpr{sql: "false", kind: kindBool}, nil
		}
	}

	var err error
	switch {
	case left.literal != nil && right.literal != nil:
		if left, err = p.bind(left, left.literal.kind); err != nil {
			return left, err
		}
		if right, err = p.bind(right, right.literal.kind); err != nil {
			return right, err
		}
	case left.literal != nil:
		if left, err = p.bind(left, right.kind); err != nil {
			return left, err
		}
	case right.literal != nil:
		if right, err = p.bind(right, left.kind); err != nil {
			return right, err
		}
	}
	if !comparable(left.kind, right.kind) {
		return left, fmt.Errorf("%s compares values of different types", name)
	}
	return odataExpr{
		sql:  "(" + left.sql + " " + op + " " + right.sql + ")",
		args: append(left.args, right.args...),
		kind: kindBool,
	}, nil
}

func comparable(a, b odataKind) bool { return a == b || a == kindNull || b == kindNull }

// in is `Property in ('a', 'b')`.
func (p *odataParser) in(left odataExpr) (odataExpr, error) {
	if left.literal != nil {
		return left, errors.New("in needs a property on its left")
	}
	if err := p.expect(tokOpen, "( after in"); err != nil {
		return left, err
	}
	var holes []string
	args := append([]any{}, left.args...)
	for {
		item, err := p.primary()
		if err != nil {
			return item, err
		}
		if item.literal == nil {
			return item, errors.New("in lists literals only")
		}
		if item, err = p.bind(item, left.kind); err != nil {
			return item, err
		}
		holes = append(holes, item.sql)
		args = append(args, item.args...)
		if p.peek().kind == tokComma {
			p.next()
			continue
		}
		break
	}
	if err := p.expect(tokClose, ") to close the in list"); err != nil {
		return left, err
	}
	return odataExpr{
		sql:  "(" + left.sql + " IN (" + strings.Join(holes, ", ") + "))",
		args: args, kind: kindBool,
	}, nil
}

func (p *odataParser) primary() (odataExpr, error) {
	t := p.next()
	switch t.kind {
	case tokOpen:
		p.depth++
		if p.depth > odataMaxDepth {
			return odataExpr{}, errors.New("is nested too deeply")
		}
		inner, err := p.or()
		p.depth--
		if err != nil {
			return inner, err
		}
		if err := p.expect(tokClose, ")"); err != nil {
			return inner, err
		}
		if inner.literal == nil {
			inner.sql = "(" + inner.sql + ")"
		}
		return inner, nil
	case tokString:
		return odataExpr{literal: &odataLiteral{text: t.text, kind: kindString}, kind: kindString}, nil
	case tokNumber:
		return odataExpr{literal: &odataLiteral{text: t.text, kind: kindNumber}, kind: kindNumber}, nil
	case tokDate:
		return odataExpr{literal: &odataLiteral{text: t.text, kind: kindDate}, kind: kindDate}, nil
	case tokDateTime:
		// An instant compared with a date column: Power Query writes these
		// for a date it believes has a time, and a figure dated a day is
		// compared by that day.
		return odataExpr{literal: &odataLiteral{text: t.text[:10], kind: kindDate}, kind: kindDate}, nil
	case tokIdent:
		switch t.text {
		case "true", "false":
			return odataExpr{sql: t.text, kind: kindBool}, nil
		case "null":
			return odataExpr{literal: &odataLiteral{kind: kindNull}, kind: kindNull}, nil
		}
		if p.peek().kind == tokOpen {
			return p.function(t.text)
		}
		if p.peek().kind == tokSlash {
			return odataExpr{}, odataUnsupported{"navigation (" + t.text + "/…)"}
		}
		prop, ok := p.props[t.text]
		if !ok {
			return odataExpr{}, fmt.Errorf("%s is not a property of this entity set", t.text)
		}
		return odataExpr{sql: prop.filterExpr(), kind: prop.kind()}, nil
	case tokEOF:
		return odataExpr{}, errors.New("ends too early")
	}
	return odataExpr{}, errors.New("unexpected punctuation")
}

// odataFunctions are the canonical functions answered, by name: what each
// takes and what it gives back.
var odataFunctions = map[string]struct {
	args []odataKind
	kind odataKind
	sql  func(args []string) string
}{
	"contains":   {[]odataKind{kindString, kindString}, kindBool, func(a []string) string { return "contains(" + a[0] + ", " + a[1] + ")" }},
	"startswith": {[]odataKind{kindString, kindString}, kindBool, func(a []string) string { return "starts_with(" + a[0] + ", " + a[1] + ")" }},
	"endswith":   {[]odataKind{kindString, kindString}, kindBool, func(a []string) string { return "ends_with(" + a[0] + ", " + a[1] + ")" }},
	"tolower":    {[]odataKind{kindString}, kindString, func(a []string) string { return "lower(" + a[0] + ")" }},
	"toupper":    {[]odataKind{kindString}, kindString, func(a []string) string { return "upper(" + a[0] + ")" }},
	"trim":       {[]odataKind{kindString}, kindString, func(a []string) string { return "trim(" + a[0] + ")" }},
	"length":     {[]odataKind{kindString}, kindNumber, func(a []string) string { return "length(" + a[0] + ")" }},
	"year":       {[]odataKind{kindDate}, kindNumber, func(a []string) string { return "year(" + a[0] + ")" }},
	"month":      {[]odataKind{kindDate}, kindNumber, func(a []string) string { return "month(" + a[0] + ")" }},
	"day":        {[]odataKind{kindDate}, kindNumber, func(a []string) string { return "day(" + a[0] + ")" }},
}

func (p *odataParser) function(name string) (odataExpr, error) {
	fn, ok := odataFunctions[name]
	if !ok {
		return odataExpr{}, odataUnsupported{"the function " + name + "()"}
	}
	p.next() // (
	var parts []string
	var args []any
	for i := range fn.args {
		if i > 0 {
			if err := p.expect(tokComma, ", between "+name+"()'s arguments"); err != nil {
				return odataExpr{}, err
			}
		}
		p.depth++
		if p.depth > odataMaxDepth {
			return odataExpr{}, errors.New("is nested too deeply")
		}
		arg, err := p.or()
		p.depth--
		if err != nil {
			return arg, err
		}
		if arg.literal != nil {
			if arg, err = p.bind(arg, fn.args[i]); err != nil {
				return arg, err
			}
		}
		if arg.kind != fn.args[i] {
			return arg, fmt.Errorf("%s() was given an argument of the wrong type", name)
		}
		parts = append(parts, arg.sql)
		args = append(args, arg.args...)
	}
	if err := p.expect(tokClose, ") to close "+name+"()"); err != nil {
		return odataExpr{}, err
	}
	return odataExpr{sql: fn.sql(parts), args: args, kind: fn.kind}, nil
}

// bind turns a literal into a parameter of the given type. The value is only
// ever bound, and the cast around the hole is chosen here from the type, so a
// literal cannot become SQL.
func (p *odataParser) bind(e odataExpr, as odataKind) (odataExpr, error) {
	lit := e.literal
	if lit == nil {
		return e, nil
	}
	p.args++
	if p.args > odataMaxArgs {
		return e, fmt.Errorf("has more than %d values", odataMaxArgs)
	}
	if as == kindNull {
		as = lit.kind
	}
	if lit.kind != as {
		return e, errors.New("compares values of different types")
	}
	switch as {
	case kindString:
		return odataExpr{sql: "?", args: []any{lit.text}, kind: kindString}, nil
	case kindDate:
		return odataExpr{sql: "CAST(? AS DATE)", args: []any{lit.text}, kind: kindDate}, nil
	case kindNumber:
		// As an exact decimal, never a float: `Value eq 0.1` has to match the
		// 0.1 Silver stored, and 0.1 as a double is not that number. The
		// literal is read as a rational and written back out in plain digits,
		// which also accepts the exponent form Power Query sometimes uses.
		r, ok := new(big.Rat).SetString(lit.text)
		if !ok {
			return e, fmt.Errorf("%q is not a number", lit.text)
		}
		scale := 0
		if dot := strings.IndexByte(lit.text, '.'); dot >= 0 {
			digits := lit.text[dot+1:]
			if exp := strings.IndexAny(digits, "eE"); exp >= 0 {
				digits = digits[:exp]
			}
			scale = len(digits)
		}
		if !r.IsInt() && scale == 0 {
			scale = 18 // an exponent that moved the point left
		}
		if scale > 18 {
			scale = 18
		}
		plain := r.FloatString(scale)
		if len(strings.TrimLeft(plain, "-")) > 38 {
			return e, fmt.Errorf("%q has more digits than a figure can", lit.text)
		}
		return odataExpr{
			sql:  fmt.Sprintf("CAST(? AS DECIMAL(38, %d))", scale),
			args: []any{plain}, kind: kindNumber,
		}, nil
	}
	return e, errors.New("cannot compare with null here")
}

// ---- $orderby ------------------------------------------------------------

// compileOrderBy reads `Prop [asc|desc], …` into an ORDER BY clause, with the
// key column last so that no two rows compare equal and the pages do not
// overlap.
func compileOrderBy(input string, props map[string]odataProperty, key string) (string, error) {
	var parts []string
	seen := map[string]bool{}
	for _, item := range strings.Split(input, ",") {
		fields := strings.Fields(item)
		if len(fields) == 0 || len(fields) > 2 {
			return "", fmt.Errorf("%q is not a property and a direction", strings.TrimSpace(item))
		}
		if strings.ContainsAny(fields[0], "(/") {
			return "", odataUnsupported{"ordering by an expression"}
		}
		prop, ok := props[fields[0]]
		if !ok {
			return "", fmt.Errorf("%s is not a property of this entity set", fields[0])
		}
		direction := "ASC"
		if len(fields) == 2 {
			switch fields[1] {
			case "asc":
			case "desc":
				direction = "DESC"
			default:
				return "", fmt.Errorf("%q is not asc or desc", fields[1])
			}
		}
		if seen[prop.Name] {
			continue
		}
		seen[prop.Name] = true
		parts = append(parts, prop.filterExpr()+" "+direction+" NULLS LAST")
	}
	if !seen[key] {
		parts = append(parts, props[key].filterExpr()+" ASC NULLS LAST")
	}
	return strings.Join(parts, ", "), nil
}
