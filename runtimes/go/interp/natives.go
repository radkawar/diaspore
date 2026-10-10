package interp

import (
	"bufio"
	"io"
	"math"
	"math/big"
	"net"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// File is a diaspore file object created by open() (capability "fs").
type File struct {
	f      *os.File
	r      *bufio.Reader
	binary bool
	closed bool
	mem    bool // overlay-backed: no OS handle
}

// newMemFile wraps in-memory content (fs overlay) as a read-only File.
func newMemFile(content string, binary bool) *File {
	return &File{r: bufio.NewReader(strings.NewReader(content)), binary: binary, mem: true}
}

func (f *File) close() {
	if !f.closed {
		if !f.mem {
			f.f.Close()
		}
		f.closed = true
	}
}

func (f *File) method(n string) (Value, error) {
	switch n {
	case "read":
		return bi("read", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs("read", args, 0, 1); err != nil {
				return nil, err
			}
			if f.closed {
				return nil, newError("ValueError", "I/O operation on closed file.")
			}
			n := int64(-1)
			if len(args) == 1 {
				v, ok := asInt(args[0])
				if !ok {
					return nil, newErrorf("TypeError",
						"argument should be integer or None, not '%s'", typeName(args[0]))
				}
				n = v
			}
			var data []byte
			var err error
			if n < 0 {
				data, err = io.ReadAll(f.r)
			} else {
				data = make([]byte, n)
				var got int
				got, err = io.ReadFull(f.r, data)
				data = data[:got]
				if err == io.EOF || err == io.ErrUnexpectedEOF {
					err = nil
				}
			}
			if err != nil {
				return nil, newError("OSError", err.Error())
			}
			if f.binary {
				return data, nil
			}
			return string(data), nil
		}), nil
	case "readline":
		return bi("readline", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if f.closed {
				return nil, newError("ValueError", "I/O operation on closed file.")
			}
			line, err := f.r.ReadBytes('\n')
			if err != nil && err != io.EOF {
				return nil, newError("OSError", err.Error())
			}
			if f.binary {
				return line, nil
			}
			return string(line), nil
		}), nil
	case "write":
		return bi("write", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := needArgs("write", args, 1, 1); err != nil {
				return nil, err
			}
			if f.closed {
				return nil, newError("ValueError", "I/O operation on closed file.")
			}
			if f.mem {
				return nil, newError("OSError", "overlay files are read-only")
			}
			if f.binary {
				data, ok := args[0].([]byte)
				if !ok {
					return nil, newErrorf("TypeError",
						"a bytes-like object is required, not '%s'", typeName(args[0]))
				}
				if _, err := f.f.Write(data); err != nil {
					return nil, newError("OSError", err.Error())
				}
				return int64(len(data)), nil
			}
			s, ok := args[0].(string)
			if !ok {
				return nil, newErrorf("TypeError",
					"write() argument must be str, not %s", typeName(args[0]))
			}
			if _, err := f.f.WriteString(s); err != nil {
				return nil, newError("OSError", err.Error())
			}
			return int64(pyRuneCount(s)), nil
		}), nil
	case "close":
		return bi("close", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			f.close()
			return None, nil
		}), nil
	}
	return nil, newErrorf("AttributeError", "'file' object has no attribute '%s'", n)
}

