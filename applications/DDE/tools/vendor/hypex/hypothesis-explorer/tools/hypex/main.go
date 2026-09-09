// hypex is a CLI for managing the hypothesis-explorer datastore.
//
// Usage:
//
//	hypex <verb> [flags] [args]
//
// Verbs: init-run, add-hypothesis, next-id, validate, list, status, report
//
// See 'hypex --help' for the full command tree.
package main

import "github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/cmd"

func main() {
	cmd.Execute()
}
