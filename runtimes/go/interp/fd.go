package interp

import (
	"errors"
	"io"
	"math"
	"math/big"
	"os"
	"reflect"
	"strings"
	"syscall"
)

// Guest descriptors never contain host descriptor numbers. Even inheritable
// guest descriptors retain the host's close-on-exec protection.
type guestFD struct {
	file        *os.File
	memory      *strings.Reader
	inheritable bool
	readable    bool
	writable    bool
	directory   bool
}

func fdInteger(vm *VM, value Value) (int64, error) {
	value = rawPrim(value)
	if inst, ok := value.(*Instance); ok {
		if _, found := inst.Class.Lookup("__index__"); found {
			v, err := vm.callDunder(inst, "__index__", nil)
			if err != nil {
				return 0, err
			}
			value = rawPrim(v)
			if _, ok := asInt(value); !ok {
				if _, ok := value.(*big.Int); !ok {
					return 0, newErrorf("TypeError", "__index__ returned non-int (type %s)", typeName(value))
				}
			}
		}
	}
	if n, ok := asInt(value); ok {
		return n, nil
	}
	if n, ok := value.(*big.Int); ok {
		if !n.IsInt64() {
			return 0, newError("OverflowError", "Python int too large to convert to C long long")
		}
		return n.Int64(), nil
	}
	return 0, newErrorf("TypeError", "'%s' object cannot be interpreted as an integer", typeName(value))
}

func fdPath(vm *VM, value Value) (string, error) {
	value = rawPrim(value)
	if inst, ok := value.(*Instance); ok {
		if _, found := inst.Class.Lookup("__fspath__"); found {
			v, err := vm.callDunder(inst, "__fspath__", nil)
			if err != nil {
				return "", err
			}
			value = rawPrim(v)
		}
	}
	var name string
	switch v := value.(type) {
	case string:
		name = v
	case []byte:
		name = string(v)
	default:
		return "", newErrorf("TypeError", "expected str, bytes or os.PathLike object, not %s", typeName(value))
	}
	if strings.IndexByte(name, 0) >= 0 {
		return "", newError("ValueError", "embedded null byte")
	}
	return name, nil
}

func fdError(err error, filename Value) error {
	var errno syscall.Errno
	if !errors.As(err, &errno) {
		if errors.Is(err, os.ErrClosed) {
			errno = syscall.EBADF
		} else {
			return newError("OSError", err.Error())
		}
	}
	kind := "OSError"
	switch errno {
	case syscall.EACCES, syscall.EPERM:
		kind = "PermissionError"
	case syscall.ENOENT:
		kind = "FileNotFoundError"
	case syscall.EEXIST:
		kind = "FileExistsError"
	case syscall.EISDIR:
		kind = "IsADirectoryError"
	case syscall.ENOTDIR:
		kind = "NotADirectoryError"
	case syscall.EAGAIN:
		kind = "BlockingIOError"
	case syscall.EINTR:
		kind = "InterruptedError"
	case syscall.EPIPE:
		kind = "BrokenPipeError"
	}
	message := errno.Error()
	if len(message) > 0 {
		message = strings.ToUpper(message[:1]) + message[1:]
	}
	exc := makeExc(kind, int64(errno), message)
	exc.dictSet("errno", int64(errno))
	exc.dictSet("strerror", message)
	exc.dictSet("filename", filename)
	exc.dictSet("filename2", None)
	return &Raised{Exc: exc}
}

func statUnsigned(n uint64) Value {
	if n <= math.MaxInt64 {
		return int64(n)
	}
	return new(big.Int).SetUint64(n)
}