func biOpen(vm *VM, args []Value, kw []KwArg) (Value, error) {
	if err := argCount("open", args, 1, 2); err != nil {
		return nil, err
	}
	path, err := wantStr("path", args[0])
	if err != nil {
		return nil, err
	}
	mode := "r"
	if len(args) == 2 {
		m, err := wantStr("mode", args[1])
		if err != nil {
			return nil, err
		}
		mode = m
	}
	if v, ok := kwGet(kw, "mode"); ok {
		m, err := wantStr("mode", v)
		if err != nil {
			return nil, err
		}
		mode = m
	}
	var flags int
	kind := "write"
	switch mode {
	case "r", "rb":
		flags = os.O_RDONLY
		kind = "read"
	case "w", "wb":
		flags = os.O_WRONLY | os.O_CREATE | os.O_TRUNC
	case "a":
		flags = os.O_WRONLY | os.O_CREATE | os.O_APPEND
	default:
		return nil, newErrorf("ValueError", "invalid mode: '%s'", mode)
	}
	if encoding, ok := kwGet(kw, "encoding"); ok {
		if _, none := encoding.(NoneType); !none {
			if strings.Contains(mode, "b") {
				return nil, newError("ValueError", "binary mode doesn't take an encoding argument")
			}
			name, err := wantStr("encoding", encoding)
			if err != nil {
				return nil, err
			}
			switch strings.ToLower(name) {
			case "utf8", "utf-8", "utf_8":
			default:
				return nil, newError("NotImplementedError", "only UTF-8 text is supported")
			}
		}
	}
	if vm.fsOverlay != nil {
		// The overlay replaces the filesystem entirely (wasm hosts have
		// none); policy matching is lexical - no OS resolution.
		if err := vm.requireFSLexical(kind, path); err != nil {
			return nil, err
		}
		if kind == "read" {
			if content, ok := vm.fsOverlay[path]; ok {
				return newMemFile(content, mode == "rb"), nil
			}
		}
		return nil, newErrorf("OSError",
			"[Errno 2] No such file or directory: %s", reprString(path))
	}
	if content, ok := vm.virtualFiles[path]; ok && kind == "read" {
		if err := vm.requireFSLexical(kind, path); err != nil {
			return nil, err
		}
		return newMemFile(content, mode == "rb"), nil
	}
	real, err := vm.requireFS(kind, path)
	if err != nil {
		return nil, err
	}
	f, err2 := os.OpenFile(real, flags, 0o644)
	if err2 != nil {
		if os.IsNotExist(err2) {
			return nil, newErrorf("OSError",
				"[Errno 2] No such file or directory: %s", reprString(path))
		}
		return nil, newError("OSError", err2.Error())
	}
	return &File{f: f, r: bufio.NewReader(f), binary: mode == "rb" || mode == "wb"}, nil
}

func (vm *VM) makeNativeModules() map[string]*NativeModule {
	modules := map[string]*NativeModule{
		"time":             vm.makeTimeModule(),
		"math":             vm.makeMathModule(),
		"sys":              vm.makeSysModule(),
		"net":              vm.makeNetModule(),
		"re":               vm.makeReModule(),
		"tasks":            vm.makeTasksModule(),
		"flow":             vm.makeFlowModule(),
		"_os":              vm.makeOSModule(),
		"_json":            vm.makeJSONAcceleratorModule(),
		"_abc":             vm.makeABCModule(),
		"_operator":        vm.makeOperatorModule(),
		"_diaspore_stdlib": vm.makeStdlibModule(),
		"_diaspore_hash":   vm.makeHashModule(),
		"builtins":         {Name: "builtins", NS: vm.builtins},
	}
	for name, module := range modules {
		module.NS["__name__"] = name
		module.NS["__doc__"] = None
		docs := nativeFunctionDocumentation[name]
		for _, value := range module.NS {
			if function, ok := value.(*Builtin); ok {
				function.NoBind = true
				if doc, declared := docs[function.Name]; declared {
					function.doc = doc
				}
			}
		}
	}
	return modules
}

// round6 mirrors diaspore-py's round(x, 6) on the deterministic logical clock.
func round6(x float64) float64 {
	s := strconv.FormatFloat(x, 'f', 6, 64)
	r, _ := strconv.ParseFloat(s, 64)
	return r
}

func (vm *VM) makeTimeModule() *NativeModule {
	var timeFn, monoFn, sleepFn *Builtin
	if vm.determin {
		clock := new(float64)
		tick := func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			*clock = round6(*clock + 0.001)
			return *clock, nil
		}
		timeFn = bi("time", tick)
		monoFn = bi("monotonic", tick)
		sleepFn = bi("sleep", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("sleep", args, 1, 1); err != nil {
				return nil, err
			}
			secs, ok := asFloat(args[0])
			if !ok {
				return nil, newError("TypeError", "an integer or float is required")
			}
			*clock = round6(*clock + secs)
			return None, nil
		})
	} else {
		timeFn = bi("time", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return float64(time.Now().UnixNano()) / 1e9, nil
		})
		monoFn = bi("monotonic", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return float64(time.Since(startTime)) / 1e9, nil
		})
		sleepFn = bi("sleep", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("sleep", args, 1, 1); err != nil {
				return nil, err
			}
			secs, ok := asFloat(args[0])
			if !ok {
				return nil, newError("TypeError", "an integer or float is required")
			}
			if secs < 0 {
				return nil, newError("ValueError", "sleep length must be non-negative")
			}
			vm.blocking(func() {
				time.Sleep(time.Duration(secs * float64(time.Second)))
			})
			return None, nil
		})
	}
	perfFn := bi("perf_counter", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := noKw("time.perf_counter", kw); err != nil {
			return nil, err
		}
		if len(args) != 0 {
			return nil, newErrorf("TypeError", "time.perf_counter() takes no arguments (%d given)", len(args))
		}
		return monoFn.Fn(vm, nil, nil)
	})
	return &NativeModule{Name: "time", NS: map[string]Value{
		"time":         vm.journaled("time", "time", timeFn),
		"monotonic":    vm.journaled("time", "monotonic", monoFn),
		"perf_counter": vm.journaled("time", "perf_counter", perfFn),
		"sleep":        vm.journaled("time", "sleep", sleepFn),
	}}
}

