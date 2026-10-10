// Command diaspore-go compiles Python source and executes Diaspore bundles.
//
// Usage:
//
//	diaspore-go run <bundle> [prog-args...]
//	         [--allow-net [SPEC]] [--allow-fs [SPEC]] [--allow-spawn]
//	         [--max-steps N] [--max-depth N]
//	         [--durable] [--snapshot PATH] [--deterministic]
//	diaspore-go resume <bundle> <snapshot> [--send JSON] [--snapshot OUT]
//	         [--max-steps N] [--max-depth N]
//	diaspore-go build <entry.py> [-o OUT[.gz]] [--debug] [--pretty] [--path DIR ...]
//	         [--self-contained]
//
// Capability flags accept optional scoped specs (spec 13.1):
// --allow-net listen:HOST:PORT / connect:HOST:PORT ('*' wildcards),
// --allow-fs read:PATH / write:PATH. Flags may appear before or after the
// bundle path; everything after "--" is passed to the program verbatim.
package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func usage() {
	fmt.Fprintln(os.Stderr,
		"usage: diaspore-go run <bundle> [prog-args...] [--allow-net [SPEC]] [--allow-fs [SPEC]]\n"+
			"                  [--allow-spawn] [--max-steps N] [--max-depth N]\n"+
			"                  [--durable] [--snapshot PATH] [--deterministic]\n"+
			"       diaspore-go resume <bundle> <snapshot> [--send JSON] [--snapshot OUT] [--max-steps N] [--max-depth N]\n"+
			"       diaspore-go build <entry.py> [-o OUT[.gz]] [--debug] [--pretty] [--path DIR ...]\n"+
			"                         [--self-contained]")
	os.Exit(2)
}

func die(msg string) {
	fmt.Fprintf(os.Stderr, "diaspore: %s\n", msg)
	os.Exit(2)
}

// readMaybeGzip loads a file, transparently gunzipping (magic 1f 8b).
func readMaybeGzip(path string) []byte {
	data, err := os.ReadFile(path)
	if err != nil {
		die(err.Error())
	}
	if len(data) >= 2 && data[0] == 0x1f && data[1] == 0x8b {
		zr, err := gzip.NewReader(bytes.NewReader(data))
		if err != nil {
			die("bad gzip container: " + err.Error())
		}
		defer zr.Close()
		if data, err = io.ReadAll(zr); err != nil {
			die("bad gzip container: " + err.Error())
		}
	}
	return data
}

func loadBundle(path string) map[string]any {
	data, err := os.ReadFile(path)
	if err != nil {
		die(err.Error())
	}
	bundle, err := interp.LoadBundle(data)
	if err != nil {
		die(err.Error())
	}
	return bundle
}

type cliArgs struct {
	pos           []string
	netSpecs      []string // "" = bare grant
	fsSpecs       []string
	allowSpawn    bool
	maxSteps      int64
	maxDepth      int
	durable       bool
	deterministic bool
	snapshot      string
	send          string
	hasSend       bool
}

func specFor(kinds []string, tok string) (string, bool) {
	for _, k := range kinds {
		if strings.HasPrefix(tok, k+":") || tok == "all" {
			return tok, true
		}
	}
	return "", false
}