// fileInfoStat shares the host metadata mapping used by stat and fstat.
func fileInfoStat(info os.FileInfo) *Dict {
	out := NewDict()
	out.SetStr("st_size", info.Size())
	out.SetStr("st_mtime_ns", info.ModTime().UnixNano())
	stat := reflect.ValueOf(info.Sys())
	if stat.Kind() == reflect.Pointer {
		stat = stat.Elem()
	}
	if stat.IsValid() && stat.Kind() == reflect.Struct {
		for _, field := range []struct{ host, guest string }{
			{"Mode", "st_mode"}, {"Ino", "st_ino"}, {"Dev", "st_dev"},
			{"Nlink", "st_nlink"}, {"Uid", "st_uid"}, {"Gid", "st_gid"},
			{"Rdev", "st_rdev"}, {"Blksize", "st_blksize"}, {"Blocks", "st_blocks"},
		} {
			v := stat.FieldByName(field.host)
			if v.IsValid() && v.CanUint() {
				out.SetStr(field.guest, statUnsigned(v.Uint()))
			} else if v.IsValid() && v.CanInt() {
				out.SetStr(field.guest, v.Int())
			}
		}
		for _, field := range []struct{ host, guest string }{
			{"Atim", "st_atime_ns"}, {"Mtim", "st_mtime_ns"}, {"Ctim", "st_ctime_ns"},
			{"Atimespec", "st_atime_ns"}, {"Mtimespec", "st_mtime_ns"}, {"Ctimespec", "st_ctime_ns"},
		} {
			v := stat.FieldByName(field.host)
			if v.IsValid() && v.Kind() == reflect.Struct {
				sec, nano := v.FieldByName("Sec"), v.FieldByName("Nsec")
				if sec.IsValid() && nano.IsValid() && sec.CanInt() && nano.CanInt() {
					seconds, nanos := sec.Int(), nano.Int()
					if seconds >= math.MinInt64/1000000000 && seconds <= (math.MaxInt64-nanos)/1000000000 {
						out.SetStr(field.guest, seconds*1000000000+nanos)
					} else {
						ns := new(big.Int).Mul(big.NewInt(seconds), big.NewInt(1000000000))
						ns.Add(ns, big.NewInt(nanos))
						out.SetStr(field.guest, ns)
					}
				}
			}
		}
	}
	for _, field := range []string{"st_atime", "st_mtime", "st_ctime"} {
		if ns, ok := out.GetStr(field + "_ns"); ok {
			seconds, _ := asFloat(ns)
			out.SetStr(field, seconds/1e9)
		}
	}
	return out
}

func memoryFDStat(size int64, directory bool) *Dict {
	out := NewDict()
	// Overlays have no host inode or clock; this is their virtual metadata,
	// matching the read-only files exposed by the path-based overlay API.
	for _, field := range []string{"st_ino", "st_dev", "st_uid", "st_gid", "st_atime_ns", "st_mtime_ns", "st_ctime_ns"} {
		out.SetStr(field, int64(0))
	}
	out.SetStr("st_mode", int64(0o100644))
	if directory {
		out.SetStr("st_mode", int64(0o40755))
	}
	out.SetStr("st_nlink", int64(1))
	out.SetStr("st_size", size)
	for _, field := range []string{"st_atime", "st_mtime", "st_ctime"} {
		out.SetStr(field, float64(0))
	}
	return out
}