var startTime = time.Now()

func mathFn(name string, arity int, f func(xs []float64) (float64, error)) *Builtin {
	return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := noKw(name, kw); err != nil {
			return nil, err
		}
		if err := argCount(name, args, arity, arity); err != nil {
			return nil, err
		}
		var values [2]float64
		xs := values[:arity]
		for i, a := range args {
			x, err := mathFloat(vm, a)
			if err != nil {
				return nil, err
			}
			xs[i] = x
		}
		r, err := f(xs)
		if err != nil {
			return nil, err
		}
		return r, nil
	})
}

func domainCheck(v float64) (float64, error) {
	if math.IsNaN(v) {
		return 0, newError("ValueError", "math domain error")
	}
	return v, nil
}

func (vm *VM) makeMathModule() *NativeModule {
	intRound := func(name string, f func(float64) float64) *Builtin {
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount(name, args, 1, 1); err != nil {
				return nil, err
			}
			if n, ok := asInt(args[0]); ok {
				return n, nil
			}
			if z, ok := args[0].(*big.Int); ok {
				return z, nil
			}
			x, ok := args[0].(float64)
			if !ok {
				return nil, newErrorf("TypeError",
					"must be real number, not %s", typeName(args[0]))
			}
			if math.IsNaN(x) {
				return nil, newError("ValueError", "cannot convert float NaN to integer")
			}
			if math.IsInf(x, 0) {
				return nil, newError("OverflowError", "cannot convert float infinity to integer")
			}
			return intFromFloat(f(x)), nil
		})
	}
	return &NativeModule{Name: "math", NS: map[string]Value{
		"pi":        math.Pi,
		"e":         math.E,
		"tau":       2 * math.Pi,
		"asin":      mathUnary("asin", math.Asin, false),
		"atan":      mathUnary("atan", math.Atan, false),
		"atan2":     mathBinary("atan2", math.Atan2),
		"gamma":     mathUnary("gamma", math.Gamma, true),
		"expm1":     mathUnary("expm1", math.Expm1, true),
		"log1p":     mathUnary("log1p", math.Log1p, true),
		"trunc":     bi("trunc", biMathTrunc),
		"degrees":   mathUnary("degrees", func(x float64) float64 { return x * (180 / math.Pi) }, false),
		"radians":   mathUnary("radians", func(x float64) float64 { return x * (math.Pi / 180) }, false),
		"factorial": bi("factorial", biMathFactorial),
		"gcd":       bi("gcd", biMathGCD),
		"lcm":       bi("lcm", biMathLCM),
		"comb":      bi("comb", biMathComb),
		"perm":      bi("perm", biMathPerm),
		"prod":      bi("prod", biMathProd),
		"fsum":      bi("fsum", biMathFsum),
		"remainder": mathBinary("remainder", math.Remainder),
		"ulp":       bi("ulp", biMathULP),
		"nextafter": bi("nextafter", biMathNextafter),
		"isclose":   bi("isclose", biMathIsclose),
		"dist":      bi("dist", biMathDist),
		"acos": mathFn("acos", 1, func(xs []float64) (float64, error) {
			if xs[0] < -1 || xs[0] > 1 {
				return 0, newError("ValueError", "math domain error")
			}
			return math.Acos(xs[0]), nil
		}),
		"lgamma": mathFn("lgamma", 1, func(xs []float64) (float64, error) {
			x := xs[0]
			if x <= 0 && x == math.Trunc(x) && !math.IsInf(x, 0) {
				return 0, newError("ValueError", "math domain error")
			}
			value, _ := math.Lgamma(x)
			return value, nil
		}),
		"isfinite": bi("isfinite", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("isfinite", args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw("isfinite", kw); err != nil {
				return nil, err
			}
			x, ok := asFloat(args[0])
			if !ok {
				return nil, newErrorf("TypeError", "must be real number, not %s", typeName(args[0]))
			}
			return !math.IsNaN(x) && !math.IsInf(x, 0), nil
		}),
		"inf":   math.Inf(1),
		"nan":   math.NaN(),
		"floor": intRound("floor", math.Floor),
		"copysign": mathFn("copysign", 2, func(xs []float64) (float64, error) {
			return math.Copysign(xs[0], xs[1]), nil
		}),
		"frexp": bi("frexp", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("frexp", args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw("frexp", kw); err != nil {
				return nil, err
			}
			x, ok := asFloat(args[0])
			if !ok {
				return nil, newErrorf("TypeError", "must be real number, not %s", typeName(args[0]))
			}
			mantissa, exponent := math.Frexp(x)
			return &Tuple{Items: []Value{mantissa, int64(exponent)}}, nil
		}),
		"ldexp": bi("ldexp", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("ldexp", args, 2, 2); err != nil {
				return nil, err
			}
			if err := noKw("ldexp", kw); err != nil {
				return nil, err
			}
			x, ok := asFloat(args[0])
			if !ok {
				return nil, newErrorf("TypeError", "must be real number, not %s", typeName(args[0]))
			}
			exponent, err := wantInt("exponent", args[1])
			if err != nil {
				return nil, err
			}
			if exponent > 100000 {
				exponent = 100000
			}
			if exponent < -100000 {
				exponent = -100000
			}
			y := math.Ldexp(x, int(exponent))
			if math.IsInf(y, 0) && !math.IsInf(x, 0) {
				return nil, newError("OverflowError", "math range error")
			}
			return y, nil
		}),
		"ceil": intRound("ceil", math.Ceil),
		"sqrt": mathFn("sqrt", 1, func(xs []float64) (float64, error) {
			if xs[0] < 0 {
				return 0, newError("ValueError", "math domain error")
			}
			return math.Sqrt(xs[0]), nil
		}),
		"fabs": mathFn("fabs", 1, func(xs []float64) (float64, error) {
			return math.Abs(xs[0]), nil
		}),
		"pow": mathFn("pow", 2, func(xs []float64) (float64, error) {
			x, y := xs[0], xs[1]
			if x == 0 && y < 0 {
				return 0, newError("ValueError", "math domain error")
			}
			if x < 0 && y != math.Trunc(y) && !math.IsInf(y, 0) {
				return 0, newError("ValueError", "math domain error")
			}
			return math.Pow(x, y), nil
		}),
		"log": mathFn("log", 1, func(xs []float64) (float64, error) {
			if xs[0] <= 0 {
				return 0, newError("ValueError", "math domain error")
			}
			return math.Log(xs[0]), nil
		}),
		"log2": mathFn("log2", 1, func(xs []float64) (float64, error) {
			if xs[0] <= 0 {
				return 0, newError("ValueError", "math domain error")
			}
			return math.Log2(xs[0]), nil
		}),
		"log10": mathFn("log10", 1, func(xs []float64) (float64, error) {
			if xs[0] <= 0 {
				return 0, newError("ValueError", "math domain error")
			}
			return math.Log10(xs[0]), nil
		}),
		"exp": mathFn("exp", 1, func(xs []float64) (float64, error) {
			return math.Exp(xs[0]), nil
		}),
		"sin": mathFn("sin", 1, func(xs []float64) (float64, error) {
			return domainCheck(math.Sin(xs[0]))
		}),
		"cos": mathFn("cos", 1, func(xs []float64) (float64, error) {
			return domainCheck(math.Cos(xs[0]))
		}),
		"tan": mathFn("tan", 1, func(xs []float64) (float64, error) {
			return domainCheck(math.Tan(xs[0]))
		}),
		"hypot": mathFn("hypot", 2, func(xs []float64) (float64, error) {
			return math.Hypot(xs[0], xs[1]), nil
		}),
		"fmod": mathFn("fmod", 2, func(xs []float64) (float64, error) {
			if xs[1] == 0 {
				return 0, newError("ValueError", "math domain error")
			}
			return math.Mod(xs[0], xs[1]), nil
		}),
		"isnan": bi("isnan", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("isnan", args, 1, 1); err != nil {
				return nil, err
			}
			x, ok := asFloat(args[0])
			if !ok {
				return nil, newErrorf("TypeError", "must be real number, not %s",
					typeName(args[0]))
			}
			return math.IsNaN(x), nil
		}),
		"isinf": bi("isinf", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("isinf", args, 1, 1); err != nil {
				return nil, err
			}
			x, ok := asFloat(args[0])
			if !ok {
				return nil, newErrorf("TypeError", "must be real number, not %s",
					typeName(args[0]))
			}
			return math.IsInf(x, 0), nil
		}),
	}}
}

