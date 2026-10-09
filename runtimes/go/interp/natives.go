package interp

import (
	"bufio"
	"io"
	"math"
	"math/big"
	"math/rand"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
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
			return int64(utf8.RuneCountInString(s)), nil
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
	return map[string]*NativeModule{
		"time":   vm.makeTimeModule(),
		"math":   vm.makeMathModule(),
		"json":   vm.makeJSONModule(),
		"random": vm.makeRandomModule(),
		"sys":    vm.makeSysModule(),
		"net":    vm.makeNetModule(),
		"re":     vm.makeReModule(),
		"tasks":  vm.makeTasksModule(),
		"flow":   vm.makeFlowModule(),
		"_os":    vm.makeOSModule(),
	}
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
	return &NativeModule{Name: "time", NS: map[string]Value{
		"time":      vm.journaled("time", "time", timeFn),
		"monotonic": vm.journaled("time", "monotonic", monoFn),
		"sleep":     vm.journaled("time", "sleep", sleepFn),
	}}
}

var startTime = time.Now()

func mathFn(name string, arity int, f func(xs []float64) (float64, error)) *Builtin {
	return bi(name, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount(name, args, arity, arity); err != nil {
			return nil, err
		}
		xs := make([]float64, len(args))
		for i, a := range args {
			x, ok := asFloat(a)
			if !ok {
				return nil, newErrorf("TypeError",
					"must be real number, not %s", typeName(a))
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
		"pi":    math.Pi,
		"e":     math.E,
		"inf":   math.Inf(1),
		"nan":   math.NaN(),
		"floor": intRound("floor", math.Floor),
		"ceil":  intRound("ceil", math.Ceil),
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

func (vm *VM) makeJSONModule() *NativeModule {
	return &NativeModule{Name: "json", NS: map[string]Value{
		"dumps": bi("dumps", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("dumps", args, 1, 2); err != nil {
				return nil, err
			}
			var indent Value = None
			if len(args) == 2 {
				indent = args[1]
			}
			if v, ok := kwGet(kw, "indent"); ok {
				indent = v
			}
			return vm.jsonDumps(args[0], indent)
		}),
		"loads": bi("loads", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("loads", args, 1, 1); err != nil {
				return nil, err
			}
			s, err := wantStr("loads arg", args[0])
			if err != nil {
				return nil, err
			}
			return jsonLoads(s)
		}),
	}}
}

func (vm *VM) randomMethods(effectName string) map[string]*Builtin {
	var rng *rand.Rand
	if vm.determin {
		rng = rand.New(rand.NewSource(0))
	} else {
		rng = rand.New(rand.NewSource(time.Now().UnixNano()))
	}
	seedFn := bi("seed", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount("seed", args, 0, 1); err != nil {
			return nil, err
		}
		if err := noKw("seed", kw); err != nil {
			return nil, err
		}
		n := int64(0)
		if !vm.determin {
			n = time.Now().UnixNano()
		}
		if len(args) != 0 {
			if _, none := args[0].(NoneType); !none {
				var ok bool
				n, ok = asInt(args[0])
				if !ok {
					return nil, newError("TypeError", "seed must be an integer in diaspore")
				}
			}
		}
		rng = rand.New(rand.NewSource(n))
		return None, nil
	})
	randomFn := bi("random", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount("random", args, 0, 0); err != nil {
			return nil, err
		}
		return rng.Float64(), nil
	})
	randintFn := bi("randint", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount("randint", args, 2, 2); err != nil {
			return nil, err
		}
		a, aok := asInt(args[0])
		b, bok := asInt(args[1])
		if !aok || !bok {
			return nil, newError("TypeError", "randint requires integers")
		}
		if b < a {
			return nil, newError("ValueError", "empty range for randint()")
		}
		width := uint64(b) - uint64(a) + 1
		if width != 0 && width <= math.MaxInt64 {
			return a + rng.Int63n(int64(width)), nil
		}
		if width == 0 {
			return int64(uint64(a) + rng.Uint64()), nil
		}
		threshold := -width % width
		for {
			value := rng.Uint64()
			if value >= threshold {
				return int64(uint64(a) + value%width), nil
			}
		}
	})
	choiceFn := bi("choice", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount("choice", args, 1, 1); err != nil {
			return nil, err
		}
		length, err := vm.diasporeLen(args[0])
		if err != nil {
			return nil, err
		}
		if length == 0 {
			return nil, newError("IndexError", "Cannot choose from an empty sequence")
		}
		return vm.getIndex(args[0], int64(rng.Intn(int(length))))
	})
	// shuffle journals the resulting order so replay reproduces the mutation.
	shuffleFn := bi("shuffle", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount("shuffle", args, 1, 1); err != nil {
			return nil, err
		}
		l, ok := args[0].(*List)
		if !ok {
			return nil, newError("TypeError", "shuffle requires a list")
		}
		shuffle := func() {
			rng.Shuffle(len(l.Items), func(i, j int) {
				l.Items[i], l.Items[j] = l.Items[j], l.Items[i]
			})
		}
		if !vm.durable {
			shuffle()
			return None, nil
		}
		order, err := vm.effect(effectName, "shuffle", func() (Value, error) {
			shuffle()
			return &List{Items: append([]Value{}, l.Items...)}, nil
		})
		if err != nil {
			return nil, err
		}
		ol, ok := order.(*List)
		if !ok {
			return nil, newError("TypeError", "shuffle journal entry is not a list")
		}
		l.Items = append(l.Items[:0], ol.Items...)
		return None, nil
	})
	return map[string]*Builtin{
		"seed":    vm.journaled(effectName, "seed", seedFn),
		"random":  vm.journaled(effectName, "random", randomFn),
		"randint": vm.journaled(effectName, "randint", randintFn),
		"choice":  vm.journaled(effectName, "choice", choiceFn),
		"shuffle": shuffleFn,
	}
}

func (vm *VM) makeRandomModule() *NativeModule {
	ns := map[string]Value{}
	for name, method := range vm.randomMethods("random") {
		ns[name] = method
	}
	randomType := &BuiltinType{Name: "Random", ctor: func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		if err := argCount("Random", args, 0, 1); err != nil {
			return nil, err
		}
		if err := noKw("Random", kw); err != nil {
			return nil, err
		}
		methods := vm.randomMethods("random.Random")
		if len(args) != 0 {
			if _, err := methods["seed"].Fn(vm, args, nil); err != nil {
				return nil, err
			}
		}
		return &NativeObject{Kind: "Random", methods: methods}, nil
	}}
	ns["Random"] = randomType
	vm.types["Random"] = randomType
	return &NativeModule{Name: "random", NS: ns}
}

func (vm *VM) makeSysModule() *NativeModule {
	argv := make([]Value, len(vm.argv))
	for i, a := range vm.argv {
		argv[i] = a
	}
	return &NativeModule{Name: "sys", NS: map[string]Value{
		"argv":              &List{Items: argv},
		"platform":          "diaspore-go",
		"stdout":            vm.makeTextStream("stdout"),
		"stderr":            vm.makeTextStream("stderr"),
		"stdin":             vm.makeTextStream("stdin"),
		"getrecursionlimit": bi("getrecursionlimit", biGetRecursionLimit),
		"setrecursionlimit": bi("setrecursionlimit", biSetRecursionLimit),
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
			ln, lerr := net.Listen("tcp", net.JoinHostPort(host, strconv.FormatInt(port, 10)))
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
				ln.Close()
				delete(st.listeners, id)
				return None, nil
			}
			if conn, exists := st.conns[id]; exists {
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
