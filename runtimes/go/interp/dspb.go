package interp

import (
	"bytes"
	"fmt"
	"sort"
	"unicode/utf8"
)

// DSPB: diaspore's optional binary bundle container (docs/dspb.md). Strictly an
// encoding of the same semantic model - JSON remains canonical and default.
// The invariant: decode(encode(bundle)) == compact(bundle), so digests and
// semantics are container-independent. This file mirrors src/diaspore/dspb.py.

var dspbMagic = []byte("DSPB01")
var dspbLinkedMagic = []byte("DSPB02")

// dspbMaxDepth bounds node nesting on hostile input (mirrors the reference).
const dspbMaxDepth = 8000

var dspbOps = []string{
	"const", "name", "attr", "index", "slicelit", "starred", "call", "binop",
	"unary", "boolop", "compare", "cond", "list", "tuple", "set", "dict",
	"fstring", "func", "funcexpr", "yield", "yieldfrom", "namedexpr",
	"expr", "assign", "augassign", "if", "while", "for", "break", "continue",
	"pass", "return", "class", "raise", "try", "with", "import", "importfrom",
	"del",
}

var dspbBinKinds = []string{"add", "sub", "mul", "div", "floordiv", "mod", "pow",
	"lshift", "rshift", "bitand", "bitor", "bitxor", "matmul"}
var dspbCmpKinds = []string{"eq", "ne", "lt", "le", "gt", "ge", "in", "notin", "is", "isnot"}
var dspbUnaryKinds = []string{"neg", "pos", "not", "invert"}
var dspbConstKinds = []string{"int", "float", "str", "bytes", "bool", "none", "ellipsis", "complex"}
var dspbConvKinds = []string{"", "r", "s", "a"}

// flag-bit optional fields per op (bit 1 upward; bit 0 is ln).
var dspbOpts = map[string][]string{
	"slicelit": {"lower", "upper", "step"},
	"yield":    {"v"},
	"return":   {"v"},
	"raise":    {"exc", "cause"},
	"import":   {"alias"},
}

func indexOf(table []string, s string) (int, bool) {
	for i, t := range table {
		if t == s {
			return i, true
		}
	}
	return 0, false
}

// LooksLikeDSPB reports whether data starts with a supported DSPB magic.
func LooksLikeDSPB(data []byte) bool {
	return bytes.HasPrefix(data, dspbMagic) || bytes.HasPrefix(data, dspbLinkedMagic)
}

