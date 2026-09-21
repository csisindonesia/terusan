//go:build !unix

package runner

import (
	"os/exec"
	"time"
)

// detach is the portable fallback: there is no process group to signal, so a
// cancelled run kills the launcher and trusts it to take its child with it.
func detach(cmd *exec.Cmd) { cmd.WaitDelay = 10 * time.Second }
