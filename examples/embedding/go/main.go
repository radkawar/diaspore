// Command host runs a sandboxed pricing policy with customer lookup bindings.
package main

import (
	"fmt"
	"os"

	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		return err
	}
	bundle, err := interp.LoadBundle(data)
	if err != nil {
		return err
	}
	tiers := map[string]string{"acme": "enterprise", "startup": "pro", "anon": "free"}
	credits := map[string]int64{"acme": 100_000, "startup": 5_000, "anon": 100}
	vm := interp.New(bundle, interp.WithBindings("host", map[string]interp.NativeFunc{
		"customer_tier": func(args []interp.Value) (interp.Value, error) {
			return tiers[args[0].(string)], nil
		},
		"credit_limit": func(args []interp.Value) (interp.Value, error) {
			return credits[args[0].(string)], nil
		},
	}), interp.WithMaxSteps(100_000))
	if vm.Run() != 0 {
		return fmt.Errorf("policy initialization failed")
	}
	for _, input := range []struct {
		name string
		qty  int
		unit float64
		rush bool
	}{
		{"acme", 100, 9.99, true},
		{"startup", 3, 49.0, false},
		{"anon", 5, 2.0, false},
	} {
		order, err := interp.FromGo(map[string]any{
			"customer": input.name,
			"items":    []any{map[string]any{"qty": input.qty, "unit": input.unit}},
			"rush":     input.rush,
		})
		if err != nil {
			return err
		}
		price, err := vm.Call("plugin", "price", order)
		if err != nil {
			return err
		}
		risk, err := vm.Call("plugin", "risk_score", order)
		if err != nil {
			return err
		}
		fmt.Printf("%s: total=%.2f risk=%v\n", input.name, price, risk)
	}
	return nil
}
