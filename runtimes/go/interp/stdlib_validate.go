package interp

import "fmt"

// DSPB supplies typed fields; these checks enforce node roles and the relational
// constraints the execution compiler relies on, without compiling unused code.
func validateStoreModule(module node) error {
	return validateStoreNodes(nList(module, "body"), 's')
}

func validateStoreNodes(nodes []any, role byte) error {
	for _, raw := range nodes {
		if err := validateStoreNode(raw, role); err != nil {
			return err
		}
	}
	return nil
}

func validateStoreNode(raw any, role byte) error {
	n, ok := raw.(map[string]any)
	if !ok {
		return fmt.Errorf("invalid module IR node")
	}
	op := nStr(n, "op")
	statement := false
	switch op {
	case "expr", "assign", "augassign", "if", "while", "for", "break", "continue", "pass", "return", "class", "raise", "try", "with", "import", "importfrom", "del":
		statement = true
	}
	if (role == 's' && !statement && op != "func") || (role == 'e' && statement) {
		return fmt.Errorf("invalid %q node in module IR", op)
	}
	if role == 't' {
		switch op {
		case "name", "attr", "index", "list", "tuple", "starred":
		default:
			return fmt.Errorf("invalid assignment target %q", op)
		}
	}
	child := func(key string, role byte) error {
		return validateStoreNode(n[key], role)
	}
	children := func(keys ...string) error {
		for _, key := range keys {
			if err := child(key, 'e'); err != nil {
				return err
			}
		}
		return nil
	}
	list := func(key string, role byte) error {
		return validateStoreNodes(nList(n, key), role)
	}
	var err error
	switch op {
	case "const":
		_, err = parseConst(n)
	case "name":
		if nStr(n, "id") == "" {
			err = fmt.Errorf("empty name in module IR")
		}
	case "attr":
		err = children("o")
	case "index":
		err = children("o", "k")
	case "slicelit":
		for _, key := range []string{"lower", "upper", "step"} {
			if n[key] != nil {
				if err = child(key, 'e'); err != nil {
					return err
				}
			}
		}
	case "starred":
		err = child("v", role)
	case "expr", "yieldfrom", "unary":
		err = children("v")
	case "yield", "return":
		if n["v"] != nil {
			err = children("v")
		}
	case "call":
		if err = children("f"); err == nil {
			err = list("args", 'e')
		}
		if err == nil {
			for _, kw := range nList(n, "kw") {
				if err = validateStoreNode(kw.(node)["v"], 'e'); err != nil {
					break
				}
			}
		}
	case "binop":
		err = children("l", "r")
	case "augassign", "namedexpr":
		if err = child("target", 't'); err == nil {
			err = children("v")
		}
	case "boolop":
		if len(nList(n, "values")) < 2 {
			return fmt.Errorf("boolop requires at least two operands")
		}
		err = list("values", 'e')
	case "compare":
		if len(nList(n, "ops")) == 0 || len(nList(n, "ops")) != len(nList(n, "comps")) {
			return fmt.Errorf("comparison operand count mismatch")
		}
		if err = children("left"); err == nil {
			err = list("comps", 'e')
		}
	case "cond":
		err = children("test", "then", "else")
	case "list", "tuple", "set":
		itemRole := byte('e')
		if role == 't' {
			itemRole = 't'
		}
		err = list("elts", itemRole)
	case "dict", "fstring":
		key := "entries"
		fields := []string{"k", "v"}
		if op == "fstring" {
			key, fields = "parts", []string{"v", "spec"}
		}
		for _, raw := range nList(n, key) {
			for _, field := range fields {
				if value := raw.(node)[field]; value != nil {
					if err = validateStoreNode(value, 'e'); err != nil {
						return err
					}
				}
			}
		}
	case "func", "funcexpr":
		p := nNode(n, "params")
		args, kwonly := nList(p, "args"), nList(p, "kwonly")
		posonly, _ := p["posonly"].(float64)
		if posonly > float64(len(args)) || len(nList(p, "defaults")) > len(args) || len(nList(p, "kwdefaults")) > len(kwonly) {
			return fmt.Errorf("invalid function parameter counts")
		}
		locals := nList(n, "locals")
		checkParam := func(name string) error {
			for _, local := range locals {
				if local == name {
					return nil
				}
			}
			return fmt.Errorf("parameter %q missing from locals", name)
		}
		for _, key := range []string{"args", "kwonly"} {
			for _, param := range nList(p, key) {
				if err = checkParam(param.(string)); err != nil {
					return err
				}
			}
		}
		for _, key := range []string{"vararg", "kwarg"} {
			if name := nStr(p, key); name != "" {
				if err = checkParam(name); err != nil {
					return err
				}
			}
		}
		for _, key := range []string{"defaults", "kwdefaults"} {
			for _, value := range nList(p, key) {
				if value != nil {
					if err = validateStoreNode(value, 'e'); err != nil {
						return err
					}
				}
			}
		}
		err = list("body", 's')
	case "assign":
		if len(nList(n, "targets")) == 0 {
			return fmt.Errorf("assignment has no targets")
		}
		if err = list("targets", 't'); err == nil {
			err = children("v")
		}
	case "if", "while", "for":
		if op == "for" {
			if err = child("target", 't'); err == nil {
				err = children("iter")
			}
		} else {
			err = children("test")
		}
		body := "body"
		if op == "if" {
			body = "then"
		}
		if err == nil {
			err = list(body, 's')
		}
		if err == nil {
			err = list("orelse", 's')
		}
	case "class":
		if err = list("bases", 'e'); err == nil {
			for _, kw := range nList(n, "kwargs") {
				if err = validateStoreNode(kw.(node)["v"], 'e'); err != nil {
					return err
				}
			}
			err = list("body", 's')
		}
	case "raise":
		for _, key := range []string{"exc", "cause"} {
			if n[key] != nil {
				if err = child(key, 'e'); err != nil {
					return err
				}
			}
		}
	case "try":
		for _, key := range []string{"body", "orelse", "finalbody"} {
			if err = list(key, 's'); err != nil {
				return err
			}
		}
		for _, raw := range nList(n, "handlers") {
			h := raw.(node)
			if h["t"] != nil {
				if err = validateStoreNode(h["t"], 'e'); err != nil {
					return err
				}
			}
			if err = validateStoreNodes(nList(h, "body"), 's'); err != nil {
				return err
			}
		}
	case "with":
		for _, raw := range nList(n, "items") {
			item := raw.(node)
			if err = validateStoreNode(item["ctx"], 'e'); err != nil {
				return err
			}
			if item["name"] != nil {
				if err = validateStoreNode(item["name"], 't'); err != nil {
					return err
				}
			}
		}
		err = list("body", 's')
	case "del":
		err = list("targets", 't')
	case "break", "continue", "pass", "import", "importfrom":
	default:
		err = fmt.Errorf("invalid module IR opcode %q", op)
	}
	return err
}