func (vm *VM) makeSysModule() *NativeModule {
	// The bundled library is installed in this logical namespace, not in a
	// host Python prefix. Native and pure modules share the same installation.
	const installationPrefix = "<stdlib>"
	argv := make([]Value, len(vm.argv))
	for i, a := range vm.argv {
		argv[i] = a
	}
	implementation := &Class{Name: "SimpleNamespace", NS: map[string]Value{}, Bases: []*Class{objectClass}}
	implementation.MRO = []*Class{implementation, objectClass}
	implementation.NS["__init__"] = bi("__init__", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount("SimpleNamespace", args, 1, 1); err != nil {
			return nil, err
		}
		self := args[0].(*Instance)
		for _, k := range kw {
			self.dictSet(k.Name, k.Val)
		}
		return None, nil
	})
	versionItems := &Tuple{Items: []Value{int64(3), int64(12), int64(12), "final", int64(0)}}
	versionClass := &Class{Name: "version_info", NS: map[string]Value{}, Prim: vm.types["tuple"], directPrim: vm.types["tuple"], Bases: []*Class{objectClass}}
	versionClass.MRO = []*Class{versionClass, objectClass}
	for index, name := range []string{"major", "minor", "micro", "releaselevel", "serial"} {
		versionClass.NS[name] = &Property{Fget: bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount(name, args, 1, 1); err != nil {
				return nil, err
			}
			return args[0].(*Instance).Prim.(*Tuple).Items[index], nil
		})}
	}
	version := newInstance(versionClass, versionItems)
	flagClass := &Class{Name: "flags", NS: map[string]Value{}, Bases: []*Class{objectClass}}
	flagClass.MRO = []*Class{flagClass, objectClass}
	flags := newInstance(flagClass, nil)
	for _, name := range []string{"debug", "inspect", "interactive", "optimize", "dont_write_bytecode", "no_user_site", "no_site", "ignore_environment", "verbose", "bytes_warning", "quiet", "hash_randomization", "isolated", "dev_mode", "utf8_mode", "warn_default_encoding", "safe_path", "int_max_str_digits"} {
		flags.dictSet(name, int64(0))
	}
	implementationValue := newInstance(implementation, nil)
	implementationValue.dictSet("name", "diaspore")
	implementationValue.dictSet("cache_tag", None)
	implementationValue.dictSet("version", version)
	implementationValue.dictSet("hexversion", int64(0x030c0cf0))
	excepthook := bi("excepthook", biExceptHook)
	unraisablehook := bi("unraisablehook", biUnraisableHook)
	return &NativeModule{Name: "sys", NS: map[string]Value{
		"audit":        bi("audit", biAudit),
		"addaudithook": bi("addaudithook", biAddAuditHook),
		"getfilesystemencoding": bi("getfilesystemencoding", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs("getfilesystemencoding", args, kw); err != nil {
				return nil, err
			}
			return "utf-8", nil
		}),
		"getfilesystemencodeerrors": bi("getfilesystemencodeerrors", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs("getfilesystemencodeerrors", args, kw); err != nil {
				return nil, err
			}
			if runtime.GOOS == "windows" {
				return "surrogatepass", nil
			}
			return "surrogateescape", nil
		}),
		"argv":                 &List{Items: argv},
		"platform":             "diaspore-go",
		"prefix":               installationPrefix,
		"exec_prefix":          installationPrefix,
		"base_prefix":          installationPrefix,
		"base_exec_prefix":     installationPrefix,
		"byteorder":            "little",
		"maxsize":              int64(math.MaxInt64),
		"maxunicode":           int64(0x10ffff),
		"float_info":           vm.makeFloatInfo(),
		"int_info":             vm.makeIntInfo(),
		"hash_info":            vm.makeHashInfo(),
		"version_info":         version,
		"version":              "3.12.12 (Diaspore)",
		"implementation":       implementationValue,
		"flags":                flags,
		"warnoptions":          &List{},
		"modules":              vm.moduleDict,
		"builtin_module_names": &Tuple{Items: []Value{"builtins", "sys", "math", "time", "posix", "_os", "tasks", "re", "_json", "_abc", "_operator", "_diaspore_stdlib", "_diaspore_hash"}},
		"_getframe": bi("_getframe", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("_getframe", args, 0, 1); err != nil {
				return nil, err
			}
			if err := noKw("_getframe", kw); err != nil {
				return nil, err
			}
			depth := int64(0)
			if len(args) != 0 {
				var err error
				depth, err = wantInt("depth", args[0])
				if err != nil {
					return nil, err
				}
			}
			fr := vm.metadata.active
			for ; depth > 0 && fr != nil; depth-- {
				fr = fr.metadata.back
			}
			if fr == nil {
				return nil, newError("ValueError", "call stack is not deep enough")
			}
			return vm.exposeMetadataFrame(fr), nil
		}),
		"intern": bi("intern", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("intern", args, 1, 1); err != nil {
				return nil, err
			}
			if err := noKw("intern", kw); err != nil {
				return nil, err
			}
			return wantStr("intern", args[0])
		}),
		"exception": bi("exception", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("exception", args, 0, 0); err != nil {
				return nil, err
			}
			if err := noKw("exception", kw); err != nil {
				return nil, err
			}
			if len(vm.excStack) == 0 {
				return None, nil
			}
			return vm.excStack[len(vm.excStack)-1], nil
		}),
		"exc_info": bi("exc_info", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := noArgs("exc_info", args, kw); err != nil {
				return nil, err
			}
			if len(vm.excStack) == 0 {
				return &Tuple{Items: []Value{None, None, None}}, nil
			}
			exception := vm.excStack[len(vm.excStack)-1]
			cls, err := ctorType(vm, []Value{exception}, nil)
			if err != nil {
				return nil, err
			}
			traceback, err := vm.getAttr(exception, "__traceback__")
			if err != nil {
				return nil, err
			}
			return &Tuple{Items: []Value{cls, exception, traceback}}, nil
		}),
		"excepthook":         excepthook,
		"__excepthook__":     excepthook,
		"unraisablehook":     unraisablehook,
		"__unraisablehook__": unraisablehook,
		"stdout":             vm.makeTextStream("stdout"),
		"stderr":             vm.makeTextStream("stderr"),
		"stdin":              vm.makeTextStream("stdin"),
		"getrecursionlimit":  bi("getrecursionlimit", biGetRecursionLimit),
		"setrecursionlimit":  bi("setrecursionlimit", biSetRecursionLimit),
		"exit": bi("exit", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			return biExit(vm, args, kw)
		}),
		"stderr_write": bi("stderr_write", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("stderr_write", args, 1, 1); err != nil {
				return nil, err
			}
			s, err := wantStr("stderr_write arg", args[0])
			if err != nil {
				return nil, err
			}
			if !vm.replaying {
				io.WriteString(vm.stderr, s)
			}
			return None, nil
		}),
	}}
}