func (vm *VM) makeFDOperations() (map[string]Value, func()) {
	table := make(map[int64]*guestFD)
	nextFD := int64(3)
	lookup := func(value Value) (int64, *guestFD, error) {
		n, err := fdInteger(vm, value)
		if err != nil {
			return 0, nil, err
		}
		if n < math.MinInt32 || n > math.MaxInt32 {
			return 0, nil, newError("OverflowError", "Python int too large to convert to C int")
		}
		fd := table[n]
		if fd == nil {
			return n, nil, fdError(syscall.EBADF, None)
		}
		return n, fd, nil
	}
	nonblockFlag, nofollowFlag, extraFlagsSupported := hostFDExtraFlags()
	operations := map[string]Value{
		"O_RDONLY": int64(os.O_RDONLY), "O_WRONLY": int64(os.O_WRONLY), "O_RDWR": int64(os.O_RDWR),
		"O_APPEND": int64(os.O_APPEND), "O_CREAT": int64(os.O_CREATE), "O_EXCL": int64(os.O_EXCL),
		"O_TRUNC": int64(os.O_TRUNC), "O_SYNC": int64(os.O_SYNC), "O_CLOEXEC": int64(syscall.O_CLOEXEC),
		"O_DIRECTORY": int64(hostDirectoryFlag),
		"O_NONBLOCK":  int64(nonblockFlag), "O_NOFOLLOW": int64(nofollowFlag),
		"SEEK_SET": int64(io.SeekStart), "SEEK_CUR": int64(io.SeekCurrent), "SEEK_END": int64(io.SeekEnd),
	}
	operations["open"] = bi("open", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount("open", args, 0, 3); err != nil {
			return nil, err
		}
		var values [3]Value
		var supplied [3]bool
		for i, value := range args {
			values[i], supplied[i] = value, true
		}
		for _, k := range kw {
			i := -1
			switch k.Name {
			case "path":
				i = 0
			case "flags":
				i = 1
			case "mode":
				i = 2
			default:
				return nil, newErrorf("TypeError", "'%s' is an invalid keyword argument for open()", k.Name)
			}
			if supplied[i] {
				return nil, newErrorf("TypeError", "argument for open() given by name ('%s') and position", k.Name)
			}
			values[i], supplied[i] = k.Val, true
		}
		if !supplied[0] || !supplied[1] {
			return nil, newError("TypeError", "open() requires path and flags")
		}
		name, err := fdPath(vm, values[0])
		if err != nil {
			return nil, err
		}
		flags, err := fdInteger(vm, values[1])
		if err != nil {
			return nil, err
		}
		if flags < math.MinInt32 || flags > math.MaxInt32 {
			return nil, newError("OverflowError", "Python int too large to convert to C int")
		}
		mode := int64(0o777)
		if supplied[2] {
			mode, err = fdInteger(vm, values[2])
			if err != nil {
				return nil, err
			}
			if mode < math.MinInt32 || mode > math.MaxInt32 {
				return nil, newError("OverflowError", "Python int too large to convert to C int")
			}
		}
		access := int(flags) & 3
		readable := access == os.O_RDONLY || access == os.O_RDWR
		writable := access == os.O_WRONLY || access == os.O_RDWR
		changes := writable || int(flags)&(os.O_CREATE|os.O_TRUNC|os.O_APPEND) != 0
		content, virtual := vm.virtualFiles[name]
		directory := false
		if vm.fsOverlay != nil {
			content, virtual = vm.fsOverlay[name]
			kind, _ := overlayMetadata(vm.fsOverlay, name, false, vm.overlayDirs)
			directory = kind == "dir"
			virtual = virtual || directory
		}
		lexical := vm.fsOverlay != nil || virtual
		hostName := name
		check := func(kind string) error {
			if lexical {
				return vm.requireFSLexical(kind, name)
			}
			var err error
			hostName, err = vm.requireFS(kind, hostName)
			return err
		}
		if readable || !changes {
			if err := check("read"); err != nil {
				return nil, err
			}
		}
		if changes {
			if err := check("write"); err != nil {
				return nil, err
			}
		}
		if name == "" {
			return nil, fdError(syscall.ENOENT, values[0])
		}
		if access == 3 && lexical {
			return nil, fdError(syscall.EINVAL, values[0])
		}
		supported := 3 | os.O_APPEND | os.O_CREATE | os.O_EXCL | os.O_TRUNC | os.O_SYNC | syscall.O_CLOEXEC | hostDirectoryFlag | nonblockFlag | nofollowFlag
		if int(flags)&^supported != 0 || (!lexical && !extraFlagsSupported && int(flags)&(nonblockFlag|nofollowFlag) != 0) {
			return nil, fdError(syscall.EINVAL, values[0])
		}
		fd := &guestFD{readable: readable, writable: writable, directory: directory}
		if lexical {
			if int(flags)&hostDirectoryFlag != 0 && virtual && !directory {
				return nil, fdError(syscall.ENOTDIR, values[0])
			}
			if virtual && int(flags)&(os.O_CREATE|os.O_EXCL) == os.O_CREATE|os.O_EXCL {
				return nil, fdError(syscall.EEXIST, values[0])
			}
			if directory && (writable || int(flags)&(os.O_CREATE|os.O_TRUNC) != 0) {
				return nil, fdError(syscall.EISDIR, values[0])
			}
			if changes {
				return nil, fdError(syscall.EROFS, values[0])
			}
			if !virtual {
				return nil, fdError(syscall.ENOENT, values[0])
			}
			fd.memory = strings.NewReader(content)
		} else {
			permissions := os.FileMode(mode) & os.ModePerm
			if mode&0o4000 != 0 {
				permissions |= os.ModeSetuid
			}
			if mode&0o2000 != 0 {
				permissions |= os.ModeSetgid
			}
			if mode&0o1000 != 0 {
				permissions |= os.ModeSticky
			}
			// Open the capability-checked canonical path, as builtin open does.
			// Inspect the final symlink first for flags that must not follow it.
			exclusive := int(flags)&(os.O_CREATE|os.O_EXCL) == os.O_CREATE|os.O_EXCL
			if strings.HasSuffix(name, "/") && !strings.HasSuffix(hostName, "/") {
				hostName += "/"
			}
			vm.blocking(func() {
				if exclusive || int(flags)&nofollowFlag != 0 {
					info, statErr := os.Lstat(name)
					if statErr != nil && !os.IsNotExist(statErr) {
						err = statErr
						return
					}
					if statErr == nil && info.Mode()&os.ModeSymlink != 0 {
						err = syscall.ELOOP
						if exclusive {
							err = syscall.EEXIST
						}
						return
					}
				}
				fd.file, err = os.OpenFile(hostName, int(flags), permissions)
			})
			if err != nil {
				return nil, fdError(err, values[0])
			}
		}
		if nextFD > math.MaxInt32 {
			if fd.file != nil {
				fd.file.Close()
			}
			return nil, fdError(syscall.EMFILE, None)
		}
		n := nextFD
		table[n] = fd
		for table[nextFD] != nil {
			nextFD++
		}
		return n, nil
	})
	for _, name := range []string{"read", "write", "lseek", "ftruncate", "close", "fstat", "isatty", "set_inheritable", "get_inheritable"} {
		operations[name] = bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			arity := 1
			switch name {
			case "read", "write", "ftruncate", "set_inheritable":
				arity = 2
			case "lseek":
				arity = 3
			}
			if err := argCount(name, args, arity, arity); err != nil {
				return nil, err
			}
			if err := noKw(name, kw); err != nil {
				return nil, err
			}
			n, fd, err := lookup(args[0])
			if err != nil {
				if name == "isatty" {
					if r, ok := err.(*Raised); ok {
						if e, ok := r.Exc.(*Instance); ok && e.dictValue("errno") == int64(syscall.EBADF) {
							return false, nil
						}
					}
				}
				return nil, err
			}
			switch name {
			case "close":
				delete(table, n)
				if n < nextFD {
					nextFD = n
				}
				if fd.file != nil {
					vm.blocking(func() { err = fd.file.Close() })
					if err != nil {
						return nil, fdError(err, None)
					}
				}
				return None, nil
			case "read":
				count, err := fdInteger(vm, args[1])
				if err != nil {
					return nil, err
				}
				if count < 0 {
					return nil, fdError(syscall.EINVAL, None)
				}
				if count > int64(int(^uint(0)>>1)) {
					return nil, newError("OverflowError", "Python int too large to convert to C ssize_t")
				}
				if !fd.readable {
					return nil, fdError(syscall.EBADF, None)
				}
				if fd.directory {
					return nil, fdError(syscall.EISDIR, None)
				}
				if fd.memory != nil && count > int64(fd.memory.Len()) {
					count = int64(fd.memory.Len())
				}
				data := make([]byte, int(count))
				var got int
				if fd.memory != nil {
					got, err = fd.memory.Read(data)
				} else {
					vm.blocking(func() { got, err = fd.file.Read(data) })
				}
				if err != nil && err != io.EOF && got == 0 {
					return nil, fdError(err, None)
				}
				return data[:got], nil
			case "write":
				value := rawPrim(args[1])
				data, err := contiguousBufferBytes(value)
				if err != nil {
					return nil, err
				}
				mutable := false
				switch buffer := value.(type) {
				case *MemoryView:
					_, mutable = buffer.owner.(*ByteArray)
				case *ByteArray:
					mutable = true
				}
				if !fd.writable {
					return nil, fdError(syscall.EBADF, None)
				}
				// Snapshot mutable storage before releasing the interpreter lock.
				if mutable {
					data = append([]byte(nil), data...)
				}
				var wrote int
				vm.blocking(func() { wrote, err = fd.file.Write(data) })
				if err != nil && wrote == 0 {
					return nil, fdError(err, None)
				}
				return int64(wrote), nil
			case "lseek":
				offset, err := fdInteger(vm, args[1])
				if err != nil {
					return nil, err
				}
				whence, err := fdInteger(vm, args[2])
				if err != nil {
					return nil, err
				}
				if whence < math.MinInt32 || whence > math.MaxInt32 {
					return nil, newError("OverflowError", "Python int too large to convert to C int")
				}
				var position int64
				if fd.memory != nil {
					position, err = fd.memory.Seek(offset, int(whence))
					if err != nil {
						return nil, fdError(syscall.EINVAL, None)
					}
				} else {
					vm.blocking(func() { position, err = fd.file.Seek(offset, int(whence)) })
					if err != nil {
						return nil, fdError(err, None)
					}
				}
				return position, nil
			case "ftruncate":
				length, err := fdInteger(vm, args[1])
				if err != nil {
					return nil, err
				}
				if !fd.writable || length < 0 {
					return nil, fdError(syscall.EINVAL, None)
				}
				vm.blocking(func() { err = fd.file.Truncate(length) })
				if err != nil {
					return nil, fdError(err, None)
				}
				return None, nil
			case "fstat":
				if fd.memory != nil {
					return memoryFDStat(fd.memory.Size(), fd.directory), nil
				}
				var info os.FileInfo
				vm.blocking(func() { info, err = fd.file.Stat() })
				if err != nil {
					return nil, fdError(err, None)
				}
				return fileInfoStat(info), nil
			case "isatty":
				if fd.memory != nil {
					return false, nil
				}
				return hostFileIsTTY(fd.file)
			case "get_inheritable":
				return fd.inheritable, nil
			default:
				value, err := fdInteger(vm, args[1])
				if err != nil {
					return nil, err
				}
				if value < math.MinInt32 || value > math.MaxInt32 {
					return nil, newError("OverflowError", "Python int too large to convert to C int")
				}
				fd.inheritable = value != 0
				return None, nil
			}
		})
	}
	cleanup := func() {
		for n, fd := range table {
			if fd.file != nil {
				fd.file.Close()
			}
			delete(table, n)
		}
		nextFD = 3
	}
	return operations, cleanup
}
