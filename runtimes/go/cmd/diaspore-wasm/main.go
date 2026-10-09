//go:build js && wasm

// Command diaspore-wasm exposes compilation, execution and a persistent Python
// REPL to JavaScript. Browser programs receive no capabilities and each input
// is fuel-limited. Installed package sources are supplied as an in-memory overlay.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"syscall/js"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

const defaultMaxSteps = 50_000_000
const compilerMaxSteps = 200_000_000

type runOpts struct {
	MaxSteps int64    `json:"maxSteps"`
	MaxDepth int      `json:"maxDepth"`
	Args     []string `json:"args"`
	// Durable snapshots and payloads follow docs/spec.md section 14.
	Durable        bool   `json:"durable"`
	ResumeSnapshot string `json:"resumeSnapshot"`
	Send           string `json:"send"`
}

func result(stdout, stderr string, code int, steps int64) map[string]any {
	return map[string]any{
		"stdout": stdout,
		"stderr": stderr,
		"code":   code,
		"steps":  steps,
	}
}

func diasporeRun(this js.Value, jsArgs []js.Value) (out any) {
	defer func() {
		if r := recover(); r != nil {
			out = result("", fmt.Sprintf("diaspore: internal error: %v\n", r), 70, 0)
		}
	}()
	if len(jsArgs) < 1 || jsArgs[0].Type() != js.TypeString {
		return result("", "diaspore: diasporeRun(bundleJSON, optsJSON) requires a JSON string\n", 2, 0)
	}
	bundle, err := interp.LoadBundle([]byte(jsArgs[0].String()))
	if err != nil {
		return result("", "diaspore: "+err.Error()+"\n", 2, 0)
	}
	opts := runOpts{}
	if len(jsArgs) > 1 && jsArgs[1].Type() == js.TypeString && jsArgs[1].String() != "" {
		if err := json.Unmarshal([]byte(jsArgs[1].String()), &opts); err != nil {
			return result("", "diaspore: invalid opts JSON: "+err.Error()+"\n", 2, 0)
		}
	}
	if opts.MaxSteps <= 0 {
		opts.MaxSteps = defaultMaxSteps
	}
	if opts.MaxDepth <= 0 {
		opts.MaxDepth = 1000
	}

	var stdout, stderr bytes.Buffer
	vmOpts := []interp.Option{
		interp.WithStdout(&stdout),
		interp.WithStderr(&stderr),
		interp.WithMaxSteps(opts.MaxSteps),
		interp.WithMaxDepth(opts.MaxDepth),
	}
	if len(opts.Args) > 0 {
		vmOpts = append(vmOpts, interp.WithArgs(opts.Args))
	}
	if opts.Durable || opts.ResumeSnapshot != "" {
		vmOpts = append(vmOpts, interp.WithDurable())
		vm := interp.New(bundle, vmOpts...)
		var out *interp.FlowOutcome
		if opts.ResumeSnapshot != "" {
			var send interp.Value
			if opts.Send != "" {
				v, err := interp.ParseJSONValue(opts.Send)
				if err != nil {
					return result("", "diaspore: bad send JSON: "+err.Error()+"\n", 2, 0)
				}
				send = v
			}
			var rerr error
			out, rerr = vm.Resume([]byte(opts.ResumeSnapshot), send)
			if rerr != nil {
				return result("", "diaspore: "+rerr.Error()+"\n", 2, 0)
			}
		} else {
			out = vm.RunDurable()
		}
		res := result(stdout.String(), stderr.String(), out.Code, vm.Steps())
		if out.Suspended {
			res["code"] = 7
			res["suspended"] = true
			res["snapshot"] = string(out.Snapshot)
			if p, err := interp.PayloadJSON(out.Payload); err == nil {
				res["payload"] = p
			}
		}
		return res
	}
	vm := interp.New(bundle, vmOpts...)
	code := vm.Run()
	return result(stdout.String(), stderr.String(), code, vm.Steps())
}