type netState struct {
	nextID    int64
	listeners map[int64]net.Listener
	conns     map[int64]net.Conn
	selectors map[*netSelectWatch]struct{}
}

func (vm *VM) makeNetModule() *NativeModule {
	st := &netState{nextID: 1, listeners: map[int64]net.Listener{}, conns: map[int64]net.Conn{}}
	gate := func(name string, fn func(vm *VM, args []Value, kw []KwArg) (Value, error)) *Builtin {
		return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := vm.requireCap("net"); err != nil {
				return nil, err
			}
			return fn(vm, args, kw)
		})
	}
	return &NativeModule{Name: "net", NS: map[string]Value{
		"select": gate("select", st.selectCall),
		"listen": bi("listen", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("listen", args, 2, 2); err != nil {
				return nil, err
			}
			host, err := wantStr("host", args[0])
			if err != nil {
				return nil, err
			}
			port, ok := asInt(args[1])
			if !ok {
				return nil, newError("TypeError", "port must be an integer")
			}
			if err := vm.requireNet("listen", host, port); err != nil {
				return nil, err
			}
			network := "tcp"
			if address := net.ParseIP(host); address != nil && address.To4() != nil {
				network = "tcp4"
			}
			ln, lerr := net.Listen(network, net.JoinHostPort(host, strconv.FormatInt(port, 10)))
			if lerr != nil {
				return nil, newError("OSError", lerr.Error())
			}
			id := st.nextID
			st.nextID++
			st.listeners[id] = ln
			return id, nil
		}),
		"connect": bi("connect", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("connect", args, 2, 2); err != nil {
				return nil, err
			}
			host, err := wantStr("host", args[0])
			if err != nil {
				return nil, err
			}
			port, ok := asInt(args[1])
			if !ok {
				return nil, newError("TypeError", "port must be an integer")
			}
			if err := vm.requireNet("connect", host, port); err != nil {
				return nil, err
			}
			var conn net.Conn
			var cerr error
			vm.blocking(func() {
				conn, cerr = net.Dial("tcp", net.JoinHostPort(host, strconv.FormatInt(port, 10)))
			})
			if cerr != nil {
				return nil, newError("OSError", cerr.Error())
			}
			cid := st.nextID
			st.nextID++
			st.conns[cid] = conn
			return cid, nil
		}),
		"accept": gate("accept", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("accept", args, 1, 1); err != nil {
				return nil, err
			}
			id, ok := asInt(args[0])
			if !ok {
				return nil, newError("TypeError", "listener id must be an integer")
			}
			ln, exists := st.listeners[id]
			if !exists {
				return nil, newErrorf("OSError", "bad socket id: %d", id)
			}
			var conn net.Conn
			var aerr error
			vm.blocking(func() { conn, aerr = ln.Accept() })
			if aerr != nil {
				return nil, newError("OSError", aerr.Error())
			}
			cid := st.nextID
			st.nextID++
			st.conns[cid] = conn
			return cid, nil
		}),
		"recv": gate("recv", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("recv", args, 2, 2); err != nil {
				return nil, err
			}
			id, ok := asInt(args[0])
			n, nok := asInt(args[1])
			if !ok || !nok {
				return nil, newError("TypeError", "recv requires integers")
			}
			conn, exists := st.conns[id]
			if !exists {
				return nil, newErrorf("OSError", "bad socket id: %d", id)
			}
			buf := make([]byte, n)
			var got int
			var rerr error
			vm.blocking(func() { got, rerr = conn.Read(buf) })
			if rerr == io.EOF {
				return []byte{}, nil
			}
			if rerr != nil {
				return nil, newError("OSError", rerr.Error())
			}
			return buf[:got], nil
		}),
		"send": gate("send", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("send", args, 2, 2); err != nil {
				return nil, err
			}
			id, ok := asInt(args[0])
			if !ok {
				return nil, newError("TypeError", "connection id must be an integer")
			}
			data, dok := args[1].([]byte)
			if !dok {
				return nil, newError("TypeError", "net.send() requires bytes")
			}
			conn, exists := st.conns[id]
			if !exists {
				return nil, newErrorf("OSError", "bad socket id: %d", id)
			}
			var werr error
			vm.blocking(func() { _, werr = conn.Write(data) })
			if werr != nil {
				return nil, newError("OSError", werr.Error())
			}
			return int64(len(data)), nil
		}),
		"close": gate("close", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("close", args, 1, 1); err != nil {
				return nil, err
			}
			id, ok := asInt(args[0])
			if !ok {
				return nil, newError("TypeError", "socket id must be an integer")
			}
			if ln, exists := st.listeners[id]; exists {
				if err := st.notifySocketClose(id); err != nil {
					return nil, err
				}
				ln.Close()
				delete(st.listeners, id)
				return None, nil
			}
			if conn, exists := st.conns[id]; exists {
				if err := st.notifySocketClose(id); err != nil {
					return nil, err
				}
				conn.Close()
				delete(st.conns, id)
				return None, nil
			}
			return nil, newErrorf("OSError", "bad socket id: %d", id)
		}),
		"peer": gate("peer", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("peer", args, 1, 1); err != nil {
				return nil, err
			}
			id, ok := asInt(args[0])
			if !ok {
				return nil, newError("TypeError", "connection id must be an integer")
			}
			conn, exists := st.conns[id]
			if !exists {
				return nil, newErrorf("OSError", "bad socket id: %d", id)
			}
			return conn.RemoteAddr().String(), nil
		}),
	}}
}
