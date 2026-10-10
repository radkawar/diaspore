package interp

import "regexp/syntax"

// reLoop records the progress guard for an unbounded repetition. A successful
// empty iteration exits the loop without discarding that iteration's captures.
type reLoop struct {
	slot int
	exit uint32
}

type reProgress struct {
	slot  int
	reset bool
}

type reProgramBuilder struct {
	program      syntax.Prog
	loops        map[uint32]reLoop
	progress     map[uint32]reProgress
	guards       int
	specialNodes map[int]*reSpecialNode
	specials     map[uint32]reSpecialInst
}

func (b *reProgramBuilder) emit(in syntax.Inst) uint32 {
	pc := uint32(len(b.program.Inst))
	b.program.Inst = append(b.program.Inst, in)
	return pc
}

func (b *reProgramBuilder) alternate(first, second uint32) uint32 {
	return b.emit(syntax.Inst{Op: syntax.InstAlt, Out: first, Arg: second})
}

func (b *reProgramBuilder) compile(r *syntax.Regexp, next uint32) uint32 {
	switch r.Op {
	case syntax.OpNoMatch:
		return 0
	case syntax.OpEmptyMatch:
		return next
	case syntax.OpLiteral:
		for i := len(r.Rune) - 1; i >= 0; i-- {
			next = b.emit(syntax.Inst{Op: syntax.InstRune, Out: next, Rune: r.Rune[i : i+1], Arg: uint32(r.Flags & syntax.FoldCase)})
		}
		return next
	case syntax.OpCharClass:
		return b.emit(syntax.Inst{Op: syntax.InstRune, Out: next, Rune: r.Rune})
	case syntax.OpAnyChar:
		return b.emit(syntax.Inst{Op: syntax.InstRuneAny, Out: next})
	case syntax.OpAnyCharNotNL:
		return b.emit(syntax.Inst{Op: syntax.InstRuneAnyNotNL, Out: next})
	case syntax.OpBeginLine, syntax.OpEndLine, syntax.OpBeginText, syntax.OpEndText, syntax.OpWordBoundary, syntax.OpNoWordBoundary:
		ops := map[syntax.Op]syntax.EmptyOp{
			syntax.OpBeginLine:      syntax.EmptyBeginLine,
			syntax.OpEndLine:        syntax.EmptyEndLine,
			syntax.OpBeginText:      syntax.EmptyBeginText,
			syntax.OpEndText:        syntax.EmptyEndText,
			syntax.OpWordBoundary:   syntax.EmptyWordBoundary,
			syntax.OpNoWordBoundary: syntax.EmptyNoWordBoundary,
		}
		return b.emit(syntax.Inst{Op: syntax.InstEmptyWidth, Out: next, Arg: uint32(ops[r.Op])})
	case syntax.OpCapture:
		if special, ok := b.specialNodes[r.Cap]; ok {
			if special.kind == "branch" {
				return b.compile(special.node, next)
			}
			instruction := reSpecialInst{kind: special.kind, flags: special.flags, slot: special.slot, width: special.width}
			switch special.kind {
			case "reference":
			case "conditional":
				instruction.entry = b.compile(special.node.Sub[0], next)
				instruction.alternate = b.compile(special.node.Sub[1], next)
			default:
				instruction.entry = b.compile(special.node, 1)
			}
			pc := b.emit(syntax.Inst{Op: syntax.InstNop, Out: next})
			b.specials[pc] = instruction
			return pc
		}
		close := b.emit(syntax.Inst{Op: syntax.InstCapture, Out: next, Arg: uint32(2*r.Cap + 1)})
		body := b.compile(r.Sub[0], close)
		return b.emit(syntax.Inst{Op: syntax.InstCapture, Out: body, Arg: uint32(2 * r.Cap)})
	case syntax.OpConcat:
		for i := len(r.Sub) - 1; i >= 0; i-- {
			next = b.compile(r.Sub[i], next)
		}
		return next
	case syntax.OpAlternate:
		first := b.compile(r.Sub[len(r.Sub)-1], next)
		for i := len(r.Sub) - 2; i >= 0; i-- {
			first = b.alternate(b.compile(r.Sub[i], next), first)
		}
		return first
	case syntax.OpQuest:
		body := b.compile(r.Sub[0], next)
		if r.Flags&syntax.NonGreedy != 0 {
			return b.alternate(next, body)
		}
		return b.alternate(body, next)
	case syntax.OpStar, syntax.OpPlus:
		slot := b.program.NumCap + b.guards
		b.guards++
		head := b.emit(syntax.Inst{Op: syntax.InstAlt})
		body := b.compile(r.Sub[0], head)
		enter := b.emit(syntax.Inst{Op: syntax.InstNop, Out: body})
		b.progress[enter] = reProgress{slot: slot}
		b.loops[head] = reLoop{slot: slot, exit: next}
		if r.Flags&syntax.NonGreedy != 0 {
			b.program.Inst[head].Out, b.program.Inst[head].Arg = next, enter
		} else {
			b.program.Inst[head].Out, b.program.Inst[head].Arg = enter, next
		}
		first := head
		if r.Op == syntax.OpPlus {
			first = enter
		}
		reset := b.emit(syntax.Inst{Op: syntax.InstNop, Out: first})
		b.progress[reset] = reProgress{slot: slot, reset: true}
		return reset
	case syntax.OpRepeat:
		// Bounded repetitions retain separate progress decisions and share
		// capture slots, just as the original expression does.
		required := r.Min
		if r.Max < 0 {
			op := syntax.OpStar
			if required > 0 {
				op = syntax.OpPlus
				required--
			}
			repeated := &syntax.Regexp{Op: op, Flags: r.Flags, Sub: r.Sub}
			next = b.compile(repeated, next)
		} else {
			optional := &syntax.Regexp{Op: syntax.OpQuest, Flags: r.Flags, Sub: r.Sub}
			for range r.Max - r.Min {
				next = b.compile(optional, next)
			}
		}
		for range required {
			next = b.compile(r.Sub[0], next)
		}
		return next
	}
	panic("unexpected regexp syntax operation")
}

func reCompileProgram(parsed *syntax.Regexp, specialNodes map[int]*reSpecialNode) (*syntax.Prog, map[uint32]reLoop, map[uint32]reProgress, int, map[uint32]reSpecialInst) {
	builder := &reProgramBuilder{
		program:      syntax.Prog{NumCap: 2 * (parsed.MaxCap() + 1)},
		loops:        make(map[uint32]reLoop),
		progress:     make(map[uint32]reProgress),
		specialNodes: specialNodes,
		specials:     make(map[uint32]reSpecialInst),
	}
	builder.emit(syntax.Inst{Op: syntax.InstFail})
	end := builder.emit(syntax.Inst{Op: syntax.InstMatch})
	builder.program.Start = int(builder.compile(parsed, end))
	return &builder.program, builder.loops, builder.progress, builder.guards, builder.specials
}