func validStdlibID(id string) bool {
	if len(id) != 71 || id[:7] != "sha256:" {
		return false
	}
	for i := 7; i < len(id); i++ {
		c := id[i]
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

func bundleStdlibID(bundle map[string]any) (string, error) {
	value, present := bundle["stdlib"]
	if !present {
		return "", nil
	}
	id, ok := value.(string)
	if !ok || !validStdlibID(id) {
		return "", fmt.Errorf("invalid bundle stdlib: expected sha256: followed by 64 lowercase hexadecimal digits")
	}
	return id, nil
}

// LoadBundle decodes bundle bytes in any supported container: gzip is
// transparently decompressed (and its payload re-sniffed, so .dspb.gz
// works), DSPB is decoded by magic, anything else is parsed as JSON.
func LoadBundle(data []byte) (map[string]any, error) {
	data, err := maybeGunzip(data)
	if err != nil {
		return nil, err
	}
	if LooksLikeDSPB(data) {
		return DecodeDSPB(data)
	}
	value, err := DecodeJSON(data)
	if err != nil {
		return nil, fmt.Errorf("invalid bundle JSON: %w", err)
	}
	if value == nil {
		return nil, nil
	}
	bundle, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("invalid bundle JSON: expected an object")
	}
	if _, err := bundleStdlibID(bundle); err != nil {
		return nil, err
	}
	return bundle, nil
}

type dspbWriter struct {
	buf     bytes.Buffer
	strings map[string]int
	strlist []string
	err     error
}

func (w *dspbWriter) fail(format string, a ...any) {
	if w.err == nil {
		w.err = fmt.Errorf(format, a...)
	}
}

func (w *dspbWriter) u(n uint64) {
	for {
		b := byte(n & 0x7F)
		n >>= 7
		if n != 0 {
			w.buf.WriteByte(b | 0x80)
		} else {
			w.buf.WriteByte(b)
			return
		}
	}
}

func (w *dspbWriter) byte(b byte) { w.buf.WriteByte(b) }

func (w *dspbWriter) s(text string) {
	idx, ok := w.strings[text]
	if !ok {
		idx = len(w.strlist)
		w.strings[text] = idx
		w.strlist = append(w.strlist, text)
	}
	w.u(uint64(idx))
}

type dspbReader struct {
	data    []byte
	pos     int
	strings []string
	depth   int
	err     error
}

func (r *dspbReader) fail(format string, a ...any) {
	if r.err == nil {
		r.err = fmt.Errorf(format, a...)
	}
}

func (r *dspbReader) u() uint64 {
	var n uint64
	shift := 0
	for {
		if r.err != nil {
			return 0
		}
		if r.pos >= len(r.data) {
			r.fail("truncated varint")
			return 0
		}
		b := r.data[r.pos]
		r.pos++
		n |= uint64(b&0x7F) << shift
		if b&0x80 == 0 {
			return n
		}
		shift += 7
		if shift > 63 {
			r.fail("varint too long")
			return 0
		}
	}
}

// count reads a length prefix from untrusted input: every counted element
// costs at least one byte, so a count beyond the remaining bytes is invalid and
// rejected before anything proportional to it is allocated.
func (r *dspbReader) count() uint64 {
	n := r.u()
	if r.err == nil && n > uint64(len(r.data)-r.pos) {
		r.fail("count exceeds remaining data")
		return 0
	}
	return n
}

func (r *dspbReader) byte() byte {
	if r.err != nil {
		return 0
	}
	if r.pos >= len(r.data) {
		r.fail("truncated")
		return 0
	}
	b := r.data[r.pos]
	r.pos++
	return b
}

func (r *dspbReader) s() string {
	idx := r.u()
	if r.err != nil {
		return ""
	}
	if idx >= uint64(len(r.strings)) {
		r.fail("bad string ref")
		return ""
	}
	return r.strings[idx]
}

func (r *dspbReader) enum(table []string, what string) string {
	b := int(r.byte())
	if r.err != nil {
		return ""
	}
	if b >= len(table) {
		r.fail("bad %s byte %d", what, b)
		return ""
	}
	return table[b]
}

func hasVal(n node, key string) bool { return n[key] != nil }

func (w *dspbWriter) flags(n node, opts []string) byte {
	var f byte
	if hasVal(n, "ln") {
		f = 1
	}
	for i, key := range opts {
		if hasVal(n, key) {
			f |= 1 << (i + 1)
		}
	}
	return f
}

func (w *dspbWriter) enum(table []string, s, what string) {
	if i, ok := indexOf(table, s); ok {
		w.byte(byte(i))
	} else {
		w.fail("unknown %s %q", what, s)
	}
}

func (w *dspbWriter) nodes(list []any) {
	w.u(uint64(len(list)))
	for _, x := range list {
		n, ok := x.(map[string]any)
		if !ok {
			w.fail("node is not an object")
			return
		}
		w.node(n)
	}
}

func (w *dspbWriter) strs(items []string) {
	w.u(uint64(len(items)))
	for _, s := range items {
		w.s(s)
	}
}

func (w *dspbWriter) optNode(n node, key string) {
	if sub := nNode(n, key); sub != nil {
		w.node(sub)
	}
}

func (w *dspbWriter) node(n node) {
	if w.err != nil {
		return
	}
	op := nStr(n, "op")
	opIdx, ok := indexOf(dspbOps, op)
	if !ok {
		w.fail("unknown op %q", op)
		return
	}
	w.byte(byte(opIdx))
	w.byte(w.flags(n, dspbOpts[op]))
	if ln, ok := n["ln"].(float64); ok {
		w.u(uint64(ln))
	}
	switch op {
	case "const":
		kind := nStr(n, "t")
		w.enum(dspbConstKinds, kind, "const kind")
		switch kind {
		case "str":
			text, err := irString(n, "v")
			if err != nil {
				w.fail("%v", err)
				return
			}
			w.s(text)
		case "int", "float", "bytes":
			w.s(nStr(n, "v"))
		case "complex":
			w.s(nStr(n, "re"))
			w.s(nStr(n, "im"))
		case "bool":
			if n["v"] == true {
				w.byte(1)
			} else {
				w.byte(0)
			}
		}
	case "name":
		w.s(nStr(n, "id"))
	case "attr":
		w.node(nNode(n, "o"))
		w.s(nStr(n, "n"))
	case "index":
		w.node(nNode(n, "o"))
		w.node(nNode(n, "k"))
	case "slicelit":
		w.optNode(n, "lower")
		w.optNode(n, "upper")
		w.optNode(n, "step")
	case "starred", "yieldfrom", "expr":
		w.node(nNode(n, "v"))
	case "call":
		w.node(nNode(n, "f"))
		w.nodes(nList(n, "args"))
		kws := nList(n, "kw")
		w.u(uint64(len(kws)))
		for _, raw := range kws {
			kw, _ := raw.(map[string]any)
			if name, ok := kw["n"].(string); ok {
				w.byte(1)
				w.s(name)
			} else {
				w.byte(0)
			}
			w.node(nNode(kw, "v"))
		}
	case "binop":
		w.enum(dspbBinKinds, nStr(n, "kind"), "binop kind")
		w.node(nNode(n, "l"))
		w.node(nNode(n, "r"))
	case "augassign":
		w.enum(dspbBinKinds, nStr(n, "kind"), "binop kind")
		w.node(nNode(n, "target"))
		w.node(nNode(n, "v"))
	case "unary":
		w.enum(dspbUnaryKinds, nStr(n, "kind"), "unary kind")
		w.node(nNode(n, "v"))
	case "boolop":
		if nStr(n, "kind") == "and" {
			w.byte(0)
		} else {
			w.byte(1)
		}
		w.nodes(nList(n, "values"))
	case "compare":
		w.node(nNode(n, "left"))
		ops := nStrs(n, "ops")
		w.u(uint64(len(ops)))
		for _, k := range ops {
			w.enum(dspbCmpKinds, k, "compare kind")
		}
		w.nodes(nList(n, "comps"))
	case "cond":
		w.node(nNode(n, "test"))
		w.node(nNode(n, "then"))
		w.node(nNode(n, "else"))
	case "list", "tuple", "set":
		w.nodes(nList(n, "elts"))
	case "dict":
		entries := nList(n, "entries")
		w.u(uint64(len(entries)))
		for _, raw := range entries {
			e, _ := raw.(map[string]any)
			if k := nNode(e, "k"); k != nil {
				w.byte(1)
				w.node(k)
			} else {
				w.byte(0)
			}
			w.node(nNode(e, "v"))
		}
	case "fstring":
		parts := nList(n, "parts")
		w.u(uint64(len(parts)))
		for _, raw := range parts {
			p, _ := raw.(map[string]any)
			if _, ok := p["s"].(string); ok {
				s, err := irString(p, "s")
				if err != nil {
					w.fail("%v", err)
					return
				}
				w.byte(0)
				w.s(s)
				continue
			}
			flag := byte(1)
			if hasVal(p, "spec") {
				flag |= 2
			}
			w.byte(flag)
			w.node(nNode(p, "v"))
			conv, _ := p["conv"].(string)
			w.enum(dspbConvKinds, conv, "conv")
			if hasVal(p, "spec") {
				w.node(nNode(p, "spec"))
			}
		}
	case "func", "funcexpr":
		w.s(nStr(n, "name"))
		var gb byte
		if n["gen"] == true {
			gb |= 1
		}
		if n["coro"] == true {
			gb |= 2
		}
		if n["asyncgen"] == true {
			gb |= 4
		}
		if n["annotation_scope"] == true {
			gb |= 8
		}
		w.byte(gb)
		p := nNode(n, "params")
		vararg := nStr(p, "vararg")
		kwarg := nStr(p, "kwarg")
		var pf byte
		if vararg != "" {
			pf |= 1
		}
		if kwarg != "" {
			pf |= 2
		}
		posonly, _ := p["posonly"].(float64)
		if posonly != 0 {
			pf |= 4
		}
		w.byte(pf)
		if pf&4 != 0 {
			w.u(uint64(posonly))
		}
		w.strs(nStrs(p, "args"))
		w.nodes(nList(p, "defaults"))
		if vararg != "" {
			w.s(vararg)
		}
		w.strs(nStrs(p, "kwonly"))
		kwd := nList(p, "kwdefaults")
		w.u(uint64(len(kwd)))
		for _, raw := range kwd {
			if d, ok := raw.(map[string]any); ok {
				w.byte(1)
				w.node(d)
			} else {
				w.byte(0)
			}
		}
		if kwarg != "" {
			w.s(kwarg)
		}
		w.strs(nStrs(n, "locals"))
		w.strs(nStrs(n, "gdecl"))
		w.strs(nStrs(n, "ndecl"))
		w.nodes(nList(n, "body"))
	case "yield", "return":
		w.optNode(n, "v")
	case "namedexpr":
		w.node(nNode(n, "target"))
		w.node(nNode(n, "v"))
	case "assign":
		w.nodes(nList(n, "targets"))
		w.node(nNode(n, "v"))
	case "if":
		w.node(nNode(n, "test"))
		w.nodes(nList(n, "then"))
		w.nodes(nList(n, "orelse"))
	case "while":
		w.node(nNode(n, "test"))
		w.nodes(nList(n, "body"))
		w.nodes(nList(n, "orelse"))
	case "for":
		w.node(nNode(n, "target"))
		w.node(nNode(n, "iter"))
		w.nodes(nList(n, "body"))
		w.nodes(nList(n, "orelse"))
	case "break", "continue", "pass":
	case "class":
		w.s(nStr(n, "name"))
		kwargs := nList(n, "kwargs")
		var classFlags byte
		if len(kwargs) > 0 {
			classFlags |= 1
		}
		binding, hasBinding := n["binding"]
		if hasBinding {
			if _, ok := binding.(string); !ok {
				w.fail("class binding must be a string")
				return
			}
			classFlags |= 2
		}
		w.byte(classFlags)
		if hasBinding {
			w.s(binding.(string))
		}
		if len(kwargs) > 0 {
			w.u(uint64(len(kwargs)))
			for _, raw := range kwargs {
				kw, _ := raw.(map[string]any)
				if name, ok := kw["n"].(string); ok {
					w.byte(1)
					w.s(name)
				} else {
					w.byte(0)
				}
				w.node(nNode(kw, "v"))
			}
		}
		w.nodes(nList(n, "bases"))
		w.nodes(nList(n, "body"))
	case "raise":
		w.optNode(n, "exc")
		w.optNode(n, "cause")
	case "try":
		if n["star"] == true {
			w.byte(1)
		} else {
			w.byte(0)
		}
		w.nodes(nList(n, "body"))
		handlers := nList(n, "handlers")
		w.u(uint64(len(handlers)))
		for _, raw := range handlers {
			h, _ := raw.(map[string]any)
			var hf byte
			if hasVal(h, "t") {
				hf |= 1
			}
			if hasVal(h, "name") {
				hf |= 2
			}
			w.byte(hf)
			if hasVal(h, "t") {
				w.node(nNode(h, "t"))
			}
			if hasVal(h, "name") {
				w.s(nStr(h, "name"))
			}
			w.nodes(nList(h, "body"))
		}
		w.nodes(nList(n, "orelse"))
		w.nodes(nList(n, "finalbody"))
	case "with":
		items := nList(n, "items")
		w.u(uint64(len(items)))
		for _, raw := range items {
			it, _ := raw.(map[string]any)
			if hasVal(it, "name") {
				w.byte(1)
			} else {
				w.byte(0)
			}
			w.node(nNode(it, "ctx"))
			if hasVal(it, "name") {
				w.node(nNode(it, "name"))
			}
		}
		w.nodes(nList(n, "body"))
	case "import":
		w.s(nStr(n, "module"))
		switch nStr(n, "bind") {
		case "root":
			w.byte(1)
		case "none":
			w.byte(2)
		default:
			w.byte(0)
		}
		if hasVal(n, "alias") {
			w.s(nStr(n, "alias"))
		}
	case "importfrom":
		w.s(nStr(n, "module"))
		names := nList(n, "names")
		w.u(uint64(len(names)))
		for _, raw := range names {
			nm, _ := raw.(map[string]any)
			w.s(nStr(nm, "n"))
			w.s(nStr(nm, "alias"))
		}
	case "del":
		w.nodes(nList(n, "targets"))
	}
}

// EncodeDSPB serializes a bundle (verbose or compact JSON model) to DSPB.
// moduleOrder fixes the module emission order (all names must be covered);
// nil means sorted names - deterministic but not necessarily byte-identical
// to another implementation's insertion order.
func EncodeDSPB(bundle map[string]any, moduleOrder []string) ([]byte, error) {
	if v, _ := bundle["diaspore"].(float64); v != 1 {
		return nil, fmt.Errorf("not a diaspore v1 bundle")
	}
	mods, _ := bundle["modules"].(map[string]any)
	if mods == nil {
		return nil, fmt.Errorf("not a diaspore v1 bundle")
	}
	stdlibID, err := bundleStdlibID(bundle)
	if err != nil {
		return nil, err
	}
	order := moduleOrder
	if order == nil {
		for name := range mods {
			order = append(order, name)
		}
		sort.Strings(order)
	}
	if len(order) != len(mods) {
		return nil, fmt.Errorf("module order does not cover the bundle")
	}
	w := &dspbWriter{strings: map[string]int{}}
	w.u(1)
	main, _ := bundle["main"].(string)
	w.s(main)
	if stdlibID != "" {
		w.s(stdlibID)
	}
	w.u(uint64(len(mods)))
	for _, name := range order {
		mod, ok := mods[name].(map[string]any)
		if !ok {
			return nil, fmt.Errorf("module %q missing or malformed", name)
		}
		w.s(name)
		w.s(nStr(mod, "file"))
		if src, ok := mod["src"].([]any); ok {
			w.byte(1)
			w.u(uint64(len(src)))
			for _, line := range src {
				ls, _ := line.(string)
				w.s(ls)
			}
		} else {
			w.byte(0)
		}
		w.nodes(nList(mod, "body"))
	}
	if w.err != nil {
		return nil, w.err
	}
	var out bytes.Buffer
	if stdlibID == "" {
		out.Write(dspbMagic)
	} else {
		out.Write(dspbLinkedMagic)
	}
	head := &dspbWriter{}
	head.u(uint64(len(w.strlist)))
	out.Write(head.buf.Bytes())
	for _, s := range w.strlist {
		var lw dspbWriter
		lw.u(uint64(len(s)))
		out.Write(lw.buf.Bytes())
		out.WriteString(s)
	}
	out.Write(w.buf.Bytes())
	return out.Bytes(), nil
}

func (r *dspbReader) decNodes() []any {
	count := r.count()
	out := make([]any, 0, count)
	for i := uint64(0); i < count && r.err == nil; i++ {
		out = append(out, r.decNode())
	}
	return out
}

func (r *dspbReader) decStrs() []any {
	count := r.count()
	out := make([]any, 0, count)
	for i := uint64(0); i < count && r.err == nil; i++ {
		out = append(out, r.s())
	}
	return out
}

func setIfAny(n node, key string, list []any) {
	if len(list) > 0 {
		n[key] = list
	}
}

func (r *dspbReader) decNode() map[string]any {
	r.depth++
	defer func() { r.depth-- }()
	if r.depth > dspbMaxDepth {
		r.fail("node nesting too deep")
		return map[string]any{}
	}
	op := r.enum(dspbOps, "opcode")
	flags := r.byte()
	hasLn := flags&1 != 0
	var ln float64
	if hasLn {
		ln = float64(r.u())
	}
	opts := dspbOpts[op]
	present := func(key string) bool {
		for i, k := range opts {
			if k == key {
				return flags&(1<<(i+1)) != 0
			}
		}
		return false
	}
	n := map[string]any{"op": op}
	if r.err != nil {
		return n
	}
	switch op {
	case "const":
		kind := r.enum(dspbConstKinds, "const kind")
		n["t"] = kind
		switch kind {
		case "str":
			text := r.s()
			n["v"] = text
			preserveIRCodepoints(n, "v", text)
		case "int", "float", "bytes":
			n["v"] = r.s()
		case "complex":
			n["re"] = r.s()
			n["im"] = r.s()
		case "bool":
			n["v"] = r.byte() == 1
		}
	case "name":
		n["id"] = r.s()
	case "attr":
		n["o"] = r.decNode()
		n["n"] = r.s()
	case "index":
		n["o"] = r.decNode()
		n["k"] = r.decNode()
	case "slicelit":
		for _, key := range []string{"lower", "upper", "step"} {
			if present(key) {
				n[key] = r.decNode()
			}
		}
	case "starred", "yieldfrom", "expr":
		n["v"] = r.decNode()
	case "call":
		n["f"] = r.decNode()
		setIfAny(n, "args", r.decNodes())
		count := r.count()
		kws := make([]any, 0, count)
		for i := uint64(0); i < count && r.err == nil; i++ {
			var name any
			if r.byte() == 1 {
				name = r.s()
			}
			kws = append(kws, map[string]any{"n": name, "v": r.decNode()})
		}
		setIfAny(n, "kw", kws)
	case "binop":
		n["kind"] = r.enum(dspbBinKinds, "binop kind")
		n["l"] = r.decNode()
		n["r"] = r.decNode()
	case "augassign":
		n["kind"] = r.enum(dspbBinKinds, "binop kind")
		n["target"] = r.decNode()
		n["v"] = r.decNode()
	case "unary":
		n["kind"] = r.enum(dspbUnaryKinds, "unary kind")
		n["v"] = r.decNode()
	case "boolop":
		if r.byte() == 0 {
			n["kind"] = "and"
		} else {
			n["kind"] = "or"
		}
		n["values"] = r.decNodes()
	case "compare":
		n["left"] = r.decNode()
		count := r.count()
		ops := make([]any, 0, count)
		for i := uint64(0); i < count && r.err == nil; i++ {
			ops = append(ops, r.enum(dspbCmpKinds, "compare kind"))
		}
		n["ops"] = ops
		n["comps"] = r.decNodes()
	case "cond":
		n["test"] = r.decNode()
		n["then"] = r.decNode()
		n["else"] = r.decNode()
	case "list", "tuple", "set":
		setIfAny(n, "elts", r.decNodes())
	case "dict":
		count := r.count()
		entries := make([]any, 0, count)
		for i := uint64(0); i < count && r.err == nil; i++ {
			var k any
			if r.byte() == 1 {
				k = r.decNode()
			}
			entries = append(entries, map[string]any{"k": k, "v": r.decNode()})
		}
		setIfAny(n, "entries", entries)
	case "fstring":
		count := r.count()
		parts := make([]any, 0, count)
		for i := uint64(0); i < count && r.err == nil; i++ {
			flag := r.byte()
			if flag == 0 {
				text := r.s()
				part := map[string]any{"s": text}
				preserveIRCodepoints(part, "s", text)
				parts = append(parts, part)
				continue
			}
			part := map[string]any{"v": r.decNode()}
			if conv := r.enum(dspbConvKinds, "conv"); conv != "" {
				part["conv"] = conv
			}
			if flag&2 != 0 {
				part["spec"] = r.decNode()
			}
			parts = append(parts, part)
		}
		n["parts"] = parts
	case "func", "funcexpr":
		n["name"] = r.s()
		gb := r.byte()
		if gb&1 != 0 {
			n["gen"] = true
		}
		if gb&2 != 0 {
			n["coro"] = true
		}
		if gb&4 != 0 {
			n["asyncgen"] = true
		}
		if gb&8 != 0 {
			n["annotation_scope"] = true
		}
		pf := r.byte()
		p := map[string]any{}
		if pf&4 != 0 {
			p["posonly"] = float64(r.u())
		}
		setIfAny(p, "args", r.decStrs())
		setIfAny(p, "defaults", r.decNodes())
		if pf&1 != 0 {
			p["vararg"] = r.s()
		}
		setIfAny(p, "kwonly", r.decStrs())
		count := r.count()
		kwd := make([]any, 0, count)
		for i := uint64(0); i < count && r.err == nil; i++ {
			if r.byte() == 1 {
				kwd = append(kwd, r.decNode())
			} else {
				kwd = append(kwd, nil)
			}
		}
		setIfAny(p, "kwdefaults", kwd)
		if pf&2 != 0 {
			p["kwarg"] = r.s()
		}
		n["params"] = p
		setIfAny(n, "locals", r.decStrs())
		setIfAny(n, "gdecl", r.decStrs())
		setIfAny(n, "ndecl", r.decStrs())
		n["body"] = r.decNodes()
	case "yield", "return":
		if present("v") {
			n["v"] = r.decNode()
		}
	case "namedexpr":
		n["target"] = r.decNode()
		n["v"] = r.decNode()
	case "assign":
		n["targets"] = r.decNodes()
		n["v"] = r.decNode()
	case "if":
		n["test"] = r.decNode()
		n["then"] = r.decNodes()
		setIfAny(n, "orelse", r.decNodes())
	case "while":
		n["test"] = r.decNode()
		n["body"] = r.decNodes()
		setIfAny(n, "orelse", r.decNodes())
	case "for":
		n["target"] = r.decNode()
		n["iter"] = r.decNode()
		n["body"] = r.decNodes()
		setIfAny(n, "orelse", r.decNodes())
	case "break", "continue", "pass":
	case "class":
		n["name"] = r.s()
		classFlags := r.byte()
		if classFlags & ^byte(3) != 0 {
			r.fail("invalid class flags %d", classFlags)
			return n
		}
		if classFlags&2 != 0 {
			n["binding"] = r.s()
		}
		if classFlags&1 != 0 {
			count := r.count()
			kwargs := make([]any, 0, count)
			for i := uint64(0); i < count && r.err == nil; i++ {
				var name any
				if r.byte() != 0 {
					name = r.s()
				}
				kwargs = append(kwargs, map[string]any{"n": name, "v": r.decNode()})
			}
			n["kwargs"] = kwargs
		}
		setIfAny(n, "bases", r.decNodes())
		n["body"] = r.decNodes()
	case "raise":
		if present("exc") {
			n["exc"] = r.decNode()
		}
		if present("cause") {
			n["cause"] = r.decNode()
		}
	case "try":
		if r.byte() != 0 {
			n["star"] = true
		}
		n["body"] = r.decNodes()
		count := r.count()
		handlers := make([]any, 0, count)
		for i := uint64(0); i < count && r.err == nil; i++ {
			hf := r.byte()
			h := map[string]any{}
			if hf&1 != 0 {
				h["t"] = r.decNode()
			}
			if hf&2 != 0 {
				h["name"] = r.s()
			}
			h["body"] = r.decNodes()
			handlers = append(handlers, h)
		}
		setIfAny(n, "handlers", handlers)
		setIfAny(n, "orelse", r.decNodes())
		setIfAny(n, "finalbody", r.decNodes())
	case "with":
		count := r.count()
		items := make([]any, 0, count)
		for i := uint64(0); i < count && r.err == nil; i++ {
			hasName := r.byte() == 1
			item := map[string]any{"ctx": r.decNode()}
			if hasName {
				item["name"] = r.decNode()
			}
			items = append(items, item)
		}
		n["items"] = items
		n["body"] = r.decNodes()
	case "import":
		n["module"] = r.s()
		switch r.byte() {
		case 1:
			n["bind"] = "root"
		case 2:
			n["bind"] = "none"
		}
		if present("alias") {
			n["alias"] = r.s()
		}
	case "importfrom":
		n["module"] = r.s()
		count := r.count()
		names := make([]any, 0, count)
		for i := uint64(0); i < count && r.err == nil; i++ {
			names = append(names, map[string]any{"n": r.s(), "alias": r.s()})
		}
		n["names"] = names
	case "del":
		n["targets"] = r.decNodes()
	}
	if hasLn {
		n["ln"] = ln
	}
	return n
}

// DecodeDSPB decodes DSPB bytes into the compact JSON bundle model (the same
// map[string]any shape the JSON loader produces).
func DecodeDSPB(data []byte) (map[string]any, error) {
	if !LooksLikeDSPB(data) {
		return nil, fmt.Errorf("not an DSPB bundle")
	}
	r := &dspbReader{data: data, pos: len(dspbMagic)}
	count := r.count()
	for i := uint64(0); i < count && r.err == nil; i++ {
		length := r.u()
		if r.err != nil {
			break
		}
		if length > uint64(len(r.data)-r.pos) {
			r.fail("truncated string table")
			break
		}
		end := r.pos + int(length)
		raw := r.data[r.pos:end]
		if !isValidUTF8(raw) {
			r.fail("invalid UTF-8 in string table")
			break
		}
		r.pos = end
		r.strings = append(r.strings, string(raw))
	}
	if v := r.u(); r.err == nil && v != 1 {
		return nil, fmt.Errorf("unsupported semantic version %d", v)
	}
	main := r.s()
	stdlibID := ""
	if bytes.HasPrefix(data, dspbLinkedMagic) {
		stdlibID = r.s()
		if r.err == nil && !validStdlibID(stdlibID) {
			r.fail("invalid stdlib id: expected sha256: followed by 64 lowercase hexadecimal digits")
		}
	}
	modules := map[string]any{}
	modCount := r.count()
	for i := uint64(0); i < modCount && r.err == nil; i++ {
		name := r.s()
		mod := map[string]any{"name": name, "file": r.s()}
		if r.byte() == 1 {
			mod["src"] = r.decStrs()
		}
		mod["body"] = r.decNodes()
		modules[name] = mod
	}
	if r.err == nil && r.pos != len(data) {
		r.fail("trailing bytes after bundle")
	}
	if r.err != nil {
		return nil, fmt.Errorf("bad DSPB bundle: %w", r.err)
	}
	bundle := map[string]any{"diaspore": float64(1), "main": main, "modules": modules}
	if stdlibID != "" {
		bundle["stdlib"] = stdlibID
	}
	return bundle, nil
}

func isValidUTF8(b []byte) bool {
	for len(b) > 0 {
		if len(b) >= 3 && b[0] == 0xed && b[1] >= 0xa0 && b[1] <= 0xbf && b[2]&0xc0 == 0x80 {
			b = b[3:]
			continue
		}
		r, size := utf8.DecodeRune(b)
		if r == utf8.RuneError && size == 1 {
			return false
		}
		b = b[size:]
	}
	return true
}
