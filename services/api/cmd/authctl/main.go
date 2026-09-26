// Command authctl manages the accounts the portal logs into.
//
// There is no sign-up form anywhere in this system, by design: the portal
// serves one organisation's warehouse, and an account is something somebody
// grants rather than something a visitor takes. This is how it is granted.
//
//	go run ./cmd/authctl create -email you@example.org
//	go run ./cmd/authctl list
//	go run ./cmd/authctl disable -email someone@example.org
//	go run ./cmd/authctl approve -email someone@example.org -role researcher
//	go run ./cmd/authctl token -email you@example.org -name "notebook" -days 90
//
// The password is read from the terminal without echoing unless -password is
// given, which exists for scripts and puts the password in the shell history
// of whoever runs it.
package main

import (
	"bufio"
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"syscall"
	"time"

	"golang.org/x/term"

	"github.com/csis/terusan/services/api/internal/appdb"
	"github.com/csis/terusan/services/api/internal/auth"
	"github.com/csis/terusan/services/api/internal/config"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "authctl:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		return errors.New("usage: authctl <create|list|approve|disable|enable|token> [flags]")
	}

	command, rest := args[0], args[1:]
	flags := flag.NewFlagSet("authctl "+command, flag.ExitOnError)
	path := flags.String("db", "", "the application database (default: APP_DB, then COLLECTIONS_DB)")
	email := flags.String("email", "", "the account's email address")
	password := flags.String("password", "",
		"the password; read from the terminal when this is not given")
	name := flags.String("name", "", "what to call the person")
	role := flags.String("role", auth.RoleResearcher,
		"researcher or admin; guests, who need an end date, are made from the Users page")
	days := flags.Int("days", auth.DefaultTokenDays, "token: days until it expires")
	if err := flags.Parse(rest); err != nil {
		return err
	}

	dbPath := *path
	if dbPath == "" {
		cfg, err := config.Load()
		if err != nil {
			return err
		}
		dbPath = cfg.AppDB
	}
	if dbPath == "" {
		return errors.New("no application database: pass -db, or set APP_DB")
	}

	db, err := appdb.Open(dbPath)
	if err != nil {
		return err
	}
	defer db.Close()

	// The iterations here have to match what the API would use, or a password
	// set from the terminal is stored weaker than the deployment asked for.
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	accounts, err := auth.New(db, auth.Config{Iterations: cfg.Auth.Iterations})
	if err != nil {
		return err
	}

	ctx := context.Background()

	switch command {
	case "create", "set-password":
		if *email == "" {
			return errors.New("-email is required")
		}
		secret := *password
		if secret == "" {
			secret, err = readPassword()
			if err != nil {
				return err
			}
		}
		user, err := accounts.PutUser(ctx, *email, secret, *name, *role)
		if err != nil {
			if errors.Is(err, auth.ErrWeakPassword) {
				return fmt.Errorf("password must be at least %d characters",
					auth.MinPasswordLength)
			}
			return err
		}
		fmt.Printf("%s can now log in (role %s, database %s)\n",
			user.Email, user.Role, db.Path())
		return nil

	case "list":
		users, err := accounts.Users(ctx)
		if err != nil {
			return err
		}
		if len(users) == 0 {
			fmt.Println("no accounts yet")
			return nil
		}
		for _, user := range users {
			last := "never"
			if user.LastLoginAt != nil {
				last = user.LastLoginAt.Format(time.RFC3339)
			}
			state := ""
			if user.Disabled {
				state = " (disabled)"
			} else if user.Status == auth.StatusPending {
				state = " (awaiting approval)"
			}
			fmt.Printf("%-34s %-8s last login %s%s\n", user.Email, user.Role, last, state)
		}
		return nil

	case "disable", "enable":
		if *email == "" {
			return errors.New("-email is required")
		}
		if err := accounts.SetDisabled(ctx, *email, command == "disable"); err != nil {
			if errors.Is(err, auth.ErrNotFound) {
				return fmt.Errorf("no account for %s", *email)
			}
			return err
		}
		fmt.Printf("%s is now %sd\n", *email, command)
		return nil

	case "approve":
		// A registration from the portal, let in from the terminal.
		if *email == "" {
			return errors.New("-email is required")
		}
		user, err := accounts.UserByEmail(ctx, *email)
		if err != nil {
			if errors.Is(err, auth.ErrNotFound) {
				return fmt.Errorf("no account for %s", *email)
			}
			return err
		}
		approved, err := accounts.Approve(ctx, user.ID, *role, nil)
		if err != nil {
			return err
		}
		fmt.Printf("%s is approved as %s\n", approved.Email, approved.Role)
		return nil

	case "token":
		// An API token for a script or another service, printed once. The
		// account's display name flag doubles as the token's label here.
		if *email == "" || *name == "" {
			return errors.New("-email and -name (what the token is for) are required")
		}
		user, err := accounts.UserByEmail(ctx, *email)
		if err != nil {
			if errors.Is(err, auth.ErrNotFound) {
				return fmt.Errorf("no account for %s", *email)
			}
			return err
		}
		secret, token, err := accounts.CreateToken(ctx, user.ID, *name, *days)
		if err != nil {
			return err
		}
		fmt.Fprintf(os.Stderr, "token %q for %s, expires %s — shown once, store it now:\n",
			token.Name, user.Email, token.ExpiresAt.Format(time.RFC3339))
		fmt.Println(secret)
		return nil

	default:
		return fmt.Errorf("unknown command %q: try create, list, approve, disable, enable or token", command)
	}
}

// readPassword asks twice, without echoing, so a typo becomes a retry rather
// than an account nobody can log into.
func readPassword() (string, error) {
	if !term.IsTerminal(syscall.Stdin) {
		// Piped in: read one line, which is what a script would do.
		line, err := bufio.NewReader(os.Stdin).ReadString('\n')
		return strings.TrimRight(line, "\r\n"), err
	}

	fmt.Print("Password: ")
	first, err := term.ReadPassword(syscall.Stdin)
	fmt.Println()
	if err != nil {
		return "", err
	}
	fmt.Print("Again: ")
	second, err := term.ReadPassword(syscall.Stdin)
	fmt.Println()
	if err != nil {
		return "", err
	}
	if string(first) != string(second) {
		return "", errors.New("the two passwords do not match")
	}
	return string(first), nil
}