func parseArgs(rest []string) *cliArgs {
	a := &cliArgs{maxDepth: 1000}
	needVal := func(i int, name string) (string, int) {
		if eq := strings.IndexByte(rest[i], '='); eq >= 0 {
			return rest[i][eq+1:], i
		}
		i++
		if i >= len(rest) {
			die(name + " requires a value")
		}
		return rest[i], i
	}
	needInt := func(i int, name string) (int64, int) {
		raw, j := needVal(i, name)
		n, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || n < 0 {
			die(name + " requires a non-negative integer")
		}
		return n, j
	}
	for i := 0; i < len(rest); i++ {
		tok := rest[i]
		switch {
		case tok == "--":
			a.pos = append(a.pos, rest[i+1:]...)
			i = len(rest)
		case tok == "--allow-net" || strings.HasPrefix(tok, "--allow-net="):
			spec := ""
			if eq := strings.IndexByte(tok, '='); eq >= 0 {
				spec = tok[eq+1:]
			} else if i+1 < len(rest) {
				if s, ok := specFor([]string{"listen", "connect"}, rest[i+1]); ok {
					spec = s
					i++
				}
			}
			if spec != "" && spec != "all" {
				kind, addr, _ := strings.Cut(spec, ":")
				if (kind != "listen" && kind != "connect") || addr == "" {
					die(fmt.Sprintf("bad --allow-net spec %q (use listen:HOST:PORT or connect:HOST:PORT)", spec))
				}
			}
			if spec == "all" {
				spec = ""
			}
			a.netSpecs = append(a.netSpecs, spec)
		case tok == "--allow-fs" || strings.HasPrefix(tok, "--allow-fs="):
			spec := ""
			if eq := strings.IndexByte(tok, '='); eq >= 0 {
				spec = tok[eq+1:]
			} else if i+1 < len(rest) {
				if s, ok := specFor([]string{"read", "write"}, rest[i+1]); ok {
					spec = s
					i++
				}
			}
			if spec != "" && spec != "all" {
				kind, path, _ := strings.Cut(spec, ":")
				if (kind != "read" && kind != "write") || path == "" {
					die(fmt.Sprintf("bad --allow-fs spec %q (use read:PATH or write:PATH)", spec))
				}
			}
			if spec == "all" {
				spec = ""
			}
			a.fsSpecs = append(a.fsSpecs, spec)
		case tok == "--allow-spawn":
			a.allowSpawn = true
		case tok == "--durable":
			a.durable = true
		case tok == "--deterministic":
			a.deterministic = true
		case tok == "--snapshot" || strings.HasPrefix(tok, "--snapshot="):
			a.snapshot, i = needVal(i, "--snapshot")
		case tok == "--send" || strings.HasPrefix(tok, "--send="):
			a.send, i = needVal(i, "--send")
			a.hasSend = true
		case tok == "--max-steps" || strings.HasPrefix(tok, "--max-steps="):
			a.maxSteps, i = needInt(i, "--max-steps")
		case tok == "--max-depth" || strings.HasPrefix(tok, "--max-depth="):
			var n int64
			n, i = needInt(i, "--max-depth")
			a.maxDepth = int(n)
		case tok == "--no-validate":
			// The Go loader does not perform JSON Schema validation.
		case strings.HasPrefix(tok, "--"):
			die("unknown flag " + tok)
		default:
			a.pos = append(a.pos, tok)
		}
	}
	return a
}

// capOptions turns parsed grant specs into VM options.
func capOptions(a *cliArgs) []interp.Option {
	var opts []interp.Option
	policy := map[string]map[string][]string{}
	if len(a.netSpecs) > 0 {
		opts = append(opts, interp.WithCaps("net"))
		scoped := map[string][]string{"listen": {}, "connect": {}}
		allScoped := true
		for _, s := range a.netSpecs {
			if s == "" {
				allScoped = false
				continue
			}
			kind, addr, _ := strings.Cut(s, ":")
			scoped[kind] = append(scoped[kind], addr)
		}
		if allScoped {
			policy["net"] = scoped
		}
	}
	if len(a.fsSpecs) > 0 {
		opts = append(opts, interp.WithCaps("fs"))
		scoped := map[string][]string{"read": {}, "write": {}}
		allScoped := true
		for _, s := range a.fsSpecs {
			if s == "" {
				allScoped = false
				continue
			}
			kind, path, _ := strings.Cut(s, ":")
			scoped[kind] = append(scoped[kind], path)
		}
		if allScoped {
			policy["fs"] = scoped
		}
	}
	if a.allowSpawn {
		opts = append(opts, interp.WithCaps("spawn"))
	}
	if len(policy) > 0 {
		opts = append(opts, interp.WithPolicy(policy))
	}
	return opts
}