// compileSource grants the compiler access only to supplied in-memory sources.
func compileSource(path, source string, files map[string]string) (dspc.Result, error) {
	compiler, err := dspc.Bundle()
	if err != nil {
		return dspc.Result{}, err
	}
	overlay := make(map[string]string, len(files)+1)
	for name, text := range files {
		overlay[name] = text
	}
	overlay[path] = source
	var stdout, stderr bytes.Buffer
	vm := interp.New(compiler,
		interp.WithArgs([]string{"dspc", "--path", "/site-packages", "--path", "/stdlib", "--debug", path}),
		interp.WithCaps("fs"),
		interp.WithPolicy(map[string]map[string][]string{"fs": {"read": {"/src", "/site-packages", "/stdlib"}}}),
		interp.WithFSOverlay(overlay),
		interp.WithMaxSteps(compilerMaxSteps),
		interp.WithMaxDepth(3000),
		interp.WithStdout(&stdout),
		interp.WithStderr(&stderr),
	)
	code := vm.Run()
	return dspc.Result{Bundle: stdout.String(), Stderr: stderr.String(), Code: code}, nil
}

func sourceArgs(args []js.Value) (string, map[string]string, error) {
	if len(args) == 0 || args[0].Type() != js.TypeString {
		return "", nil, fmt.Errorf("source must be a string")
	}
	files := map[string]string{}
	if len(args) > 1 && args[1].Type() == js.TypeString && args[1].String() != "" {
		if err := json.Unmarshal([]byte(args[1].String()), &files); err != nil {
			return "", nil, fmt.Errorf("invalid source overlay: %w", err)
		}
	}
	return args[0].String(), files, nil
}

func diasporeCompile(this js.Value, args []js.Value) (out any) {
	defer func() {
		if r := recover(); r != nil {
			out = map[string]any{"bundle": "", "stderr": fmt.Sprintf("diaspore: internal error: %v\n", r), "code": 70}
		}
	}()
	source, files, err := sourceArgs(args)
	if err != nil {
		return map[string]any{"bundle": "", "stderr": err.Error() + "\n", "code": 2}
	}
	r, err := compileSource("/src/main.py", source, files)
	if err != nil {
		return map[string]any{"bundle": "", "stderr": err.Error() + "\n", "code": 70}
	}
	return map[string]any{"bundle": r.Bundle, "stderr": r.Stderr, "code": r.Code}
}

const replSource = `class _ReplInfo:
    def __init__(self, text):
        self.text = text
    def __repr__(self):
        return self.text
    def __call__(self, *objects):
        if objects:
            obj = objects[0]
            print(getattr(obj, "__doc__", None) or repr(obj))
        else:
            print(self.text)

class _ReplExit:
    def __repr__(self):
        return "Use exit() or quit() to reset the session."
    def __call__(self):
        raise SystemExit

help = _ReplInfo("Type help(object) for help about object.")
copyright = _ReplInfo("Copyright Diaspore contributors.")
credits = _ReplInfo("Diaspore is an open-source project: https://github.com/radkawar/diaspore")
license = _ReplInfo("Diaspore is distributed under the MIT license.")
exit = _ReplExit()
quit = exit

def _diaspore_step(source):
    exec(compile(source, "<stdin>", "single"))
`

type replSession struct {
	vm     *interp.VM
	stdout bytes.Buffer
	stderr bytes.Buffer
}

var session *replSession

func newSession() (*replSession, error) {
	compiled, err := compileSource("/src/__repl__.py", replSource, nil)
	if err != nil {
		return nil, err
	}
	if compiled.Code != 0 {
		return nil, fmt.Errorf("%s", compiled.Stderr)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		return nil, err
	}
	bundle["modules"].(map[string]any)[bundle["main"].(string)].(map[string]any)["file"] = "<console>"
	s := &replSession{}
	s.vm = interp.New(bundle, interp.WithStdout(&s.stdout), interp.WithStderr(&s.stderr),
		interp.WithMaxSteps(defaultMaxSteps), interp.WithMaxDepth(1000))
	if code := s.vm.Run(); code != 0 {
		return nil, fmt.Errorf("%s", s.stderr.String())
	}
	return s, nil
}

