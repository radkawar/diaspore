package interp

import (
	"errors"
	"os"
	"path"
	"sort"
	"strings"
	"syscall"
)

func overlayPath(name string) string {
	return path.Clean("/" + name)
}

// overlayMetadata derives virtual directories only from visible file keys.
func overlayMetadata(files map[string]string, name string, list bool) (string, []string) {
	name = overlayPath(name)
	prefix := strings.TrimRight(name, "/") + "/"
	kind := ""
	var children map[string]bool
	for key := range files {
		key = overlayPath(key)
		if key == name {
			return "file", nil
		}
		if !strings.HasPrefix(key, prefix) {
			continue
		}
		kind = "dir"
		if list {
			if children == nil {
				children = map[string]bool{}
			}
			child, _, _ := strings.Cut(key[len(prefix):], "/")
			children[child] = true
		}
	}
	var names []string
	for child := range children {
		names = append(names, child)
	}
	sort.Strings(names)
	return kind, names
}

func filesystemError(err error, name string) error {
	if os.IsNotExist(err) {
		return newErrorf("FileNotFoundError", "[Errno 2] No such file or directory: %s", reprString(name))
	}
	if errors.Is(err, syscall.ENOTDIR) {
		return newErrorf("NotADirectoryError", "[Errno 20] Not a directory: %s", reprString(name))
	}
	if os.IsPermission(err) {
		return newErrorf("PermissionError", "[Errno 13] Permission denied: %s", reprString(name))
	}
	return newError("OSError", err.Error())
}

func (vm *VM) makeOSModule() *NativeModule {
	getcwd := bi("getcwd", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount("getcwd", args, 0, 0); err != nil {
			return nil, err
		}
		if err := noKw("getcwd", kw); err != nil {
			return nil, err
		}
		if err := vm.requireCap("fs"); err != nil {
			return nil, err
		}
		if vm.fsOverlay != nil {
			return "/", nil
		}
		cwd, err := os.Getwd()
		if err != nil {
			return nil, filesystemError(err, ".")
		}
		return cwd, nil
	})
	operation := func(name string) *Builtin {
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount(name, args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			filename, err := wantStr("path", args[0])
			if err != nil {
				return nil, err
			}
			if vm.fsOverlay != nil {
				if err := vm.requireFSLexical("read", filename); err != nil {
					return nil, err
				}
				if name == "realpath" {
					return overlayPath(filename), nil
				}
				kind, names := overlayMetadata(vm.fsOverlay, filename, name == "listdir")
				if filename == "" || (kind == "file" && strings.HasSuffix(filename, "/")) {
					kind = ""
				}
				if name == "kind" {
					if kind == "" {
						return None, nil
					}
					return kind, nil
				}
				if kind == "" {
					return nil, filesystemError(os.ErrNotExist, filename)
				}
				if kind != "dir" {
					return nil, filesystemError(syscall.ENOTDIR, filename)
				}
				return strListValue(names), nil
			}
			real, err := vm.requireFS("read", filename)
			if err != nil {
				return nil, err
			}
			if filename == "" && name != "realpath" {
				if name == "kind" {
					return None, nil
				}
				return nil, filesystemError(os.ErrNotExist, filename)
			}
			switch name {
			case "realpath":
				return real, nil
			case "kind":
				info, err := os.Stat(real)
				if os.IsNotExist(err) || errors.Is(err, syscall.ENOTDIR) {
					return None, nil
				}
				if err != nil {
					return nil, filesystemError(err, filename)
				}
				if info.IsDir() {
					return "dir", nil
				}
				if strings.HasSuffix(filename, "/") {
					return None, nil
				}
				if info.Mode().IsRegular() {
					return "file", nil
				}
				return "other", nil
			default:
				entries, err := os.ReadDir(real)
				if err != nil {
					return nil, filesystemError(err, filename)
				}
				names := make([]Value, len(entries))
				for i, entry := range entries {
					names[i] = entry.Name()
				}
				return &List{Items: names}, nil
			}
		})
	}
	return &NativeModule{Name: "_os", NS: map[string]Value{
		"getcwd":   vm.journaled("_os", "getcwd", getcwd),
		"kind":     vm.journaled("_os", "kind", operation("kind")),
		"listdir":  vm.journaled("_os", "listdir", operation("listdir")),
		"realpath": vm.journaled("_os", "realpath", operation("realpath")),
	}}
}
