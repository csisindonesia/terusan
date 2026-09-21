//go:build unix

package runner

import (
	"os/exec"
	"syscall"
	"time"
)

// detach puts the command in its own process group, and makes cancelling it
// signal the group rather than only the command.
//
// `uv run` is a launcher: the scraper is its child. The signal a plain
// CommandContext sends reaches uv alone, and the child then keeps going with
// nothing waiting on it — still holding the publisher's socket, still able to
// land bytes in RAW, after the job that started it has been reported stopped.
func detach(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		// A negative pid addresses the group. TERM, so a scraper mid-request
		// gets to close what it opened.
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
	}
	// And if it ignores that, the wait stops caring and the process is killed.
	cmd.WaitDelay = 10 * time.Second
}
