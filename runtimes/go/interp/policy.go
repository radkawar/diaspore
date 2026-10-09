package interp

import (
	"path/filepath"
	"strings"
)

// Scoped capability grants (spec 13.1). A bare capability grant permits
// everything; a policy entry narrows it to an explicit allowlist.

func addrMatch(pattern, host string, port int64) bool {
	i := strings.LastIndexByte(pattern, ':')
	if i <= 0 {
		return false
	}
	ph, pp := pattern[:i], pattern[i+1:]
	hostOK := ph == "*" || strings.EqualFold(ph, host)
	portOK := pp == "*" || pp == itoa64(port)
	return hostOK && portOK
}

func itoa64(n int64) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	var buf [20]byte
	i := len(buf)
	for n != 0 {
		i--
		buf[i] = byte('0' + (n%10+10)%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

// requireNet checks the net capability plus any scoped policy.
// kind: "listen" | "connect".
func (vm *VM) requireNet(kind, host string, port int64) error {
	if err := vm.requireCap("net"); err != nil {
		return err
	}
	pol, ok := vm.policy["net"]
	if !ok {
		return nil
	}
	for _, p := range pol[kind] {
		if addrMatch(p, host, port) {
			return nil
		}
	}
	return newErrorf("PermissionError",
		"diaspore: net.%s %s:%d not permitted by policy", kind, host, port)
}

// realPath resolves symlinks like os.path.realpath: nonexistent trailing
// components are resolved lexically against the deepest existing prefix.
func realPath(p string) string {
	abs, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	if resolved, err := filepath.EvalSymlinks(abs); err == nil {
		return resolved
	}
	dir, file := filepath.Split(filepath.Clean(abs))
	dir = strings.TrimRight(dir, string(filepath.Separator))
	if dir == "" || dir == abs {
		return abs
	}
	return filepath.Join(realPath(dir), file)
}

// requireFSLexical is requireFS without touching the OS (used with fs
// overlays, where paths are virtual): prefix matching on cleaned paths.
func (vm *VM) requireFSLexical(kind, path string) error {
	if err := vm.requireCap("fs"); err != nil {
		return err
	}
	pol, ok := vm.policy["fs"]
	if !ok {
		return nil
	}
	clean := filepath.Clean(path)
	sep := string(filepath.Separator)
	for _, root := range pol[kind] {
		rroot := filepath.Clean(root)
		if clean == rroot || strings.HasPrefix(clean, strings.TrimRight(rroot, sep)+sep) {
			return nil
		}
	}
	return newErrorf("PermissionError",
		"diaspore: fs.%s %s not permitted by policy", kind, path)
}

// requireFS checks the fs capability plus any scoped policy; it returns the
// resolved real path to operate on. kind: "read" | "write".
func (vm *VM) requireFS(kind, path string) (string, error) {
	if err := vm.requireCap("fs"); err != nil {
		return "", err
	}
	real := realPath(path)
	pol, ok := vm.policy["fs"]
	if !ok {
		return real, nil
	}
	sep := string(filepath.Separator)
	for _, root := range pol[kind] {
		rroot := realPath(root)
		if real == rroot || strings.HasPrefix(real, strings.TrimRight(rroot, sep)+sep) {
			return real, nil
		}
	}
	return "", newErrorf("PermissionError",
		"diaspore: fs.%s %s not permitted by policy", kind, path)
}
