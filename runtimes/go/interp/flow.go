package interp

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
)

// maybeGunzip transparently decompresses gzip content (magic 1f 8b);
// anything else passes through unchanged (spec 15).
func maybeGunzip(data []byte) ([]byte, error) {
	if len(data) < 2 || data[0] != 0x1f || data[1] != 0x8b {
		return data, nil
	}
	zr, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	defer zr.Close()
	return io.ReadAll(zr)
}

// Durable flows (spec 14): journaled replay. In a durable run every
// nondeterministic native call is an effect recorded in the journal;
// flow.suspend() aborts execution into a portable snapshot; resume replays
// the journal (recorded results, suppressed output) and continues live.

// Suspend is the internal signal raised by flow.suspend() in a durable run.
// It unwinds without running diaspore finally blocks or __exit__ methods (the
// resumed replay re-executes them).
type Suspend struct {
	// Payload is the value passed to flow.suspend.
	Payload Value
}

// Error returns the suspension signal's host-facing message.
func (s *Suspend) Error() string { return "diaspore: suspended" }

type jEntry struct {
	m string
	f string
	r Value
}

// replayNext consumes the next journal entry during replay. It returns
// (true, result) or (false, nil) once the journal is exhausted (live mode).
func (vm *VM) replayNext(mod, fname string) (bool, Value, error) {
	if !vm.replaying {
		return false, nil, nil
	}
	entry := vm.journal[vm.replayCursor]
	if entry.m != mod || entry.f != fname {
		return false, nil, &Trap{
			Msg: fmt.Sprintf("diaspore: journal divergence at entry %d "+
				"(recorded %s.%s, replay reached %s.%s); "+
				"durable programs must be deterministic",
				vm.replayCursor, entry.m, entry.f, mod, fname),
			Code: 5,
		}
	}
	vm.replayCursor++
	if vm.replayCursor >= len(vm.journal) {
		vm.replaying = false
	}
	return true, entry.r, nil
}

// journalValue validates and deep-copies a value for the journal: only
// canonical JSON types, so replay round-trips identically.
func (vm *VM) journalValue(v Value, what string) (Value, error) {
	switch t := v.(type) {
	case NoneType, bool, string, int64, float64, *big.Int:
		return t, nil
	case *List:
		items := make([]Value, len(t.Items))
		for i, x := range t.Items {
			cv, err := vm.journalValue(x, what)
			if err != nil {
				return nil, err
			}
			items[i] = cv
		}
		return &List{Items: items}, nil
	case *Dict:
		out := NewDict()
		for _, e := range t.entries {
			ks, ok := e.key.(string)
			if !ok {
				return nil, newErrorf("TypeError",
					"durable %s results must have str keys", what)
			}
			cv, err := vm.journalValue(e.val, what)
			if err != nil {
				return nil, err
			}
			out.SetStr(ks, cv)
		}
		return out, nil
	}
	return nil, newErrorf("TypeError",
		"durable %s results must be JSON-serializable "+
			"(got %s); tuples are not allowed; return lists", what, typeName(v))
}

// effect runs fn as a journaled effect: replayed results return without
// executing; live results execute and append to the journal.
func (vm *VM) effect(mod, fname string, fn func() (Value, error)) (Value, error) {
	replayed, r, err := vm.replayNext(mod, fname)
	if err != nil {
		return nil, err
	}
	if replayed {
		return r, nil
	}
	r, err = fn()
	if err != nil {
		return nil, err
	}
	jv, err := vm.journalValue(r, mod+"."+fname)
	if err != nil {
		return nil, err
	}
	vm.journal = append(vm.journal, jEntry{mod, fname, jv})
	return r, nil
}

// journaled wraps a Builtin as an effect when the VM is durable.
func (vm *VM) journaled(mod, fname string, b *Builtin) *Builtin {
	if !vm.durable {
		return b
	}
	inner := b.Fn
	wrapped := bi(fname, func(vm *VM, args []Value, kw []KwArg) (Value, error) {
		return vm.effect(mod, fname, func() (Value, error) {
			return inner(vm, args, kw)
		})
	})
	wrapped.NoBind = b.NoBind
	wrapped.doc = b.doc
	return wrapped
}

// BundleDigest is the canonical SHA-256 of a bundle (spec 14): UTF-8,
// compact separators, sorted keys, non-ASCII unescaped, no HTML escaping -
// byte-identical to diaspore-py's bundle_digest.
func BundleDigest(bundle map[string]any) string {
	var b strings.Builder
	canonicalJSON(&b, bundle)
	sum := sha256.Sum256([]byte(b.String()))
	return hex.EncodeToString(sum[:])
}