// finishDurable writes the snapshot and reports suspension like diaspore-py.
func finishDurable(out *interp.FlowOutcome, snapshotPath string) int {
	if !out.Suspended {
		return out.Code
	}
	if err := os.WriteFile(snapshotPath, out.Snapshot, 0o644); err != nil {
		die("cannot write snapshot: " + err.Error())
	}
	payload := "null"
	if p, jerr := interp.PayloadJSON(out.Payload); jerr == nil {
		payload = p
	}
	fmt.Fprintf(os.Stderr, "diaspore: suspended (payload: %s); snapshot -> %s\n",
		payload, snapshotPath)
	return 7
}

func cmdRun(rest []string) int {
	a := parseArgs(rest)
	if len(a.pos) == 0 {
		usage()
	}
	bundle := loadBundle(a.pos[0])
	stdout := bufio.NewWriter(os.Stdout)
	defer stdout.Flush()
	opts := append(capOptions(a),
		interp.WithArgs(a.pos),
		interp.WithMaxDepth(a.maxDepth),
		interp.WithStdout(stdout),
	)
	if a.maxSteps > 0 {
		opts = append(opts, interp.WithMaxSteps(a.maxSteps))
	}
	if a.deterministic {
		opts = append(opts, interp.WithDeterministic())
	}
	opts = append(opts, interp.WithSnippetCompiler(dspc.CompileSnippet))
	if a.durable {
		opts = append(opts, interp.WithDurable())
		vm := interp.New(bundle, opts...)
		defer vm.Close()
		out := vm.RunDurable()
		stdout.Flush()
		snap := a.snapshot
		if snap == "" {
			snap = a.pos[0] + ".flow.json"
		}
		return finishDurable(out, snap)
	}
	vm := interp.New(bundle, opts...)
	defer vm.Close()
	return vm.Run()
}

func cmdResume(rest []string) int {
	a := parseArgs(rest)
	if len(a.pos) < 2 {
		usage()
	}
	bundle := loadBundle(a.pos[0])
	snapshot := readMaybeGzip(a.pos[1])
	var send interp.Value
	if a.hasSend {
		v, err := interp.ParseJSONValue(a.send)
		if err != nil {
			die("bad --send JSON: " + err.Error())
		}
		send = v
	}
	stdout := bufio.NewWriter(os.Stdout)
	defer stdout.Flush()
	opts := []interp.Option{
		interp.WithDurable(),
		interp.WithMaxDepth(a.maxDepth),
		interp.WithStdout(stdout),
	}
	if a.maxSteps > 0 {
		opts = append(opts, interp.WithMaxSteps(a.maxSteps))
	}
	vm := interp.New(bundle, opts...)
	defer vm.Close()
	out, err := vm.Resume(snapshot, send)
	if err != nil {
		die(err.Error())
	}
	stdout.Flush()
	snapOut := a.snapshot
	if snapOut == "" {
		snapOut = a.pos[1]
	}
	return finishDurable(out, snapOut)
}

