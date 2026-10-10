package interp

import (
	cryptorand "crypto/rand"
	"encoding/hex"
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

// overlayMetadata combines visible file keys with explicit virtual directories.
func overlayMetadata(files map[string]string, name string, list bool, directories ...map[string]bool) (string, []string) {
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
	if name == "/" {
		kind = "dir"
	}
	for _, directorySet := range directories {
		for directory := range directorySet {
			if directory == name {
				kind = "dir"
			}
			if strings.HasPrefix(directory, prefix) {
				kind = "dir"
				if list {
					if children == nil {
						children = map[string]bool{}
					}
					child, _, _ := strings.Cut(directory[len(prefix):], "/")
					if child != "" {
						children[child] = true
					}
				}
			}
		}
	}
	var names []string
	for child := range children {
		names = append(names, child)
	}
	sort.Strings(names)
	return kind, names
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
			return nil, fdError(err, ".")
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
				kind, names := overlayMetadata(vm.fsOverlay, filename, name == "listdir", vm.overlayDirs)
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
					return nil, fdError(syscall.ENOENT, filename)
				}
				if kind != "dir" {
					return nil, fdError(syscall.ENOTDIR, filename)
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
				return nil, fdError(syscall.ENOENT, filename)
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
					return nil, fdError(err, filename)
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
					return nil, fdError(err, filename)
				}
				names := make([]Value, len(entries))
				for i, entry := range entries {
					names[i] = entry.Name()
				}
				return &List{Items: names}, nil
			}
		})
	}
	module := &NativeModule{Name: "_os", NS: map[string]Value{
		"getcwd": vm.journaled("_os", "getcwd", getcwd),
		"urandom": bi("urandom", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("urandom", args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw("urandom", kw); err != nil {
				return nil, err
			}
			length, err := wantInt("size", args[0])
			if err != nil {
				return nil, err
			}
			if length < 0 {
				return nil, newError("ValueError", "negative argument not allowed")
			}
			if vm.determin {
				return nil, newError("PermissionError", "secure entropy is unavailable in deterministic mode")
			}
			if err := vm.fuelN(length); err != nil {
				return nil, err
			}
			read := func() (Value, error) {
				data := make([]byte, length)
				if _, err := cryptorand.Read(data); err != nil {
					return nil, newError("OSError", err.Error())
				}
				return hex.EncodeToString(data), nil
			}
			var encoded Value
			if vm.durable {
				encoded, err = vm.effect("_os", "urandom", read)
			} else {
				data := make([]byte, length)
				if _, err := cryptorand.Read(data); err != nil {
					return nil, newError("OSError", err.Error())
				}
				return data, nil
			}
			if err != nil {
				return nil, err
			}
			text, ok := encoded.(string)
			if !ok {
				return nil, newError("ValueError", "invalid entropy journal entry")
			}
			data, err := hex.DecodeString(text)
			if err != nil || int64(len(data)) != length {
				return nil, newError("ValueError", "invalid entropy journal entry")
			}
			return data, nil
		}),
		"kind":     vm.journaled("_os", "kind", operation("kind")),
		"listdir":  vm.journaled("_os", "listdir", operation("listdir")),
		"realpath": vm.journaled("_os", "realpath", operation("realpath")),
		"stat":     vm.journaled("_os", "stat", vm.statOperation(false)),
		"lstat":    vm.journaled("_os", "lstat", vm.statOperation(true)),
		"readlink": vm.journaled("_os", "readlink", bi("readlink", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("readlink", args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw("readlink", kw); err != nil {
				return nil, err
			}
			name, err := wantStr("path", args[0])
			if err != nil {
				return nil, err
			}
			if vm.fsOverlay != nil {
				if err := vm.requireFSLexical("read", name); err != nil {
					return nil, err
				}
				return nil, fdError(syscall.EINVAL, name)
			}
			if _, err := vm.requireFS("read", name); err != nil {
				return nil, err
			}
			target, err := os.Readlink(name)
			if err != nil {
				return nil, fdError(err, name)
			}
			return target, nil
		})),
		"mkdir": bi("mkdir", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("mkdir", args, 1, 2); err != nil {
				return nil, err
			}
			if err := noKw("mkdir", kw); err != nil {
				return nil, err
			}
			name, err := wantStr("path", args[0])
			if err != nil {
				return nil, err
			}
			mode := int64(0o777)
			if len(args) == 2 {
				mode, err = wantInt("mode", args[1])
				if err != nil {
					return nil, err
				}
			}
			if vm.fsOverlay != nil {
				if err := vm.requireFSLexical("write", name); err != nil {
					return nil, err
				}
				clean := overlayPath(name)
				if kind, _ := overlayMetadata(vm.fsOverlay, clean, false, vm.overlayDirs); kind != "" {
					return nil, fdError(syscall.EEXIST, name)
				}
				if kind, _ := overlayMetadata(vm.fsOverlay, path.Dir(clean), false, vm.overlayDirs); kind != "dir" {
					return nil, fdError(syscall.ENOENT, name)
				}
				if vm.overlayDirs == nil {
					vm.overlayDirs = map[string]bool{}
				}
				vm.overlayDirs[clean] = true
				return None, nil
			}
			real, err := vm.requireFS("write", name)
			if err != nil {
				return nil, err
			}
			if err := os.Mkdir(real, os.FileMode(mode&0o7777)); err != nil {
				return nil, fdError(err, name)
			}
			return None, nil
		}),
	}}
	operations, cleanup := vm.makeFDOperations()
	vm.closeFDs = cleanup
	for name, operation := range operations {
		module.NS[name] = operation
	}
	return module
}

func (vm *VM) statOperation(link bool) *Builtin {
	name := "stat"
	if link {
		name = "lstat"
	}
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
		out := NewDict()
		for _, field := range []string{"st_mode", "st_ino", "st_dev", "st_nlink", "st_uid", "st_gid", "st_size", "st_atime_ns", "st_mtime_ns", "st_ctime_ns"} {
			out.SetStr(field, int64(0))
		}
		if vm.fsOverlay != nil {
			if err := vm.requireFSLexical("read", filename); err != nil {
				return nil, err
			}
			kind, _ := overlayMetadata(vm.fsOverlay, filename, false, vm.overlayDirs)
			if kind == "" {
				return nil, fdError(syscall.ENOENT, filename)
			}
			mode := int64(0o100644)
			if kind == "dir" {
				mode = 0o40755
			}
			out.SetStr("st_mode", mode)
			out.SetStr("st_nlink", int64(1))
			for key, contents := range vm.fsOverlay {
				if overlayPath(key) == overlayPath(filename) {
					out.SetStr("st_size", int64(len(contents)))
					break
				}
			}
		} else {
			real, err := vm.requireFS("read", filename)
			if err != nil {
				return nil, err
			}
			var info os.FileInfo
			if link {
				info, err = os.Lstat(filename)
			} else {
				info, err = os.Stat(real)
			}
			if err != nil {
				return nil, fdError(err, filename)
			}
			return fileInfoStat(info), nil
		}
		for _, field := range []string{"st_atime", "st_mtime", "st_ctime"} {
			ns, _ := out.GetStr(field + "_ns")
			out.SetStr(field, float64(ns.(int64))/1e9)
		}
		return out, nil
	})
}