func canonicalJSON(b *strings.Builder, v any) {
	switch t := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if t {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case string:
		canonicalEscape(b, t)
	case json.Number:
		b.WriteString(t.String())
	case float64:
		if t == math.Trunc(t) && math.Abs(t) < 1e15 {
			b.WriteString(strconv.FormatInt(int64(t), 10))
		} else {
			b.WriteString(pyFloatStr(t))
		}
	case int64:
		b.WriteString(strconv.FormatInt(t, 10))
	case int:
		b.WriteString(strconv.Itoa(t))
	case []any:
		b.WriteByte('[')
		for i, x := range t {
			if i > 0 {
				b.WriteByte(',')
			}
			canonicalJSON(b, x)
		}
		b.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			canonicalEscape(b, k)
			b.WriteByte(':')
			canonicalJSON(b, t[k])
		}
		b.WriteByte('}')
	}
}

// canonicalEscape leaves Unicode unescaped except lone surrogates, which have
// no strict UTF-8 encoding and use their JSON escape in portable digests.
func canonicalEscape(b *strings.Builder, s string) {
	b.WriteByte('"')
	for offset := 0; offset < len(s); {
		r, size := pyDecodeRuneInString(s[offset:])
		offset += size
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if r < 0x20 || (r >= 0xd800 && r <= 0xdfff) {
				fmt.Fprintf(b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}

// FlowOutcome is the result of a durable run: either completed with an exit
// code, or suspended with the flow.suspend payload and a portable snapshot
// (serialized JSON, resumable on any runtime).
type FlowOutcome struct {
	// Suspended reports whether execution stopped at flow.suspend.
	Suspended bool
	// Code is the exit code when execution completes.
	Code int
	// Payload is the value passed to flow.suspend.
	Payload Value
	// Snapshot holds the portable JSON journal and replay state.
	Snapshot []byte
}

// RunDurable starts a durable run on this (freshly constructed) VM.
func (vm *VM) RunDurable() *FlowOutcome {
	if vm.badBundle == nil && !vm.durable {
		vm.badBundle = fmt.Errorf("durable API requires WithDurable()")
	}
	return vm.driveDurable()
}

// Resume continues a suspended durable run from a snapshot (JSON, possibly
// gzipped) on this freshly constructed VM: the journal replays with output
// suppressed, the pending flow.suspend() returns send, then execution
// continues live. Snapshots are portable across processes and VMs.
func (vm *VM) Resume(snapshot []byte, send Value) (*FlowOutcome, error) {
	if vm.badBundle != nil {
		return nil, vm.badBundle
	}
	if !vm.durable {
		return nil, fmt.Errorf("durable API requires WithDurable()")
	}
	data, err := maybeGunzip(snapshot)
	if err != nil {
		return nil, fmt.Errorf("bad snapshot: %w", err)
	}
	snapVal, jerr := jsonLoads(string(data))
	if jerr != nil {
		return nil, fmt.Errorf("bad snapshot: not valid JSON")
	}
	snap, ok := snapVal.(*Dict)
	if !ok {
		return nil, fmt.Errorf("not a diaspore flow snapshot")
	}
	if v, _ := snap.GetStr("diaspore_flow"); v != int64(1) {
		return nil, fmt.Errorf("not a diaspore flow snapshot")
	}
	snapshotStdlib := ""
	if value, present := snap.GetStr("stdlib"); present {
		id, valid := value.(string)
		if !valid || !validStdlibID(id) {
			return nil, fmt.Errorf("bad snapshot stdlib: expected sha256: followed by 64 lowercase hexadecimal digits")
		}
		snapshotStdlib = id
	}
	if snapshotStdlib != vm.stdlibID {
		return nil, fmt.Errorf("snapshot stdlib mismatch (snapshot %q, this bundle %q)", snapshotStdlib, vm.stdlibID)
	}
	if expect, ok := snap.GetStr("bundle_sha256"); ok {
		if es, ok := expect.(string); ok && es != "" {
			actual := BundleDigest(vm.bundle)
			if es != actual {
				return nil, fmt.Errorf(
					"snapshot was taken from a different bundle (snapshot %s..., this bundle %s...)",
					es[:12], actual[:12])
			}
		}
	}
	if av, ok := snap.GetStr("argv"); ok {
		if al, ok := av.(*List); ok && len(al.Items) > 0 {
			argv := make([]string, 0, len(al.Items))
			for _, x := range al.Items {
				if s, ok := x.(string); ok {
					argv = append(argv, s)
				}
			}
			vm.setArgv(argv)
		}
	}
	jv, _ := snap.GetStr("journal")
	jl, ok := jv.(*List)
	if !ok {
		return nil, fmt.Errorf("not a diaspore flow snapshot")
	}
	vm.journal = vm.journal[:0]
	for _, raw := range jl.Items {
		ed, ok := raw.(*Dict)
		if !ok {
			return nil, fmt.Errorf("malformed journal entry")
		}
		m, _ := ed.GetStr("m")
		f, _ := ed.GetStr("f")
		r, _ := ed.GetStr("r")
		ms, ok1 := m.(string)
		fs, ok2 := f.(string)
		if !ok1 || !ok2 {
			return nil, fmt.Errorf("malformed journal entry")
		}
		if r == nil {
			r = None
		}
		vm.journal = append(vm.journal, jEntry{ms, fs, r})
	}
	if send == nil {
		send = None
	}
	vm.journal = append(vm.journal, jEntry{"flow", "suspend", send})
	vm.replayCursor = 0
	vm.replaying = len(vm.journal) > 0
	return vm.driveDurable(), nil
}

func (vm *VM) driveDurable() *FlowOutcome {
	if vm.badBundle != nil {
		fmt.Fprintf(vm.stderr, "diaspore: %v\n", vm.badBundle)
		return &FlowOutcome{Code: 2}
	}
	defer vm.flushOut()
	vm.gil.Lock()
	defer vm.gil.Unlock()
	vm.suspended = false
	_, err := vm.importModule(vm.main)
	if err == nil {
		return &FlowOutcome{Code: 0}
	}
	if s, ok := err.(*Suspend); ok {
		vm.suspended = true
		return &FlowOutcome{
			Suspended: true,
			Payload:   s.Payload,
			Snapshot:  vm.buildSnapshot(),
		}
	}
	return &FlowOutcome{Code: vm.ReportError(err)}
}

// buildSnapshot serializes the flow snapshot with the diaspore JSON writer so
// journal object values keep their key order.
func (vm *VM) buildSnapshot() []byte {
	var b strings.Builder
	b.WriteString(`{"diaspore_flow": 1, "bundle_sha256": `)
	canonicalEscape(&b, BundleDigest(vm.bundle))
	if vm.stdlibID != "" {
		b.WriteString(`, "stdlib": `)
		canonicalEscape(&b, vm.stdlibID)
	}
	b.WriteString(`, "argv": `)
	argvItems := make([]Value, len(vm.argv))
	for i, a := range vm.argv {
		argvItems[i] = a
	}
	argvJSON, _ := vm.jsonDumps(&List{Items: argvItems}, None)
	b.WriteString(argvJSON)
	b.WriteString(`, "journal": [`)
	for i, e := range vm.journal {
		if i > 0 {
			b.WriteString(", ")
		}
		b.WriteString(`{"m": `)
		canonicalEscape(&b, e.m)
		b.WriteString(`, "f": `)
		canonicalEscape(&b, e.f)
		b.WriteString(`, "r": `)
		rJSON, err := vm.jsonDumps(e.r, None)
		if err != nil {
			rJSON = "null"
		}
		b.WriteString(rJSON)
		b.WriteByte('}')
	}
	b.WriteString("]}")
	return []byte(b.String())
}

// PayloadJSON serializes a diaspore JSON value compactly (CPython's default
// separators) - for hosts reporting suspend payloads.
func PayloadJSON(v Value) (string, error) {
	if v == nil {
		return "null", nil
	}
	var b strings.Builder
	if err := jsonWrite(&b, v, 0, false, 0); err != nil {
		return "", err
	}
	return b.String(), nil
}

// ParseJSONValue parses a JSON literal into a diaspore value (int/float/str/
// bool/None/*List/*Dict) - for hosts passing resume send values.
func ParseJSONValue(s string) (Value, error) {
	return jsonLoads(s)
}

// PrettyJSON serializes a diaspore JSON value with 2-space indentation.
func PrettyJSON(v Value) (string, error) {
	var b strings.Builder
	if err := jsonWrite(&b, v, 2, true, 0); err != nil {
		return "", err
	}
	return b.String(), nil
}

func (vm *VM) makeFlowModule() *NativeModule {
	return &NativeModule{Name: "flow", NS: map[string]Value{
		"suspend": bi("suspend", func(vm *VM, args []Value, kw []KwArg) (Value, error) {
			if err := argCount("suspend", args, 0, 1); err != nil {
				return nil, err
			}
			if !vm.durable {
				return nil, newError("RuntimeError",
					"flow.suspend() requires a durable run (diaspore run --durable)")
			}
			replayed, r, err := vm.replayNext("flow", "suspend")
			if err != nil {
				return nil, err
			}
			if replayed {
				return r, nil
			}
			var payload Value = None
			if len(args) == 1 {
				payload = args[0]
			}
			jv, err := vm.journalValue(payload, "flow.suspend")
			if err != nil {
				return nil, err
			}
			return nil, &Suspend{Payload: jv}
		}),
	}}
}