// cmdBuild compiles entry.py by running the embedded self-hosted compiler
// (selfhost/dspc.py) inside the VM, sandboxed to read the entry and search directories.
func cmdBuild(rest []string) int {
	var entry, out string
	var searchPaths []string
	var debug, pretty, selfContained bool
	for i := 0; i < len(rest); i++ {
		tok := rest[i]
		switch {
		case tok == "-o" || tok == "--output" || strings.HasPrefix(tok, "-o=") || strings.HasPrefix(tok, "--output="):
			if eq := strings.IndexByte(tok, '='); eq >= 0 {
				out = tok[eq+1:]
			} else {
				i++
				if i >= len(rest) {
					die("-o requires a value")
				}
				out = rest[i]
			}
		case tok == "--path" || strings.HasPrefix(tok, "--path="):
			var path string
			if eq := strings.IndexByte(tok, '='); eq >= 0 {
				path = tok[eq+1:]
			} else {
				i++
				if i >= len(rest) {
					die("--path requires a value")
				}
				path = rest[i]
			}
			searchPaths = append(searchPaths, path)
		case tok == "--debug":
			debug = true
		case tok == "--pretty":
			pretty = true
		case tok == "--self-contained":
			selfContained = true
		case tok == "--binding" || strings.HasPrefix(tok, "--binding="):
			die("--binding is not supported by the embedded compiler " +
				"(declare bindings when building with the Python CLI: uv run diaspore build --binding NAME)")
		case strings.HasPrefix(tok, "-"):
			die("unknown flag " + tok)
		default:
			if entry != "" {
				die("build takes exactly one entry file")
			}
			entry = tok
		}
	}
	if entry == "" {
		usage()
	}
	if _, err := os.Stat(entry); err != nil {
		die(err.Error())
	}
	r, err := dspc.CompileFile(entry, dspc.CompileOptions{
		Debug: debug, SearchPaths: searchPaths, SelfContained: selfContained,
	})
	if err != nil {
		die(err.Error())
	}
	os.Stderr.WriteString(r.Stderr)
	if r.Code != 0 {
		return r.Code
	}

	text := strings.TrimRight(r.Bundle, "\n")
	bundle, err := interp.LoadBundle([]byte(text))
	if err != nil {
		die("embedded compiler produced invalid JSON: " + err.Error())
	}
	if out == "" {
		fmt.Println(text)
		return 0
	}
	base := strings.TrimSuffix(out, ".gz")
	toDSPB := strings.HasSuffix(base, ".dspb")
	payload := []byte(text + "\n")
	if toDSPB {
		order := moduleOrderFromJSON(text)
		lb, err := interp.EncodeDSPB(bundle, order)
		if err != nil {
			die("cannot encode DSPB: " + err.Error())
		}
		payload = lb
	} else if pretty {
		v, perr := interp.ParseJSONValue(text)
		if perr != nil {
			die("cannot pretty-print bundle: " + perr.Error())
		}
		pjson, perr2 := interp.PrettyJSON(v)
		if perr2 != nil {
			die("cannot pretty-print bundle: " + perr2.Error())
		}
		payload = []byte(pjson + "\n")
	}
	if strings.HasSuffix(out, ".gz") {
		var zbuf bytes.Buffer
		zw, _ := gzip.NewWriterLevel(&zbuf, gzip.BestCompression)
		zw.Write(payload)
		zw.Close() // zero ModTime: output is reproducible
		payload = zbuf.Bytes()
	}
	if err := os.WriteFile(out, payload, 0o644); err != nil {
		die(err.Error())
	}
	n := 0
	if mods, ok := bundle["modules"].(map[string]any); ok {
		n = len(mods)
	}
	plural := "s"
	if n == 1 {
		plural = ""
	}
	fmt.Fprintf(os.Stderr, "diaspore: built %s (%d module%s, %d bytes)\n", out, n, plural, len(payload))
	return 0
}

// moduleOrderFromJSON recovers the compiler's module emission order from the
// serialized bundle text (Go maps are unordered), so DSPB output is
// deterministic and matches the reference encoder's insertion order.
func moduleOrderFromJSON(text string) []string {
	v, err := interp.ParseJSONValue(text)
	if err != nil {
		return nil
	}
	bundle, ok := v.(*interp.Dict)
	if !ok {
		return nil
	}
	modsV, _ := bundle.GetStr("modules")
	mods, ok := modsV.(*interp.Dict)
	if !ok {
		return nil
	}
	var order []string
	for _, k := range mods.Keys() {
		if s, ok := k.(string); ok {
			order = append(order, s)
		}
	}
	return order
}

func main() {
	args := os.Args[1:]
	if len(args) == 0 {
		usage()
	}
	switch args[0] {
	case "run":
		os.Exit(cmdRun(args[1:]))
	case "resume":
		os.Exit(cmdResume(args[1:]))
	case "build":
		os.Exit(cmdBuild(args[1:]))
	default:
		usage()
	}
}