var diagnosticLine = regexp.MustCompile(`/src/__repl_input__\.py:(\d+):\s*(.*)`)

func replDiagnostic(source, diagnostic string) string {
	match := diagnosticLine.FindStringSubmatch(diagnostic)
	if match == nil {
		return strings.ReplaceAll(diagnostic, "/src/__repl_input__.py", "<stdin>")
	}
	line, _ := strconv.Atoi(match[1])
	message := strings.TrimPrefix(match[2], "syntax error: ")
	lines := strings.Split(source, "\n")
	excerpt := ""
	if line > 0 && line <= len(lines) {
		excerpt = "    " + lines[line-1] + "\n"
	}
	return fmt.Sprintf("  File \"<stdin>\", line %d\n%sSyntaxError: %s\n", line, excerpt, message)
}

func diasporeRepl(this js.Value, args []js.Value) (out any) {
	defer func() {
		if r := recover(); r != nil {
			out = result("", fmt.Sprintf("diaspore: internal error: %v\n", r), 70, 0)
		}
	}()
	source, files, err := sourceArgs(args)
	if err != nil {
		return result("", err.Error()+"\n", 2, 0)
	}
	if session == nil {
		session, err = newSession()
		if err != nil {
			return result("", err.Error()+"\n", 70, 0)
		}
	}
	s := session
	s.stdout.Reset()
	s.stderr.Reset()
	compiled, err := compileSource("/src/__repl_input__.py", source, files)
	if err != nil {
		return result("", err.Error()+"\n", 70, 0)
	}
	if compiled.Code != 0 {
		return result("", replDiagnostic(source, compiled.Stderr), compiled.Code, 0)
	}
	bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
	if err != nil {
		return result("", err.Error()+"\n", 2, 0)
	}
	if err := s.vm.AddModules(bundle); err != nil {
		return result("", err.Error()+"\n", 2, 0)
	}
	modules := bundle["modules"].(map[string]any)
	body := modules[bundle["main"].(string)].(map[string]any)["body"].([]any)
	interp.WithSnippetCompiler(func(text, mode string) ([]any, error) {
		if text == source {
			return body, nil
		}
		nested, err := compileSource("/src/__repl_eval__.py", text, files)
		if err != nil {
			return nil, err
		}
		if nested.Code != 0 {
			return nil, fmt.Errorf("%s", nested.Stderr)
		}
		b, err := interp.LoadBundle([]byte(nested.Bundle))
		if err != nil {
			return nil, err
		}
		return b["modules"].(map[string]any)[b["main"].(string)].(map[string]any)["body"].([]any), nil
	})(s.vm)
	before := s.vm.Steps()
	interp.WithMaxSteps(before + defaultMaxSteps)(s.vm)
	_, err = s.vm.Call("__repl__", "_diaspore_step", source)
	code := 0
	if err != nil {
		// The adapter frame is not part of the user's interactive input.
		if raised, ok := err.(*interp.Raised); ok && len(raised.TB) > 0 {
			raised.TB = raised.TB[:len(raised.TB)-1]
		}
		code = s.vm.ReportError(err)
		if raised, ok := err.(*interp.Raised); ok {
			if exception, ok := raised.Exc.(*interp.Instance); ok && exception.Class.Name == "SystemExit" {
				session = nil
				fmt.Fprintln(&s.stdout, "Session reset.")
			}
		}
	}
	return result(s.stdout.String(), s.stderr.String(), code, s.vm.Steps()-before)
}

func diasporeReplReset(this js.Value, args []js.Value) any {
	session = nil
	return result("", "", 0, 0)
}

func main() {
	js.Global().Set("diasporeRun", js.FuncOf(diasporeRun))
	js.Global().Set("diasporeCompile", js.FuncOf(diasporeCompile))
	js.Global().Set("diasporeRepl", js.FuncOf(diasporeRepl))
	js.Global().Set("diasporeReplReset", js.FuncOf(diasporeReplReset))
	select {}
}
