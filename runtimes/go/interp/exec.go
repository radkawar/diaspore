package interp

import (
	"fmt"
	"strings"
)

// CodeObject is the result of compile(): pre-parsed IR statement nodes plus
// the mode ("exec", "eval" or "single") and origin filename.
type CodeObject struct {
	// Body holds the compiled IR statement nodes.
	Body []any
	// Mode selects statement execution, expression evaluation or interactive display.
	Mode string
	// Filename identifies the source in diagnostics.
	Filename string
	// Source preserves input lines for tracebacks.
	Source string
}

// compileSnippetBody obtains the IR body for a source string or code object.
func (vm *VM) compileSnippetBody(code Value, mode string) ([]any, string, error) {
	switch t := code.(type) {
	case *CodeObject:
		return t.Body, t.Mode, nil
	case string:
		if vm.snippetCompiler == nil {
			return nil, "", newError("NotImplementedError",
				"exec/eval/compile require a snippet compiler (unavailable in this host)")
		}
		body, err := vm.snippetCompiler(t, mode)
		if err != nil {
			return nil, "", newError("SyntaxError", err.Error())
		}
		return body, mode, nil
	}
	return nil, "", newError("TypeError", "exec/eval arg 1 must be a string or code object")
}

// compileSourceObject implements compile(source, filename, mode).
func (vm *VM) compileSourceObject(source Value, filename, mode string) (Value, error) {
	if co, ok := source.(*CodeObject); ok {
		return co, nil
	}
	src, ok := source.(string)
	if !ok {
		return nil, newError("TypeError", "compile() arg 1 must be a string")
	}
	if vm.snippetCompiler == nil {
		return nil, newError("NotImplementedError",
			"compile() requires a snippet compiler (unavailable in this host)")
	}
	body, err := vm.snippetCompiler(src, mode)
	if err != nil {
		return nil, newError("SyntaxError", err.Error())
	}
	return &CodeObject{Body: body, Mode: mode, Filename: filename, Source: src}, nil
}

// dictToCells materializes a namespace dict into a fresh cell table.
func dictToCells(d *Dict) map[string]*cell {
	cells := make(map[string]*cell, d.Len())
	for _, e := range d.entries {
		if k, ok := e.key.(string); ok {
			cells[k] = &cell{v: e.val}
		}
	}
	return cells
}

// cellsToDict writes a cell table back into a namespace dict.
func cellsToDict(cells map[string]*cell, d *Dict) {
	for k, c := range cells {
		if c.v != nil {
			d.SetStr(k, c.v)
		}
	}
}

// execEval runs exec()/eval(). code is a source string or CodeObject; glob is
// nil (default: the caller's module namespace) or an explicit namespace dict.
func (vm *VM) execEval(caller *frame, code Value, glob, loc Value, mode string) (Value, error) {
	body, bodyMode, err := vm.compileSnippetBody(code, mode)
	if err != nil {
		return nil, err
	}
	if mode == "eval" && bodyMode != "eval" {
		return nil, newError("SyntaxError", "eval() argument must be an expression")
	}

	var cells map[string]*cell
	var writeBack *Dict
	nsDict := glob
	if _, isNone := glob.(NoneType); isNone {
		nsDict = nil
	}
	if loc != nil {
		if _, isNone := loc.(NoneType); !isNone {
			nsDict = loc
		}
	}
	if nsDict == nil {
		// Default: run in the caller's module namespace so exec("y=1") is
		// visible afterward.
		cells = caller.globals
		if cells == nil {
			cells = caller.cells
		}
	} else {
		if namespace, ok := nsDict.(*metadataNamespace); ok && namespace.cells != nil {
			cells = namespace.cells
		} else {
			d, ok := nsDict.(*Dict)
			if !ok {
				return nil, newError("TypeError", "exec()/eval() globals must be a dict")
			}
			cells = dictToCells(d)
			writeBack = d
		}
	}

	name := fmt.Sprintf("<exec#%d>", vm.execSeq)
	vm.execSeq++
	if co, ok := code.(*CodeObject); ok {
		vm.modMeta[name] = moduleMeta{file: co.Filename, src: strings.Split(co.Source, "\n")}
	}

	if mode == "eval" {
		exprStmt, _ := body[0].(map[string]any)
		expr := nNode(exprStmt, "v")
		evalFn, cerr := compileSnippetExpr(vm, name, expr, cells)
		if cerr != nil {
			delete(vm.modCells, name)
			return nil, newError("SyntaxError", cerr.Error())
		}
		fr := &frame{cells: cells, globals: cells, moduleName: name, fname: "<eval>"}
		vm.enterMetadataFrame(fr)
		defer vm.leaveMetadataFrame(fr)
		v, rerr := evalFn(fr)
		delete(vm.modCells, name)
		if writeBack != nil {
			cellsToDict(cells, writeBack)
		}
		return v, rerr
	}

	execFn, cerr := compileSnippet(vm, name, body, cells, bodyMode == "single")
	if cerr != nil {
		delete(vm.modCells, name)
		return nil, newError("SyntaxError", cerr.Error())
	}
	fr := &frame{cells: cells, globals: cells, moduleName: name, fname: "<exec>"}
	vm.enterMetadataFrame(fr)
	defer vm.leaveMetadataFrame(fr)
	rerr := execFn(fr)
	if raised, ok := rerr.(*Raised); ok {
		vm.recordMetadataTraceback(raised, fr)
		raised.TB = append(raised.TB, tbEntry{module: name, fname: "<module>", line: fr.line})
	}
	if writeBack != nil {
		cellsToDict(cells, writeBack)
	}
	if rerr != nil && rerr != errReturn {
		return nil, rerr
	}
	return None, nil
}

// globalsDict returns the caller module's namespace as a dict.
func (vm *VM) globalsDict(fr *frame) Value {
	return vm.globalNamespace(fr)
}

// localsDict returns the caller's locals as a dict.
func (vm *VM) localsDict(fr *frame) Value {
	if fr.namespace != nil {
		return fr.namespace
	}
	if fr.cells != nil {
		return vm.frameNamespace(fr)
	}
	d := NewDict()
	if fr.vars != nil {
		for k, v := range fr.vars {
			if !strings.HasPrefix(k, ".") {
				d.SetStr(k, v)
			}
		}
		return d
	}
	for name, idx := range fr.layout {
		if !strings.HasPrefix(name, ".") && idx < len(fr.slots) && fr.slots[idx] != nil {
			d.SetStr(name, fr.slots[idx])
		}
	}
	return d
}
